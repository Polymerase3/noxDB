# Project import

The master importer loads a whole project folder (`project.yaml`,
`subjects.csv`, `visits.csv`, `samples.csv`, `files/manifest.csv`)
into the database in a single transaction. See the [CLI page](../cli.md)
for the command-line wrapper.

## Checking a bundle without importing

`validate_bundle` runs every check the import runs, without writing.
Without a cursor it needs no database, which suits a submission form;
with one it also compares the bundle with what is already stored.

```python
from noxdb import init_pool, transaction
from noxdb._import import load_project_dir, validate_bundle

bundle = load_project_dir("IBD_Vienna/")
result = validate_bundle(bundle)                # offline checks only

init_pool()
with transaction() as cur:                      # nothing is written
    result = validate_bundle(bundle, cur=cur, force=True)

for problem in result.errors:
    print("error:", problem)
for note in result.warnings:
    print("warning:", note)
```

A bundle can also be built in memory from `ProjectMeta`, `SubjectRow`,
`VisitRow`, `SampleRow` and `FileRow` instead of read from a folder.

### Adding to an existing project

A bundle that adds to an existing project (`force=True`) usually repeats
subjects, visits and samples the database already has. The import reuses
those rows as they are, so the bundle must agree with them:

- A **different** value for a stored field (a subject's sex, a visit's
  age or group, a sample's visit, type, plate coordinates, library or
  antibody class) is an **error**, and the import is refused. Correct the
  bundle, or update the database explicitly first.
- A value for a field the database has **empty** (an age, a sample's
  SQR) is a **warning**: the import does not fill it in.
- A metadata value that **changes** is a **warning**: the import
  overwrites it.
- An empty value in the bundle asserts nothing.

## Top-level entry point

::: noxdb._import

## Runner

::: noxdb._import.runner

## Loader

::: noxdb._import.loader

## Schema

::: noxdb._import.schema
