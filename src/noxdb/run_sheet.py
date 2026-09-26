"""Read the sequencing run sheet and apply it to samples already in noxDB.

The run sheet is the ``All_SQRs`` tab of the lab's "Overview_SQRs"
workbook, exported as ``;``-separated CSV: a ``Barcodes`` banner row,
then the column names, then one row per sequenced well. It is the only
source of ``SQR``/``SQRP`` and of the i7/i5 barcodes. Columns are found
by name: ``SQR#``, ``SQRP#``, ``SampleName``, ``i7 index``,
``i7 index ID``, ``i5 index``, ``i5 index ID``.

The sheet and the database spell some samples differently, so matching
takes two passes:

1.  **Exact name.** Covers most rows. A leading ``Sample_`` on the sheet
    side is stripped first.
2.  **IP plate and well.** The sheet has been seen to write a subject id
    with hyphens where the database uses underscores, to carry a
    different project label, and to append ``_REPEAT``. The
    ``RxxPxx_<well>`` prefix survives all three.

A well sequenced in more than one run matches several rows. Only one
measurement per well ever reached the database, so the later run is
taken, as ``scripts/backfill_sequencing_coords.py`` did.

Applying goes through
[`samples.sequencing_changes`][noxdb.samples.sequencing_changes]: empty
columns are filled in, equal ones left alone, and a different stored
value is a conflict that is only replaced on request. The
``scripts/apply_run_sheet.py`` command wraps all of this.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from noxdb import samples
from noxdb.samples import canonical_plate_id

# Sheet column names, keyed by the field they fill.
_HEADERS = {
    "sqr": "SQR#",
    "sqrp": "SQRP#",
    "sample_name": "SampleName",
    "i7_index": "i7 index",
    "i7_index_id": "i7 index ID",
    "i5_index": "i5 index",
    "i5_index_id": "i5 index ID",
}

# 'R08P01_81_Mock_1_A_T_C2' -> IP run 08, IP plate 01, well 81. The well
# number is compared as an int so '1' and '01' are one well.
_WELL_RE = re.compile(r"^R(\d+)P(\d+)_(\d+)_")


@dataclass(frozen=True)
class RunSheetRow:
    """One sequenced well of the run sheet."""
    line: int
    sample_name: str
    sqr: str
    sqrp: str
    i7_index: str | None = None
    i7_index_id: str | None = None
    i5_index: str | None = None
    i5_index_id: str | None = None

    def sequencing(self) -> dict[str, str | None]:
        """The values this row sets, as keyword arguments for ``samples``."""
        return {
            "sqr": self.sqr, "sqrp": self.sqrp,
            "i7_index": self.i7_index, "i7_index_id": self.i7_index_id,
            "i5_index": self.i5_index, "i5_index_id": self.i5_index_id,
        }


@dataclass(frozen=True)
class Match:
    """A sample in noxDB and the run-sheet row chosen for it."""
    sample: dict[str, Any]
    row: RunSheetRow
    by: str            # "name" or "well"
    offered: int       # rows that matched; more than one means re-sequenced


@dataclass
class MatchResult:
    matches: list[Match] = field(default_factory=list)
    unmatched_samples: list[dict[str, Any]] = field(default_factory=list)
    unused_rows: list[RunSheetRow] = field(default_factory=list)


@dataclass(frozen=True)
class Change:
    """What applying a match would do to one sample."""
    sample_id: int
    sample_name: str
    changes: dict[str, Any]
    conflicts: list[str]
    error: str | None = None


def sheet_name(raw: str) -> str:
    """Normalize a sheet SampleName to the database's spelling."""
    name = raw.strip()
    return name[len("Sample_"):] if name.startswith("Sample_") else name


def well_key(name: str) -> tuple[str, str, int] | None:
    """IP plate and well of a sample name, or ``None`` if it carries neither."""
    m = _WELL_RE.match(sheet_name(name))
    if m is None:
        return None
    return (
        canonical_plate_id(m.group(1)),
        canonical_plate_id(m.group(2)),
        int(m.group(3)),
    )


def read_run_sheet(path: str | Path, *, encoding: str = "utf-8-sig") -> list[RunSheetRow]:
    """Return every sequenced well in the run sheet.

    Args:
        path: The ``Overview_SQRs(All_SQRs)`` CSV export.
        encoding: File encoding. Undecodable bytes are replaced.

    Returns:
        One [`RunSheetRow`][noxdb.run_sheet.RunSheetRow] per row with a
        sample name, in sheet order. ``SQR``/``SQRP`` are canonical;
        barcodes are stripped, and ``None`` when empty.

    Raises:
        ValueError: If no header row has all the expected column names.
    """
    with Path(path).open(encoding=encoding, errors="replace", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))

    for h, header in enumerate(rows[:10]):
        cells = [c.strip() for c in header]
        if all(name in cells for name in _HEADERS.values()):
            col = {key: cells.index(name) for key, name in _HEADERS.items()}
            break
    else:
        raise ValueError(
            f"{path}: no header row with all of {sorted(_HEADERS.values())}"
        )

    width = max(col.values()) + 1
    out: list[RunSheetRow] = []
    for line, row in enumerate(rows[h + 1:], start=h + 2):
        row = row + [""] * (width - len(row))
        name = sheet_name(row[col["sample_name"]])
        if not name:
            continue
        cell = {key: row[i].strip() or None for key, i in col.items()}
        out.append(RunSheetRow(
            line=line,
            sample_name=name,
            sqr=canonical_plate_id(cell["sqr"]),
            sqrp=canonical_plate_id(cell["sqrp"]),
            i7_index=cell["i7_index"],
            i7_index_id=cell["i7_index_id"],
            i5_index=cell["i5_index"],
            i5_index_id=cell["i5_index_id"],
        ))
    return out


