#!/usr/bin/env python3
"""Monitoring sweep for ccr_metadata (noxDB) — runs natively on ccr-lab.

No SSH tunnel is used: this script is meant to run on the ccr-lab VM itself,
already inside the LiSC internal network, so ``init_pool()`` connects
directly using ``~/.my.cnf``'s ``[noxdb]`` section (no ``[noxdb-ssh]``
section needed there).

Usage:
    noxdb_sweep.py --mode {heartbeat,weekly,monthly,manual}

Modes:
    heartbeat  Liveness only. Silent on success, emails immediately on
               failure or a slow response. Meant to run daily.
    weekly     Liveness + schema fingerprint + population snapshot +
               integrity check + DB->disk drift + audit-log summary.
               Always emails. Meant to run weekly.
    monthly    Everything weekly does, plus the full disk->DB filesystem
               walk and a 30-day uptime summary. Always emails. Meant to
               run monthly.
    manual     Every check monthly runs, triggered by hand. Always emails.

See README.md in this directory for deployment instructions (credentials
file format, cron entries, the ccr-lab-specific mariadb pip pin).
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import signal
import smtplib
import socket
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from noxdb import close_pool, init_pool, projects, queries, transaction

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

LOG_DIR = Path(os.environ.get("NOXDB_SWEEP_LOG_DIR", str(Path.home() / "logs"))).expanduser()
LOG_TEXT = LOG_DIR / "noxdb_sweep.log"
LOG_JSONL = LOG_DIR / "noxdb_sweep.jsonl"
SCHEMA_FINGERPRINT_FILE = LOG_DIR / "noxdb_schema_fingerprint.json"
LOCK_FILE = Path(os.environ.get("NOXDB_SWEEP_LOCK", "/tmp/noxdb_sweep.lock"))
CREDENTIALS_FILE = Path(
    os.environ.get("NOXDB_SWEEP_CREDENTIALS", str(Path.home() / ".config" / "noxdb_sweep" / "credentials"))
).expanduser()

TIMEOUT_SECONDS = 30 * 60
SLOW_RESPONSE_MS = 5000
AUDIT_LOG_WINDOW_DAYS = 7
UPTIME_WINDOW_DAYS = 30

CHECK_SETS: dict[str, list[str]] = {
    "heartbeat": ["liveness"],
    "weekly": [
        "liveness", "schema_fingerprint", "population", "integrity",
        "db_files_missing_on_disk", "audit_log",
    ],
    "monthly": [
        "liveness", "schema_fingerprint", "population", "integrity",
        "db_files_missing_on_disk", "disk_files_missing_in_db", "audit_log",
    ],
    "manual": [
        "liveness", "schema_fingerprint", "population", "integrity",
        "db_files_missing_on_disk", "disk_files_missing_in_db", "audit_log",
    ],
}

_LEVEL_COLOR = {"ok": "#2e7d32", "warn": "#e65100", "error": "#c62828"}
_LEVEL_BANNER = {"ok": "✅ All good", "warn": "⚠️ Warnings", "error": "\U0001f534 Failures"}


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

def log_text(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_TEXT.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_jsonl(record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _iter_jsonl() -> list[dict[str, Any]]:
    if not LOG_JSONL.exists():
        return []
    records = []
    with LOG_JSONL.open(encoding="utf-8") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def last_jsonl_record_with(key: str) -> dict[str, Any] | None:
    """Most recent JSONL record that has *key* at top level, or None."""
    last = None
    for rec in _iter_jsonl():
        if key in rec:
            last = rec
    return last


# --------------------------------------------------------------------------- #
# Lock file / timeout guard
# --------------------------------------------------------------------------- #

@contextmanager
def lock():
    """PID-based lock so overlapping cron runs can't happen. Stale-lock-safe."""
    if LOCK_FILE.exists():
        raw = LOCK_FILE.read_text().strip()
        held = False
        try:
            pid = int(raw)
            os.kill(pid, 0)
            held = True
        except ValueError:
            log_text(f"lock file {LOCK_FILE} has non-numeric contents ({raw!r}); treating as stale")
        except ProcessLookupError:
            log_text(f"lock file {LOCK_FILE} references dead pid {raw}; treating as stale")
        except PermissionError:
            held = True  # process exists, owned by someone else
        if held:
            log_text(f"another sweep is already running (pid {raw}); exiting")
            sys.exit(1)
        LOCK_FILE.unlink()
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    try:
        yield
    finally:
        LOCK_FILE.unlink(missing_ok=True)


