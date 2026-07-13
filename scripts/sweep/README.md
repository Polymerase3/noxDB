# noxdb_sweep — monitoring cron job

`noxdb_sweep.py` runs on the **ccr-lab LiSC VM**, checks the health of
`ccr_metadata`, and emails a report. It's independent of any other cron job
on that VM (own credentials, own logs).

Because it runs on ccr-lab — already inside the LiSC internal network — no
SSH tunnel is needed: `init_pool()` connects straight to the DB using
`~/.my.cnf`'s `[noxdb]` section. Do **not** add a `[noxdb-ssh]` section there.

## 1. Install (ccr-lab-specific)

ccr-lab's system MariaDB Connector/C is `3.2.6`. noxDB's `pyproject.toml`
pins `mariadb>=1.1.10`, which requires Connector/C `>=3.3.1` and fails to
build there. `mariadb==1.0.11` builds fine against `3.2.6` and is API-compatible
with everything noxDB uses (verified live against ccr-lab). This is a
ccr-lab-only override — don't change the package's own `pyproject.toml` pin.

```bash
python3 -m venv ~/noxdb-venv
source ~/noxdb-venv/bin/activate
pip install "mariadb==1.0.11" sshtunnel pyyaml pandas
git clone https://github.com/Polymerase3/phiper-db.git ~/noxdb   # or pull if already cloned
cd ~/noxdb
pip install --no-deps -e .    # --no-deps: skip re-resolving mariadb>=1.1.10
```

`pandas` is required for `queries.list_inputs` / `find_db_files_missing_on_disk`
/ `find_disk_files_missing_in_db`, which this script calls.

## 2. `~/.my.cnf` on ccr-lab

```ini
[noxdb]
host = <galera-host>
port = 3306
user = <readonly-user>
password = <...>
database = ccr_metadata
```

A read-only DB user is enough — every check this script runs is read-only.

## 3. Storage roots

`/lisc/archive` and `/lisc/work` must be mounted on ccr-lab for the disk-drift
checks to mean anything. Also set `NOXDB_WORK_ROOT` if the real work root
isn't `/lisc/work` — noxDB defaults to `/lisc/work`, but production files may
live under `/lisc/data/work`. Check with the team before assuming the default
is right; a wrong root makes `integrity_check`'s `files_outside_tier_root` and
the disk-drift checks report false positives. Export overrides in the
crontab, e.g. `NOXDB_WORK_ROOT=/lisc/data/work`.

## 4. SMTP credentials

`~/.config/noxdb_sweep/credentials`, mode `600`:

```ini
[smtp]
smtp_host = smtp.example.org
smtp_port = 587
smtp_user = you@example.org
smtp_password = ...
mail_from = noxdb-sweep@example.org
mail_to = you@example.org
```

```bash
mkdir -p ~/.config/noxdb_sweep
chmod 600 ~/.config/noxdb_sweep/credentials
```

This file holds **SMTP credentials only** — DB credentials always come from
`~/.my.cnf`, never from here.

## 5. Logs / lock / schema fingerprint

All under `~/logs/` by default (override with `NOXDB_SWEEP_LOG_DIR`):

- `noxdb_sweep.log` — human-readable run log
- `noxdb_sweep.jsonl` — one JSON record per run; feeds week-over-week deltas
  and the monthly uptime %
- `noxdb_schema_fingerprint.json` — last-seen schema hash, for diffing

Lock file: `/tmp/noxdb_sweep.lock` (override with `NOXDB_SWEEP_LOCK`),
PID-based, self-clears if the owning process is gone.

## 6. Crontab

```cron
NOXDB_WORK_ROOT=/lisc/data/work
PATH=/home/USER/noxdb-venv/bin:/usr/local/bin:/usr/bin:/bin

# Daily liveness — silent on success, emails immediately on failure
0 1 * * *  /home/USER/noxdb-venv/bin/python /home/USER/noxdb/scripts/sweep/noxdb_sweep.py --mode heartbeat

# Weekly full report — Sundays 02:00. Always emails.
0 2 * * 0  /home/USER/noxdb-venv/bin/python /home/USER/noxdb/scripts/sweep/noxdb_sweep.py --mode weekly

# Monthly summary + full disk->DB walk — last day of the month, 03:00. Always emails.
0 3 28-31 * *  test $(date -d tomorrow +\%d) = 01 && /home/USER/noxdb-venv/bin/python /home/USER/noxdb/scripts/sweep/noxdb_sweep.py --mode monthly
```

Only set `NOXDB_WORK_ROOT` if the real root differs from `/lisc/work` (see
§3). Adjust `NOXDB_WORK_ROOT` and the two paths above (`noxdb-venv`, `noxdb`
checkout) to wherever you actually put them.

## 7. Manual run

Run any mode by hand, any time — it always emails when invoked this way for
`weekly`/`monthly`/`manual`, or use `--mode manual` to run every check
(including the slow disk→DB walk) on demand and always get a report,
regardless of whether anything's wrong:

```bash
source ~/noxdb-venv/bin/activate
python ~/noxdb/scripts/sweep/noxdb_sweep.py --mode manual
```

## What each mode checks

| Check | heartbeat | weekly | monthly | manual |
|---|:-:|:-:|:-:|:-:|
| Liveness (`SELECT 1`, response time) | ✓ | ✓ | ✓ | ✓ |
| Schema fingerprint diff | | ✓ | ✓ | ✓ |
| Population snapshot (projects/subjects/visits/samples/controls/inputs/files) | | ✓ | ✓ | ✓ |
| Integrity check (per project) | | ✓ | ✓ | ✓ |
| DB→disk drift (registered files missing on disk) | | ✓ | ✓ | ✓ |
| Disk→DB drift (full filesystem walk — slow) | | | ✓ | ✓ |
| Audit log summary (writes in last 7d) | | ✓ | ✓ | ✓ |
| 30-day uptime % | | | ✓ | |

If the liveness check itself fails, every other check for that run is
skipped (no point walking the filesystem when the DB is down) and the
report is just "the DB is down."

Exit code is non-zero if any check ended in `error` level — useful if you
ever want cron's own failure handling on top of the emails.
