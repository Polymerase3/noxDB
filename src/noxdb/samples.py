"""CRUD wrapper for the `samples` table.

Same call style as the other table modules: cursor first, dict returns,
writes audit-logged via ``_LoggingCursor``.

``sample_name`` is **globally unique** (not scoped to a visit), so the
natural-key lookup is [`get_by_name`][noxdb.samples.get_by_name]
and [`get_or_create`][noxdb.samples.get_or_create] keys on
``sample_name`` alone.

Two independent coordinate systems live on this table and must not be
confused — they were, until ``004_ip_and_sequencing_coords``:

``IPR`` / ``IPRP``
    Immunoprecipitation run and plate, from columns N (``IP run #``)
    and O (``Plate #``) of the lab's "Overview of IP runs" sheet. The
    ``RxxPxx`` in the sample name is *not* a reliable source: plates were
    relabelled on some files, so the name is never read for this.
    Plate-based control linking keys on this pair, because controls
    occupy wells 81-96 of the IP plate.

``SQR`` / ``SQRP``
    Sequencing run and its plate, from the run sheet. Unrelated to the
    name: ``R14P02_77_..`` was sequenced as SQR 07, plate 02.

The same run sheet gives each sequenced sample its barcodes
(``007_sample_barcodes``): ``i7_index`` / ``i5_index`` are the index
sequences, ``i7_index_id`` / ``i5_index_id`` the kit's names for them.
NULL means not known. [`set_sequencing`][noxdb.samples.set_sequencing]
sets these and ``SQR`` / ``SQRP`` on a sample that already exists.
"""

from __future__ import annotations

from typing import Any

import mariadb

_COLUMNS = (
    "sample_id",
    "visit_id",
    "sample_name",
    "sample_type",
    "IPR",
    "IPRP",
    "SQR",
    "SQRP",
    "i7_index",
    "i7_index_id",
    "i5_index",
    "i5_index_id",
    "library",
    "antibody_class",
    "created_at",
)
_ORDERABLE = frozenset(_COLUMNS)

# Columns set_sequencing may write, all from the sequencing run sheet.
SEQUENCING_COLUMNS = ("SQR", "SQRP", "i7_index", "i7_index_id", "i5_index", "i5_index_id")

_INDEX_BASES = frozenset("ACGTN")


def _row_to_dict(cur, row) -> dict[str, Any]:
    return dict(zip([d[0] for d in cur.description], row))


# "Absent" sentinels collapsed to a single canonical empty string. Both
# coordinate pairs are matched by exact string equality (control
# auto-link, queries, migration backfill), so formatting drift silently
# breaks linking unless every write goes through one canonical form.
# Padding (e.g. "01") is intentionally preserved — it is the
# established canonical shape in this dataset, not noise.
_PLATE_NULLISH = frozenset({"", "na", "n/a"})


def canonical_plate_id(value: str | None) -> str:
    """Return the canonical form of a run or plate identifier.

    Strips surrounding whitespace, collapses the "absent" sentinels
    (``""``, ``"NA"``, ``"N/A"``, case-insensitive) to a single
    canonical empty string, and zero-pads a purely numeric identifier
    to two characters. Anything non-numeric is returned stripped but
    otherwise verbatim.

    The padding matters: matching is by exact string, so ``"5"`` and
    ``"05"`` used to describe one physical plate as two, which left
    whole projects with no controls. Normalizing here means every write
    path agrees on one spelling.

    This is the one normalization chokepoint for both coordinate pairs;
    [`create`][noxdb.samples.create] / [`update`][noxdb.samples.update]
    call it on every write and the importer reuses it so the value the
    DB stores is always canonical and coordinate matching is reliable.

    Args:
        value: Raw IPR / IPRP / SQR / SQRP cell (may be ``None``).

    Returns:
        The canonical identifier (possibly ``""`` for "no plate").
    """
    s = (value or "").strip()
    if s.lower() in _PLATE_NULLISH:
        return ""
    return s.zfill(2) if s.isdigit() else s


