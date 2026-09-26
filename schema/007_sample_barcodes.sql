-- =========================================================
-- Migration: 007_sample_barcodes
-- Description: Store each sample's sequencing barcodes.
--
--              The run sheet ("Overview_SQRs", All_SQRs) gives
--              every sequenced well an i7 and an i5 index: the
--              sequence and the kit's name for it (e.g.
--              TAACTTGGTC / IDT10_i7_1). They belong next to
--              SQR / SQRP, which come from the same sheet.
--
-- Author: Mateusz F. Kołek
-- Created: 2026-09-26
-- Version: 7.0
--
-- Compatible with:
--   - MariaDB >= 10.5 (PCRE2 regular expressions)
--   - InnoDB storage engine
--   - Galera cluster
--
-- Notes:
-- All four columns are nullable: NULL means "not known", which
--   is every existing row until the run sheet is applied with
--   scripts/apply_run_sheet.py.
-- Sequences are upper case A/C/G/T/N only. The (?-i) makes the
--   check case sensitive; REGEXP ignores case under the table's
--   utf8mb4_unicode_ci collation otherwise. noxdb upper-cases
--   sequences before writing them.
--
-- How to run (PRODUCTION database is `ccr_metadata`; the dev
-- database is `dbmaria_project` — change the USE below for dev):
--   mysql -u <user> -p ccr_metadata < 007_sample_barcodes.sql
--   OR inside MariaDB:
--   SOURCE 007_sample_barcodes.sql;
--
-- =========================================================

USE ccr_metadata;

ALTER TABLE samples
    ADD COLUMN i7_index    VARCHAR(32) NULL AFTER SQRP,
    ADD COLUMN i7_index_id VARCHAR(50) NULL AFTER i7_index,
    ADD COLUMN i5_index    VARCHAR(32) NULL AFTER i7_index_id,
    ADD COLUMN i5_index_id VARCHAR(50) NULL AFTER i5_index,
    ADD CONSTRAINT chk_samples_i7_index
        CHECK (i7_index IS NULL OR i7_index REGEXP '(?-i)^[ACGTN]+$'),
    ADD CONSTRAINT chk_samples_i5_index
        CHECK (i5_index IS NULL OR i5_index REGEXP '(?-i)^[ACGTN]+$');

-- ---------------------------------------------------------
-- Post-flight: must list the four columns after SQRP, all NULL.
--   SHOW COLUMNS FROM samples LIKE 'i_\_index%';
--   SELECT COUNT(*) FROM samples WHERE i7_index IS NOT NULL;   -- 0
-- ---------------------------------------------------------
