"""Generate the SQL that backfills `samples.SQR` / `samples.SQRP`.

Run this once, immediately after `schema/004_ip_and_sequencing_coords.sql`.
That migration moves the IP coordinates into `IPR`/`IPRP` and leaves
`SQR`/`SQRP` holding their old (IP) values; this script produces the
UPDATEs that replace them with the real sequencing coordinates read from
the run sheet.

The sheet is the `Overview_SQRs(All_SQRs)` export: semicolon-separated,
two header rows, columns `SQR#`, `SQRP#`, `SampleName`, `Project`, ...

Matching the sheet to the database takes two passes, because the two
spell the same sample differently:

1.  **Exact name.** Covers most rows. A leading ``Sample_`` on the sheet
    side is stripped first.
2.  **IP plate and well.** The sheet has been seen to write a subject id
    with hyphens where the database uses underscores, to carry a
    different project label, and to append ``_REPEAT``. The
    ``RxxPxx_<well>`` prefix survives all three, and is unique per row
    among the names that pass 1.

A handful of control wells appear twice in the sheet, having been
sequenced in two runs. Only one measurement per well ever reached the
database, so a single coordinate has to be chosen: this takes the later
run. See ``--report`` for which rows that affects.

This script only reads. It writes a .sql file for a human to apply.
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from noxdb.connection import close_pool, execute, init_pool  # noqa: E402
from noxdb.samples import canonical_plate_id  # noqa: E402


# Sheet layout: row 1 is a spanning 'Barcodes' banner, row 2 the column
# names, data from row 3.
_SHEET_HEADER_ROWS = 2
_SHEET_DELIMITER = ";"

# 'R08P01_81_Mock_1_A_T_C2' -> IP run 08, IP plate 01, well 81. The well
# number is compared as an int so '1' and '01' are one well.
_WELL_RE = re.compile(r"^R(\d+)P(\d+)_(\d+)_")


def sheet_name(raw: str) -> str:
    """Normalize a sheet SampleName to the database's spelling."""
    name = raw.strip()
    return name[len("Sample_"):] if name.startswith("Sample_") else name


def well_key(name: str) -> tuple[str, str, int] | None:
    """IP plate and well of a sample name, or None if it carries neither."""
    m = _WELL_RE.match(sheet_name(name))
    if m is None:
        return None
    return (
        canonical_plate_id(m.group(1)),
        canonical_plate_id(m.group(2)),
        int(m.group(3)),
    )


def read_sheet(path: Path) -> tuple[dict[str, set], dict[tuple, set]]:
    """Index the run sheet by exact name and by IP well.

    Returns:
        ``(by_name, by_well)`` — each maps its key to the set of
        ``(SQR, SQRP)`` coordinates the sheet gives it. A set with more
        than one member is a well that was sequenced more than once.
    """
    by_name: dict[str, set] = collections.defaultdict(set)
    by_well: dict[tuple, set] = collections.defaultdict(set)
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh, delimiter=_SHEET_DELIMITER)
        for _ in range(_SHEET_HEADER_ROWS):
            next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            coords = (canonical_plate_id(row[0]), canonical_plate_id(row[1]))
            by_name[sheet_name(row[2])].add(coords)
            key = well_key(row[2])
            if key is not None:
                by_well[key].add(coords)
    return by_name, by_well


def resolve(db_rows, by_name, by_well):
    """Pair every database row with one ``(SQR, SQRP)`` from the sheet.

    Returns:
        ``(updates, repeats, unresolved)``. *updates* is a list of
        ``(sample_id, sample_name, sqr, sqrp)``; *repeats* lists the
        rows where the sheet offered several coordinates and the later
        run was taken; *unresolved* lists rows neither pass matched.
    """
    updates, repeats, unresolved = [], [], []
    for row in db_rows:
        name = row["sample_name"]
        coords = by_name.get(sheet_name(name))
        if not coords:
            key = well_key(name)
            coords = by_well.get(key) if key is not None else None
        if not coords:
            unresolved.append(row)
            continue
        chosen = sorted(coords)[-1]
        if len(coords) > 1:
            repeats.append((name, sorted(coords), chosen))
        updates.append((row["sample_id"], name, chosen[0], chosen[1]))
    return updates, repeats, unresolved