def canonical_index(value: str | None) -> str | None:
    """Return the canonical form of an i7 / i5 index sequence.

    Strips whitespace and upper-cases. The DB only accepts upper-case
    ``A``, ``C``, ``G``, ``T`` and ``N``.

    Args:
        value: Raw index sequence (may be ``None``).

    Returns:
        The sequence, or ``None`` for an empty value ("not known").

    Raises:
        ValueError: If anything other than A, C, G, T or N remains.
    """
    s = (value or "").strip().upper()
    if not s:
        return None
    if not set(s) <= _INDEX_BASES:
        raise ValueError(
            f"index sequence {value!r} may only contain A, C, G, T and N"
        )
    return s


def canonical_index_id(value: str | None) -> str | None:
    """Return an index's kit name (e.g. ``IDT10_i7_1``) stripped, ``None`` if empty."""
    s = (value or "").strip()
    return s or None


def create(
    cur,
    visit_id: int,
    sample_name: str,
    sample_type: str,
    sqr: str,
    sqrp: str,
    library: str,
    *,
    ipr: str,
    iprp: str,
    antibody_class: str | None = None,
    i7_index: str | None = None,
    i7_index_id: str | None = None,
    i5_index: str | None = None,
    i5_index_id: str | None = None,
) -> int:
    """Insert a sample and return its new ``sample_id``.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        visit_id: Parent visit. Must already exist.
        sample_name: Globally unique sample name.
        sample_type: One of ``'sample'``, ``'mockIP'``, ``'input'``,
            ``'anchor'``, ``'NC'`` (DB-side `ENUM` constraint).
        sqr: Sequencing run, from the run sheet. Canonicalized via
            [`canonical_plate_id`][noxdb.samples.canonical_plate_id]
            before storage.
        sqrp: Sequencing plate within that run. Canonicalized like
            ``sqr``.
        library: Library identifier.
        ipr: IP run, from the IP-runs overview sheet. Canonicalized via
            [`canonical_plate_id`][noxdb.samples.canonical_plate_id]
            before storage.
        iprp: IP plate within that run, ``""`` for a run-only sample
            such as an input. Canonicalized like ``ipr``.
        antibody_class: Optional antibody class label.
        i7_index: i7 index sequence, from the run sheet. Canonicalized
            via [`canonical_index`][noxdb.samples.canonical_index].
        i7_index_id: The kit's name for the i7 index (e.g. ``IDT10_i7_1``).
        i5_index: i5 index sequence. Canonicalized like ``i7_index``.
        i5_index_id: The kit's name for the i5 index.

    Returns:
        The newly inserted ``sample_id``.

    Raises:
        ValueError: If an index sequence has characters other than
            A, C, G, T and N.
        mariadb.IntegrityError: If ``sample_name`` already exists
            (global UNIQUE), ``visit_id`` does not reference an
            existing visit, or ``sample_type`` is outside the allowed
            enum.
    """
    cur.execute(
        "INSERT INTO samples "
        "(visit_id, sample_name, sample_type, IPR, IPRP, SQR, SQRP, "
        "i7_index, i7_index_id, i5_index, i5_index_id, library, antibody_class) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            visit_id, sample_name, sample_type,
            canonical_plate_id(ipr), canonical_plate_id(iprp),
            canonical_plate_id(sqr), canonical_plate_id(sqrp),
            canonical_index(i7_index), canonical_index_id(i7_index_id),
            canonical_index(i5_index), canonical_index_id(i5_index_id),
            library, antibody_class,
        ),
    )
    return cur.lastrowid


def get(cur, sample_id: int) -> dict[str, Any] | None:
    """Return the sample row for a given id.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        sample_id: Primary key to look up.

    Returns:
        The row as ``dict[str, Any]``, or ``None`` if not found.
    """
    cur.execute("SELECT * FROM samples WHERE sample_id = ?", (sample_id,))
    row = cur.fetchone()
    return _row_to_dict(cur, row) if row is not None else None


def get_by_name(cur, sample_name: str) -> dict[str, Any] | None:
    """Return the sample row for a given name.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        sample_name: Globally unique sample name.

    Returns:
        The row as ``dict[str, Any]``, or ``None`` if not found.
    """
    cur.execute("SELECT * FROM samples WHERE sample_name = ?", (sample_name,))
    row = cur.fetchone()
    return _row_to_dict(cur, row) if row is not None else None


