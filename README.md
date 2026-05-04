# phiper-db

Schema, migrations, and Python tooling for the lab's MariaDB metadata database.

## Structure

- `schema/`     — numbered SQL migrations (`001_initial.sql`, ...)
- `users.sql`   — role and privilege definitions (passwords NOT committed)
- `labdb/`      — Python wrapper package
- `scripts/`    — maintenance scripts (weekly sweep, backups)
- `tests/`      — tests
- `docs/`       — extended documentation

## Quick start

```bash
pip install -e .
```

Credentials go in `~/.my.cnf` (see `docs/credentials.md` — TODO).

## Status

Work in progress. Contact: <your name / email>.samples).
