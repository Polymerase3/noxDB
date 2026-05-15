-- =========================================================
-- User / privilege definitions for dbmaria_project
--
-- Author: Mateusz F. Kołek, Gabriel Innocenti
-- Created: 2026-05-04
--
-- HOW TO APPLY
-- ------------
-- 1. Copy this file:
--      cp users/users.sql users/users_with_passwords.sql
-- 2. Replace every CHANGEME_* token with a strong password.
--    (users_with_passwords.sql is gitignored — never commit it.)
-- 3. Apply:
--      mysql -u root -p < users/users_with_passwords.sql
--
-- HOST RESTRICTION
-- ----------------
-- All accounts are restricted to 'lisc.%' (any host in the LISC
-- domain). If the setup moves to a single static IP, replace
-- 'lisc.%' with that IP on every line below.
-- =========================================================

USE dbmaria_project;

-- =========================================================
-- Admins  (ALL PRIVILEGES + GRANT OPTION)
-- =========================================================
CREATE USER IF NOT EXISTS 'mateusz.kolek'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_mateusz.kolek';

CREATE USER IF NOT EXISTS 'gabriel.innocenti'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_gabriel.innocenti';

GRANT ALL PRIVILEGES ON dbmaria_project.*
    TO 'mateusz.kolek'@'lisc.%'
    WITH GRANT OPTION;

GRANT ALL PRIVILEGES ON dbmaria_project.*
    TO 'gabriel.innocenti'@'lisc.%'
    WITH GRANT OPTION;


CREATE USER IF NOT EXISTS 'lovro.trgovec-greif'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_lovro.trgovec-greif';

CREATE USER IF NOT EXISTS 'melanie.prinzensteiner'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_melanie.prinzensteiner';

GRANT SELECT ON dbmaria_project.*
    TO 'lovro.trgovec-greif'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'melanie.prinzensteiner'@'lisc.%';


-- =========================================================
-- Read-only  (SELECT)
-- =========================================================
CREATE USER IF NOT EXISTS 'michaela.fehringer'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_michaela.fehringer';

CREATE USER IF NOT EXISTS 'carlos.reynablanco'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_carlos.reynablanco';

CREATE USER IF NOT EXISTS 'anna-lena.pirker'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_anna-lena.pirker';

CREATE USER IF NOT EXISTS 'ioanna.filimonova'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_ioanna.filimonova';

CREATE USER IF NOT EXISTS 'nicolai.hoerstke'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_nicolai.hoerstke';

CREATE USER IF NOT EXISTS 'thomas.vogl'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_thomas.vogl';

CREATE USER IF NOT EXISTS 'nikolas.basler'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_nikolas.basler';

CREATE USER IF NOT EXISTS 'magdalena.huebner'@'lisc.%'
    IDENTIFIED BY 'CHANGEME_magdalena.huebner';

GRANT SELECT ON dbmaria_project.*
    TO 'michaela.fehringer'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'carlos.reynablanco'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'anna-lena.pirker'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'ioanna.filimonova'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'nicolai.hoerstke'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'thomas.vogl'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'nikolas.basler'@'lisc.%';

GRANT SELECT ON dbmaria_project.*
    TO 'magdalena.huebner'@'lisc.%';


-- =========================================================
FLUSH PRIVILEGES;