def get_or_create(
    cur,
    visit_id: int,
    sample_name: str,
    sample_type: str,
    sqr: str,
    sqrp: str,
    library: str,
    *,
    ipr: str,
    iprp: str,
    antibody_class: str | None = None,
    i7_index: str | None = None,
    i7_index_id: str | None = None,
    i5_index: str | None = None,
    i5_index_id: str | None = None,
) -> tuple[int, bool]:
    """Idempotently return the sample id, inserting if needed.

    Existing rows are returned as-is — the other columns are not used
    to update an existing row. Falls back to a re-fetch on the
    UNIQUE-violation race.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        visit_id: Parent visit (used only on insert).
        sample_name: Globally unique sample name.
        sample_type: Used only on insert. See
            [`create`][noxdb.samples.create] for allowed values.
        sqr: Sequencing run. Used only on insert.
        sqrp: Sequencing plate. Used only on insert.
        library: Used only on insert.
        ipr: IP run. Used only on insert.
        iprp: IP plate. Used only on insert.
        antibody_class: Used only on insert.
        i7_index: Used only on insert.
        i7_index_id: Used only on insert.
        i5_index: Used only on insert.
        i5_index_id: Used only on insert.

    Returns:
        ``(sample_id, created)`` where ``created`` is ``True`` iff this
        call inserted the row.

    Raises:
        mariadb.IntegrityError: If the race-recovery fetch also misses.
    """
    existing = get_by_name(cur, sample_name)
    if existing is not None:
        return int(existing["sample_id"]), False
    try:
        new_id = create(
            cur,
            visit_id,
            sample_name,
            sample_type,
            sqr,
            sqrp,
            library,
            ipr=ipr,
            iprp=iprp,
            antibody_class=antibody_class,
            i7_index=i7_index,
            i7_index_id=i7_index_id,
            i5_index=i5_index,
            i5_index_id=i5_index_id,
        )
        return new_id, True
    except mariadb.IntegrityError:
        existing = get_by_name(cur, sample_name)
        if existing is None:
            raise
        return int(existing["sample_id"]), False


def link_to_project(cur, project_id: int, sample_id: int) -> None:
    """Register ``sample_id`` under ``project_id`` in ``project_samples``.

    Idempotent: ``INSERT IGNORE`` so re-linking an already-linked
    (project, sample) pair is a no-op. ``project_samples`` is the sole
    source of truth for which samples belong to which project — a
    sample may be linked to several projects (e.g. plate controls
    shared across studies).

    Args:
        cur: Audit-logging cursor from `transaction()`.
        project_id: Project to link the sample to. Must already exist.
        sample_id: Sample to link. Must already exist.
    """
    cur.execute(
        "INSERT IGNORE INTO project_samples (project_id, sample_id) "
        "VALUES (?, ?)",
        (project_id, sample_id),
    )


def list_for_visit(
    cur, visit_id: int, *, order_by: str = "sample_id"
) -> list[dict[str, Any]]:
    """Return all samples belonging to a visit.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        visit_id: Visit to list.
        order_by: Column name to order by. Must be a column of ``samples``.

    Returns:
        All matching rows as ``list[dict[str, Any]]``.

    Raises:
        ValueError: If ``order_by`` is not a known column name.
    """
    if order_by not in _ORDERABLE:
        raise ValueError(
            f"order_by must be one of {sorted(_ORDERABLE)}, got {order_by!r}"
        )
    cur.execute(
        f"SELECT * FROM samples WHERE visit_id = ? ORDER BY {order_by}",
        (visit_id,),
    )
    rows = cur.fetchall()
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in rows]


def count_for_visit(cur, visit_id: int) -> int:
    """Return the number of samples for a visit.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        visit_id: Visit to count.

    Returns:
        Number of sample rows.
    """
    cur.execute(
        "SELECT COUNT(*) FROM samples WHERE visit_id = ?", (visit_id,)
    )
    return int(cur.fetchone()[0])