class SweepTimeout(Exception):
    pass


@contextmanager
def timeout_guard(seconds: int):
    def _handler(signum, frame):
        raise SweepTimeout(f"sweep exceeded {seconds}s hard timeout")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


# --------------------------------------------------------------------------- #
# SMTP credentials + mail
# --------------------------------------------------------------------------- #

def load_smtp_config() -> dict[str, Any]:
    if not CREDENTIALS_FILE.exists():
        raise SystemExit(f"credentials file not found: {CREDENTIALS_FILE}")
    mode = CREDENTIALS_FILE.stat().st_mode & 0o777
    if mode & 0o077:
        log_text(f"WARNING: {CREDENTIALS_FILE} is group/world-readable (mode {oct(mode)}); chmod 600 recommended")

    parser = configparser.ConfigParser()
    parser.read(CREDENTIALS_FILE)
    if "smtp" not in parser:
        raise SystemExit(f"[smtp] section missing in {CREDENTIALS_FILE}")
    s = parser["smtp"]
    required = ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "mail_from", "mail_to"]
    missing = [k for k in required if k not in s]
    if missing:
        raise SystemExit(f"{CREDENTIALS_FILE} missing keys: {missing}")

    return {
        "host": s["smtp_host"],
        "port": int(s["smtp_port"]),
        "user": s["smtp_user"],
        "password": s["smtp_password"],
        "mail_from": s["mail_from"],
        "mail_to": s["mail_to"],
    }


def send_email(smtp_cfg: dict[str, Any], subject: str, html_body: str) -> None:
    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = smtp_cfg["mail_from"]
    msg["To"] = smtp_cfg["mail_to"]
    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"], timeout=30) as server:
        server.starttls()
        server.login(smtp_cfg["user"], smtp_cfg["password"])
        server.sendmail(smtp_cfg["mail_from"], [smtp_cfg["mail_to"]], msg.as_string())


# --------------------------------------------------------------------------- #
# Checks — each takes a cursor, returns
# {"name", "ok", "level", "summary", "details"}
# --------------------------------------------------------------------------- #

def run_check(name: str, fn, cur) -> dict[str, Any]:
    try:
        return fn(cur)
    except Exception as exc:
        log_text(f"check {name!r} raised: {exc!r}")
        return {
            "name": name,
            "ok": False,
            "level": "error",
            "summary": f"check crashed: {exc}",
            "details": {"traceback": traceback.format_exc()},
        }


def check_liveness(cur) -> dict[str, Any]:
    t0 = time.monotonic()
    cur.execute("SELECT 1")
    cur.fetchone()
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    level = "warn" if elapsed_ms > SLOW_RESPONSE_MS else "ok"
    return {
        "name": "liveness",
        "ok": True,
        "level": level,
        "summary": f"DB responded in {elapsed_ms} ms",
        "details": {"response_ms": elapsed_ms},
    }


def check_schema_fingerprint(cur) -> dict[str, Any]:
    cur.execute(
        "SELECT table_name, column_name, column_type, is_nullable, column_key "
        "FROM information_schema.columns WHERE table_schema = DATABASE() "
        "ORDER BY table_name, ordinal_position"
    )
    lines = [f"{t}.{c} {ctype} null={n} key={k}" for t, c, ctype, n, k in cur.fetchall()]
    digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()

    previous = None
    if SCHEMA_FINGERPRINT_FILE.exists():
        previous = json.loads(SCHEMA_FINGERPRINT_FILE.read_text())

    changed = previous is not None and previous["hash"] != digest
    diff: list[str] = []
    if changed:
        prev_lines = set(previous["lines"])
        cur_lines = set(lines)
        diff = [f"- {l}" for l in sorted(prev_lines - cur_lines)] + [f"+ {l}" for l in sorted(cur_lines - prev_lines)]

    SCHEMA_FINGERPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_FINGERPRINT_FILE.write_text(json.dumps({
        "hash": digest,
        "lines": lines,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }))

    return {
        "name": "schema_fingerprint",
        "ok": True,
        "level": "warn" if changed else "ok",
        "summary": "schema changed since last run" if changed else "schema unchanged since last run",
        "details": {"hash": digest, "previous_hash": previous["hash"] if previous else None, "diff": diff},
    }


