#!/usr/bin/env python3
"""Per-project metadata completeness in noxDB.

For each project, over its study samples only (controls and inputs carry no
clinical metadata by design):

    has_sex          share of subjects with sex M or F
    has_age          share of visits with an age
    has_group        share of visits with a group_test other than '' / 'unknown'
    is_longitudinal  any subject with more than one timepoint; also the share
                     of subjects that have several, and the timepoints used
    meta fields      each visit_metadata key and the share of visits that have it
    has_counts       share of samples with a registered counts file

Imported by ``noxdb_sweep.py`` (the ``metadata`` check); run on its own for
the full table::

    python scripts/sweep/metadata_completeness.py [--out completeness.csv]
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

UNKNOWN_GROUPS = ("", "unknown")
COMPLETE_AT = 90.0     # a field counts as "complete" for a project at >= this %


def fetch(cur) -> Tuple[List[tuple], Dict[int, Set[str]], Set[int]]:
    cur.execute(
        "SELECT p.project_name, s.sample_id, v.visit_id, v.subject_id, sb.sex, v.age, "
        "v.group_test, v.timepoint "
        "FROM projects p "
        "JOIN project_samples ps ON ps.project_id = p.project_id "
        "JOIN samples s ON s.sample_id = ps.sample_id "
        "JOIN visits v ON v.visit_id = s.visit_id "
        "JOIN subjects sb ON sb.subject_id = v.subject_id "
        "WHERE s.sample_type = 'sample'"
    )
    rows = cur.fetchall()
    cur.execute("SELECT visit_id, key_name FROM visit_metadata")
    meta: Dict[int, Set[str]] = defaultdict(set)
    for vid, key in cur.fetchall():
        meta[vid].add(key)
    cur.execute("SELECT DISTINCT sample_id FROM sample_files WHERE file_type = 'counts'")
    counts = {r[0] for r in cur.fetchall()}
    return rows, meta, counts


def _pct(a: int, b: int) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


def compute_completeness(rows: Iterable[tuple], visit_meta: Dict[int, Set[str]],
                         with_counts: Set[int]) -> Dict[str, Any]:
    """rows: (project, sample_id, visit_id, subject_id, sex, age, group_test, timepoint)."""
    per: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "samples": set(), "visits": {}, "subjects": {}, "tps": defaultdict(set)})
    for project, sid, vid, subj, sex, age, group, tp in rows:
        d = per[project]
        d["samples"].add(sid)
        d["visits"][vid] = (age, group)
        d["subjects"][subj] = sex
        d["tps"][subj].add(tp)

    projects = []
    for name, d in sorted(per.items()):
        n_subj, n_vis, n_smp = len(d["subjects"]), len(d["visits"]), len(d["samples"])
        multi = sum(1 for tps in d["tps"].values() if len(tps) > 1)
        keys = Counter(k for vid in d["visits"] for k in visit_meta.get(vid, ()))
        tp_counts = Counter(tp for tps in d["tps"].values() for tp in tps)
        projects.append({
            "project": name, "samples": n_smp, "subjects": n_subj, "visits": n_vis,
            "has_sex": _pct(sum(1 for s in d["subjects"].values() if s in ("M", "F")), n_subj),
            "has_age": _pct(sum(1 for a, _ in d["visits"].values() if a is not None), n_vis),
            "has_group": _pct(sum(1 for _, g in d["visits"].values() if (g or "").strip() not in UNKNOWN_GROUPS), n_vis),
            "is_longitudinal": multi > 0,
            "multi_timepoint_subjects": _pct(multi, n_subj),
            "timepoints": [tp for tp, _ in tp_counts.most_common(8)],
            "meta_fields": {k: _pct(v, n_vis) for k, v in sorted(keys.items())},
            "has_counts": _pct(len(d["samples"] & with_counts), n_smp),
        })

    def complete(p: Dict[str, Any], f: str) -> bool:
        return p[f] >= COMPLETE_AT

    n = len(projects)
    return {
        "projects": projects,
        "n_projects": n,
        "complete_sex": sum(complete(p, "has_sex") for p in projects),
        "complete_age": sum(complete(p, "has_age") for p in projects),
        "complete_group": sum(complete(p, "has_group") for p in projects),
        "complete_all": sum(all(complete(p, f) for f in ("has_sex", "has_age", "has_group")) for p in projects),
        "longitudinal": sum(p["is_longitudinal"] for p in projects),
    }


def completeness_report(cur) -> Dict[str, Any]:
    return compute_completeness(*fetch(cur))


def summary_line(r: Dict[str, Any]) -> str:
    return (f"{r['complete_all']} of {r['n_projects']} projects have sex, age and group for "
            f">={COMPLETE_AT:g}% of samples (sex {r['complete_sex']}, age {r['complete_age']}, "
            f"group {r['complete_group']}); {r['longitudinal']} longitudinal")


def worst_first(projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(projects, key=lambda p: (min(p["has_sex"], p["has_age"], p["has_group"]), p["project"]))


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=None, help="write the per-project table to this CSV")
    args = ap.parse_args(argv)

    from noxdb import close_pool, init_pool, transaction
    init_pool()
    try:
        with transaction() as cur:
            r = completeness_report(cur)
    finally:
        close_pool()
    print(summary_line(r))
    print(f"\n{'project':40} {'samples':>7} {'sex%':>6} {'age%':>6} {'group%':>6} {'longit':>6} {'counts%':>7}  meta fields")
    for p in worst_first(r["projects"]):
        print(f"{p['project'][:40]:40} {p['samples']:7} {p['has_sex']:6} {p['has_age']:6} {p['has_group']:6} "
              f"{('yes' if p['is_longitudinal'] else 'no'):>6} {p['has_counts']:7}  {', '.join(p['meta_fields'])[:60]}")
    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["project", "samples", "subjects", "visits", "has_sex_pct", "has_age_pct", "has_group_pct",
                        "is_longitudinal", "multi_timepoint_subjects_pct", "timepoints", "has_counts_pct", "meta_fields"])
            for p in r["projects"]:
                w.writerow([p["project"], p["samples"], p["subjects"], p["visits"], p["has_sex"], p["has_age"],
                            p["has_group"], int(p["is_longitudinal"]), p["multi_timepoint_subjects"],
                            "|".join(p["timepoints"]), p["has_counts"],
                            "; ".join(f"{k} {v}%" for k, v in p["meta_fields"].items())])


if __name__ == "__main__":
    main()
