"""Unit tests for noxdb.connection that do not require a live DB."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from noxdb import connection as conn_mod
from noxdb.connection import (
    _load_credentials,
    _log_if_write,
    _resolve_credentials,
    init_pool,
)


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch, tmp_path):
    """Reset pool sentinel and redirect the audit log between tests."""
    monkeypatch.setattr(conn_mod, "_pool", None, raising=False)
    monkeypatch.delenv("NOXDB_DATABASE", raising=False)
    monkeypatch.setenv("NOXDB_AUDIT_LOG", str(tmp_path / "audit.log"))
    # Detach any handlers attached during a previous test
    for handler in list(conn_mod._logger.handlers):
        handler.close()
        conn_mod._logger.removeHandler(handler)
    yield
    for handler in list(conn_mod._logger.handlers):
        handler.close()
        conn_mod._logger.removeHandler(handler)


def _write_cnf(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# _load_credentials
# --------------------------------------------------------------------------- #

def test_load_credentials_happy(tmp_path):
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb]\nhost=db.example\nport=3307\nuser=alice\npassword=secret\ndatabase=lab\n",
    )
    creds = _load_credentials(cnf, "noxdb")
    assert creds == {
        "host": "db.example",
        "port": 3307,
        "user": "alice",
        "password": "secret",
        "database": "lab",
    }


def test_load_credentials_uses_defaults_for_optional_keys(tmp_path):
    cnf = _write_cnf(tmp_path / "my.cnf", "[noxdb]\nuser=alice\npassword=s\n")
    creds = _load_credentials(cnf, "noxdb")
    assert creds["host"] == "localhost"
    assert creds["port"] == 3306
    assert creds["database"] == "ccr_metadata"


def test_load_credentials_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        _load_credentials(tmp_path / "does_not_exist.cnf", "noxdb")


def test_load_credentials_missing_section(tmp_path):
    cnf = _write_cnf(tmp_path / "my.cnf", "[client]\nuser=x\npassword=y\n")
    with pytest.raises(RuntimeError, match=r"\[noxdb\] not found"):
        _load_credentials(cnf, "noxdb")


@pytest.mark.parametrize("missing", ["user", "password"])
def test_load_credentials_missing_required_key(tmp_path, missing):
    keep = "password=y" if missing == "user" else "user=x"
    cnf = _write_cnf(tmp_path / "my.cnf", f"[noxdb]\n{keep}\n")
    with pytest.raises(RuntimeError, match=f"Missing required key {missing!r}"):
        _load_credentials(cnf, "noxdb")


def test_load_credentials_custom_section(tmp_path):
    cnf = _write_cnf(tmp_path / "my.cnf", "[client]\nuser=x\npassword=y\n")
    creds = _load_credentials(cnf, "client")
    assert creds["user"] == "x"


# --------------------------------------------------------------------------- #
# _resolve_credentials
# --------------------------------------------------------------------------- #

def test_resolve_explicit_overrides_win(tmp_path):
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb]\nhost=file_host\nuser=file_user\npassword=file_pw\ndatabase=file_db\n",
    )
    creds = _resolve_credentials(
        cnf, "noxdb",
        {"host": "explicit_host", "port": None, "user": None,
         "password": None, "database": "explicit_db"},
    )
    assert creds["host"] == "explicit_host"
    assert creds["database"] == "explicit_db"
    assert creds["user"] == "file_user"  # not overridden


def test_resolve_env_var_database(monkeypatch, tmp_path):
    monkeypatch.setenv("NOXDB_DATABASE", "env_db")
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb]\nuser=u\npassword=p\ndatabase=file_db\n",
    )
    creds = _resolve_credentials(cnf, "noxdb", _empty_overrides())
    assert creds["database"] == "env_db"


def test_resolve_explicit_database_beats_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("NOXDB_DATABASE", "env_db")
    cnf = _write_cnf(tmp_path / "my.cnf", "[noxdb]\nuser=u\npassword=p\n")
    creds = _resolve_credentials(
        cnf, "noxdb",
        {**_empty_overrides(), "database": "explicit_db"},
    )
    assert creds["database"] == "explicit_db"


def test_resolve_no_config_path_uses_overrides_only():
    creds = _resolve_credentials(
        None, "noxdb",
        {"host": "h", "port": 1234, "user": "u", "password": "p", "database": "d"},
    )
    assert creds == {"host": "h", "port": 1234, "user": "u",
                     "password": "p", "database": "d"}


def test_resolve_no_config_path_missing_required_raises():
    with pytest.raises(RuntimeError, match="'user' is missing"):
        _resolve_credentials(
            None, "noxdb",
            {"host": "h", "port": None, "user": None,
             "password": "p", "database": None},
        )


def _empty_overrides():
    return {"host": None, "port": None, "user": None,
            "password": None, "database": None}


# --------------------------------------------------------------------------- #
# init_pool — validation paths that do not touch mariadb
# --------------------------------------------------------------------------- #

def test_init_pool_rejects_missing_credentials():
    with pytest.raises(RuntimeError, match="missing"):
        init_pool(config_path=None, host="h", database="d")  # no user/password


def test_init_pool_raises_when_already_initialized(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(conn_mod, "_pool", sentinel, raising=False)
    with pytest.raises(RuntimeError, match="already initialized"):
        init_pool(config_path=None, user="u", password="p")


# --------------------------------------------------------------------------- #
# audit logger
# --------------------------------------------------------------------------- #

def _read_audit_log() -> str:
    """Flush handlers and read the configured audit log file."""
    log_path = Path(os.environ["NOXDB_AUDIT_LOG"])
    for handler in conn_mod._logger.handlers:
        handler.flush()
    return log_path.read_text(encoding="utf-8") if log_path.exists() else ""


@pytest.mark.parametrize(
    "query, should_log",
    [
        ("INSERT INTO t VALUES (1)", True),
        ("  insert into t values (1)", True),
        ("UPDATE t SET x=1", True),
        ("DELETE FROM t", True),
        ("REPLACE INTO t VALUES (1)", True),
        ("SELECT * FROM t", False),
        ("WITH cte AS (...) SELECT 1", False),
        ("CREATE TABLE t (x INT)", False),
    ],
)
def test_log_if_write_filters_by_statement_kind(query, should_log):
    conn_mod._setup_audit_logger()
    _log_if_write(query, ("p",), 1)
    contents = _read_audit_log()
    if should_log:
        assert contents.strip(), f"expected log for {query!r}"
    else:
        assert not contents.strip(), f"unexpected log for {query!r}"


def test_log_if_write_truncates_long_query():
    conn_mod._setup_audit_logger()
    long_q = "INSERT INTO t VALUES (" + "1," * 500 + "1)"
    _log_if_write(long_q, None, 1)
    contents = _read_audit_log()
    assert "..." in contents
    assert len(contents) < len(long_q)


def test_audit_log_writes_to_configured_path(tmp_path, monkeypatch):
    log_path = tmp_path / "subdir" / "audit.log"
    monkeypatch.setenv("NOXDB_AUDIT_LOG", str(log_path))
    # Detach handlers configured by the autouse fixture so the new env var
    # takes effect on next setup.
    for handler in list(conn_mod._logger.handlers):
        handler.close()
        conn_mod._logger.removeHandler(handler)
    conn_mod._setup_audit_logger()
    _log_if_write("INSERT INTO t VALUES (?)", (1,), 1)
    for handler in conn_mod._logger.handlers:
        handler.flush()
    assert log_path.exists()
    contents = log_path.read_text(encoding="utf-8")
    assert "INSERT INTO t" in contents
