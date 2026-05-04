# phiper-db

[![CI](https://github.com/Polymerase3/phiper-db/actions/workflows/ci.yml/badge.svg)](https://github.com/Polymerase3/phiper-db/actions/workflows/ci.yml)

Schema, migrations, and Python tooling for the lab's MariaDB metadata database.

## Structure

- `schema/`     — numbered SQL migrations (`001_initial.sql`, ...)
- `users.sql`   — role and privilege definitions (passwords NOT committed)
- `labdb/`      — Python wrapper package
- `scripts/`    — maintenance scripts (weekly sweep, backups)
- `seed/`       — fake/test data for development and CI
- `tests/`      — tests
- `docs/`       — extended documentation

## Quick start

```bash
pip install -e .
```

Credentials go in `~/.my.cnf` (see `docs/credentials.md` — TODO).

To run the test suite locally, see [`docs/testing.md`](docs/testing.md).

## Status

Work in progress. Contact: <your name / email>.samples).
