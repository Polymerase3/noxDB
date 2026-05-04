"""Smoke test: schema loads, seed loads, and EAV coverage is complete."""

from __future__ import annotations

from seed.load_fake_data import load


def _scalar(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]


def _column(cur, sql: str) -> set:
    cur.execute(sql)
    return {row[0] for row in cur.fetchall()}


def test_seed_loads_and_covers_eav_types(db_conn):
    load(db_conn)
    cur = db_conn.cursor()

    assert _scalar(cur, "SELECT COUNT(*) FROM projects") == 1
    assert _scalar(cur, "SELECT COUNT(*) FROM subjects") == 2
    assert _scalar(cur, "SELECT COUNT(*) FROM visits") == 4
    assert _scalar(cur, "SELECT COUNT(*) FROM samples") >= 8
    assert _scalar(cur, "SELECT COUNT(*) FROM sample_files") >= 1

    expected_types = {"int", "numeric", "bool", "text"}
    assert _column(cur, "SELECT DISTINCT value_type FROM visit_metadata") == expected_types
    assert _column(cur, "SELECT DISTINCT value_type FROM sample_metadata") == expected_types

    # Longitudinal subject has multiple visits
    cur.execute(
        "SELECT subject_id, COUNT(*) c FROM visits GROUP BY subject_id ORDER BY c"
    )
    counts = [row[1] for row in cur.fetchall()]
    assert counts == [1, 3]

    # All four sample_type enum values appear
    sample_types = _column(cur, "SELECT DISTINCT sample_type FROM samples")
    assert {"sample", "input", "mockIP", "anchor"}.issubset(sample_types)
