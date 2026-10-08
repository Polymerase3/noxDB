"""Raw FASTQ QC results: the `sample_fastq_qc` table (schema 008).

One row per sample with the headline numbers and flags of its FASTQ quality
control, written by ``noxqc noxdb register`` from the sample's QC JSON (which
is itself registered in ``sample_files`` as file type ``fastq_qc``)::

    fastq_qc.upsert(cur, sample_id, row)       # -> "inserted" | "updated" | "unchanged"
    fastq_qc.get(cur, sample_id)               # -> dict | None
    fastq_qc.for_project(cur, project_id)      # -> DataFrame, one row per project sample
    fastq_qc.delete(cur, sample_id)            # -> bool

``upsert`` is idempotent (``INSERT ... ON DUPLICATE KEY UPDATE`` on the
primary key): re-running the QC of a sample overwrites its row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import pandas as pd

from noxdb.queries import _fetch_dicts, _pd

SetResult = Literal["inserted", "updated", "unchanged"]

COLUMNS = (
    "qc_version", "qc_run_at", "reads_r1", "reads_r2", "pair_ok", "sequencing_run", "n_lanes",
    "lane_min_frac", "top_index", "index_purity", "barcode_match", "q30_r1", "q30_r2", "avg_qual_r1",
    "avg_qual_r2", "gc_r1", "gc_r2", "dedup_r1", "adapter_max", "polyg_max", "n_max",
    "overrep_top_pct_r1", "depth_rel", "worst_flag", "flags",
)
_REQUIRED = ("qc_version", "qc_run_at", "pair_ok", "worst_flag")
WORST_FLAGS = ("FAIL", "WARN", "INFO", "OK")
BARCODE_MATCH = ("match", "mismatch", "unknown")


def _utc_naive(value: datetime | str) -> datetime:
    """ISO string or datetime -> naive UTC datetime (the column is DATETIME)."""
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _validate(row: dict[str, Any]) -> dict[str, Any]:
    unknown = set(row) - set(COLUMNS)
    if unknown:
        raise ValueError(f"unknown sample_fastq_qc columns: {', '.join(sorted(unknown))}")
    missing = [c for c in _REQUIRED if row.get(c) is None]
    if missing:
        raise ValueError(f"sample_fastq_qc row needs {', '.join(missing)}")
    if row["worst_flag"] not in WORST_FLAGS:
        raise ValueError(f"worst_flag must be one of {WORST_FLAGS}, got {row['worst_flag']!r}")
    out = {c: row.get(c) for c in COLUMNS}
    out["barcode_match"] = out["barcode_match"] or "unknown"
    if out["barcode_match"] not in BARCODE_MATCH:
        raise ValueError(f"barcode_match must be one of {BARCODE_MATCH}, got {out['barcode_match']!r}")
    out["qc_run_at"] = _utc_naive(out["qc_run_at"])
    out["pair_ok"] = bool(out["pair_ok"])
    return out


def upsert(cur, sample_id: int, row: dict[str, Any]) -> SetResult:
    """Insert or overwrite the QC row of a sample. Idempotent.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        sample_id: The sample (must exist).
        row: Column -> value. Unknown columns are rejected; ``qc_version``,
            ``qc_run_at`` (datetime or ISO string, stored as UTC),
            ``pair_ok`` and ``worst_flag`` are required, the rest default
            to NULL (``barcode_match`` to ``'unknown'``).

    Returns:
        ``"inserted"``, ``"updated"`` or ``"unchanged"``.

    Raises:
        ValueError: On unknown or missing columns, or an invalid enum value.
    """
    values = _validate(row)
    cols = ", ".join(COLUMNS)
    marks = ", ".join("?" for _ in COLUMNS)
    updates = ", ".join(f"{c} = VALUES({c})" for c in COLUMNS)
    cur.execute(
        f"INSERT INTO sample_fastq_qc (sample_id, {cols}) VALUES (?, {marks}) "
        f"ON DUPLICATE KEY UPDATE {updates}",
        (sample_id, *values.values()),
    )
    # MariaDB INSERT ... ON DUPLICATE KEY UPDATE rowcount:
    #   1 = row inserted, 2 = row updated, 0 = row matched but unchanged.
    rc = cur.rowcount
    if rc == 1:
        return "inserted"
    if rc == 2:
        return "updated"
    return "unchanged"


def get(cur, sample_id: int) -> dict[str, Any] | None:
    """Return the QC row of a sample, or ``None`` if it has none."""
    cur.execute("SELECT * FROM sample_fastq_qc WHERE sample_id = ?", (sample_id,))
    rows = _fetch_dicts(cur)
    return rows[0] if rows else None


def delete(cur, sample_id: int) -> bool:
    """Delete the QC row of a sample. Returns ``True`` if a row was removed."""
    cur.execute("DELETE FROM sample_fastq_qc WHERE sample_id = ?", (sample_id,))
    return cur.rowcount > 0


def for_project(cur, project_id: int) -> "pd.DataFrame":
    """One row per sample of a project (controls included), with its QC.

    Samples without a QC result are kept, with NULL QC columns, so the
    result also shows what is still unchecked.

    Args:
        cur: Audit-logging cursor from `transaction()`.
        project_id: Project to scan.

    Returns:
        A ``pandas.DataFrame`` with ``sample_id``, ``sample_name``,
        ``sample_type``, ``SQR``, ``SQRP``, ``IPR``, ``IPRP`` and every
        ``sample_fastq_qc`` column.

    Raises:
        ImportError: If pandas is not installed.
    """
    pd = _pd()
    qc_cols = ", ".join(f"q.{c}" for c in COLUMNS)
    cur.execute(
        "SELECT sm.sample_id, sm.sample_name, sm.sample_type, sm.SQR, sm.SQRP, sm.IPR, sm.IPRP, "
        f"{qc_cols} "
        "FROM project_samples ps "
        "JOIN samples sm ON sm.sample_id = ps.sample_id "
        "LEFT JOIN sample_fastq_qc q ON q.sample_id = sm.sample_id "
        "WHERE ps.project_id = ? ORDER BY sm.sample_name",
        (project_id,),
    )
    return pd.DataFrame(_fetch_dicts(cur))
