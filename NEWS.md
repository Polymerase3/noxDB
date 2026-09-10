# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

Every pull request must bump the version in `pyproject.toml` and add a
matching entry below; this is enforced by `.github/workflows/pr-checks.yml`.

## [Unreleased]

## [0.7.5] - 2026-09-10

Monitoring sweep gains the checks it was missing. Closes #2.

### Added
- **Row counts** per table, compared with the previous run. A table that
  shrank at all warns; one that lost 10% or more errors. This runs in the
  nightly heartbeat as well as weekly, so data loss is caught within a day
  instead of a week. It is deliberately separate from the population
  snapshot, which sums per-project figures and therefore double-counts a
  shared sample and cannot see one linked to no project — the population
  figure read 11582 files where `sample_files` held 13345 rows.
- **Orphan check** for rows that hang off nothing: samples in no project,
  subjects with no visit, visits with no sample. Foreign-key orphans cannot
  happen, so these are the semantic kind that queries silently skip past.
  Controls awaiting a study sample on their plate are reported but not
  flagged, since that state is legitimate.
- **Duplicate detection**, in two passes: rows identical on every column but
  the surrogate key and `created_at`, and samples whose plate, well and
  identity match while their names are padded differently — the shape that
  had one specimen registered twice as `R05P01_01_..` and `R05P01_1_..`.
- **Activity summary** from `created_at`: new rows per table and new samples
  per project over the last 7 days. The existing audit-log check only sees
  writes made through noxdb on the sweep's own host, so it misses anything
  done from another machine.
- **Database size** on disk, per table, largest first.
- **Run metadata** in every report and log line: start time, duration, CPU
  seconds, peak memory, the commit the script ran from, and the Python and
  host it ran on.

## [0.7.4] - 2026-09-10

Closes the loop on the plate-coordinate work: 0.7.3 shipped the tooling
fixes, the production data was backfilled by hand, and this stops the
importer from reintroducing the drift on the next run.

### Added
- `samples.plate_coords_from_name`: reads the `(SQR, SQRP)` plate
  coordinates out of a sample name. `R42P02_09_..` is run 42 plate 02, and
  a run-only name such as `R31_input1_01_..` is run 31 with no plate.

### Changed
- `scripts/prepare_migration.py` and `scripts/add_controls.py` now derive
  the plate coordinates from the sample name instead of reading them from
  the manifest CSV. The filename is the authoritative plate identity: it
  matches the physical 96-file plates on disk, while manifest coordinates
  were found to collide, putting two different plates on one key. Both
  scripts report rows where the CSV disagrees, and fall back to the CSV
  only when a name carries no coordinates at all.
- `samples.canonical_plate_id` now zero-pads a purely numeric identifier to
  two characters. Padding used to be preserved verbatim, so `5` and `05`
  described one physical plate as two; because control linking matches by
  exact string, that split left PIC_MUW, SAR_MUW and SNMG with no controls
  and misattributed others. Non-numeric and wider values are unchanged.
- `scripts/add_controls.py`'s plate key runs through the same
  canonicalization as the study samples rather than its own verbatim
  strip, so the two sides cannot drift apart again.

## [0.7.3] - 2026-09-10

### Fixed
- `queries.find_disk_files_missing_in_db` now defaults to walking only the
  directories that already hold registered files, exposed as the new
  `queries.registered_file_dirs`. It previously walked the tier roots, and
  on this cluster `/lisc/data/work` is the entire institute's work
  filesystem: the sweep spent its whole 30-minute budget enumerating other
  groups' data and counted every readable file it found as unregistered.
  Passing `roots=` explicitly still walks whatever you name.
- The monitoring sweep's disk-to-DB check now reports the directories it
  scanned, so the emailed report states its own scope.

### Changed
- `docs/quickstart.md` regenerated again. Production data changed underneath
  it: `samples.SQR` / `samples.SQRP` were backfilled from the sample name, so
  the plate coordinates in the examples now match the `RxxPxx` in the
  filenames, and rebuilding the control links moved project 7 from 239
  samples to 175. The input series listing also changed, since the eight
  undeliverable `R01P02` rows were deleted and 96 `R31_input` samples added.

## [0.7.2] - 2026-09-10

Dependency and documentation fixes found while re-running the quickstart
probe against the migrated `ccr_metadata`.

