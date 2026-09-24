"""Unit tests for SSH tunnel credential resolution and the OpenSSH tunnel.

These tests exercise `_resolve_ssh_credentials`, `_load_ssh_credentials` and
`_open_tunnel` without a real SSH server: the ssh command is swapped for a
small Python process. The opt-in integration test at the bottom is skipped
unless `NOXDB_SSH_HOST` is set in the environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from noxdb import connection
from noxdb.connection import (
    _is_port_open,
    _load_ssh_credentials,
    _open_tunnel,
    _resolve_ssh_credentials,
    _ssh_command,
)


SSH_ENV_VARS = (
    "NOXDB_SSH_HOST",
    "NOXDB_SSH_PORT",
    "NOXDB_SSH_USER",
    "NOXDB_SSH_PASSWORD",
    "NOXDB_SSH_PKEY",
    "NOXDB_SSH_PKEY_PASSWORD",
)


@pytest.fixture(autouse=True)
def _clear_ssh_env(monkeypatch):
    for var in SSH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def _write_cnf(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# _load_ssh_credentials
# --------------------------------------------------------------------------- #

def test_load_ssh_credentials_full_section(tmp_path):
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb-ssh]\n"
        "ssh_host=ccr-lab.lisc.univie.ac.at\n"
        "ssh_port=2222\n"
        "ssh_user=alice\n"
        "ssh_pkey=~/.ssh/id_ed25519\n",
    )
    creds = _load_ssh_credentials(cnf, "noxdb-ssh")
    assert creds == {
        "ssh_host": "ccr-lab.lisc.univie.ac.at",
        "ssh_port": 2222,
        "ssh_user": "alice",
        "ssh_pkey": "~/.ssh/id_ed25519",
    }


def test_load_ssh_credentials_missing_section_returns_empty(tmp_path):
    cnf = _write_cnf(tmp_path / "my.cnf", "[noxdb]\nuser=alice\npassword=s\n")
    assert _load_ssh_credentials(cnf, "noxdb-ssh") == {}


def test_load_ssh_credentials_missing_file_returns_empty(tmp_path):
    assert _load_ssh_credentials(tmp_path / "does-not-exist.cnf", "noxdb-ssh") == {}


# --------------------------------------------------------------------------- #
# _resolve_ssh_credentials precedence
# --------------------------------------------------------------------------- #

def test_resolve_ssh_no_config_no_env_returns_empty():
    assert _resolve_ssh_credentials(None, "noxdb-ssh", {}) == {}


def test_resolve_ssh_ini_only(tmp_path):
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb-ssh]\nssh_host=h.example\nssh_user=alice\n",
    )
    creds = _resolve_ssh_credentials(cnf, "noxdb-ssh", {})
    assert creds == {"ssh_host": "h.example", "ssh_user": "alice"}


def test_resolve_ssh_env_overrides_ini(tmp_path, monkeypatch):
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb-ssh]\nssh_host=ini.example\nssh_user=alice\nssh_port=22\n",
    )
    monkeypatch.setenv("NOXDB_SSH_HOST", "env.example")
    monkeypatch.setenv("NOXDB_SSH_PORT", "2222")
    creds = _resolve_ssh_credentials(cnf, "noxdb-ssh", {})
    assert creds["ssh_host"] == "env.example"
    assert creds["ssh_port"] == 2222
    assert creds["ssh_user"] == "alice"  # ini value preserved


def test_resolve_ssh_kwargs_override_env_and_ini(tmp_path, monkeypatch):
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb-ssh]\nssh_host=ini.example\nssh_user=ini_user\n",
    )
    monkeypatch.setenv("NOXDB_SSH_HOST", "env.example")
    monkeypatch.setenv("NOXDB_SSH_USER", "env_user")
    creds = _resolve_ssh_credentials(
        cnf,
        "noxdb-ssh",
        {"ssh_host": "kwarg.example", "ssh_user": "kwarg_user"},
    )
    assert creds["ssh_host"] == "kwarg.example"
    assert creds["ssh_user"] == "kwarg_user"


def test_resolve_ssh_kwargs_none_does_not_override(tmp_path):
    cnf = _write_cnf(
        tmp_path / "my.cnf",
        "[noxdb-ssh]\nssh_host=ini.example\nssh_user=alice\n",
    )
    creds = _resolve_ssh_credentials(
        cnf,
        "noxdb-ssh",
        {"ssh_host": None, "ssh_user": None, "ssh_password": None},
    )
    assert creds["ssh_host"] == "ini.example"
    assert creds["ssh_user"] == "alice"


def test_resolve_ssh_port_coerced_to_int_from_env(monkeypatch):
    monkeypatch.setenv("NOXDB_SSH_HOST", "h")
    monkeypatch.setenv("NOXDB_SSH_PORT", "2200")
    creds = _resolve_ssh_credentials(None, "noxdb-ssh", {})
    assert creds["ssh_port"] == 2200
    assert isinstance(creds["ssh_port"], int)


def test_resolve_ssh_config_path_none_still_reads_env(monkeypatch):
    monkeypatch.setenv("NOXDB_SSH_HOST", "h.example")
    monkeypatch.setenv("NOXDB_SSH_USER", "alice")
    creds = _resolve_ssh_credentials(None, "noxdb-ssh", {})
    assert creds == {"ssh_host": "h.example", "ssh_user": "alice"}


# --------------------------------------------------------------------------- #
# _open_tunnel: the ssh command is replaced by a Python stand-in
# --------------------------------------------------------------------------- #

CREDS = {"ssh_host": "jump.example.org", "ssh_user": "someone"}


def _fake_ssh(monkeypatch, script: str) -> None:
    """Run *script* instead of ssh; it gets the local port as argv[1]."""
    monkeypatch.setattr(
        connection, "_ssh_command",
        lambda creds, host, port, local_port: [sys.executable, "-c", script, str(local_port)],
    )


def test_ssh_command_full():
    cmd = _ssh_command(
        {**CREDS, "ssh_port": 2222, "ssh_pkey": "~/.ssh/id_ed25519"},
        "mariadb.lisc", 3306, 40000,
    )
    assert cmd[:2] == ["ssh", "-N"]
    assert "BatchMode=yes" in cmd and "ExitOnForwardFailure=yes" in cmd
    assert cmd[cmd.index("-L") + 1] == "127.0.0.1:40000:mariadb.lisc:3306"
    assert cmd[cmd.index("-p") + 1] == "2222"
    assert cmd[cmd.index("-i") + 1] == str(Path("~/.ssh/id_ed25519").expanduser())
    assert cmd[-1] == "someone@jump.example.org"


def test_ssh_command_defaults_port_22_and_no_key():
    cmd = _ssh_command(CREDS, "db", 3306, 40000)
    assert cmd[cmd.index("-p") + 1] == "22"
    assert "-i" not in cmd


def test_open_tunnel_missing_user_raises():
    with pytest.raises(RuntimeError, match="ssh_user"):
        _open_tunnel({"ssh_host": "jump.example.org"}, "db", 3306)


def test_open_tunnel_reports_ssh_stderr(monkeypatch):
    _fake_ssh(monkeypatch, "import sys; sys.stderr.write('Permission denied (publickey).'); sys.exit(255)")
    with pytest.raises(RuntimeError, match=r"jump\.example\.org:22: Permission denied \(publickey\)"):
        _open_tunnel(CREDS, "db", 3306)


def test_open_tunnel_ssh_not_found(monkeypatch):
    monkeypatch.setattr(
        connection, "_ssh_command", lambda *a: ["/nonexistent/ssh"]
    )
    with pytest.raises(RuntimeError, match="not on PATH"):
        _open_tunnel(CREDS, "db", 3306)


def test_open_tunnel_times_out(monkeypatch):
    monkeypatch.setattr(connection, "TUNNEL_START_TIMEOUT", 0.5)
    _fake_ssh(monkeypatch, "import time; time.sleep(30)")
    with pytest.raises(RuntimeError, match="no listener"):
        _open_tunnel(CREDS, "db", 3306)


def test_open_tunnel_listens_then_stop_ends_process(monkeypatch):
    _fake_ssh(monkeypatch, (
        "import socket, sys, time\n"
        "s = socket.socket(); s.bind(('127.0.0.1', int(sys.argv[1]))); s.listen()\n"
        "time.sleep(30)\n"
    ))
    tunnel = _open_tunnel(CREDS, "db", 3306)
    host, port = tunnel.local_bind_address
    assert host == "127.0.0.1" and _is_port_open(port)
    tunnel.stop()
    assert tunnel._proc.poll() is not None
    assert not _is_port_open(port)


# --------------------------------------------------------------------------- #
# Opt-in integration test: opens a real tunnel through ccr-lab. Skipped unless
# NOXDB_SSH_HOST is set. Run from outside LiSC with DB_HOST pointing at the
# Galera cluster's internal hostname.
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    not os.environ.get("NOXDB_SSH_HOST"),
    reason="NOXDB_SSH_HOST not set; skipping live SSH tunnel test",
)
def test_real_tunnel_select_one():
    from noxdb import close_pool, execute, init_pool

    init_pool(
        config_path=None,
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "ccr_metadata"),
    )
    try:
        rows = execute("SELECT 1 AS ok")
        assert rows == [{"ok": 1}]
    finally:
        close_pool()