def update(
    cur,
    sample_id: int,
    *,
    sample_name: str | None = None,
    sample_type: str | None = None,
    ipr: str | None = None,
    iprp: str | None = None,
    sqr: str | None = None,
    sqrp: str | None = None,
    library: str | None = None,
    antibody_class: str | None = None,
    i7_index: str | None = None,
    i7_index_id: str | None = None,
    i5_index: str | None = None,
    i5_index_id: str | None = None,
) -> bool:
    """Partial update of a sample row.

    Only kwargs with non-None values are written. ``visit_id`` and
    ``created_at`` are intentionally NOT updatable — re-parenting a
    sample would corrupt downstream lineage. Setting *antibody_class*
    or a barcode to NULL is also out of scope (the helper treats None
    as "skip"); use raw SQL if you need that. To fill in sequencing
    values without overwriting any by accident, use
    [`set_sequencing`][noxdb.samples.set_sequencing].

    Args:
        cur: Audit-logging cursor from `transaction()`.
        sample_id: Row to update.
        sample_name: New name (if not None).
        sample_type: New type (if not None). See
            [`create`][noxdb.samples.create] for allowed values.
        ipr: New IP run (if not None). Canonicalized via
            [`canonical_plate_id`][noxdb.samples.canonical_plate_id].
        iprp: New IP plate (if not None). Canonicalized like ``ipr``.
        sqr: New sequencing run (if not None). Canonicalized like
            ``ipr``.
        sqrp: New sequencing plate (if not None). Canonicalized like
            ``ipr``.
        library: New library (if not None).
        antibody_class: New antibody class (if not None).
        i7_index: New i7 index sequence (if not None). Canonicalized
            via [`canonical_index`][noxdb.samples.canonical_index].
        i7_index_id: New i7 index name (if not None).
        i5_index: New i5 index sequence (if not None). Canonicalized
            like ``i7_index``.
        i5_index_id: New i5 index name (if not None).

    Returns:
        ``True`` iff exactly one row was updated.

    Raises:
        ValueError: If an index sequence has characters other than
            A, C, G, T and N.
    """
    fields = {
        "sample_name": sample_name,
        "sample_type": sample_type,
        "IPR": canonical_plate_id(ipr) if ipr is not None else None,
        "IPRP": canonical_plate_id(iprp) if iprp is not None else None,
        "SQR": canonical_plate_id(sqr) if sqr is not None else None,
        "SQRP": canonical_plate_id(sqrp) if sqrp is not None else None,
        "i7_index": canonical_index(i7_index),
        "i7_index_id": canonical_index_id(i7_index_id),
        "i5_index": canonical_index(i5_index),
        "i5_index_id": canonical_index_id(i5_index_id),
        "library": library,
        "antibody_class": antibody_class,
    }
    assignments = [(col, val) for col, val in fields.items() if val is not None]
    if not assignments:
        return False
    set_clause = ", ".join(f"{col} = ?" for col, _ in assignments)
    params = [val for _, val in assignments]
    params.append(sample_id)
    cur.execute(
        f"UPDATE samples SET {set_clause} WHERE sample_id = ?", tuple(params)
    )
    return cur.rowcount > 0