### Fixed
- `paramiko>=3.0,<4.0` moved from the `analysis` extra into the core
  dependencies. `sshtunnel` 0.4.0 references `paramiko.DSSKey`, which
  paramiko 4.x removed, so a plain install of noxDB picked up paramiko 4
  and every tunneled `init_pool()` failed with
  `module 'paramiko' has no attribute 'DSSKey'`.

### Changed
- Repository links updated from the old `phiper-db` name to `noxDB`: the
  clone URL in `scripts/sweep/README.md` and two links in
  `docs/monitoring.md`.
- `docs/quickstart.md` regenerated from a fresh `scripts/probe_quickstart.py`
  run against `ccr_metadata`. Every figure moved: project 7 now has 239
  samples and 476 files, the project list is 20 entries, and the example
  file paths carry the `mariaDB/` segment added by the September path
  backfill. The claim that `init_pool()` errors out when no tunnel is
  listening was also wrong and is now corrected.
- `scripts/probe_quickstart.py` extended to cover `include_controls=False`,
  the column subset used in §14, and a metadata-only `fetch.export_project`,
  so the export byte sizes in §14 are now measured rather than approximate.
  Its tunnel hint no longer prints a hardcoded database IP.

## [0.7.1] - 2026-09-09

Follow-up to the 2026-09-06 weekly sweep, which reported 5792 integrity
issues and 13302 registered files missing on disk. Neither was data loss.

### Added
- `scripts/sweep/noxdb_sweep.py`: standalone monitoring script for the
  ccr-lab VM — liveness, schema fingerprint, population snapshot with
  week-over-week deltas, per-project integrity, DB↔disk drift, and audit
  log summary, emailed as an HTML report. Deployment instructions in
  `scripts/sweep/README.md`, check-by-check rationale in
  `docs/monitoring.md`.

### Changed
- `counts` is now a **work**-tier file type instead of archive.
  `docs/schema.md` already documented it that way and every counts row in
  production was already stored as work, so `files.py` was the outlier.
- `_resolve_tier()` now enforces the `file_type → tier` invariant it was
  documented to enforce: an `archive` ↔ `work` override that disagrees
  with the type-derived tier is rejected. `scratch` / `external`
  overrides are unaffected. This also makes `files.update()`'s existing
  guard real rather than a no-op.
- `DEFAULT_LISC_ROOT` in `scripts/prepare_migration.py` and
  `scripts/add_controls.py` is now
  `/lisc/data/work/ccr/mariaDB`; the `mariaDB` path segment was missing,
  so generated manifests pointed at paths that do not exist.

### Fixed
- `_setup_audit_logger()` skipped setup whenever the `noxdb.audit` logger
  already had **any** handler attached, so it never installed its own file
  handler if something else got there first — pytest's logging plugin, or
  any application with its own handler. The audit log was silently never
  written. Setup and teardown now act only on the handler the module owns,
  which also stops `close_pool()` from tearing down a caller's logging
  setup.
- `get_connection()` rolls back on `BaseException` rather than
  `Exception`, so a `KeyboardInterrupt` or `SystemExit` mid-transaction no
  longer returns a connection to the pool still holding its locks.
- The project importer's pre-flight validator enforced the pre-003 schema
  and rejected samples shared across projects. It now blocks only on a
  `file_path` bound to a different sample, the one global UNIQUE that
  re-use cannot resolve.

## [0.7.0] - 2026-05-16

Many-to-many project↔sample schema. Applied to production `ccr_metadata`
via `schema/003_cross_project_samples.sql`.

### Added
- `schema/003_cross_project_samples.sql`: new `project_samples`
  junction table — the sole source of truth for project↔sample
  membership (a sample can belong to several projects). Backfills
  study/input/control links and canonicalizes existing `SQR`/`SQRP`.
- `samples.link_to_project()`: idempotent project↔sample link helper.
- `samples.canonical_plate_id()`: single SQR/SQRP canonicalization
  chokepoint (strip whitespace, `NA`/`N/A`/empty → `""`, padding
  preserved); applied on every write and reused by the importer, which
  now also validates plate ids and reports normalizations.
- `subjects.get_or_create()` raises on a reused `subject_code` with a
  conflicting `sex`/`origin` (loud cross-study collision guard).

### Changed
- Project membership lives entirely in `project_samples`; `subjects`
  no longer carries `project_id` and `subject_code` is now globally
  unique. `subject → visit → sample` is pure lineage with no project
  affiliation.
- Controls (mockIP/anchor/NC) are linked to every study project that
  shares their plate (SQR+SQRP) at import/migration time instead of
  living in dedicated control projects.
