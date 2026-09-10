# Schema

The database stores **metadata and file pointers** for biological
samples. Bulk data (fastq, BAM, counts, etc.) lives on disk under
`/lisc/archive` and `/lisc/work`; the database only knows where to
find it.

---

## Model: lineage + project membership

Since migration `003_cross_project_samples` the data model has **two
independent axes**:

- **Lineage** (`subject → visit → sample`) — pure provenance, carries
  **no** project affiliation:
    - **subject** — one person / donor. Stable attributes only (`sex`,
      `origin`). `subject_code` is **globally unique** (no longer scoped
      to a project). `sex` is nullable for control subjects.
    - **visit** — one timepoint / collection event for a subject.
      Time-varying clinical metadata (`age`, `group_test`, `timepoint`)
      goes here. `age` is nullable for control visits.
    - **sample** — one physical sample / library / Ig-class measurement
      attached to a visit. `sample_name` is globally unique.
- **Project membership** (`project_samples`) — a many-to-many junction
  that is the **sole source of truth** for which samples belong to
  which project. One sample can belong to several projects (shared
  plate controls, shared HC cohorts); a project is just a named set of
  samples.

Flexible typed key/value metadata can be attached to visits
(`visit_metadata`) and samples (`sample_metadata`). File paths are
tracked in `sample_files`.

---

## Tables

### `projects`

