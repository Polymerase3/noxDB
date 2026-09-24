-- =========================================================
-- Migration: 006_sample_files_archive_member
-- Description: Let a `sample_files` row point at one member of a
--              tar archive.
--
--              The FASTQ files live in per-run tars under
--              <archive root>/ccr/mariaDB/fastq_tar/. For such a
--              row `file_path` is the tar, `archive_member` the
--              member's name inside it and `archive_offset` the
--              byte position of the member's data, so one file can
--              be read without scanning the whole tar.
--              `file_size_bytes` / `checksum_md5` describe the
--              member, not the tar.
--
-- Author: Mateusz F. Kołek
-- Created: 2026-09-24
-- Version: 6.0
--
-- Compatible with:
--   - MariaDB >= 10.5 (UNIQUE over long columns is kept as a hash)
--   - InnoDB storage engine
--   - Galera cluster
--
-- Notes:
-- Plain files keep archive_member = '' and archive_offset = NULL,
--   so every existing row is unchanged and stays unique by path.
-- '' rather than NULL, because a UNIQUE key treats NULLs as
--   distinct and would let the same plain path be registered twice.
--
-- How to run (PRODUCTION database is `ccr_metadata`; the dev
-- database is `dbmaria_project` — change the USE below for dev):
--   mysql -u <user> -p ccr_metadata < 006_sample_files_archive_member.sql
--   OR inside MariaDB:
--   SOURCE 006_sample_files_archive_member.sql;
--
-- =========================================================

USE ccr_metadata;

ALTER TABLE sample_files
    ADD COLUMN archive_member VARCHAR(512) NOT NULL DEFAULT '' AFTER file_path,
    ADD COLUMN archive_offset BIGINT UNSIGNED NULL AFTER archive_member,
    DROP INDEX uq_sample_files_path,
    ADD CONSTRAINT uq_sample_files_path_member UNIQUE (file_path, archive_member),
    ADD CONSTRAINT chk_sample_files_offset_member
        CHECK (archive_offset IS NULL OR archive_member <> '');

-- ---------------------------------------------------------
-- Post-flight:
--   SHOW COLUMNS FROM sample_files LIKE 'archive%';
--   SHOW INDEX FROM sample_files WHERE Key_name LIKE 'uq_%';
-- ---------------------------------------------------------
