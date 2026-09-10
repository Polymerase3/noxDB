-- =========================================================
-- Migration: 004_ip_and_sequencing_coords
-- Description: Separate the two coordinate systems that were
--              conflated in one pair of columns.
--
--              `SQR`/`SQRP` were named for the sequencing run
--              and its plate, but 0.7.3 backfilled them from
--              the `RxxPxx` in the sample name — which is the
--              *immunoprecipitation* run and plate, a different
--              thing entirely. This adds `IPR`/`IPRP` for the
--              IP coordinates, moves the current values there,
--              and frees `SQR`/`SQRP` to hold the sequencing
--              coordinates they were always meant to hold.
--
-- Author: Mateusz F. Kołek
-- Created: 2026-09-10
-- Version: 4.0
--
-- Compatible with:
--   - MariaDB >= 10.x
--   - InnoDB storage engine
--   - Galera cluster
--
-- Notes:
-- A sample sits in exactly one IP well, so `IPR`/`IPRP` are
--   derived from the sample name and are always populated.
-- `SQR`/`SQRP` come from the sequencing run sheet and are NOT
--   set by this file. It leaves the pre-migration (IP) values
--   in place so the table is never in a half-described state;
--   `scripts/backfill_sequencing_coords.py` generates the
--   UPDATEs that overwrite them. Run that immediately after
--   this migration — between the two, `SQR`/`SQRP` still read
--   as IP coordinates.
-- Plate-based control linking keys on `IPR`/`IPRP` from here
--   on: controls occupy wells 81-96 of the IP plate, so it was
--   always an IP-plate relationship. Because the pre-migration
--   `SQR`/`SQRP` held IP values, the links already in
--   `project_samples` stay correct and need no rebuild.
--
-- How to run (PRODUCTION database is `ccr_metadata`; the dev
-- database is `dbmaria_project` — change the USE below for dev):
--   mysql -u <user> -p ccr_metadata < 004_ip_and_sequencing_coords.sql
--   OR inside MariaDB:
--   SOURCE 004_ip_and_sequencing_coords.sql;
--
-- Pre-flight (must return 0 rows — abort otherwise, because the
-- IP coordinates would not be recoverable from the columns):
--   SELECT COUNT(*) FROM samples WHERE sample_name NOT REGEXP '^R[0-9]+(P[0-9]+)?_';
--
-- =========================================================

USE ccr_metadata;

-- ---------------------------------------------------------
-- IP run / plate. NOT NULL with an empty default, matching
-- how SQR/SQRP model "no plate" (inputs carry a run and an
-- empty plate) rather than using NULL.
-- ---------------------------------------------------------
ALTER TABLE samples
    ADD COLUMN IPR  VARCHAR(10) NOT NULL DEFAULT '' AFTER sample_type,
    ADD COLUMN IPRP VARCHAR(10) NOT NULL DEFAULT '' AFTER IPR;

-- ---------------------------------------------------------
-- Move the IP coordinates into the columns that name them.
-- Every current SQR/SQRP was written by the 0.7.3 backfill
-- straight from the sample name, so this is a pure rename of
-- the data, not a re-derivation.
-- ---------------------------------------------------------
UPDATE samples SET IPR = SQR, IPRP = SQRP;

-- ---------------------------------------------------------
-- Matched by exact string equality (control auto-link, the
-- importer, project-scoped queries), same as SQR/SQRP.
-- ---------------------------------------------------------
ALTER TABLE samples
    ADD KEY idx_samples_ipr  (IPR),
    ADD KEY idx_samples_iprp (IPRP);

-- ---------------------------------------------------------
-- Post-flight: IPR must be populated for every row, and must
-- still agree with the sample name. Both must return 0.
--   SELECT COUNT(*) FROM samples WHERE IPR = '';
--   SELECT COUNT(*) FROM samples
--   WHERE CONCAT('R', IPR, IF(IPRP = '', '_', CONCAT('P', IPRP, '_')))
--         <> LEFT(sample_name,
--                 CHAR_LENGTH(CONCAT('R', IPR,
--                     IF(IPRP = '', '_', CONCAT('P', IPRP, '_')))));
-- ---------------------------------------------------------
