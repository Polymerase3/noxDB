"""Test fixtures: reset the database and load the schema once per session."""

from __future__ import annotations

import os
import re
from pathlib import Path

import mariadb
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = REPO_ROOT / "schema" / "001_initial.sql"
DB_NAME = os.environ.get("DB_NAME", "dbmaria_project")

if not re.fullmatch(r"[A-Za-z0-9_]+", DB_NAME):
    raise RuntimeError(
        f"Invalid DB_NAME {DB_NAME!r}: must match ^[A-Za-z0-9_]+$"
    )


def _server_conn():
    return mariadb.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        autocommit=True,
    )


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    for raw_line in sql.splitlines():
        line = raw_line.rstrip()
        # Strip full-line SQL comments
        if line.lstrip().startswith("--") or not line.strip():
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).rstrip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    return statements


def _is_db_selection_stmt(stmt: str) -> bool:
    head = stmt.lstrip().upper()
    return head.startswith("CREATE DATABASE") or head.startswith("USE ")


@pytest.fixture(scope="session")
def fresh_db():
    """Drop, recreate, and load schema. Yields nothing — env vars carry config."""
    conn = _server_conn()
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{DB_NAME}`")
    cur.execute(
        f"CREATE DATABASE `{DB_NAME}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cur.execute("SET sql_mode = 'NO_ENGINE_SUBSTITUTION'")
    cur.close()
    conn.close()

    schema_sql = SCHEMA_FILE.read_text()
    conn = _server_conn()
    cur = conn.cursor()
    cur.execute(f"USE `{DB_NAME}`")
    for stmt in _split_sql(schema_sql):
        if _is_db_selection_stmt(stmt):
            continue
        cur.execute(stmt)
    cur.close()
    conn.close()
    yield


@pytest.fixture
def db_conn(fresh_db):
    conn = mariadb.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        database=DB_NAME,
        autocommit=False,
    )
    yield conn
    conn.close()
