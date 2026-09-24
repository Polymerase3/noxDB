#!/usr/bin/env python3
"""Coverage of the lab's sequencing master sheet by noxDB.

The master sheet lists every well the lab ever sequenced (one row per
sample/control per sequencing run and plate). This module answers: of what
was sequenced, how much is in noxDB, per sequencing run and per project?

It is imported by ``noxdb_sweep.py`` (the ``coverage`` check) and can be run
on its own for the full report::

    python scripts/sweep/sqr_coverage.py [--master PATH] [--out report.csv]

Master sheet columns used (CSV, comma-separated, header row):

    sqr, sqrp, ipr, iprp, sheet_name, seq_name, kind, label, noxdb_project,
    status_note, has_counts, has_fastq, has_bam, has_parquet

``seq_name`` is the name the sample was sequenced/processed under (the
count-file name on LiSC); ``sheet_name`` is the run-sheet spelling, used when
``seq_name`` is empty. ``kind`` is ``sample`` for study samples and
``mockIP``/``anchor``/``NC``/``input``/... for everything else. The ``has_*``
columns are ``1``/``0`` snapshots taken when the sheet was built.

A master row counts as "in noxDB" when a ``samples.sample_name`` equals its
name, equals it after normalisation (separators, ``Sample_`` prefix,
well zero-padding and case), or sits in the same IP plate and well with one
name extending the other (noxDB added a project tag to some PIC/SAR names
that the files lack). Older run sheets spell names differently from what
was sequenced, so an exact match alone undercounts.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_MASTER = "/lisc/data/work/ccr/mariaDB/master_sequenced_samples.csv"
MASTER_ENV = "NOXDB_MASTER_SHEET"
FILE_FLAGS = ("has_counts", "has_fastq", "has_bam", "has_parquet")


def master_path() -> Path:
    return Path(os.environ.get(MASTER_ENV, DEFAULT_MASTER)).expanduser()


def norm_key(name: str) -> str:
    """Formatting-insensitive sample-name key."""
    s = re.sub(r"^Sample_", "", (name or "").strip())
    s = re.sub(r"[-/. ,#()]+", "_", s).upper()
    s = re.sub(r"^(R\d+(?:P\d+)?_(?:INPUT\d?_)?)0*(\d+)_", r"\1\2_", s)
    return re.sub(r"_+", "_", s).strip("_")


_WELL_RE = re.compile(r"^(R\d+P\d+)_(\d+)_")
_LIB_RE = re.compile(r"_(A_T_C2.*|A_V0_S|A)$")


def _plate_well(key: str) -> Optional[Tuple[str, str]]:
    m = _WELL_RE.match(key)
    return (m.group(1), m.group(2)) if m else None


def _stem(key: str) -> str:
    return _LIB_RE.sub("", key)


def read_master(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_db_samples(cur) -> Dict[str, List[str]]:
    """sample_name -> project names it is linked to (empty list if none)."""
    cur.execute(
        "SELECT s.sample_name, p.project_name FROM samples s "
        "LEFT JOIN project_samples ps ON ps.sample_id = s.sample_id "
        "LEFT JOIN projects p ON p.project_id = ps.project_id"
    )
    out: Dict[str, List[str]] = defaultdict(list)
    for name, project in cur.fetchall():
        lst = out[name]
        if project:
            lst.append(project)
    return out


def _flag(row: Dict[str, str], key: str) -> bool:
    return (row.get(key) or "").strip() in ("1", "true", "True", "yes")


def compute_coverage(master: Iterable[Dict[str, str]], db: Dict[str, List[str]]) -> Dict[str, Any]:
    """Pure computation, separated from I/O for testing."""
    by_norm = {norm_key(n): n for n in db}
    # Third fallback: noxDB sometimes appended a project tag the run sheet and
    # the files lack (R42P02_01_SAR19_A_T_C2 vs ..._SAR19_PIC_SAR_MUW_A_T_C2).
    # Same IP plate + well, and one name extends the other.
    by_well: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for k, n in by_norm.items():
        pw = _plate_well(k)
        if pw:
            by_well[pw].append(k)

    def match(name: str) -> Optional[str]:
        if name in db:
            return name
        k = norm_key(name)
        if k in by_norm:
            return by_norm[k]
        pw = _plate_well(k)
        if not pw:
            return None
        a = _stem(k)
        hits = [c for c in by_well.get(pw, ()) if _stem(c).startswith(a + "_") or a.startswith(_stem(c) + "_")]
        return by_norm[hits[0]] if len(hits) == 1 else None

    matched_db = set()
    totals = Counter()
    by_sqr: Dict[str, Counter] = defaultdict(Counter)
    by_label: Dict[str, Dict[str, Any]] = {}
    files = Counter()

    for row in master:
        name = (row.get("seq_name") or row.get("sheet_name") or "").strip()
        if not name:
            continue
        kind = (row.get("kind") or "sample").strip()
        is_sample = kind == "sample"
        hit = match(name)
        if hit:
            matched_db.add(hit)
        grp = "samples" if is_sample else "controls"
        totals[grp] += 1
        totals[f"{grp}_in_db"] += bool(hit)
        sqr = (row.get("sqr") or "").strip()
        by_sqr[sqr][grp] += 1
        by_sqr[sqr][f"{grp}_in_db"] += bool(hit)
        if is_sample:
            for flag in FILE_FLAGS:
                files[flag] += _flag(row, flag)
            label = (row.get("label") or "").strip() or "(no project label)"
            lab = by_label.setdefault(label, {"samples": 0, "in_db": 0, "noxdb_projects": set(),
                                               "status_notes": Counter()})
            lab["samples"] += 1
            lab["in_db"] += bool(hit)
            if hit:
                lab["noxdb_projects"].update(db[hit])
            note = (row.get("status_note") or "").strip()
            if note and not hit:
                lab["status_notes"][note] += 1

    labels = []
    for label, d in sorted(by_label.items()):
        status = "complete" if d["in_db"] == d["samples"] else "absent" if d["in_db"] == 0 else "partial"
        labels.append({"label": label, "samples": d["samples"], "in_db": d["in_db"],
                       "missing": d["samples"] - d["in_db"], "status": status,
                       "noxdb_projects": sorted(d["noxdb_projects"]),
                       "why_missing": dict(d["status_notes"])})
    status_counts = Counter(x["status"] for x in labels if x["label"] != "(no project label)")
    db_only = sorted(n for n in db if n not in matched_db)

    def pct(a: int, b: int) -> float:
        return round(100.0 * a / b, 1) if b else 0.0

    return {
        "samples": totals["samples"], "samples_in_db": totals["samples_in_db"],
        "samples_pct": pct(totals["samples_in_db"], totals["samples"]),
        "controls": totals["controls"], "controls_in_db": totals["controls_in_db"],
        "projects_complete": status_counts["complete"], "projects_partial": status_counts["partial"],
        "projects_absent": status_counts["absent"],
        "by_sqr": {k: dict(v) for k, v in sorted(by_sqr.items())},
        "by_label": labels,
        "file_flags": {k: files[k] for k in FILE_FLAGS},
        "db_samples_not_in_master": len(db_only),
        "db_samples_not_in_master_sample": db_only[:20],
    }


def coverage_report(cur, path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or master_path()
    report = compute_coverage(read_master(path), fetch_db_samples(cur))
    report["master_path"] = str(path)
    report["master_mtime"] = os.path.getmtime(path)
    return report


def summary_line(r: Dict[str, Any]) -> str:
    return (f"{r['samples_in_db']} of {r['samples']} sequenced samples in noxDB ({r['samples_pct']}%); "
            f"projects: {r['projects_complete']} complete, {r['projects_partial']} partial, "
            f"{r['projects_absent']} absent")


def _print_report(r: Dict[str, Any]) -> None:
    print(f"master sheet: {r['master_path']}")
    print(summary_line(r))
    print(f"controls: {r['controls_in_db']} of {r['controls']} in noxDB")
    print("files among sequenced samples: " + ", ".join(f"{k}={v}" for k, v in r["file_flags"].items()))
    print(f"noxDB samples not in the master sheet: {r['db_samples_not_in_master']}")
    print("\nby sequencing run (samples in noxDB / sequenced):")
    for sqr, d in r["by_sqr"].items():
        print(f"  SQR{sqr}: {d.get('samples_in_db', 0)}/{d.get('samples', 0)}"
              f"   controls {d.get('controls_in_db', 0)}/{d.get('controls', 0)}")
    print("\nprojects not complete:")
    for x in r["by_label"]:
        if x["status"] != "complete":
            why = "; ".join(f"{k} ({v})" for k, v in x["why_missing"].items())
            print(f"  {x['status']:8} {x['label']:40} {x['in_db']:5}/{x['samples']:<5} {why}")


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", type=Path, default=None, help=f"master sheet (default ${MASTER_ENV} or {DEFAULT_MASTER})")
    ap.add_argument("--out", type=Path, default=None, help="write the per-project table to this CSV")
    args = ap.parse_args(argv)

    from noxdb import close_pool, init_pool, transaction
    init_pool()
    try:
        with transaction() as cur:
            report = coverage_report(cur, args.master)
    finally:
        close_pool()
    _print_report(report)
    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["label", "status", "samples", "in_db", "missing", "noxdb_projects", "why_missing"])
            for x in report["by_label"]:
                w.writerow([x["label"], x["status"], x["samples"], x["in_db"], x["missing"],
                            "|".join(x["noxdb_projects"]),
                            "; ".join(f"{k} ({v})" for k, v in x["why_missing"].items())])


if __name__ == "__main__":
    main()