- `queries.*` project-scoped helpers, `workflows.register_subject_with_visit`,
  and the project importer rewritten to the junction model;
  `project_summary.n_samples` now counts every linked sample (controls
  included).
- `scripts/add_controls.py` redesigned to emit controls into the
  plate-sharing study bundles rather than dedicated control projects.
- Docs (`schema.md`, `index.md`, `quickstart.md`,
  `reference/index.md`) updated to the junction model; `quickstart.md`
  regenerated from a live run against the migrated database.

### Removed
- Dedicated `mockIP` / `anchor` / `NC` projects (deleted by migration
  003; the `input` umbrella project is retained).
- `scripts/fix_controls_projects.py` (obsolete under the new model).

### Security
- `users/revoke_readwrite.sql`: revoked INSERT/UPDATE/DELETE from the
  two analyst accounts (SELECT-only like other non-admin users) —
  ad-hoc writes would bypass the `project_samples` invariant.

## [0.5.2] - 2026-05-15

### Fixed
- `README.md`: corrected all documentation site links from `noxdb` to `noxDB`
  to match the renamed GitHub Pages URL.
- `README.md`: corrected codecov badge and link to point to the renamed
  repository (`noxDB`).

## [0.5.1] - 2026-05-15

### Changed
- `docs/index.md`: replaced CLI card with cards for Preparing data, Testing,
  and Contributing; added Gabriel Innocenti's email to the contact section.
- `README.md`: restored WIP status, codecov, and version badges that were
  removed in a previous documentation pass.

## [0.5.0] - 2026-05-15

### Added
- `docs/quickstart.md` section 14: full `fetch` module example with live
  output — project structure (`projects.get`, `project_summary`), file
  manifest (`files_for_project`), and `fetch.export_project` producing a
  local folder with `metadata.csv` and `README.txt`. Also shows the
  `include_files=True` / `layout` / `file_types` path for on-LiSC use.
- `docs/data-preparation.md`: new data-preparation section covering how
  raw exports are transformed before import.

### Changed
- `docs/schema.md` fully rewritten: every table now has a complete
  column-by-column reference (type, nullability, constraints). Controls
  design documented in a dedicated section — mockIP, anchor, NC, and input
  each live in their own project (ids 61, 64, 67, 58 respectively) and are
  linked back to study projects via the `SQR`/`SQRP` plate coordinates.
  Nullable `sex`/`age` and the `NC` ENUM addition from migration `002` are
  called out inline. Previously wrong column names (`meta_key` → `key_name`,
  `value_float` → `value_numeric`) corrected.
- `docs/cli.md` removed; CLI is no longer part of the package.
- `mkdocs.yml` updated to reflect the new documentation structure.
- DB renamed to `noxDB`.

## [0.4.5] - 2026-05-15

### Added
- `scripts/add_controls.py`: recovers anchor, mock, NC, and input control
  samples from `Overview_SQRs.csv` (absent from per-project metadata CSVs)
  and appends them to the four master CSVs in the import directory.
  Input samples are placed in a standalone `"input"` project. Safe to
  re-run — already-present `sample_name` entries are silently skipped.

### Fixed
- `subjects.create` / `subjects.get_or_create`: the `sex or None`
  normalisation now also converts the literal strings `'NA'` / `'N/A'`
  to `NULL`, preventing a DB-side `CHECK` constraint violation when
  `prepare_migration.py` writes those placeholders.
- Import validation (`runner._validate_schema`, `runner._commit`): sex
  and age values of `'NA'` / `'N/A'` (written by `prepare_migration.py`
  for real samples with missing demographics) are now treated as nullish,
  consistent with the DB schema allowing `NULL` for both columns.

## [0.4.4] - 2026-05-15

### Added
- `schema/002_controls_support.sql`: migration that makes `subjects.sex`
  and `visits.age` nullable (non-null values still constrained to
  `'M'/'F'` and `>= 0` respectively) and adds `'NC'` to the
  `samples.sample_type` ENUM.
- `scripts/prepare_migration.py`: `_detect_sample_type()` infers
  `sample_type` from substrings in `SampleName` (`Anchor` → `anchor`,
  `Mock` → `mockIP`, `NC` → `NC`, `input` → `input`). Control samples
  no longer generate spurious sex/age warnings and are written with
  blank sex/age (stored as NULL) rather than a placeholder `0`.
- `users/revoke_readwrite.sql`: one-time script to downgrade
  `lovro.trgovec-greif` and `melanie.prinzensteiner` to `SELECT`-only.

