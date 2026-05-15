# Running tests locally

The test suite loads `schema/001_initial.sql` and the seed script against a
real MariaDB instance, then asserts row counts and EAV-type coverage.

## Prerequisites

- A running MariaDB the test can connect to as a privileged user
- `libmariadb-dev` system package (required by the `mariadb` Python driver)
- Python 3.10+ in a virtualenv

```bash
sudo apt-get install -y libmariadb-dev python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

## Start MariaDB (Docker)

Matches the CI setup:

```bash
docker run -d --name phiper-mariadb \
    -e MARIADB_ROOT_PASSWORD=rootpw \
    -p 3306:3306 \
    mariadb:10.11

# Wait a few seconds, then sanity-check:
docker exec phiper-mariadb mariadb -uroot -prootpw -e "SELECT 1"
```

Stop and remove when done: `docker rm -f phiper-mariadb`.

## Run the tests

The fixture reads connection settings from environment variables. Put them
on a single line — multi-line `\`-continuations are easy to break in zsh:

```bash
DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=root DB_PASSWORD=rootpw DB_NAME=ccr_metadata pytest -v
```

Or export once for the shell session:

```bash
export DB_HOST=127.0.0.1 DB_PORT=3306 DB_USER=root DB_PASSWORD=rootpw DB_NAME=ccr_metadata
pytest -v
```

## Notes and warnings

- The session fixture **drops and recreates** `ccr_metadata` on every
  run. Do not point `DB_NAME` at a database you care about. To be safe
  against an existing local DB, use a separate name:

  ```bash
  DB_NAME=ccr_metadata_test pytest -v
  ```

- If you see `Access denied ... (using password: NO)`, the env vars did not
  reach pytest. Check for stray characters after `\` continuations or use
  the single-line form above.

- `libmariadb-dev` must be installed *before* `pip install` — the Python
  `mariadb` package builds against it.

## Running the seed without the test harness

The schema must already be loaded:

```bash
mariadb -h127.0.0.1 -uroot -prootpw < schema/001_initial.sql
DB_HOST=127.0.0.1 DB_USER=root DB_PASSWORD=rootpw python seed/load_fake_data.py
```
