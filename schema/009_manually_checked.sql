-- =========================================================
-- Migration: 009_manually_checked
-- Description: Mark samples whose metadata has been checked
--              by hand.
--
--              Older notes were kept inconsistently, so every
--              sample's metadata (subject, visit, group, sex,
--              age, antibody class, plate coordinates, EAV
--              fields) is to be checked by hand. The new flag
--              records which samples have been done.
--
-- Author: Mateusz F. Kołek
-- Created: 2026-10-14
-- Version: 9.0
--
-- Compatible with:
--   - MariaDB >= 10.5
--   - InnoDB storage engine
--   - Galera cluster
--
-- Notes:
-- Every existing row starts as FALSE (not checked).
-- Set it with noxdb.samples.update(cur, sample_id,
--   manually_checked=True) so the change is audit-logged.
--
-- How to run (PRODUCTION database is `ccr_metadata`; the dev
-- database is `dbmaria_project` — change the USE below for dev):
--   mysql -u <user> -p ccr_metadata < 009_manually_checked.sql
--   OR inside MariaDB:
--   SOURCE 009_manually_checked.sql;
--
-- =========================================================

USE ccr_metadata;

ALTER TABLE samples
    ADD COLUMN manually_checked BOOLEAN NOT NULL DEFAULT FALSE AFTER antibody_class;

-- ---------------------------------------------------------
-- Post-flight: must list the column after antibody_class, all FALSE.
--   SHOW COLUMNS FROM samples LIKE 'manually_checked';
--   SELECT COUNT(*) FROM samples WHERE manually_checked;   -- 0
-- ---------------------------------------------------------
