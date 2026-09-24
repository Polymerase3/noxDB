-- =========================================================
-- Migration: 005_zigp_loose_file_type
-- Description: Add the `zigp_loose` file type.
--
--              `zigp_norm` is the ZIGP output cut at Holm
--              padj <= 0.05. `zigp_loose` is the same fit kept
--              down to neglogp >= 2, with the fitted lambda /
--              theta / p_i per peptide: the permissive list
--              IDR needs to tune the enrichment cutoff.
--
-- Author: Mateusz F. Kołek
-- Created: 2026-09-24
-- Version: 5.0
--
-- Compatible with:
--   - MariaDB >= 10.x
--   - InnoDB storage engine
--   - Galera cluster
--
-- Notes:
-- The ENUM is only extended; existing values keep their order,
--   so no stored row changes.
-- Files live in <work root>/ccr/mariaDB/zigp_loose_cutoff/ as
--   <name>.csv.gz, next to zigp/ (tier `work`).
--
-- How to run (PRODUCTION database is `ccr_metadata`; the dev
-- database is `dbmaria_project` — change the USE below for dev):
--   mysql -u <user> -p ccr_metadata < 005_zigp_loose_file_type.sql
--   OR inside MariaDB:
--   SOURCE 005_zigp_loose_file_type.sql;
--
-- =========================================================

USE ccr_metadata;

ALTER TABLE sample_files
    MODIFY COLUMN file_type ENUM(
        'fastq_r1',
        'fastq_r2',
        'fastq_single',
        'bam',
        'counts',
        'beer_norm',
        'zigp_norm',
        'edger_norm',
        'zigp_loose'
    ) NOT NULL;

-- ---------------------------------------------------------
-- Post-flight: must list 'zigp_loose' last.
--   SHOW COLUMNS FROM sample_files LIKE 'file_type';
-- ---------------------------------------------------------