### Changed
- `subjects.create` / `subjects.get_or_create`: `sex` parameter is now
  `str | None`; empty string is normalised to `NULL` on insert.
- `visits.create` / `visits.get_or_create`: `age` parameter is now
  `int | None`.
- Import validation (`runner._validate_schema`): sex and age checks are
  skipped when the value is empty/null rather than raising an error.
- `users/users.sql`: `lovro.trgovec-greif` and `melanie.prinzensteiner`
  reduced to `SELECT`-only, consistent with all other non-admin users.

## [0.4.3] - 2026-05-14

### Added
- `scripts/prepare_migration.py` and `scripts/bulk_import.py`: two CLI
  scripts for bulk-loading legacy data. `prepare_migration.py` transforms
  a raw export directory into the canonical import folder layout expected
  by `import_project_from_dir`; `bulk_import.py` wraps it to drive
  multiple projects in one run.

### Changed
- `counts` files may now be stored in the `work` tier as well as
  `archive`. The hard `archive ↔ work` flip rejection in
  `files._resolve_tier` is removed; the `scratch`/`external` escape
  hatches are unchanged.
- `files.register` / `files.get_or_register` gain a `skip_disk_check`
  parameter (forwarded from `_inspect_file`): when `True`, path-prefix
  and on-disk stat/MD5 checks are skipped and `file_size_bytes` is stored
  as `NULL`. Useful when registering files that are not yet mounted on
  the current host.

### Fixed
- `paramiko` pinned to `<4.0` in the `analysis` optional-dependency
  group. `paramiko` 4+ removed `DSSKey`; `sshtunnel` 0.4.0 still
  references it, causing an `AttributeError` on `init_pool()` when the
  SSH tunnel path is taken.

### Maintenance
- `migration_import/`, `migrations/`, and `notebooks/` added to
  `.gitignore`.

## [0.4.2] - 2026-05-11

### Changed
- `README.md` rewritten as a plain, copy-pasteable onboarding page:
  short summary of the repo's purpose, step-by-step install (system
  deps → `git clone` → venv → `pip install -e ".[analysis]"` → smoke
  test), minimal `~/.my.cnf` example, links to the docs site for
  everything else. The access-tier section is dropped (lives on the
  docs site / is managed off-repo); the Contact section now lists
  Gabriel Innocenti alongside Mateusz Kołek.

## [0.4.1] - 2026-05-11

### Changed
- Converted all public docstrings in `noxdb` (`connection`,
  `projects`, `subjects`, `visits`, `samples`, `metadata`, `files`,
  `queries`, `workflows`, `fetch`) and the `_import` subpackage to
  Google style with explicit `Args:` / `Returns:` / `Raises:` sections.
  No code behavior changes. The API-reference pages on the docs site
  now render structured parameter tables.
- `docs.yml` runs `mkdocs build` without `--strict`. Griffe warns on
  the deliberately-untyped `cur` parameter (cursor can be either a
  `mariadb` cursor or a `_LoggingCursor` wrapper); adding `cur: Any`
  everywhere would be churn without semantic gain.

## [0.4.0] - 2026-05-11

### Added
- Documentation site scaffold built with MkDocs + Material theme +
  mkdocstrings (`mkdocs.yml`, `docs/{index,install,quickstart,schema,
  cli,contributing,changelog}.md`, and per-module pages under
  `docs/reference/`). Auto-generates API reference from package
  docstrings; `NEWS.md` and `CONTRIBUTING.md` are surfaced via
  `mkdocs-include-markdown-plugin`.
- `CONTRIBUTING.md` documenting dev setup, branching, the version +
  `NEWS.md` requirement enforced by `pr-checks.yml`, docstring style,
  and how to preview the docs locally.
- `.github/workflows/docs.yml` — builds the site with `mkdocs build
  --strict` on every PR and deploys to GitHub Pages on every push to
  `main`. Concurrency group keeps deploys serialized.
- `docs` optional-dependency group in `pyproject.toml`
  (`mkdocs-material`, `mkdocstrings[python]`,
  `mkdocs-include-markdown-plugin`). `pip install -e ".[docs]"` then
  `mkdocs serve` is the local preview path.

## [0.3.0] - 2026-05-11