Top-level study or dataset. A project owns no rows directly — its
samples are attached through the [`project_samples`](#project_samples)
junction. The `input` project is kept as an umbrella for input-DNA
controls; the old `mockIP` / `anchor` / `NC` projects were removed in
migration `003` — see [Controls](#controls) below.

| Column         | Type                      | Nullable | Notes                          |
|----------------|---------------------------|----------|--------------------------------|
| `project_id`   | `BIGINT UNSIGNED` PK AI   | NO       |                                |
| `project_name` | `VARCHAR(100)`            | NO       | UNIQUE                         |
| `description`  | `TEXT`                    | YES      |                                |
| `pi_name`      | `VARCHAR(100)`            | YES      |                                |
| `created_at`   | `TIMESTAMP`               | NO       | DEFAULT `CURRENT_TIMESTAMP`    |

---

### `subjects`

One row per person / donor. Stable subject-level attributes only —
**no project affiliation** (membership lives in `project_samples`,
reached via the sample → visit → subject lineage).

| Column         | Type                      | Nullable | Notes                                                  |
|----------------|---------------------------|----------|--------------------------------------------------------|
| `subject_id`   | `BIGINT UNSIGNED` PK AI   | NO       |                                                        |
| `subject_code` | `VARCHAR(100)`            | NO       | **Globally UNIQUE** (`uq_subjects_subject_code`)       |
| `sex`          | `CHAR(1)`                 | YES      | `'M'` or `'F'` when set; NULL for control subjects     |
| `origin`       | `VARCHAR(100)`            | YES      |                                                        |
| `created_at`   | `TIMESTAMP`               | NO       | DEFAULT `CURRENT_TIMESTAMP`                            |

`sex` was made nullable in migration `002_controls_support` to
accommodate control rows that have no biological donor. Migration
`003_cross_project_samples` dropped `subjects.project_id` and replaced
the old per-project `(project_id, subject_code)` key with a global
UNIQUE on `subject_code` — a subject is now the same subject regardless
of which study references it. `subjects.get_or_create` raises if a
reused `subject_code` arrives with a conflicting `sex`/`origin`, so an
accidental cross-study collision fails loudly instead of silently
merging two donors.

---

### `visits`

One row per subject × timepoint / collection event. Time-varying clinical data belongs here.

| Column       | Type                      | Nullable | Notes                                                       |
|--------------|---------------------------|----------|-------------------------------------------------------------|
| `visit_id`   | `BIGINT UNSIGNED` PK AI   | NO       |                                                             |
| `subject_id` | `BIGINT UNSIGNED` FK      | NO       | → `subjects.subject_id` CASCADE                             |
| `timepoint`  | `VARCHAR(50)`             | YES      | UNIQUE within subject: `(subject_id, timepoint)`; NULL rows not deduplicated |
| `group_test` | `VARCHAR(100)`            | NO       | Clinical group / arm (e.g. `Controls`, `Cases`)             |
| `age`        | `INT`                     | YES      | Age at collection; ≥ 0 when set; NULL for control visits    |
| `created_at` | `TIMESTAMP`               | NO       | DEFAULT `CURRENT_TIMESTAMP`                                 |

`age` was made nullable in migration `002_controls_support`.

---

### `samples`

One row per physical sample / library / Ig-class measurement. `sample_name` is globally unique across all projects.

| Column           | Type                                                         | Nullable | Notes                                          |
|------------------|--------------------------------------------------------------|----------|------------------------------------------------|
| `sample_id`      | `BIGINT UNSIGNED` PK AI                                      | NO       |                                                |
| `visit_id`       | `BIGINT UNSIGNED` FK                                         | NO       | → `visits.visit_id` CASCADE                    |
| `sample_name`    | `VARCHAR(100)`                                               | NO       | UNIQUE globally                                |
| `sample_type`    | `ENUM('sample','mockIP','input','anchor','NC')`              | NO       | See [Controls](#controls)                      |
| `IPR`            | `VARCHAR(10)`                                                | NO       | IP run — derived from `sample_name`, zero-padded |
| `IPRP`           | `VARCHAR(10)`                                                | NO       | IP plate within that run — derived from `sample_name`, zero-padded |
| `SQR`            | `VARCHAR(10)`                                                | NO       | Sequencing run — from the run sheet, zero-padded |
| `SQRP`           | `VARCHAR(10)`                                                | NO       | Sequencing plate within that run — from the run sheet, zero-padded |
| `library`        | `VARCHAR(50)`                                                | NO       |                                                |
| `antibody_class` | `VARCHAR(50)`                                                | YES      |                                                |
| `created_at`     | `TIMESTAMP`                                                  | NO       | DEFAULT `CURRENT_TIMESTAMP`                    |

`NC` was added to the `sample_type` ENUM in migration `002_controls_support`.

#### Two coordinate systems

A sample carries **two unrelated pairs of coordinates**, and confusing
them is the bug that migration `004_ip_and_sequencing_coords` exists to
fix. Both are matched by exact string equality, so both are zero-padded
to a canonical form on every write
([`samples.canonical_plate_id`][noxdb.samples.canonical_plate_id]).

`IPR` / `IPRP` — **immunoprecipitation** run and plate. This is the
`RxxPxx` in `sample_name`, and the grain of the files on disk: a plate
is 96 wells, wells 81-96 being its controls. Derived from the name by
[`samples.ip_coords_from_name`][noxdb.samples.ip_coords_from_name] on
insert and never supplied by a caller, so a stored pair cannot
contradict the name it came from. `R42P02_09_..` is IP run `42`, plate
`02`; a run-only name such as `R31_input1_01_..` is run `31` with an
empty plate.

`SQR` / `SQRP` — **sequencing** run and the plate within it. These come
from the run sheet and from nowhere else; nothing in the sample name
encodes them. `R14P02_77_..` sits on IP plate `R14P02` but was
sequenced as run `07`, plate `02`.

The two pair up almost one-to-one at the plate level — each of the 150
sequencing plates in production carries exactly one IP plate — but the
*numbering* is unrelated, so neither can be computed from the other.
The runs do not pair up at all: 40 IP runs map onto 23 sequencing runs,
one sequencing run pooling up to five IP runs.

Three IP plates span two sequencing plates. On `R08P01` and `R08P02`
the study wells 1-80 and the control wells 81-96 were sequenced
separately, so a control can carry a different `SQR` from the samples
it controls for. This is exactly why control linking keys on `IPR` /
`IPRP` and not on the sequencing pair.

!!! warning "Releases 0.7.3 and 0.7.4"

    Those releases read the sample name for `SQR`/`SQRP`, on the belief
    that the name encoded the sequencing coordinates. It does not, and
    the backfill put IP values in every sequencing column in production.
    `004` moves them to `IPR`/`IPRP`, and
    `scripts/backfill_sequencing_coords.py` rewrites `SQR`/`SQRP` from
    the run sheet. Any analysis that grouped by `SQR` between those
    releases and 0.8.0 was grouping by IP run.

Every write also goes through one canonicalization chokepoint
([`samples.canonical_plate_id`][noxdb.samples.canonical_plate_id]):
whitespace is stripped, the "absent" sentinels (`NA`, `N/A`, empty)
collapse to a single canonical empty string, and a purely numeric value
is **zero-padded to two characters**. That padding rule exists because
exact-string matching once read `5` and `05` as two different plates,
which left whole projects with no controls at all. Values wider than two
characters and non-numeric values pass through unchanged. The importer
validates and reports any value it normalizes.

---

### `project_samples`

Many-to-many junction between `projects` and `samples` — the **sole
source of truth** for project membership (added in migration
`003_cross_project_samples`). A sample with no row here belongs to no
project and is invisible to every project-scoped query.

| Column       | Type                    | Nullable | Notes                                            |
|--------------|-------------------------|----------|--------------------------------------------------|
| `project_id` | `BIGINT UNSIGNED` FK    | NO       | → `projects.project_id` CASCADE                  |
| `sample_id`  | `BIGINT UNSIGNED` FK    | NO       | → `samples.sample_id` CASCADE                    |

Primary key is the composite `(project_id, sample_id)`, so a link is
idempotent; an extra index on `sample_id` serves the reverse lookup.
Both foreign keys are `ON DELETE CASCADE`: deleting a project drops its
membership rows (the samples and their lineage survive), and deleting a
sample drops all of its links.

Write through [`samples.link_to_project`][noxdb.samples.link_to_project]
(`INSERT IGNORE`). The importer links every imported sample to its
project and additionally auto-links plate controls (see
[Controls](#controls)).

---

### `visit_metadata`

EAV (Entity-Attribute-Value) typed key/value pairs attached to a visit. Examples: BMI, smoker status, disease activity, treatment status.

| Column        | Type                                   | Nullable | Notes                                      |
|---------------|----------------------------------------|----------|--------------------------------------------|
| `id`          | `BIGINT UNSIGNED` PK AI                | NO       |                                            |
| `visit_id`    | `BIGINT UNSIGNED` FK                   | NO       | → `visits.visit_id` CASCADE                |
| `key_name`    | `VARCHAR(100)`                         | NO       | UNIQUE within visit: `(visit_id, key_name)` |
| `value_int`   | `INTEGER`                              | YES      | Set when `value_type = 'int'`              |
| `value_numeric` | `DECIMAL(20,6)`                      | YES      | Set when `value_type = 'numeric'`          |
| `value_bool`  | `BOOLEAN`                              | YES      | Set when `value_type = 'bool'`             |
| `value_text`  | `TEXT`                                 | YES      | Set when `value_type = 'text'`             |
| `value_type`  | `ENUM('int','numeric','bool','text')`  | NO       | Discriminator; exactly one value column is non-NULL (enforced by CHECK) |
| `created_at`  | `TIMESTAMP`                            | NO       | DEFAULT `CURRENT_TIMESTAMP`                |

---

### `sample_metadata`

EAV typed key/value pairs attached to a sample. Examples: well position, plate barcode, dilution factor, `passed_qc`.

| Column        | Type                                   | Nullable | Notes                                        |
|---------------|----------------------------------------|----------|----------------------------------------------|
| `id`          | `BIGINT UNSIGNED` PK AI                | NO       |                                              |
| `sample_id`   | `BIGINT UNSIGNED` FK                   | NO       | → `samples.sample_id` CASCADE                |
| `key_name`    | `VARCHAR(100)`                         | NO       | UNIQUE within sample: `(sample_id, key_name)` |
| `value_int`   | `INTEGER`                              | YES      | Set when `value_type = 'int'`                |
| `value_numeric` | `DECIMAL(20,6)`                      | YES      | Set when `value_type = 'numeric'`            |
| `value_bool`  | `BOOLEAN`                              | YES      | Set when `value_type = 'bool'`               |
| `value_text`  | `TEXT`                                 | YES      | Set when `value_type = 'text'`               |
| `value_type`  | `ENUM('int','numeric','bool','text')`  | NO       | Discriminator; exactly one value column is non-NULL (enforced by CHECK) |
| `created_at`  | `TIMESTAMP`                            | NO       | DEFAULT `CURRENT_TIMESTAMP`                  |

---

### `sample_files`

File pointers registered for a sample. The database never stores file content — only the absolute path and optional metadata.

| Column            | Type                                                                                    | Nullable | Notes                                                      |
|-------------------|-----------------------------------------------------------------------------------------|----------|------------------------------------------------------------|
| `file_id`         | `BIGINT UNSIGNED` PK AI                                                                 | NO       |                                                            |
| `sample_id`       | `BIGINT UNSIGNED` FK                                                                    | NO       | → `samples.sample_id` RESTRICT on delete                   |
| `file_type`       | `ENUM('fastq_r1','fastq_r2','fastq_single','bam','counts','beer_norm','zigp_norm','edger_norm')` | NO |                                                     |
| `file_path`       | `VARCHAR(1024)`                                                                         | NO       | UNIQUE globally; must be absolute (enforced by CHECK `LIKE '/%'`) |
| `file_size_bytes` | `BIGINT UNSIGNED`                                                                       | YES      |                                                            |
| `checksum_md5`    | `CHAR(32)`                                                                              | YES      | Must match `^[a-f0-9]{32}$` when set                       |
| `storage_tier`    | `ENUM('work','archive','scratch','external')`                                           | NO       | DEFAULT `'work'`; see [Storage tiers](#storage-tiers)      |
| `created_at`      | `TIMESTAMP`                                                                             | NO       | DEFAULT `CURRENT_TIMESTAMP`                                |

Deleting a sample that still has files is rejected (`ON DELETE RESTRICT`). Files must be deregistered first.

---

## Controls

Control samples (mockIP, anchor, NC, input) have **no project of their
own**. Migration `003_cross_project_samples` deleted the dedicated
`mockIP` / `anchor` / `NC` projects; only `input` survives as an
umbrella for input-DNA controls. A control is a normal `samples` row
(with its own subject/visit lineage) linked into the relevant projects
through [`project_samples`](#project_samples) like any other sample.

### How controls are linked to projects

A control is linked to **every study project whose real samples share
its IP plate**, identified by the canonical `IPR` + `IPRP` coordinates
that every sample row carries. This is an IP relationship: a control
occupies a well of the physical plate it controls for, and says nothing
about which sequencing run happened to read it. This link is materialized into
`project_samples`, not computed at query time:

- **At import** — the importer links each imported sample to its
  project, then auto-links any existing control (mockIP/anchor/NC)
  whose `IPR`+`IPRP` matches a real (`sample`-type) row in the bundle.
- **For historical data** — migration `003`'s Backfill 3 inserts the
  same control→study-project links for every plate-sharing project.

Because one plate can span several study projects, the same physical
control is linked to several projects — that is the many-to-many
relationship working as intended, with exactly one underlying sample
row. `queries.controls_for_project` is then a plain `project_samples`
scan filtered to the control `sample_type`s, and
`queries.samples_for_project` returns controls because they are simply
project members.

### Nulls in control rows

Control subjects have no biological donor, so `subjects.sex` and `visits.age` are NULL for all control rows. The non-null CHECK constraints on both columns were loosened in migration `002_controls_support` to permit this:

- `subjects.sex`: NULL or `'M'`/`'F'`
- `visits.age`: NULL or `>= 0`

---

## EAV metadata

`visit_metadata` and `sample_metadata` follow the
[Entity-Attribute-Value](https://en.wikipedia.org/wiki/Entity%E2%80%93attribute%E2%80%93value_model)
pattern: each row stores one `(key_name, value)` pair for one entity.
Exactly one of `value_int`, `value_numeric`, `value_bool`, `value_text` is non-NULL per row — `value_type` says which. A CHECK constraint enforces this.

Use [`metadata.set_visit`][noxdb.metadata.set_visit] /
[`metadata.set_sample`][noxdb.metadata.set_sample] for writes; they are
idempotent (`INSERT … ON DUPLICATE KEY UPDATE`) and return
`"inserted" | "updated" | "unchanged"`.

To get metadata back as wide-form columns, use
[`queries.samples_with_metadata`][noxdb.queries.samples_with_metadata]
or [`queries.project_tidy_table`][noxdb.queries.project_tidy_table].

---

## Storage tiers

`sample_files.storage_tier` enforces a `file_type → tier` invariant:

| `file_type`                          | Required tier         | Root env var          | Default         |
|--------------------------------------|-----------------------|-----------------------|-----------------|
| `fastq_r1`, `fastq_r2`, `fastq_single`, `bam` | `archive` | `NOXDB_ARCHIVE_ROOT`  | `/lisc/archive` |
| `counts`, `beer_norm`, `zigp_norm`, `edger_norm` | `work`  | `NOXDB_WORK_ROOT`     | `/lisc/work`    |
| anything                             | `scratch`, `external` | —                     | —               |

Flipping `archive` ↔ `work` on an existing row is rejected by
[`files.update`][noxdb.files.update]. `scratch` / `external` overrides
are still allowed for one-off cases.

---

## Naming conventions

- **Tables**: plural `snake_case` (`projects`, `samples`, `sample_files`).
- **Primary keys**: `<table_singular>_id` (e.g. `subject_id`); EAV tables use plain `id`.
- **Foreign keys**: reuse the parent PK name (e.g. `visits.subject_id`, `project_samples.project_id`).
- **Migrations**: `schema/NNN_description.sql`, numbered and append-only. Never edit a merged migration — add the next number.
- **Stored file paths** must be absolute. Enforced by a CHECK constraint and re-validated in [`files.register`][noxdb.files.register].

---

## Where the SQL lives

- `schema/001_initial.sql` — initial schema.
- `schema/002_controls_support.sql` — nullable `sex`/`age` for control rows; adds `NC` to `sample_type` ENUM.
- `schema/003_cross_project_samples.sql` — adds the `project_samples` junction; drops `subjects.project_id` (global UNIQUE on `subject_code`); canonicalizes `SQR`/`SQRP`; backfills study/input/control membership; deletes the `mockIP`/`anchor`/`NC` projects.
- `schema/004_ip_and_sequencing_coords.sql` — adds `IPR`/`IPRP`; moves the existing (IP) values into them so `SQR`/`SQRP` can be backfilled with the real sequencing coordinates by `scripts/backfill_sequencing_coords.py`.
- `users/users.sql` — role and privilege definitions (the matching `users_with_passwords.sql` is gitignored).
- `seed/load_fake_data.py` — fake-data seed covering all four EAV value types and a longitudinal subject example.