def check_population(cur) -> dict[str, Any]:
    all_projects = projects.list_all(cur)
    totals = {"n_subjects": 0, "n_visits": 0, "n_samples": 0, "n_files": 0, "n_controls": 0}
    files_by_type: dict[str, int] = {}
    controls_by_type: dict[str, int] = {}
    per_project = []
    for p in all_projects:
        summary = queries.project_summary(cur, p["project_id"])
        per_project.append({"project_id": p["project_id"], "project_name": p["project_name"], **summary})
        for k in totals:
            totals[k] += summary[k]
        for ft, n in summary["files_by_type"].items():
            files_by_type[ft] = files_by_type.get(ft, 0) + n
        for ct, n in summary["controls_by_type"].items():
            controls_by_type[ct] = controls_by_type.get(ct, 0) + n

    n_inputs = len(queries.list_inputs(cur))
    n_projects = len(all_projects)

    return {
        "name": "population",
        "ok": True,
        "level": "ok",
        "summary": (
            f"{n_projects} projects, {totals['n_subjects']} subjects, "
            f"{totals['n_samples']} samples ({totals['n_controls']} controls, {n_inputs} inputs), "
            f"{totals['n_files']} files"
        ),
        "details": {
            "n_projects": n_projects,
            **totals,
            "n_inputs": n_inputs,
            "files_by_type": files_by_type,
            "controls_by_type": controls_by_type,
            "per_project": per_project,
        },
    }


def check_integrity(cur) -> dict[str, Any]:
    all_projects = projects.list_all(cur)
    issues_by_project = []
    total_issues = 0
    for p in all_projects:
        report = queries.integrity_check(cur, p["project_id"])
        n_issues = sum(len(report[k]) for k in (
            "samples_without_files", "archive_files_missing_md5",
            "files_outside_tier_root", "unknown_file_types",
        ))
        if n_issues:
            total_issues += n_issues
            issues_by_project.append({
                "project_id": p["project_id"], "project_name": p["project_name"],
                "n_issues": n_issues, "report": report,
            })

    return {
        "name": "integrity",
        "ok": total_issues == 0,
        "level": "ok" if total_issues == 0 else "warn",
        "summary": (
            "no integrity issues across any project" if total_issues == 0
            else f"{total_issues} integrity issue(s) across {len(issues_by_project)} project(s)"
        ),
        "details": {"total_issues": total_issues, "issues_by_project": issues_by_project},
    }


def check_db_files_missing_on_disk(cur) -> dict[str, Any]:
    missing = queries.find_db_files_missing_on_disk(cur)
    n = len(missing)
    return {
        "name": "db_files_missing_on_disk",
        "ok": n == 0,
        "level": "ok" if n == 0 else "warn",
        "summary": "all registered files present on disk" if n == 0 else f"{n} registered file(s) missing on disk",
        "details": {"n_missing": n, "sample": missing.head(20).to_dict("records") if n else []},
    }


def check_disk_files_missing_in_db(cur) -> dict[str, Any]:
    found = queries.find_disk_files_missing_in_db(cur)
    n = len(found)
    return {
        "name": "disk_files_missing_in_db",
        "ok": n == 0,
        "level": "ok" if n == 0 else "warn",
        "summary": "no unregistered files found on disk" if n == 0 else f"{n} file(s) on disk are not registered in the DB",
        "details": {"n_unregistered": n, "sample": found.head(20).to_dict("records") if n else []},
    }


_AUDIT_LINE_RE = re.compile(r"^(?P<ts>[\d-]+ [\d:,]+) \| (?P<user>[^|]+) \| (?P<rest>.+)$")
_AUDIT_TABLE_RE = re.compile(
    r"^(?:INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+`?(\w+)`?",
    re.IGNORECASE,
)


