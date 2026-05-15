# Project import

The master importer loads a whole project folder (`project.yaml`,
`subjects.csv`, `visits.csv`, `samples.csv`, `files/manifest.csv`)
into the database in a single transaction. See the [CLI page](../cli.md)
for the command-line wrapper.

## Top-level entry point

::: noxdb._import

## Runner

::: noxdb._import.runner

## Loader

::: noxdb._import.loader

## Schema

::: noxdb._import.schema