### Added
- Master import: load a whole project folder (`project.yaml`,
  `subjects.csv`, `visits.csv`, `samples.csv`, `files/manifest.csv`)
  into the database in one atomic transaction.
  - `noxdb._import.import_project_from_dir(root, *, dry_run,
    force, compute_md5, skip_disk_check, log_dir)` is the library entry
    point; it raises `ProjectImportError` with an exhaustive list of
    collected errors when validation fails.
  - `scripts/import_project.py` is the matching CLI, exits 0 / 2 / 3
    for success / validation failure / unexpected error and writes a
    JSON report to stdout plus `<log_dir>/<ts>_<project>.log`.
  - CSV columns prefixed with `meta_` are treated as typed metadata
    keys; values are coerced int → float → bool → str.
  - Re-running on the same folder requires `--force` (or `force=True`).
    With `--force` the importer is idempotent: re-uses every existing
    row via `get_or_create`/`set_*` and reports `inserted` vs
    `existing`/`unchanged` per table.
  - Cross-project collisions on `sample_name` / `file_path` are blocked
    even with `--force` (those UNIQUEs are global by design).
- `pyyaml>=6.0` added as a hard dependency (required to read
  `project.yaml`).

## [0.2.3] - 2026-05-11

### Added
- `fetch` module that materializes a project from the database to a
  local folder:
  - `export_metadata_table` — write the project tidy table to CSV and/or
    XLSX (requires `pandas`, plus `openpyxl` for `xlsx`).
  - `download_files_for_project` — copy every registered file for a
    project into a target directory. Uses paramiko SFTP when an
    `ssh_host` is configured (via `~/.my.cnf [noxdb-ssh]`, `NOXDB_SSH_*`
    env vars, or kwargs), and falls back to `shutil.copyfile` when
    running on LiSC with the storage mounted locally. Supports
    `by_sample`, `by_type`, and `flat` output layouts; resumes by
    skipping destinations that already exist.
  - `export_project` — one-shot snapshot: `metadata.csv` (and optionally
    `metadata.xlsx`), `files/<layout>/...`, and a `README.txt` summary.
- `paramiko>=3.0` added to the `analysis` optional-dependency group.

## [0.2.2] - 2026-05-11

### Fixed
- `get_connection()` now forces server-side `SET autocommit=0` on every
  pool checkout instead of relying on the Python-side `conn.autocommit`
  setter. The setter elides the `SET` command when its cached flag
  already matches, so after a pool reset the server could be left in
  autocommit=1 while Python thought it was 0. Inserts then auto-committed
  and `rollback()` was a silent no-op. Triggered intermittently for
  later-session tests; surfaced by a regression test against the
  `subjects` table.

### Added
- `workflows` module bundling atomic, idempotent high-level operations
  on top of the per-table CRUD helpers:
  - `register_subject_with_visit` — `subjects.get_or_create` +
    `visits.get_or_create` + optional `visit_metadata` upsert, all in
    one transaction.
  - `register_sample_with_files` — `samples.get_or_create` + optional
    `sample_metadata` upsert + per-file `files.get_or_register`, all in
    one transaction. A disk-validation error on any file rolls back the
    sample and its metadata too.
- Both workflows take an optional `cur=None`; when None they open their
  own `transaction()`, otherwise they piggyback on the caller's cursor
  via `contextlib.nullcontext`, so they compose cleanly inside a larger
  atomic block.

## [0.2.1] - 2026-05-11

### Added
- Composite read-only `queries` module in `noxdb`:
  - `samples_for_project` — join `projects → subjects → visits → samples`
    with optional filters on `file_type`, `sample_type`, and `has_files`.
  - `samples_with_metadata` / `project_tidy_table` — EAV pivot from
    `sample_metadata` (and optionally `visit_metadata`) into a wide-form
    `pandas.DataFrame`. Visit-level keys colliding with sample-level keys
    are renamed with a `visit_` prefix. Metadata key names are filtered
    against `^[A-Za-z_][A-Za-z0-9_]*$` for safe DataFrame columns.
  - `files_for_project` — `sample_files` joined with parent identifiers,
    filtered by `file_type` and/or `storage_tier`.
  - `project_summary` — counts of subjects/visits/samples/files plus
    per-`file_type` breakdown (returns a `dict`, no pandas dependency).
  - `find_db_files_missing_on_disk` — DB rows whose `file_path` is gone
    from disk; suitable for a cron sweep.
  - `find_disk_files_missing_in_db` — regular files under
    `NOXDB_ARCHIVE_ROOT` / `NOXDB_WORK_ROOT` (or caller-provided roots)
    that are not registered in `sample_files`.
  - `integrity_check` — per-project report covering samples without files,
    archive files without MD5, and files outside their tier root.