def check_audit_log(cur) -> dict[str, Any]:
    """Tail the local audit log. *cur* is unused (kept for uniform dispatch)."""
    audit_path = Path(os.environ.get("NOXDB_AUDIT_LOG", str(Path.home() / ".noxdb" / "audit.log"))).expanduser()
    if not audit_path.exists():
        return {
            "name": "audit_log", "ok": True, "level": "ok",
            "summary": "no audit log found (no writes yet on this host?)", "details": {},
        }

    cutoff = datetime.now() - timedelta(days=AUDIT_LOG_WINDOW_DAYS)
    by_table: dict[str, int] = {}
    total = 0
    with audit_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _AUDIT_LINE_RE.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S,%f")
            except ValueError:
                continue
            if ts < cutoff:
                continue
            tm = _AUDIT_TABLE_RE.match(m.group("rest"))
            table = tm.group(1) if tm else "unknown"
            by_table[table] = by_table.get(table, 0) + 1
            total += 1

    return {
        "name": "audit_log",
        "ok": True,
        "level": "ok",
        "summary": (
            f"{total} write(s) in the last {AUDIT_LOG_WINDOW_DAYS}d" if total
            else f"no writes in the last {AUDIT_LOG_WINDOW_DAYS}d"
        ),
        "details": {"total": total, "by_table": by_table},
    }


CHECK_FUNCS = {
    "liveness": check_liveness,
    "schema_fingerprint": check_schema_fingerprint,
    "population": check_population,
    "integrity": check_integrity,
    "db_files_missing_on_disk": check_db_files_missing_on_disk,
    "disk_files_missing_in_db": check_disk_files_missing_in_db,
    "audit_log": check_audit_log,
}


# --------------------------------------------------------------------------- #
# Deltas / uptime
# --------------------------------------------------------------------------- #

def compute_deltas(current_population: dict[str, Any]) -> dict[str, Any] | None:
    prev = last_jsonl_record_with("population")
    if prev is None:
        return None
    prev_pop = prev["population"]["details"]
    keys = ["n_projects", "n_subjects", "n_visits", "n_samples", "n_files", "n_controls", "n_inputs"]
    return {
        "since": prev["timestamp"],
        "deltas": {k: current_population.get(k, 0) - prev_pop.get(k, 0) for k in keys},
    }


def compute_uptime(days: int = UPTIME_WINDOW_DAYS) -> dict[str, Any] | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    total = 0
    ok = 0
    for rec in _iter_jsonl():
        liveness = rec.get("liveness")
        ts_raw = rec.get("timestamp")
        if liveness is None or not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts < cutoff:
            continue
        total += 1
        if liveness.get("ok"):
            ok += 1
    if total == 0:
        return None
    return {"n_runs": total, "n_ok": ok, "uptime_pct": round(100 * ok / total, 2)}


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

def _overall_level(results: list[dict[str, Any]]) -> str:
    level = "ok"
    for r in results:
        if r["level"] == "error":
            return "error"
        if r["level"] == "warn":
            level = "warn"
    return level


