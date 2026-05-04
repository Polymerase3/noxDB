# seed/

Fake-but-realistic data for development and CI. Not for production.

## Contents

- `load_fake_data.py` — inserts 1 project, 2 subjects (cross-sectional +
  longitudinal), several samples per visit, a few `sample_files` rows, and
  `visit_metadata` / `sample_metadata` rows covering all four EAV value
  types (`int`, `numeric`, `bool`, `text`).

## Usage

The schema (`schema/001_initial.sql`) must already be loaded.

```bash
# Linux: install the MariaDB Connector/C system library first
sudo apt-get install libmariadb-dev

pip install -e .

DB_HOST=127.0.0.1 DB_USER=root DB_PASSWORD=... \
    python seed/load_fake_data.py
```

Recognised env vars: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`,
`DB_NAME` (default `dbmaria_project`).

The script runs in a single transaction. Re-running against an
already-seeded database will fail on unique constraints — drop and
recreate the database first.