def render_sql(updates) -> str:
    """Render the updates as one temporary-table join.

    6722 standalone UPDATEs would be 6722 round trips and 6722 audit-log
    lines; staging them and joining once keeps it to a single statement
    against `samples`, which also means one Galera write-set.
    """
    values = ",\n".join(
        f"  ({sid}, '{sqr}', '{sqrp}')" for sid, _name, sqr, sqrp in updates
    )
    return f"""-- Backfill samples.SQR / samples.SQRP from the run sheet.
-- Generated by scripts/backfill_sequencing_coords.py — do not hand-edit.
-- Apply AFTER schema/004_ip_and_sequencing_coords.sql.
--
-- Rows: {len(updates)}

USE ccr_metadata;

CREATE TEMPORARY TABLE _seq_coords (
    sample_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    SQR       VARCHAR(10) NOT NULL,
    SQRP      VARCHAR(10) NOT NULL
) ENGINE=InnoDB;

INSERT INTO _seq_coords (sample_id, SQR, SQRP) VALUES
{values};

START TRANSACTION;

UPDATE samples s
JOIN _seq_coords t ON t.sample_id = s.sample_id
SET s.SQR = t.SQR, s.SQRP = t.SQRP;

-- Must report 0. Any row left behind still holds an IP coordinate.
SELECT COUNT(*) AS samples_not_backfilled
FROM samples s LEFT JOIN _seq_coords t ON t.sample_id = s.sample_id
WHERE t.sample_id IS NULL;

COMMIT;

DROP TEMPORARY TABLE _seq_coords;
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate the SQR/SQRP backfill SQL from the run sheet.",
    )
    ap.add_argument("sheet", type=Path,
                    help="Overview_SQRs(All_SQRs) CSV export.")
    ap.add_argument("-o", "--out", type=Path,
                    default=Path("backfill_sequencing_coords.sql"),
                    help="Where to write the generated SQL.")
    ap.add_argument("--report", action="store_true",
                    help="List the re-sequenced wells and the run chosen.")
    args = ap.parse_args()

    if not args.sheet.is_file():
        print(f"ERROR: no such sheet: {args.sheet}", file=sys.stderr)
        return 2

    by_name, by_well = read_sheet(args.sheet)
    print(f"sheet: {len(by_name)} names, {len(by_well)} IP wells")

    init_pool()
    try:
        db_rows = execute(
            "SELECT sample_id, sample_name, sample_type FROM samples "
            "ORDER BY sample_id"
        )
    finally:
        close_pool()
    print(f"database: {len(db_rows)} samples")

    updates, repeats, unresolved = resolve(db_rows, by_name, by_well)

    print(f"resolved: {len(updates)}  re-sequenced: {len(repeats)}  "
          f"unresolved: {len(unresolved)}")
    if args.report and repeats:
        print("\nre-sequenced wells (took the later run):")
        for name, offered, chosen in repeats:
            others = ", ".join(f"{a}/{b}" for a, b in offered if (a, b) != chosen)
            print(f"  {name}: {chosen[0]}/{chosen[1]}  (dropped {others})")
    if unresolved:
        print(f"\nERROR: {len(unresolved)} samples matched no sheet row; "
              "refusing to write a partial backfill:", file=sys.stderr)
        for row in unresolved[:20]:
            print(f"  {row['sample_name']}", file=sys.stderr)
        return 1

    args.out.write_text(render_sql(updates), encoding="utf-8")
    print(f"\nwrote {args.out} ({len(updates)} rows)")
    print("apply with:")
    print(f"  mysql -u <user> -p ccr_metadata < {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
