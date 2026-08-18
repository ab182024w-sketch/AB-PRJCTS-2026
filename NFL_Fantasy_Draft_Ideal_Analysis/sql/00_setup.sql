-- 00_setup.sql — database, schemas, warehouse, file format, stage (README §5).
-- Run once. Every later script assumes these objects exist.
-- NOT YET EXECUTED: no Snowflake account was available when this was authored.

CREATE WAREHOUSE IF NOT EXISTS FANTASY_WH
    WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT = 'Single-season player-week volumes; XSMALL is ample (README §5).';

CREATE DATABASE IF NOT EXISTS FANTASY;

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;

CREATE SCHEMA IF NOT EXISTS RAW;
CREATE SCHEMA IF NOT EXISTS STAGING;
CREATE SCHEMA IF NOT EXISTS MARTS;

-- EMPTY_FIELD_AS_NULL keeps blank cells as NULL so staging can count cast
-- failures separately from legitimate blanks-mean-zero (README §2 note 4).
CREATE FILE FORMAT IF NOT EXISTS RAW.FF_NFL_CSV
    TYPE = CSV
    SKIP_HEADER = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    EMPTY_FIELD_AS_NULL = TRUE
    TRIM_SPACE = TRUE
    NULL_IF = ('', 'NULL', 'null')
    ERROR_ON_COLUMN_COUNT_MISMATCH = TRUE;

-- The stage mirrors the source layout: <season>/<week>/<POS>.csv. That path is
-- the ONLY place season and week exist — they are not columns (README §2).
CREATE STAGE IF NOT EXISTS RAW.FANTASY_STAGE
    FILE_FORMAT = RAW.FF_NFL_CSV
    DIRECTORY = (ENABLE = TRUE)
    COMMENT = 'PUT target for pipeline/download.py --put-to-stage';

-- Populated by Phase 0:
--   python -m pipeline.download --season 2025 --weeks 1-18 --put-to-stage
-- which issues, per file:
--   PUT 'file://…/data/2025/1/QB.csv' @RAW.FANTASY_STAGE/2025/1 AUTO_COMPRESS = TRUE OVERWRITE = TRUE;
