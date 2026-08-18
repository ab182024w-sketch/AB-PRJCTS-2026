-- 00_setup.sql — database, schemas, warehouse, file format, stage (README §5).
-- Run once. Every later script assumes these objects exist.
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season).

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

-- nflverse team results (README §5a). PARSE_HEADER instead of SKIP_HEADER: the
-- team-week release carries ~138 columns whose order is not promised across
-- seasons, so 15_team_results.sql loads it by column NAME. NA is nflverse's
-- (R's) null spelling.
CREATE FILE FORMAT IF NOT EXISTS RAW.FF_NFLVERSE_CSV
    TYPE = CSV
    PARSE_HEADER = TRUE
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    TRIM_SPACE = TRUE
    NULL_IF = ('', 'NA', 'NULL', 'null')
    EMPTY_FIELD_AS_NULL = TRUE;

-- The stage mirrors the source layout: <season>/<week>/<POS>.csv. That path is
-- the ONLY place season and week exist — they are not columns (README §2).
-- nflverse assets live beside it under nflverse/ (README §5a).
CREATE STAGE IF NOT EXISTS RAW.FANTASY_STAGE
    FILE_FORMAT = RAW.FF_NFL_CSV
    DIRECTORY = (ENABLE = TRUE)
    COMMENT = 'PUT target for pipeline/download.py --put-to-stage';

-- Populated by Phase 0:
--   python -m pipeline.download --season 2025 --weeks 1-18 --put-to-stage
-- which issues, per file:
--   PUT 'file://…/data/2025/1/QB.csv' @RAW.FANTASY_STAGE/2025/1 AUTO_COMPRESS = TRUE OVERWRITE = TRUE;

-- Team abbreviation normalization, shared by the player feed (20_staging.sql)
-- and the nflverse feed (15_team_results.sql) — which is why it is defined here
-- rather than in either one. hvpkod's 2025 files were checked and are already
-- clean (32 abbreviations + 'FA'), so for that source this is an identity map;
-- it earns its keep on nflverse, where the Rams are LA, and on older seasons
-- with relocated franchises (README §5, §5a).
CREATE TABLE IF NOT EXISTS STAGING.TEAM_ALIAS (
    raw_team    VARCHAR NOT NULL PRIMARY KEY,
    team        VARCHAR NOT NULL
);

MERGE INTO STAGING.TEAM_ALIAS t
USING (
    SELECT * FROM VALUES
        ('JAC', 'JAX'), ('JAX', 'JAX'),
        -- nflverse LA = Rams. LAC = Chargers on BOTH sides, so it must NOT be
        -- folded into LA: that would merge two defenses into one.
        ('LA',  'LAR'), ('LAR', 'LAR'),
        ('WSH', 'WAS'), ('WFT', 'WAS'), ('WAS', 'WAS'),
        ('SD',  'LAC'), ('LAC', 'LAC'),
        ('OAK', 'LV'),  ('LV',  'LV'),
        ('STL', 'LAR'),
        ('ARI','ARI'),('ATL','ATL'),('BAL','BAL'),('BUF','BUF'),('CAR','CAR'),
        ('CHI','CHI'),('CIN','CIN'),('CLE','CLE'),('DAL','DAL'),('DEN','DEN'),
        ('DET','DET'),('GB','GB'),('HOU','HOU'),('IND','IND'),('KC','KC'),
        ('MIA','MIA'),('MIN','MIN'),('NE','NE'),('NO','NO'),('NYG','NYG'),
        ('NYJ','NYJ'),('PHI','PHI'),('PIT','PIT'),('SEA','SEA'),('SF','SF'),
        ('TB','TB'),('TEN','TEN'),('FA','FA')
    AS v(raw_team, team)
) s ON t.raw_team = s.raw_team
WHEN MATCHED THEN UPDATE SET t.team = s.team
WHEN NOT MATCHED THEN INSERT (raw_team, team) VALUES (s.raw_team, s.team);
