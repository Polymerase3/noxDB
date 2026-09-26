#!/usr/bin/env python
"""Set SQR/SQRP and the i7/i5 barcodes of existing samples from the run sheet.

Usage:
    python scripts/apply_run_sheet.py SHEET [--project NAME] [--out CSV]
                                      [--overwrite] [--commit]

SHEET is the ``Overview_SQRs(All_SQRs)`` CSV export. Every sample in noxDB
(or only PROJECT's, with --project) is matched to its row by name, falling
back to IP plate and well; see ``noxdb.run_sheet``.

Without --commit nothing is written: the script reports what it would
fill in, and --out writes the per-sample changes to a CSV for review.
Empty columns are filled in. A stored value that differs from the sheet
is a conflict; --commit refuses while there are any, unless --overwrite
is given. Everything is written in one transaction.

Exit codes:
  0  nothing to do, dry run finished, or changes written
  1  refused: conflicts without --overwrite, or rows that can't be applied
  2  the sheet could not be read
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from noxdb import run_sheet  # noqa: E402
from noxdb.connection import close_pool, init_pool, transaction  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Set SQR/SQRP and barcodes of existing samples from the run sheet.",
    )
    ap.add_argument("sheet", type=Path, help="Overview_SQRs(All_SQRs) CSV export.")
    ap.add_argument("--project", help="Only this project's samples.")
    ap.add_argument("--out", type=Path, help="Write the planned changes to this CSV.")
    ap.add_argument("--overwrite", action="store_true",
                    help="Replace stored values that differ from the sheet.")
    ap.add_argument("--commit", action="store_true",
                    help="Write the changes. Without it this is a dry run.")
    return ap.parse_args(argv)


def summarize(rows, db_samples, result, changes, *, project: str | None) -> list[str]:
    """The report's lines."""
    by = Counter(m.by for m in result.matches)
    resequenced = sum(1 for m in result.matches if m.offered > 1)
    filled = Counter(col for c in changes if not c.error for col in c.changes)
    conflicting = [c for c in changes if c.conflicts]
    errors = [c for c in changes if c.error]
    scope = f"in project {project!r}" if project else "in noxDB"
    lines = [
        f"run sheet: {len(rows)} rows",
        f"samples {scope}: {len(db_samples)}",
        f"matched: {len(result.matches)} ({by['name']} by name, {by['well']} by IP plate "
        f"and well; {resequenced} sequenced more than once, later run taken)",
        f"samples without a sheet row: {len(result.unmatched_samples)}",
        f"sheet rows no sample took: {len(result.unused_rows)}",
        f"samples that would change: {sum(1 for c in changes if not c.error)}",
    ]
    lines += [f"  {col}: {n}" for col, n in sorted(filled.items())]
    lines.append(f"samples whose stored values differ from the sheet: {len(conflicting)}")
    lines += [f"  {c.sample_name}: {'; '.join(c.conflicts)}" for c in conflicting[:20]]
    if len(conflicting) > 20:
        lines.append(f"  ... and {len(conflicting) - 20} more (see --out)")
    lines.append(f"rows that cannot be applied: {len(errors)}")
    lines += [f"  {c.sample_name}: {c.error}" for c in errors[:20]]
    return lines


def write_changes(path: Path, changes) -> None:
    """One CSV line per changed column: sample, column, old, new, conflict."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_name", "column", "new_value", "replaces_a_different_value", "error"])
        for c in changes:
            if c.error:
                w.writerow([c.sample_name, "", "", "", c.error])
            conflicted = {text.split(" ", 1)[0] for text in c.conflicts}
            for col, value in c.changes.items():
                w.writerow([c.sample_name, col, value, col in conflicted, ""])


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        rows = run_sheet.read_run_sheet(args.sheet)
    except (OSError, ValueError) as exc:
        print(f"ERROR reading the run sheet: {exc}", file=sys.stderr)
        return 2

    init_pool()
    try:
        with transaction() as cur:
            db_samples = run_sheet.samples_to_match(cur, project_name=args.project)
            result = run_sheet.match_samples(rows, db_samples)
            changes = run_sheet.plan_changes(result.matches)
            print("\n".join(summarize(rows, db_samples, result, changes, project=args.project)))
            if args.out:
                write_changes(args.out, changes)
                print(f"\nwrote {args.out}")
            if not args.commit:
                print("\nDry run: nothing written. Rerun with --commit to write.")
                return 0
            try:
                n = run_sheet.apply(cur, changes, overwrite=args.overwrite)
            except ValueError as exc:
                print(f"\nREFUSED, nothing written: {exc}", file=sys.stderr)
                return 1
            print(f"\nwrote {n} sample(s)")
    finally:
        close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