def render_html(mode: str, results: list[dict[str, Any]], deltas: dict[str, Any] | None, uptime: dict[str, Any] | None) -> str:
    by_name = {r["name"]: r for r in results}
    overall = _overall_level(results)

    parts = [
        f"<h2 style='color:{_LEVEL_COLOR[overall]};margin-bottom:4px'>{_LEVEL_BANNER[overall]}</h2>",
        f"<p style='color:#555;margin-top:0'>noxdb_sweep &middot; mode={mode} &middot; "
        f"host={socket.gethostname()} &middot; {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>",
    ]

    liveness = by_name.get("liveness")
    if liveness:
        parts.append(f"<p><b>Liveness:</b> {liveness['summary']}</p>")

    population = by_name.get("population")
    if population and population["ok"]:
        d = population["details"]
        rows = [
            ("Projects", "n_projects"), ("Subjects", "n_subjects"), ("Visits", "n_visits"),
            ("Samples", "n_samples"), ("Controls", "n_controls"), ("Inputs", "n_inputs"),
            ("Files", "n_files"),
        ]
        parts.append("<h3>Population</h3><table cellpadding='4' style='border-collapse:collapse'>")
        parts.extend(f"<tr><td style='color:#555'>{label}</td><td><b>{d[key]}</b></td></tr>" for label, key in rows)
        parts.append("</table>")
        if deltas:
            changed = {k: v for k, v in deltas["deltas"].items() if v != 0}
            since = deltas["since"]
            if changed:
                summary = ", ".join(f"{'+' if v >= 0 else ''}{v} {k}" for k, v in changed.items())
                parts.append(f"<p style='color:#555'>Since {since}: {summary}</p>")
            else:
                parts.append(f"<p style='color:#555'>No change since {since}</p>")

    if uptime:
        parts.append(f"<p><b>Uptime ({UPTIME_WINDOW_DAYS}d):</b> {uptime['uptime_pct']}% ({uptime['n_ok']}/{uptime['n_runs']} runs)</p>")

    schema = by_name.get("schema_fingerprint")
    if schema:
        parts.append(f"<h3>Schema</h3><p style='color:{_LEVEL_COLOR[schema['level']]}'>{schema['summary']}</p>")
        if schema["details"].get("diff"):
            parts.append(
                "<pre style='background:#f5f5f5;padding:8px;overflow-x:auto'>"
                + "\n".join(schema["details"]["diff"]) + "</pre>"
            )

    integrity = by_name.get("integrity")
    if integrity:
        parts.append("<h3>Integrity</h3>")
        if integrity["ok"]:
            parts.append(f"<p style='color:{_LEVEL_COLOR['ok']}'>no integrity issues across any project</p>")
        else:
            parts.append(f"<p style='color:{_LEVEL_COLOR['warn']}'>{integrity['summary']}</p>")
            for proj in integrity["details"]["issues_by_project"]:
                parts.append(f"<p><b>{proj['project_name']}</b> (project {proj['project_id']}): {proj['n_issues']} issue(s)</p>")
                parts.append(
                    "<pre style='background:#f5f5f5;padding:8px;overflow-x:auto'>"
                    + json.dumps(proj["report"], indent=2, default=str)[:3000] + "</pre>"
                )

    for name in ("db_files_missing_on_disk", "disk_files_missing_in_db", "audit_log"):
        r = by_name.get(name)
        if not r:
            continue
        parts.append(f"<h3>{name.replace('_', ' ').title()}</h3>")
        parts.append(f"<p style='color:{_LEVEL_COLOR[r['level']]}'>{r['summary']}</p>")
        if r["level"] != "ok" and r["details"].get("sample"):
            parts.append(
                "<pre style='background:#f5f5f5;padding:8px;overflow-x:auto'>"
                + json.dumps(r["details"]["sample"], indent=2, default=str)[:3000] + "</pre>"
            )

    for r in results:
        if r["level"] == "error" and "traceback" in r.get("details", {}):
            parts.append(f"<h3 style='color:{_LEVEL_COLOR['error']}'>{r['name']} crashed</h3>")
            parts.append(f"<pre style='background:#f5f5f5;padding:8px;overflow-x:auto'>{r['details']['traceback']}</pre>")

    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Sweep runner
# --------------------------------------------------------------------------- #

def run_sweep(mode: str) -> list[dict[str, Any]]:
    init_pool()
    try:
        with transaction() as cur:
            liveness = run_check("liveness", check_liveness, cur)
            results = [liveness]
            if not liveness["ok"]:
                log_text("liveness check failed; skipping remaining checks for this run")
                return results
            for name in CHECK_SETS[mode]:
                if name == "liveness":
                    continue
                results.append(run_check(name, CHECK_FUNCS[name], cur))
    finally:
        close_pool()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="noxDB monitoring sweep")
    parser.add_argument("--mode", choices=sorted(CHECK_SETS), required=True)
    args = parser.parse_args()

    with lock():
        try:
            with timeout_guard(TIMEOUT_SECONDS):
                results = run_sweep(args.mode)
        except Exception:
            log_text(f"sweep crashed during {args.mode} run:\n{traceback.format_exc()}")
            try:
                smtp_cfg = load_smtp_config()
                send_email(
                    smtp_cfg,
                    f"[noxdb_sweep] CRASH during {args.mode} run on {socket.gethostname()}",
                    f"<pre>{traceback.format_exc()}</pre>",
                )
            except Exception:
                log_text("failed to send crash alert email")
            sys.exit(1)

    by_name = {r["name"]: r for r in results}
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "mode": args.mode}
    record.update(by_name)
    log_jsonl(record)

    overall = _overall_level(results)
    deltas = compute_deltas(by_name["population"]["details"]) if "population" in by_name and by_name["population"]["ok"] else None
    uptime = compute_uptime() if args.mode == "monthly" else None

    should_email = args.mode != "heartbeat" or overall != "ok"
    log_text(f"sweep mode={args.mode} overall={overall} email={'yes' if should_email else 'no'}")

    if should_email:
        smtp_cfg = load_smtp_config()
        subject = f"[noxdb_sweep] {overall.upper()} — {args.mode} run on {socket.gethostname()}"
        send_email(smtp_cfg, subject, render_html(args.mode, results, deltas, uptime))

    sys.exit(0 if overall != "error" else 1)


if __name__ == "__main__":
    main()
