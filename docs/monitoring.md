# Monitoring: the noxdb_sweep cron job

`scripts/sweep/noxdb_sweep.py` is a standalone monitoring script meant to run
on the **ccr-lab LiSC VM** via cron. It answers the operational questions
that don't fit into notebook analysis: is the database live, has the schema
changed, how big is it, and is anything inconsistent between the DB and the
files on disk. Results are emailed as an HTML report.

It's built entirely on top of [`queries`][noxdb.queries] and
[`projects`][noxdb.projects] — nothing here talks to the database directly
outside a handful of small checks (liveness, schema fingerprint, audit log)
that don't have a library function of their own.

Full deployment instructions (installing on ccr-lab, `~/.my.cnf`,
credentials file, crontab entries) live in
[`scripts/sweep/README.md`](https://github.com/Polymerase3/phiper-db/blob/main/scripts/sweep/README.md)
in the repo. This page covers what it checks and why.

## Why it runs on ccr-lab directly

The production Galera cluster is only reachable from inside the LiSC
network. Every other noxDB use case (notebooks, imports) runs from outside
that network and opens an SSH tunnel through ccr-lab (see
[Install](install.md)). The sweep script instead runs natively *on*
ccr-lab, so `init_pool()` connects straight to `host:port` from
`~/.my.cnf`'s `[noxdb]` section — no `[noxdb-ssh]` section, no tunnel.

## Modes

| Mode | Cadence | Emails |
|---|---|---|
| `heartbeat` | daily | only on failure or a slow response |
| `weekly` | weekly | always |
| `monthly` | monthly | always |
| `manual` | on demand | always |

`manual` runs the full check set (same as `monthly`, including the slow
filesystem walk) for an on-demand "tell me the state of things right now"
report.

## What's checked

- **Liveness** — `SELECT 1` through [`transaction()`][noxdb.connection.transaction],
  timed. If this fails, every other check for that run is skipped — no point
  walking the filesystem when the DB itself is unreachable.
- **Schema fingerprint** — hashes `information_schema.columns` for the
  connected database and diffs against the last run's hash. Catches both
  deliberate migrations and accidental `ALTER`s.
- **Population snapshot** — loops [`projects.list_all`][noxdb.projects.list_all]
  through [`queries.project_summary`][noxdb.queries.project_summary] and adds
  [`queries.list_inputs`][noxdb.queries.list_inputs], giving total projects,
  subjects, visits, samples (patient/mockIP/anchor/NC/input broken out),
  and files. Each run's snapshot is appended to a JSONL history file, so
  every report shows the delta since the previous snapshot.
- **Integrity** — loops [`queries.integrity_check`][noxdb.queries.integrity_check]
  per project; the report only lists projects that actually have issues.
- **DB→disk drift** — [`queries.find_db_files_missing_on_disk`][noxdb.queries.find_db_files_missing_on_disk],
  cheap enough to run weekly.
- **Disk→DB drift** — [`queries.find_disk_files_missing_in_db`][noxdb.queries.find_disk_files_missing_in_db],
  a full recursive walk of `/lisc/archive` and `/lisc/work`. Reserved for
  `monthly`/`manual` because it can take minutes.
- **Audit log** — tails `~/.noxdb/audit.log` and counts writes per table
  over the last 7 days, as a lightweight "is this thing actually being
  used" signal.
- **Uptime %** — `monthly` only, computed from the JSONL history's liveness
  results over the trailing 30 days.

## Operational details

Lock file, 30-minute hard timeout, and a top-level crash handler that emails
a "sweep crashed" alert with the traceback are all documented alongside the
credentials/log paths in
[`scripts/sweep/README.md`](https://github.com/Polymerase3/phiper-db/blob/main/scripts/sweep/README.md).
