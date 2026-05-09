"""Integration tests for dbmaria_utils.connection (require a live MariaDB)."""

from __future__ import annotations

import pytest

from dbmaria_utils import execute, get_connection, transaction


@pytest.fixture
def kv_table(_init_pool):
    """Create a fresh key/value table for the test, drop it on teardown."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS _test_kv")
        cur.execute(
            "CREATE TABLE _test_kv ("
            "k VARCHAR(50) PRIMARY KEY, v INT NOT NULL"
            ") ENGINE=InnoDB"
        )
        cur.close()
    yield "_test_kv"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS _test_kv")
        cur.close()


# --------------------------------------------------------------------------- #
# get_connection
# --------------------------------------------------------------------------- #

def test_get_connection_commits_on_success(kv_table):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("a", 1))
        cur.close()

    rows = execute("SELECT v FROM _test_kv WHERE k = ?", ("a",))
    assert rows == [{"v": 1}]


def test_get_connection_rolls_back_on_exception(kv_table):
    with pytest.raises(RuntimeError, match="boom"):
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("b", 2))
            cur.close()
            raise RuntimeError("boom")

    rows = execute("SELECT v FROM _test_kv WHERE k = ?", ("b",))
    assert rows == []


# --------------------------------------------------------------------------- #
# execute
# --------------------------------------------------------------------------- #

def test_execute_select_returns_dicts(kv_table):
    execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("x", 10))
    execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("y", 20))

    rows = execute("SELECT k, v FROM _test_kv ORDER BY k")
    assert rows == [{"k": "x", "v": 10}, {"k": "y", "v": 20}]


def test_execute_write_returns_empty(kv_table):
    result = execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("z", 99))
    assert result == []


def test_execute_writes_audit_log(kv_table, _init_pool):
    audit_path = _init_pool
    execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("audit_key", 7))
    contents = audit_path.read_text(encoding="utf-8")
    assert "INSERT INTO _test_kv" in contents
    assert "rows=1" in contents


def test_execute_does_not_log_select(kv_table, _init_pool):
    audit_path = _init_pool
    before = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    execute("SELECT 1")
    after = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    assert before == after


# --------------------------------------------------------------------------- #
# transaction
# --------------------------------------------------------------------------- #

def test_transaction_commits_all_steps(kv_table):
    with transaction() as cur:
        cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("t1", 1))
        cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("t2", 2))

    rows = execute("SELECT k FROM _test_kv WHERE k IN (?, ?) ORDER BY k",
                   ("t1", "t2"))
    assert [r["k"] for r in rows] == ["t1", "t2"]


def test_transaction_rolls_back_atomically(kv_table):
    with pytest.raises(RuntimeError):
        with transaction() as cur:
            cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("r1", 1))
            cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("r2", 2))
            raise RuntimeError("rollback me")

    rows = execute("SELECT k FROM _test_kv WHERE k IN (?, ?)", ("r1", "r2"))
    assert rows == []


def test_transaction_lastrowid_accessible(kv_table):
    # Ensure the cursor wrapper forwards driver attributes like lastrowid.
    with transaction() as cur:
        cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("lr", 1))
        # _test_kv has no AUTO_INCREMENT, but the attribute must still resolve.
        assert hasattr(cur, "lastrowid")
        assert hasattr(cur, "rowcount")
        assert cur.rowcount == 1


def test_transaction_logs_each_write(kv_table, _init_pool):
    audit_path = _init_pool
    before = audit_path.read_text(encoding="utf-8")
    with transaction() as cur:
        cur.execute("INSERT INTO _test_kv (k, v) VALUES (?, ?)", ("log1", 1))
        cur.execute("UPDATE _test_kv SET v = ? WHERE k = ?", (2, "log1"))
    after = audit_path.read_text(encoding="utf-8")
    new = after[len(before):]
    assert "INSERT INTO _test_kv" in new
    assert "UPDATE _test_kv" in new
