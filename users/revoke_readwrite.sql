-- =========================================================
-- One-time migration: revoke INSERT/UPDATE/DELETE from
-- lovro.trgovec-greif and melanie.prinzensteiner, reducing
-- them to SELECT-only like all other non-admin users.
--
-- Author: Mateusz F. Kołek
-- Created: 2026-05-15
--
-- HOW TO APPLY
-- ------------
--   mysql -u root -p < users/revoke_readwrite.sql
-- =========================================================

USE dbmaria_project;

REVOKE INSERT, UPDATE, DELETE ON dbmaria_project.*
    FROM 'lovro.trgovec-greif'@'lisc.%';

REVOKE INSERT, UPDATE, DELETE ON dbmaria_project.*
    FROM 'melanie.prinzensteiner'@'lisc.%';

FLUSH PRIVILEGES;
