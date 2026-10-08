-- =========================================================
-- Migration: 008_fastq_qc
-- Description: Store the raw FASTQ quality control of every
--              sample.
--
--              noxqc checks each sample's FASTQ pair (archive
--              MD5, gzip integrity, R1/R2 consistency, depth,
--              quality, adapters, lanes, index purity and the
--              barcode against the run sheet) and writes one
--              JSON per sample. That JSON is registered as a
--              `sample_files` row of the new type `fastq_qc`
--              (tier `work`). Its headline numbers and flags go
--              to the new table `sample_fastq_qc`, one row per
--              sample, so dashboards and queries can use them
--              without reading files.
--
-- Author: Mateusz F. Kołek
-- Created: 2026-10-08
-- Version: 8.0
--
-- Compatible with:
--   - MariaDB >= 10.5
--   - InnoDB storage engine
--   - Galera cluster
--
-- Notes:
-- The file_type ENUM is only extended; existing values keep
--   their order, so no stored row changes.
-- QC files live in <work root>/ccr/mariaDB/fastq_qc/json/ as
--   <sample_name>.fastq_qc.json.
-- sample_fastq_qc rows are written by noxdb.fastq_qc.upsert
--   (noxqc noxdb register); a re-run overwrites the row.
-- Percentages are 0-100, fractions (index_purity,
--   lane_min_frac) are 0-1. depth_rel is the sample's reads
--   over the median of the Sample wells on its sequencing
--   plate (NULL when the plate has none).
--
-- How to run (PRODUCTION database is `ccr_metadata`; the dev
-- database is `dbmaria_project` — change the USE below for dev):
--   mysql -u <user> -p ccr_metadata < 008_fastq_qc.sql
--   OR inside MariaDB:
--   SOURCE 008_fastq_qc.sql;
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
        'zigp_loose',
        'fastq_qc'
    ) NOT NULL;

CREATE TABLE sample_fastq_qc (
    sample_id           BIGINT UNSIGNED NOT NULL,

    qc_version          VARCHAR(32) NOT NULL,
    qc_run_at           DATETIME NOT NULL,

    reads_r1            BIGINT UNSIGNED NULL,
    reads_r2            BIGINT UNSIGNED NULL,
    pair_ok             BOOLEAN NOT NULL,

    sequencing_run      VARCHAR(100) NULL,
    n_lanes             TINYINT UNSIGNED NULL,
    lane_min_frac       DECIMAL(6,5) NULL,
    top_index           VARCHAR(80) NULL,
    index_purity        DECIMAL(6,5) NULL,
    barcode_match       ENUM('match', 'mismatch', 'unknown') NOT NULL DEFAULT 'unknown',

    q30_r1              DECIMAL(7,3) NULL,
    q30_r2              DECIMAL(7,3) NULL,
    avg_qual_r1         DECIMAL(6,3) NULL,
    avg_qual_r2         DECIMAL(6,3) NULL,
    gc_r1               DECIMAL(7,3) NULL,
    gc_r2               DECIMAL(7,3) NULL,
    dedup_r1            DECIMAL(7,3) NULL,
    adapter_max         DECIMAL(9,5) NULL,
    polyg_max           DECIMAL(9,5) NULL,
    n_max               DECIMAL(9,5) NULL,
    overrep_top_pct_r1  DECIMAL(9,5) NULL,
    depth_rel           DECIMAL(12,5) NULL,

    worst_flag          ENUM('FAIL', 'WARN', 'INFO', 'OK') NOT NULL,
    flags               TEXT NULL,

    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (sample_id),

    CONSTRAINT fk_sample_fastq_qc_sample
        FOREIGN KEY (sample_id)
        REFERENCES samples(sample_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    CONSTRAINT chk_sample_fastq_qc_fractions
        CHECK ((index_purity IS NULL OR index_purity BETWEEN 0 AND 1)
           AND (lane_min_frac IS NULL OR lane_min_frac BETWEEN 0 AND 1)),

    KEY idx_sample_fastq_qc_worst_flag (worst_flag)
) ENGINE=InnoDB;

-- ---------------------------------------------------------
-- Post-flight: 'fastq_qc' must be listed last, the table empty.
--   SHOW COLUMNS FROM sample_files LIKE 'file_type';
--   SHOW CREATE TABLE sample_fastq_qc;
--   SELECT COUNT(*) FROM sample_fastq_qc;   -- 0
-- ---------------------------------------------------------