def sequencing_changes(
    stored: dict[str, Any],
    *,
    sqr: str | None = None,
    sqrp: str | None = None,
    i7_index: str | None = None,
    i7_index_id: str | None = None,
    i5_index: str | None = None,
    i5_index_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Work out what setting sequencing values on a stored sample would change.

    Nothing is written. The values are canonicalized the way they would
    be stored; an empty or ``None`` value means "leave as is".

    Args:
        stored: The sample row, e.g. from [`get`][noxdb.samples.get].
        sqr: Sequencing run.
        sqrp: Sequencing plate.
        i7_index: i7 index sequence.
        i7_index_id: i7 index name.
        i5_index: i5 index sequence.
        i5_index_id: i5 index name.

    Returns:
        ``(changes, conflicts)``. *changes* maps each column whose value
        would change to its new value. *conflicts* describes the changes
        that replace a value the sample already had (rather than fill in
        an empty one), e.g. ``"SQR '05' → '07'"``.

    Raises:
        ValueError: If an index sequence has characters other than
            A, C, G, T and N.
    """
    incoming = {
        "SQR": canonical_plate_id(sqr),
        "SQRP": canonical_plate_id(sqrp),
        "i7_index": canonical_index(i7_index),
        "i7_index_id": canonical_index_id(i7_index_id),
        "i5_index": canonical_index(i5_index),
        "i5_index_id": canonical_index_id(i5_index_id),
    }
    changes: dict[str, Any] = {}
    conflicts: list[str] = []
    for column, new in incoming.items():
        old = stored.get(column)
        if new in (None, "") or new == old:
            continue
        changes[column] = new
        if old not in (None, ""):
            conflicts.append(f"{column} {old!r} → {new!r}")
    return changes, conflicts


def set_sequencing(
    cur,
    sample_id: int,
    *,
    sqr: str | None = None,
    sqrp: str | None = None,
    i7_index: str | None = None,
    i7_index_id: str | None = None,
    i5_index: str | None = None,
    i5_index_id: str | None = None,
    overwrite: bool = False,
) -> bool:
    """Set the sequencing run, plate and barcodes of an existing sample.

    Only the values given are written. An empty column is filled in and
    one that already holds the same value is left alone. One that holds
    a *different* value is refused unless *overwrite* is set: a sample's
    sequencing values normally change only when it is re-sequenced, so a
    difference is more often a mismatched row than a correction.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        sample_id: Sample to update. Must exist.
        sqr: Sequencing run. Canonicalized via
            [`canonical_plate_id`][noxdb.samples.canonical_plate_id].
        sqrp: Sequencing plate. Canonicalized like ``sqr``.
        i7_index: i7 index sequence. Canonicalized via
            [`canonical_index`][noxdb.samples.canonical_index].
        i7_index_id: i7 index name, e.g. ``IDT10_i7_1``.
        i5_index: i5 index sequence. Canonicalized like ``i7_index``.
        i5_index_id: i5 index name.
        overwrite: Replace values that differ instead of refusing.

    Returns:
        ``True`` iff the row changed.

    Raises:
        ValueError: If the sample does not exist, if a value would
            replace a different stored one and *overwrite* is not set,
            or if an index sequence has characters other than A, C, G,
            T and N.
    """
    stored = get(cur, sample_id)
    if stored is None:
        raise ValueError(f"no sample with sample_id={sample_id}")
    changes, conflicts = sequencing_changes(
        stored, sqr=sqr, sqrp=sqrp,
        i7_index=i7_index, i7_index_id=i7_index_id,
        i5_index=i5_index, i5_index_id=i5_index_id,
    )
    if conflicts and not overwrite:
        raise ValueError(
            f"sample {stored['sample_name']!r} already has other sequencing "
            f"values ({'; '.join(conflicts)}); pass overwrite=True to replace them"
        )
    if not changes:
        return False
    set_clause = ", ".join(f"{column} = ?" for column in changes)
    cur.execute(
        f"UPDATE samples SET {set_clause} WHERE sample_id = ?",
        (*changes.values(), sample_id),
    )
    return cur.rowcount > 0


def delete(cur, sample_id: int) -> bool:
    """Delete a sample.

    ``sample_metadata.fk_sample_metadata_sample`` is ``ON DELETE
    CASCADE``, so this also removes every metadata row owned by the
    sample. ``sample_files.fk_sample_files_sample`` is ``ON DELETE
    RESTRICT`` and will block the delete instead — clean those up
    first.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        sample_id: Row to delete.

    Returns:
        ``True`` iff a row was removed.
    """
    cur.execute("DELETE FROM samples WHERE sample_id = ?", (sample_id,))
    return cur.rowcount > 0


def exists(
    cur,
    sample_id: int | None = None,
    *,
    name: str | None = None,
) -> bool:
    """Return whether a sample with the given id or name exists.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        sample_id: Id to check (exclusive with ``name``).
        name: Name to check (exclusive with ``sample_id``).

    Returns:
        ``True`` if a matching row exists.

    Raises:
        ValueError: If both or neither of ``sample_id`` / ``name`` is given.
    """
    if (sample_id is None) == (name is None):
        raise ValueError("exists() requires exactly one of sample_id or name")
    if sample_id is not None:
        cur.execute("SELECT 1 FROM samples WHERE sample_id = ?", (sample_id,))
    else:
        cur.execute("SELECT 1 FROM samples WHERE sample_name = ?", (name,))
    return cur.fetchone() is not None