def samples_to_match(cur, *, project_name: str | None = None) -> list[dict[str, Any]]:
    """The samples a run sheet is matched against, with their sequencing columns.

    Args:
        cur: Cursor from [`transaction`][noxdb.connection.transaction].
        project_name: Only this project's samples (including its linked
            controls). ``None`` for every sample.

    Returns:
        One dict per sample: ``sample_id``, ``sample_name`` and the
        ``samples.SEQUENCING_COLUMNS``.
    """
    columns = ", ".join(f"s.{c}" for c in ("sample_id", "sample_name", *samples.SEQUENCING_COLUMNS))
    if project_name is None:
        cur.execute(f"SELECT {columns} FROM samples s ORDER BY s.sample_id")
    else:
        cur.execute(
            f"SELECT {columns} FROM samples s "
            "JOIN project_samples ps ON ps.sample_id = s.sample_id "
            "JOIN projects p ON p.project_id = ps.project_id "
            "WHERE p.project_name = ? ORDER BY s.sample_id",
            (project_name,),
        )
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def match_samples(rows: Iterable[RunSheetRow], db_samples: Iterable[dict[str, Any]]) -> MatchResult:
    """Pair each sample with the run-sheet row that describes it.

    Args:
        rows: From [`read_run_sheet`][noxdb.run_sheet.read_run_sheet].
        db_samples: Dicts with at least ``sample_name``, e.g. from
            [`samples_to_match`][noxdb.run_sheet.samples_to_match].

    Returns:
        A [`MatchResult`][noxdb.run_sheet.MatchResult]: the matches, the
        samples no row describes, and the rows no sample took.
    """
    rows = list(rows)
    by_name: dict[str, list[RunSheetRow]] = defaultdict(list)
    by_well: dict[tuple, list[RunSheetRow]] = defaultdict(list)
    for r in rows:
        by_name[r.sample_name].append(r)
        key = well_key(r.sample_name)
        if key is not None:
            by_well[key].append(r)

    result = MatchResult()
    used: set[int] = set()
    for s in db_samples:
        offered, by = by_name.get(sheet_name(s["sample_name"])), "name"
        if not offered:
            key = well_key(s["sample_name"])
            offered, by = (by_well.get(key) if key is not None else None), "well"
        if not offered:
            result.unmatched_samples.append(s)
            continue
        used.update(r.line for r in offered)
        chosen = max(offered, key=lambda r: (r.sqr, r.sqrp, r.line))
        result.matches.append(Match(sample=s, row=chosen, by=by, offered=len(offered)))
    result.unused_rows = [r for r in rows if r.line not in used]
    return result


def plan_changes(matches: Iterable[Match]) -> list[Change]:
    """Work out what applying each match would change. Nothing is written.

    Returns:
        One [`Change`][noxdb.run_sheet.Change] per match that would
        change something or cannot be applied (``error`` set, e.g. an
        index sequence with characters other than A, C, G, T and N).
    """
    out: list[Change] = []
    for m in matches:
        try:
            changes, conflicts = samples.sequencing_changes(m.sample, **m.row.sequencing())
        except ValueError as exc:
            out.append(Change(m.sample["sample_id"], m.sample["sample_name"], {}, [],
                              error=f"line {m.row.line}: {exc}"))
            continue
        if changes:
            out.append(Change(m.sample["sample_id"], m.sample["sample_name"], changes, conflicts))
    return out


def apply(cur, changes: Iterable[Change], *, overwrite: bool = False) -> int:
    """Write planned changes, all or nothing.

    Rows that set the same columns are written together, so a whole run
    sheet is a handful of statements rather than one per sample.

    Args:
        cur: Audit-logging cursor from
            [`transaction`][noxdb.connection.transaction].
        changes: From [`plan_changes`][noxdb.run_sheet.plan_changes].
        overwrite: Also replace values that differ from the stored ones.

    Returns:
        The number of samples written.

    Raises:
        ValueError: Before writing anything, if a change has an error,
            or replaces a stored value and *overwrite* is not set.
    """
    changes = list(changes)
    errors = [c.error for c in changes if c.error]
    if errors:
        raise ValueError(f"{len(errors)} run-sheet row(s) cannot be applied: {errors[:5]}")
    conflicting = [c for c in changes if c.conflicts]
    if conflicting and not overwrite:
        raise ValueError(
            f"{len(conflicting)} sample(s) already have other sequencing values, "
            f"e.g. {conflicting[0].sample_name!r}: {'; '.join(conflicting[0].conflicts)}; "
            "pass overwrite=True to replace them"
        )
    groups: dict[tuple[str, ...], list[tuple]] = defaultdict(list)
    for c in changes:
        columns = tuple(sorted(c.changes))
        groups[columns].append((*(c.changes[col] for col in columns), c.sample_id))
    for columns, params in groups.items():
        set_clause = ", ".join(f"{col} = ?" for col in columns)
        cur.executemany(f"UPDATE samples SET {set_clause} WHERE sample_id = ?", params)
    return sum(len(p) for p in groups.values())
