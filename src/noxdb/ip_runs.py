"""Read IP coordinates from the lab's "Overview of IP runs" sheet.

The sheet (``Overview of IP runs TV(Overview).csv``, exported from Excel
as ``;``-separated cp1252) is the only source of ``IPR``/``IPRP``.
Columns N (``IP run #``) and O (``Plate #``) are the true coordinates
and are unique per plate. Column P (``Combined*``) is the old ``RxxPxx``
label that some files and sample names still carry; it is not unique
(``R08P01``/``R08P02`` each name two plates) and is kept here only so
records named under the old scheme can be looked up.

N is a merged cell in the workbook, so the export fills it on the first
plate of each run only; it is carried down to the plates below.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from noxdb.samples import canonical_plate_id

_RUN_HEADER = "IP run #"
_PLATE_HEADER = "Plate #"
_OLD_HEADER = "Combined*"
_DESCRIPTION_COL = 2  # C: "Short plate info (original plate info)"

_OLD_PLATE_RE = re.compile(r"^R(\d+)P(\d+)_")
_OLD_RUN_RE = re.compile(r"^R(\d+)_")


@dataclass(frozen=True)
class IPPlate:
    ipr: str
    iprp: str
    old_label: str | None
    description: str
    line: int

    @property
    def label(self) -> str:
        return f"R{self.ipr}P{self.iprp}"


def load_plates(path: str | Path, *, encoding: str = "cp1252") -> list[IPPlate]:
    """Return every plate in the sheet with its canonical IP coordinates.

    Args:
        path: The Overview CSV export.
        encoding: File encoding; Excel writes cp1252 on the lab machines.

    Returns:
        One [`IPPlate`][noxdb.ip_runs.IPPlate] per row that has a plate
        number, in sheet order.

    Raises:
        ValueError: If the header row is missing, a plate has no run to
            inherit, or two rows share the same run and plate.
    """
    with Path(path).open(encoding=encoding, newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))

    for h, header in enumerate(rows[:10]):
        cells = [c.strip() for c in header]
        if _RUN_HEADER in cells and _PLATE_HEADER in cells:
            run_col = cells.index(_RUN_HEADER)
            plate_col = cells.index(_PLATE_HEADER)
            old_col = cells.index(_OLD_HEADER) if _OLD_HEADER in cells else None
            break
    else:
        raise ValueError(f"{path}: no header row with {_RUN_HEADER!r} and {_PLATE_HEADER!r}")

    plates: list[IPPlate] = []
    seen: dict[tuple[str, str], int] = {}
    run = ""
    for line, row in enumerate(rows[h + 1:], start=h + 2):
        row = row + [""] * (max(run_col, plate_col, old_col or 0) + 1 - len(row))
        if row[run_col].strip():
            run = row[run_col].strip()
        plate = row[plate_col].strip()
        if not plate.isdigit():
            continue
        if not run.isdigit():
            raise ValueError(f"{path} line {line}: plate {plate!r} has no IP run above it")
        ipr, iprp = canonical_plate_id(run), canonical_plate_id(plate)
        if (ipr, iprp) in seen:
            raise ValueError(
                f"{path} line {line}: R{ipr}P{iprp} already on line {seen[ipr, iprp]}"
            )
        seen[ipr, iprp] = line
        old = "".join(row[old_col].split()).upper() if old_col is not None else ""
        plates.append(IPPlate(ipr, iprp, old or None, row[_DESCRIPTION_COL].strip(), line))
    return plates


def by_old_label(plates: list[IPPlate]) -> dict[str, list[IPPlate]]:
    """Group plates by the ``RxxPxx`` label files were named under.

    A plate with no ``Combined*`` entry was named with its true
    coordinates. Most labels map to one plate; a list longer than one is
    a clash the caller has to settle by other evidence (e.g. project).
    """
    out: dict[str, list[IPPlate]] = {}
    for p in plates:
        out.setdefault(p.old_label or p.label, []).append(p)
    return out


def _mentions(description: str, project: str) -> bool:
    words = project.replace("_", " ").replace("-", " ").lower()
    return bool(words) and words in description.lower()


def coords_for_old_name(
    index: dict[str, list[IPPlate]],
    sample_name: str,
    projects: set[str] | frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """Translate a name carrying an old ``RxxPxx`` label to sheet coordinates.

    This is a lookup of the label in the sheet's ``Combined*`` column,
    for records named before the sheet became the source: the
    coordinates returned are the sheet's, never the name's.

    A label the sheet gives to several plates is narrowed to the plates
    whose description names one of *projects*. A run-only name
    (``R31_input1_..``) maps to the true run of every plate labelled
    under that old run, with an empty plate.

    Args:
        index: [`by_old_label`][noxdb.ip_runs.by_old_label] of the sheet.
        sample_name: Name as stored or as written on the files.
        projects: Projects the record belongs to, to settle clashes.

    Returns:
        Candidate ``(IPR, IPRP)`` pairs. Exactly one is a match; none
        means the label is not in the sheet; several is a clash the
        caller must not guess at.
    """
    m = _OLD_PLATE_RE.match(sample_name or "")
    if m:
        label = f"R{int(m.group(1)):02d}P{int(m.group(2)):02d}"
        plates = index.get(label, [])
        if len(plates) > 1:
            narrowed = [
                p for p in plates
                if any(_mentions(p.description, pr) for pr in projects)
            ]
            plates = narrowed or plates
        return [(p.ipr, p.iprp) for p in plates]
    m = _OLD_RUN_RE.match(sample_name or "")
    if m:
        prefix = f"R{int(m.group(1)):02d}P"
        runs = {p.ipr for lab, ps in index.items() if lab.startswith(prefix) for p in ps}
        return sorted((run, "") for run in runs)
    return []
