"""Validate a :class:`ProjectBundle` and (optionally) commit it.

The runner is split into two distinct phases so import errors never
leave the database in a half-written state:

1. **Validation** — schema, referential, duplicate, on-disk path, and
   project-existence checks. Errors are collected exhaustively (not
   short-circuit) so the user sees every problem in one pass.
2. **Commit** — a single :func:`transaction` block calling the
   existing CRUD wrappers in hierarchical order. An exception anywhere
   rolls back the entire import; partial states are impossible.

The split also gives ``--dry-run`` for free: skip phase 2.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from noxdb import files as files_mod
from noxdb import metadata, projects, samples, subjects, visits
from noxdb._import import loader, schema
from noxdb.connection import transaction


class ProjectImportError(RuntimeError):
    """Raised when validation fails or the project already exists without --force."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = list(errors or [])


@dataclass
class ImportReport:
    project_name: str
    project_id: int | None = None
    dry_run: bool = False
    force: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_id": self.project_id,
            "dry_run": self.dry_run,
            "force": self.force,
            "warnings": self.warnings,
            "errors": self.errors,
            "counts": self.counts,
            "duration_seconds": round(self.duration_seconds, 3),
        }


@dataclass
class ValidationResult:
    """What [`validate_bundle`][noxdb._import.runner.validate_bundle] found.

    ``errors`` block an import; ``warnings`` don't, but are worth reading.
    """
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` when there are no errors."""
        return not self.errors


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _barcodes_of(r: loader.SampleRow) -> dict[str, str | None]:
    """A sample row's barcode cells, keyed by column name."""
    return {column: getattr(r, column) for column in schema.BARCODE_COLUMNS}


def _validate_schema(bundle: loader.ProjectBundle) -> list[str]:
    """Schema-level checks: enum membership and parseable numerics.

    Doesn't look at the database, just at the row contents.
    """
    _NULLISH = frozenset({"", "na", "n/a"})

    errs: list[str] = []
    for r in bundle.subjects:
        sex = (r.sex or "").strip()
        if sex.lower() not in _NULLISH and sex not in schema.ALLOWED_SEX:
            errs.append(
                f"subjects.csv row {r.row_num}: sex={r.sex!r} not in "
                f"{sorted(schema.ALLOWED_SEX)}"
            )
        if not r.subject_code:
            errs.append(f"subjects.csv row {r.row_num}: subject_code is empty")

    for r in bundle.visits:
        if not r.timepoint:
            # The schema permits NULL timepoints, but get_or_create rejects
            # them (UNIQUE doesn't dedupe NULL) — so the importer does too.
            errs.append(
                f"visits.csv row {r.row_num}: timepoint is empty; "
                "non-null timepoints are required for idempotent import"
            )
        age_raw = (r.age or "").strip()
        if age_raw.lower() not in _NULLISH:
            try:
                age = schema.coerce_int(r.age, field=f"visits.csv row {r.row_num}.age")
                if age < 0:
                    errs.append(
                        f"visits.csv row {r.row_num}: age must be >= 0, got {age}"
                    )
            except ValueError as exc:
                errs.append(str(exc))

    for r in bundle.samples:
        if r.sample_type not in schema.ALLOWED_SAMPLE_TYPE:
            errs.append(
                f"samples.csv row {r.row_num}: sample_type={r.sample_type!r} "
                f"not in {sorted(schema.ALLOWED_SAMPLE_TYPE)}"
            )
        for fld, val in (
            ("ipr", r.ipr), ("iprp", r.iprp), ("sqr", r.sqr), ("sqrp", r.sqrp),
        ):
            try:
                schema.validate_plate_id(
                    val, field=f"samples.csv row {r.row_num}.{fld}"
                )
            except ValueError as exc:
                errs.append(str(exc))
        errs.extend(schema.barcode_errors(
            _barcodes_of(r), where=f"samples.csv row {r.row_num}",
        ))

    for r in bundle.files:
        if r.file_type not in schema.ALLOWED_FILE_TYPE:
            errs.append(
                f"manifest.csv row {r.row_num}: file_type={r.file_type!r} "
                f"not in {sorted(schema.ALLOWED_FILE_TYPE)}"
            )
        if r.storage_tier is not None and r.storage_tier not in schema.ALLOWED_STORAGE_TIER:
            errs.append(
                f"manifest.csv row {r.row_num}: storage_tier={r.storage_tier!r} "
                f"not in {sorted(schema.ALLOWED_STORAGE_TIER)}"
            )

    return errs


def _plate_warnings(bundle: loader.ProjectBundle) -> list[str]:
    """Surface IPR/IPRP/SQR/SQRP values that get normalized on the way in.

    Canonicalization (whitespace strip, ``NA``/empty → ``""``) happens
    silently in :func:`samples.create`; echoing it as an import warning
    keeps the transformation visible in the report rather than a
    surprise when a later query behaves on the canonical form.
    Length-overflow is handled as a hard error in
    :func:`_validate_schema`, so it is swallowed here.
    """
    warnings: list[str] = []
    for r in bundle.samples:
        for fld, val in (
            ("ipr", r.ipr), ("iprp", r.iprp), ("sqr", r.sqr), ("sqrp", r.sqrp),
        ):
            try:
                _canon, warn = schema.validate_plate_id(
                    val, field=f"samples.csv row {r.row_num}.{fld}"
                )
            except ValueError:
                continue
            if warn is not None:
                warnings.append(warn)
    return warnings


def _validate_referential(bundle: loader.ProjectBundle) -> list[str]:
    """Cross-file references and duplicates within the bundle."""
    errs: list[str] = []

    # subjects.csv → unique subject_code
    seen_subjects: set[str] = set()
    for r in bundle.subjects:
        if r.subject_code in seen_subjects:
            errs.append(
                f"subjects.csv row {r.row_num}: duplicate subject_code "
                f"{r.subject_code!r}"
            )
        seen_subjects.add(r.subject_code)

    # visits.csv → unique (subject_code, timepoint); subject must exist
    seen_visits: set[tuple[str, str]] = set()
    for r in bundle.visits:
        if r.subject_code not in seen_subjects:
            errs.append(
                f"visits.csv row {r.row_num}: subject_code "
                f"{r.subject_code!r} not in subjects.csv"
            )
        key = (r.subject_code, r.timepoint)
        if key in seen_visits:
            errs.append(
                f"visits.csv row {r.row_num}: duplicate (subject_code, "
                f"timepoint)={key!r}"
            )
        seen_visits.add(key)

    # samples.csv → unique sample_name; (subject_code, timepoint) must exist
    seen_samples: set[str] = set()
    for r in bundle.samples:
        if (r.subject_code, r.timepoint) not in seen_visits:
            errs.append(
                f"samples.csv row {r.row_num}: (subject_code, timepoint)="
                f"({r.subject_code!r}, {r.timepoint!r}) not in visits.csv"
            )
        if r.sample_name in seen_samples:
            errs.append(
                f"samples.csv row {r.row_num}: duplicate sample_name "
                f"{r.sample_name!r}"
            )
        seen_samples.add(r.sample_name)

    # manifest.csv → unique file_path; sample must exist in samples.csv
    seen_paths: set[str] = set()
    for r in bundle.files:
        if r.sample_name not in seen_samples:
            errs.append(
                f"manifest.csv row {r.row_num}: sample_name {r.sample_name!r} "
                "not in samples.csv"
            )
        if r.file_path in seen_paths:
            errs.append(
                f"manifest.csv row {r.row_num}: duplicate file_path "
                f"{r.file_path!r}"
            )
        seen_paths.add(r.file_path)

    return errs


def _validate_disk(bundle: loader.ProjectBundle) -> list[str]:
    """Confirm every manifest path resolves on the local filesystem."""
    return [
        f"manifest.csv row {r.row_num}: file does not exist on disk: {r.file_path}"
        for r in bundle.files
        if not os.path.exists(r.file_path)
    ]


def _validate_db_collisions(cur, bundle: loader.ProjectBundle) -> list[str]:
    """Block on the one remaining global UNIQUE that can't be resolved
    by re-use: a ``file_path`` bound to a *different* sample.

    ``samples.sample_name`` is no longer a cross-project collision —
    samples are intentionally shared across projects via
    ``project_samples`` (plate controls, shared HC cohorts). An
    existing sample is re-used (``get_or_create`` keys on the global
    ``sample_name``) and merely linked to this project on commit.

    ``sample_files.file_path`` is globally UNIQUE. Because samples are
    shared, the same physical file legitimately reappears in another
    project's bundle under the **same** ``sample_name`` — that is a
    no-op re-use (``get_or_register`` keys on the path), NOT a
    conflict. It is only a genuine integrity conflict when the path is
    already registered to a *different* sample, since one path cannot
    describe two samples. Project ownership is irrelevant now — only
    sample identity matters.
    """
    errs: list[str] = []

    for r in bundle.files:
        existing = files_mod.get_by_path(cur, r.file_path)
        if existing is None:
            continue
        existing_sample = samples.get(cur, existing["sample_id"])
        existing_name = (
            existing_sample["sample_name"] if existing_sample else None
        )
        if existing_name != r.sample_name:
            errs.append(
                f"manifest.csv row {r.row_num}: file_path {r.file_path!r} "
                f"already registered to a different sample "
                f"({existing_name!r}); a file path cannot describe two "
                f"samples"
            )

    return errs


# Values per ``IN (...)`` query when comparing a bundle with the database.
_LOOKUP_CHUNK = 500

# DECIMAL(20,6): the precision a numeric metadata value is stored with.
_NUMERIC_SCALE = Decimal("0.000001")

_METADATA_COLUMNS = "key_name, value_int, value_numeric, value_bool, value_text, value_type"


def _fetch_in(cur, select: str, column: str, values: Iterable[Any]) -> list[dict[str, Any]]:
    """Run ``<select> WHERE <column> IN (...)`` over *values*, in chunks."""
    unique = list(dict.fromkeys(values))
    rows: list[dict[str, Any]] = []
    for i in range(0, len(unique), _LOOKUP_CHUNK):
        chunk = unique[i:i + _LOOKUP_CHUNK]
        marks = ", ".join(["?"] * len(chunk))
        cur.execute(f"{select} WHERE {column} IN ({marks})", tuple(chunk))
        names = [d[0] for d in cur.description]
        rows.extend(dict(zip(names, row)) for row in cur.fetchall())
    return rows


def _stored_metadata(cur, table: str, parent_col: str, parent_ids: list[int]) -> dict[tuple[int, str], Any]:
    """Existing metadata of *parent_ids*, keyed by ``(parent_id, key_name)``."""
    rows = _fetch_in(
        cur, f"SELECT {parent_col}, {_METADATA_COLUMNS} FROM {table}", parent_col, parent_ids,
    )
    return {(r[parent_col], r["key_name"]): metadata._row_to_value(r) for r in rows}


def _same_value(stored: Any, incoming: Any) -> bool:
    """Whether a stored metadata value equals the one a bundle would write.

    Numbers compare by value at the stored precision, since DECIMAL(20,6)
    comes back as ``Decimal``. Booleans only equal booleans.
    """
    if isinstance(stored, bool) or isinstance(incoming, bool):
        return isinstance(stored, bool) and isinstance(incoming, bool) and stored == incoming
    numbers = (int, float, Decimal)
    if isinstance(stored, numbers) and isinstance(incoming, numbers):
        return (Decimal(str(stored)).quantize(_NUMERIC_SCALE)
                == Decimal(str(incoming)).quantize(_NUMERIC_SCALE))
    return stored == incoming


def _compare(where: str, what: str, column: str, stored: Any, incoming: Any,
             errs: list[str], warns: list[str], *, fill_hint: str | None = None) -> None:
    """Check one column of a row the database already has.

    The import keeps existing rows as they are, so a bundle value that
    differs from the stored one would be dropped without a word: that is
    an error. A stored empty value the bundle would fill is dropped the
    same way, but loses nothing, so it is a warning, ending in
    *fill_hint* when there is another way to set it. An empty bundle
    value asserts nothing.
    """
    if incoming in (None, ""):
        return
    if stored in (None, ""):
        warns.append(
            f"{where}: {what} exists without {column}; the import does not "
            f"fill in {column}={incoming!r} on existing rows"
            + (f"; {fill_hint}" if fill_hint else "")
        )
    elif stored != incoming:
        errs.append(
            f"{where}: {what} already exists with {column}={stored!r}; "
            f"the bundle has {incoming!r}"
        )


def _metadata_overwrites(where: str, what: str, new: dict[str, Any], parent_id: int,
                         stored: dict[tuple[int, str], Any]) -> list[str]:
    """Warn about metadata keys whose stored value the import would replace."""
    warns = []
    for key, value in new.items():
        if (parent_id, key) in stored and not _same_value(stored[(parent_id, key)], value):
            warns.append(
                f"{where}: {what} metadata {key}={stored[(parent_id, key)]!r} "
                f"will be overwritten with {value!r}"
            )
    return warns


def _canonical_barcode(column: str, raw: str | None) -> str | None:
    """A barcode cell as it would be stored (an invalid sequence is a schema error)."""
    if column.endswith("_id"):
        return samples.canonical_index_id(raw)
    try:
        return samples.canonical_index(raw)
    except ValueError:
        return None


def _parse_age(raw: str) -> int | None:
    """The age the commit would store, or ``None`` (unparseable ages are schema errors)."""
    raw = (raw or "").strip()
    if raw.upper() in ("", "NA", "N/A"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _validate_existing(cur, bundle: loader.ProjectBundle) -> tuple[list[str], list[str]]:
    """Compare the rows a bundle shares with the database.

    A bundle adding to an existing project repeats subjects, visits and
    samples the database already holds, and the commit reuses those rows
    unchanged (``get_or_create``). Any value that differs would be lost
    silently, so each one is reported here, before anything is written.
    Metadata is different: the commit does overwrite it, so a changed
    value is a warning, not an error.

    Returns:
        ``(errors, warnings)``.
    """
    errs: list[str] = []
    warns: list[str] = []

    codes = [s.subject_code for s in bundle.subjects]
    stored_subjects = {
        r["subject_code"]: r
        for r in _fetch_in(
            cur, "SELECT subject_id, subject_code, sex, origin FROM subjects", "subject_code", codes,
        )
    }
    for r in bundle.subjects:
        stored = stored_subjects.get(r.subject_code)
        if stored is None:
            continue
        where, what = f"subjects.csv row {r.row_num}", f"subject {r.subject_code!r}"
        _compare(where, what, "sex", stored["sex"], subjects._norm_sex(r.sex), errs, warns)
        _compare(where, what, "origin", stored["origin"], r.origin, errs, warns)

    code_of = {r["subject_id"]: code for code, r in stored_subjects.items()}
    stored_visits = {
        (code_of[r["subject_id"]], r["timepoint"]): r
        for r in _fetch_in(
            cur, "SELECT visit_id, subject_id, timepoint, group_test, age FROM visits",
            "subject_id", list(code_of),
        )
    }
    visit_meta = _stored_metadata(
        cur, "visit_metadata", "visit_id", [r["visit_id"] for r in stored_visits.values()],
    )
    for r in bundle.visits:
        stored = stored_visits.get((r.subject_code, r.timepoint))
        if stored is None:
            continue
        where = f"visits.csv row {r.row_num}"
        what = f"visit ({r.subject_code!r}, {r.timepoint!r})"
        _compare(where, what, "group_test", stored["group_test"], r.group_test, errs, warns)
        _compare(where, what, "age", stored["age"], _parse_age(r.age), errs, warns)
        warns.extend(_metadata_overwrites(where, what, r.metadata, stored["visit_id"], visit_meta))

    stored_samples = {
        r["sample_name"]: r
        for r in _fetch_in(
            cur,
            "SELECT s.sample_id, s.sample_name, s.sample_type, s.IPR, s.IPRP, s.SQR, s.SQRP, "
            "s.i7_index, s.i7_index_id, s.i5_index, s.i5_index_id, "
            "s.library, s.antibody_class, v.timepoint, sub.subject_code "
            "FROM samples s JOIN visits v ON v.visit_id = s.visit_id "
            "JOIN subjects sub ON sub.subject_id = v.subject_id",
            "s.sample_name", [s.sample_name for s in bundle.samples],
        )
    }
    sample_meta = _stored_metadata(
        cur, "sample_metadata", "sample_id", [r["sample_id"] for r in stored_samples.values()],
    )
    for r in bundle.samples:
        stored = stored_samples.get(r.sample_name)
        if stored is None:
            continue
        where, what = f"samples.csv row {r.row_num}", f"sample {r.sample_name!r}"
        if (stored["subject_code"], stored["timepoint"]) != (r.subject_code, r.timepoint):
            errs.append(
                f"{where}: {what} already exists under visit "
                f"({stored['subject_code']!r}, {stored['timepoint']!r}); the bundle "
                f"puts it under ({r.subject_code!r}, {r.timepoint!r})"
            )
        _compare(where, what, "sample_type", stored["sample_type"], r.sample_type, errs, warns)
        for column, value in (("IPR", r.ipr), ("IPRP", r.iprp)):
            _compare(where, what, column, stored[column],
                     samples.canonical_plate_id(value), errs, warns)
        sequencing = {"SQR": samples.canonical_plate_id(r.sqr),
                      "SQRP": samples.canonical_plate_id(r.sqrp)}
        sequencing.update({column: _canonical_barcode(column, value)
                           for column, value in _barcodes_of(r).items()})
        for column, value in sequencing.items():
            _compare(where, what, column, stored[column], value, errs, warns,
                     fill_hint="set it with scripts/apply_run_sheet.py")
        _compare(where, what, "library", stored["library"], r.library, errs, warns)
        _compare(where, what, "antibody_class", stored["antibody_class"], r.antibody_class,
                 errs, warns)
        warns.extend(_metadata_overwrites(where, what, r.metadata, stored["sample_id"], sample_meta))

    return errs, warns


def validate_bundle(
    bundle: loader.ProjectBundle,
    *,
    cur=None,
    force: bool = False,
    skip_disk_check: bool = False,
) -> ValidationResult:
    """Check a bundle without writing anything.

    Every problem is collected, not just the first. Without *cur* only
    the checks that need no database run: allowed values, references
    between the files, duplicates and (unless *skip_disk_check*) whether
    the manifest's files exist. That is enough for a form that has no
    database access.

    With *cur* the bundle is also checked against the database: the
    project must not exist yet unless *force* is set, a file path must
    not belong to another sample, and every subject, visit and sample
    the database already holds must agree with the bundle (see
    ``_validate_existing``). The import runs exactly these checks before
    it commits.

    Args:
        bundle: A bundle from
            [`load_project_dir`][noxdb._import.loader.load_project_dir],
            or one built in memory.
        cur: Cursor to read the database with, e.g. from
            [`transaction`][noxdb.connection.transaction]. Nothing is written.
        force: Allow adding to a project that already exists.
        skip_disk_check: Don't check that manifest files exist on this host.

    Returns:
        A [`ValidationResult`][noxdb._import.runner.ValidationResult].
    """
    result = ValidationResult(warnings=list(bundle.warnings) + _plate_warnings(bundle))
    result.errors.extend(_validate_schema(bundle))
    result.errors.extend(_validate_referential(bundle))
    if not skip_disk_check:
        result.errors.extend(_validate_disk(bundle))
    if cur is None:
        return result

    existing_project = projects.get_by_name(cur, bundle.project.project_name)
    if existing_project is not None and not force:
        result.errors.append(
            f"project {bundle.project.project_name!r} already exists "
            f"(project_id={existing_project['project_id']}); pass force=True "
            "(--force on the command line) to add to it."
        )
    result.errors.extend(_validate_db_collisions(cur, bundle))
    errs, warns = _validate_existing(cur, bundle)
    result.errors.extend(errs)
    result.warnings.extend(warns)
    return result


# --------------------------------------------------------------------------- #
# Commit
# --------------------------------------------------------------------------- #

def _commit(
    cur, bundle: loader.ProjectBundle, *, compute_md5: bool, skip_disk_check: bool = False,
) -> tuple[dict[str, dict[str, int]], int]:
    """Write the bundle to the database. Caller owns the transaction."""
    counts = {
        "projects":   {"inserted": 0, "existing": 0},
        "subjects":   {"inserted": 0, "existing": 0},
        "visits":     {"inserted": 0, "existing": 0},
        "samples":    {"inserted": 0, "existing": 0},
        "project_samples": {"linked": 0, "controls_linked": 0},
        "files":      {"inserted": 0, "existing": 0},
        "metadata":   {"inserted": 0, "updated": 0, "unchanged": 0},
    }

    pid, created = projects.get_or_create(
        cur, bundle.project.project_name,
        description=bundle.project.description,
        pi_name=bundle.project.pi_name,
    )
    counts["projects"]["inserted" if created else "existing"] += 1

    subject_ids: dict[str, int] = {}
    for s in bundle.subjects:
        sid, created = subjects.get_or_create(
            cur, s.subject_code, s.sex, origin=s.origin,
        )
        subject_ids[s.subject_code] = sid
        counts["subjects"]["inserted" if created else "existing"] += 1

    visit_ids: dict[tuple[str, str], int] = {}
    for v in bundle.visits:
        age_raw = (v.age or "").strip()
        age = schema.coerce_int(age_raw, field=f"visits.csv row {v.row_num}.age") if age_raw and age_raw.upper() not in ("NA", "N/A") else None
        vid, created = visits.get_or_create(
            cur, subject_ids[v.subject_code], v.timepoint, v.group_test, age,
        )
        visit_ids[(v.subject_code, v.timepoint)] = vid
        counts["visits"]["inserted" if created else "existing"] += 1
        for key, val in v.metadata.items():
            result = metadata.set_visit(cur, vid, key, val)
            counts["metadata"][result] += 1

    sample_ids: dict[str, int] = {}
    for sm in bundle.samples:
        vid = visit_ids[(sm.subject_code, sm.timepoint)]
        sid, created = samples.get_or_create(
            cur, vid, sm.sample_name, sm.sample_type, sm.sqr, sm.sqrp,
            sm.library, ipr=sm.ipr, iprp=sm.iprp,
            antibody_class=sm.antibody_class,
            **_barcodes_of(sm),
        )
        sample_ids[sm.sample_name] = sid
        counts["samples"]["inserted" if created else "existing"] += 1
        # project_samples is the sole project↔sample link. Register
        # every sample in this bundle under the imported project.
        samples.link_to_project(cur, pid, sid)
        counts["project_samples"]["linked"] += 1
        for key, val in sm.metadata.items():
            result = metadata.set_sample(cur, sid, key, val)
            counts["metadata"][result] += 1

    # Auto-link plate controls: any control (mockIP/anchor/NC) that sat
    # on the same IP plate as a real sample in this bundle is also
    # linked to this project, so controls_for_project keeps working.
    # This is an IP relationship, not a sequencing one — controls
    # occupy wells 81-96 of the IP plate, and a plate's samples can be
    # split across sequencing runs. Match on the canonical form, since
    # samples.create stored canonical IPR/IPRP.
    ip_coords = {
        (samples.canonical_plate_id(sm.ipr), samples.canonical_plate_id(sm.iprp))
        for sm in bundle.samples
        if sm.sample_type == "sample"
    }
    for ipr, iprp in ip_coords:
        cur.execute(
            "SELECT sample_id FROM samples "
            "WHERE IPR = ? AND IPRP = ? "
            "AND sample_type IN ('mockIP', 'anchor', 'NC')",
            (ipr, iprp),
        )
        for (ctrl_id,) in cur.fetchall():
            samples.link_to_project(cur, pid, ctrl_id)
            counts["project_samples"]["controls_linked"] += 1

    for f in bundle.files:
        fid, created = files_mod.get_or_register(
            cur,
            sample_ids[f.sample_name],
            f.file_path,
            f.file_type,
            compute_md5=compute_md5 and f.checksum_md5 is None,
            checksum_md5=f.checksum_md5,
            storage_tier=f.storage_tier,
            skip_disk_check=skip_disk_check,
        )
        counts["files"]["inserted" if created else "existing"] += 1

    return counts, pid


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #

def import_project_from_dir(
    root: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    compute_md5: bool = False,
    skip_disk_check: bool = False,
    log_dir: str | Path | None = None,
) -> ImportReport:
    """Validate and (unless *dry_run*) import the project under *root*.

    The runner performs validation in a read-only pass before any
    writes happen (see
    [`validate_bundle`][noxdb._import.runner.validate_bundle]), so a
    failed import never leaves the database in a half-written state.
    With ``force=True`` a re-run on the same folder, or a bundle adding
    to an existing project, re-uses existing rows via the
    ``get_or_create`` / ``get_or_register`` / ``set_*`` semantics of
    the CRUD layer; the report distinguishes ``inserted`` from
    ``existing`` (or, for metadata, ``inserted`` vs ``updated`` vs
    ``unchanged``). A reused subject, visit or sample must carry the
    same values as the database, or the import is refused.

    Cross-project collisions on ``sample_name`` or ``file_path`` are
    refused even with ``force=True`` — those UNIQUEs are global by
    design.

    Args:
        root: Path to the project folder containing ``project.yaml``,
            ``subjects.csv``, ``visits.csv``, ``samples.csv``, and
            ``files/manifest.csv``.
        dry_run: Validate only; skip the commit phase.
        force: Allow re-import of a project that already exists.
        compute_md5: Hash files whose manifest entry has no
            ``checksum_md5``.
        skip_disk_check: Skip the per-file ``os.path.exists`` check
            (use when files live on a remote mount not visible from
            this host).
        log_dir: Directory to write the JSON report to. Defaults to
            ``~/.noxdb/imports/``.

    Returns:
        An `ImportReport` with row counts per table.

    Raises:
        ProjectImportError: With the collected error list when
            validation fails, or when the project already exists and
            ``force`` is ``False``.
    """
    start = time.monotonic()
    bundle = loader.load_project_dir(root)
    report = ImportReport(
        project_name=bundle.project.project_name,
        dry_run=dry_run,
        force=force,
    )

    # The database checks run in their own short read transaction, so
    # every validation error is presented before deciding whether to
    # commit or refuse.
    with transaction() as cur:
        result = validate_bundle(
            bundle, cur=cur, force=force, skip_disk_check=skip_disk_check,
        )
    report.warnings = result.warnings
    errors = result.errors

    if errors:
        report.errors = errors
        report.duration_seconds = time.monotonic() - start
        _write_log(log_dir, report)
        raise ProjectImportError(
            f"import refused: {len(errors)} validation error(s)", errors,
        )

    if dry_run:
        report.duration_seconds = time.monotonic() - start
        _write_log(log_dir, report)
        return report

    with transaction() as cur:
        counts, pid = _commit(cur, bundle, compute_md5=compute_md5, skip_disk_check=skip_disk_check)
    report.counts = counts
    report.project_id = pid
    report.duration_seconds = time.monotonic() - start
    _write_log(log_dir, report)
    return report


def _write_log(log_dir: str | Path | None, report: ImportReport) -> None:
    """Append the JSON report to ``<log_dir>/<ts>_<project>.log``."""
    target = Path(log_dir).expanduser() if log_dir else (
        Path.home() / ".noxdb" / "imports"
    )
    target.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "_"
                   for c in report.project_name)
    path = target / f"{ts}_{safe}.log"
    path.write_text(
        json.dumps(report.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