- `analysis` optional-dependency group in `pyproject.toml` for
  `pandas` + `openpyxl`. Tests depend on `pandas` and skip DataFrame
  assertions when it is missing.

## [0.2.0] - 2026-05-09

### Added
- Per-table CRUD modules in `noxdb`:
  - `projects` — `create`, `get`, `get_by_name`, `get_or_create`, `list_all`,
    `update`, `delete`, `exists` (id/name XOR), `count`.
  - `subjects` — same shape keyed on the composite `(project_id,
    subject_code)`, plus `list_for_project` / `count_for_project`.
  - `visits` — keyed on `(subject_id, timepoint)`; `get_or_create` rejects
    NULL timepoints because the UNIQUE doesn't deduplicate them.
  - `samples` — `sample_name` is globally UNIQUE; module mirrors the
    `projects` shape with `list_for_visit` / `count_for_visit`.
  - `metadata` — shared EAV wrapper for `visit_metadata` and
    `sample_metadata` with explicit `set_visit / get_visit /
    list_for_visit / delete_visit` (and `_sample` mirrors). Idempotent
    via `INSERT … ON DUPLICATE KEY UPDATE`; `set_*` returns
    `"inserted" | "updated" | "unchanged"`. Bool stored in BOOLEAN is
    coerced back to Python `bool` on read.
  - `files` — filesystem-validating registration for `sample_files`:
    absolute path + regular-file check, fastq/bam extension validation,
    file-type → tier derivation (archive vs work), `os.path.realpath`
    prefix check that blocks symlink escapes and sibling-prefix paths,
    optional chunked MD5 (`compute_md5`) or caller-supplied checksum,
    `register`, `get_or_register` (idempotent on `file_path` and
    UNIQUE-violation race-safe), `restat`, plus the standard CRUD
    helpers. Roots configurable via `NOXDB_ARCHIVE_ROOT` /
    `NOXDB_WORK_ROOT` (defaults `/lisc/archive`, `/lisc/work`).
  - `update(storage_tier=…)` enforces the same `file_type → tier`
    invariant as `register()` — flipping `archive` ↔ `work` is rejected;
    `scratch` / `external` overrides are still allowed.

### Changed
- CI now runs `pytest --cov` against `src/noxdb` (branch
  coverage), prints a missing-lines report in the workflow log, and
  uploads `coverage.xml` as a build artifact. `pytest-cov` is a new
  optional `test` dependency; coverage settings live under
  `[tool.coverage.*]` in `pyproject.toml`.
- `_LoggingCursor` and `execute()` pass `params` through unchanged when
  the caller provides an empty container; only `params is None` is
  substituted with `()`.
- `_log_if_write` now strips leading SQL comments and an optional
  `WITH …` CTE before classifying the statement, so audited writes are
  no longer missed when the query starts with comments or a CTE.
- `get_connection()` cleanup wraps `rollback()` and `close()` in their
  own try/except so the original DB exception is preserved; rollback /
  close failures are logged instead of masking it.

### Fixed
- `seed/load_fake_data.load()` now closes its cursor in a `try/finally`,
  preventing cursor leaks back into the pool.
- `.github/workflows/pr-checks.yml` version-extraction commands no
  longer abort the step on `grep` misses; the explicit `-z` guard now
  reports the error as intended.

## [0.1.1] - 2026-05-08

### Added
- Initial schema in `schema/001_initial.sql`: project → subject → visit →
  sample hierarchy with EAV metadata tables (`visit_metadata`,
  `sample_metadata`) and `sample_files` for tracking file pointers.
- User and role definitions in `users/users.sql` (admin / read-write /
  read-only tiers, restricted to `lisc.%` hosts).
- `noxdb` Python package with:
  - Connection pool (`init_pool`, `close_pool`, `get_connection`).
  - `transaction()` context manager with audit-logging cursor wrapper.
  - `execute()` helper returning rows as `list[dict]`.
  - Audit log of write statements at `~/.noxdb/audit.log` (override via
    `NOXDB_AUDIT_LOG`).
  - Credentials read from `~/.my.cnf` `[noxdb]` section by default;
    overridable per-call via `init_pool(...)` keyword arguments and via the
    `NOXDB_DATABASE` env var.
- Fake-data seed script (`seed/load_fake_data.py`) covering all four EAV
  value types and a longitudinal subject example.
- CI workflow running pytest against MariaDB 10.11.
