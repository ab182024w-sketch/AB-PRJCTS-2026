-- 10_raw.sql — the three RAW tables and their COPY INTO loads (README §2, §5).
-- Three tables, not one: the offensive, kicker and IDP files share only their
-- five key columns. Everything lands as VARCHAR; typing happens in staging.
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season).

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA RAW;

-- ---------------------------------------------------------------- OFFENSE ---
CREATE TABLE IF NOT EXISTS OFFENSE_RAW (
    player_name             VARCHAR,
    player_id               VARCHAR,
    pos                     VARCHAR,
    team                    VARCHAR,
    player_opponent         VARCHAR,
    passing_yds             VARCHAR,
    passing_td              VARCHAR,
    passing_int             VARCHAR,
    rushing_yds             VARCHAR,
    rushing_td              VARCHAR,
    receiving_rec           VARCHAR,
    receiving_yds           VARCHAR,
    receiving_td            VARCHAR,
    ret_td                  VARCHAR,
    fum_td                  VARCHAR,
    two_pt                  VARCHAR,   -- source column "2PT" (leading digit)
    fum                     VARCHAR,
    fan_pts_against_pts     VARCHAR,   -- source column "FanPtsAgainst-pts"
    touch_carries           VARCHAR,
    touch_receptions        VARCHAR,
    touches                 VARCHAR,
    targets_receptions      VARCHAR,
    targets                 VARCHAR,
    reception_percentage    VARCHAR,
    rz_target               VARCHAR,
    rz_touch                VARCHAR,
    rz_g2g                  VARCHAR,
    source_rank             VARCHAR,
    source_total_points     VARCHAR,
    source_file             VARCHAR,   -- METADATA$FILENAME: the source of season/week
    source_file_row         NUMBER,
    loaded_at               TIMESTAMP_NTZ
);

-- ----------------------------------------------------------------- KICKER ---
CREATE TABLE IF NOT EXISTS K_RAW (
    player_name             VARCHAR,
    player_id               VARCHAR,
    pos                     VARCHAR,
    team                    VARCHAR,
    player_opponent         VARCHAR,
    pat_made                VARCHAR,
    pat_missed              VARCHAR,
    fg_made_0_19            VARCHAR,   -- source "FgMade_0-19" (hyphen)
    fg_made_20_29           VARCHAR,
    fg_made_30_39           VARCHAR,
    fg_made_40_49           VARCHAR,
    fg_made_50              VARCHAR,
    fg_miss_0_19            VARCHAR,
    fg_miss_20_29           VARCHAR,
    fg_miss_30_39           VARCHAR,
    source_rank             VARCHAR,
    source_total_points     VARCHAR,
    source_file             VARCHAR,
    source_file_row         NUMBER,
    loaded_at               TIMESTAMP_NTZ
);

-- ------------------------------------------------------------ DEFENSE/IDP ---
CREATE TABLE IF NOT EXISTS DEFENSE_RAW (
    player_name             VARCHAR,
    player_id               VARCHAR,
    pos                     VARCHAR,
    team                    VARCHAR,
    player_opponent         VARCHAR,
    tackles_tot             VARCHAR,
    tackles_ast             VARCHAR,
    tackles_sck             VARCHAR,
    tackles_tfl             VARCHAR,
    turnover_int            VARCHAR,
    turnover_frc_fum        VARCHAR,
    turnover_fum_rec        VARCHAR,
    score_int_td            VARCHAR,
    score_fum_td            VARCHAR,
    score_blk_td            VARCHAR,
    score_saf               VARCHAR,
    score_def_2pt_ret       VARCHAR,
    blk                     VARCHAR,
    pdef                    VARCHAR,
    qb_hit                  VARCHAR,
    return_int_yds          VARCHAR,
    return_fum_yds          VARCHAR,
    source_rank             VARCHAR,
    source_total_points     VARCHAR,
    source_file             VARCHAR,
    source_file_row         NUMBER,
    loaded_at               TIMESTAMP_NTZ
);

-- Season-total files, loaded for reconciliation only — never an input to the
-- marts (README §2, §6). Same three shapes minus PlayerOpponent, which the
-- season files do not carry.
CREATE TABLE IF NOT EXISTS OFFENSE_SEASON_RAW LIKE OFFENSE_RAW;
CREATE TABLE IF NOT EXISTS K_SEASON_RAW       LIKE K_RAW;
CREATE TABLE IF NOT EXISTS DEFENSE_SEASON_RAW LIKE DEFENSE_RAW;

-- =============================== LOADS =====================================
-- Re-runnable: truncate the target for the season being loaded, then COPY with
-- FORCE so a re-PUT of a corrected file is picked up. ON_ERROR = CONTINUE keeps
-- one bad row from failing the load; the rejected rows are reviewed below
-- rather than silently discarded (README §5).

TRUNCATE TABLE IF EXISTS OFFENSE_RAW;
TRUNCATE TABLE IF EXISTS K_RAW;
TRUNCATE TABLE IF EXISTS DEFENSE_RAW;
TRUNCATE TABLE IF EXISTS OFFENSE_SEASON_RAW;
TRUNCATE TABLE IF EXISTS K_SEASON_RAW;
TRUNCATE TABLE IF EXISTS DEFENSE_SEASON_RAW;

COPY INTO OFFENSE_RAW
FROM (
    SELECT
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
        $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
        $21, $22, $23, $24, $25, $26, $27, $28, $29,
        METADATA$FILENAME,
        METADATA$FILE_ROW_NUMBER,
        CURRENT_TIMESTAMP()
    FROM @RAW.FANTASY_STAGE
)
PATTERN = '.*[0-9]{4}/[0-9]{1,2}/(QB|RB|WR|TE)[.]csv([.]gz)?'
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFL_CSV)
ON_ERROR = CONTINUE
FORCE = TRUE;

-- Review, do not ignore, whatever ON_ERROR = CONTINUE skipped. VALIDATE's
-- '_last' means "the last COPY in this session", so each call has to sit
-- immediately after its own COPY.
SELECT * FROM TABLE(VALIDATE(OFFENSE_RAW, JOB_ID => '_last'));

COPY INTO K_RAW
FROM (
    SELECT
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
        $11, $12, $13, $14, $15, $16, $17,
        METADATA$FILENAME,
        METADATA$FILE_ROW_NUMBER,
        CURRENT_TIMESTAMP()
    FROM @RAW.FANTASY_STAGE
)
PATTERN = '.*[0-9]{4}/[0-9]{1,2}/K[.]csv([.]gz)?'
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFL_CSV)
ON_ERROR = CONTINUE
FORCE = TRUE;

SELECT * FROM TABLE(VALIDATE(K_RAW, JOB_ID => '_last'));

COPY INTO DEFENSE_RAW
FROM (
    SELECT
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
        $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
        $21, $22, $23, $24,
        METADATA$FILENAME,
        METADATA$FILE_ROW_NUMBER,
        CURRENT_TIMESTAMP()
    FROM @RAW.FANTASY_STAGE
)
PATTERN = '.*[0-9]{4}/[0-9]{1,2}/(DB|LB|DL)[.]csv([.]gz)?'
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFL_CSV)
ON_ERROR = CONTINUE
FORCE = TRUE;

SELECT * FROM TABLE(VALIDATE(DEFENSE_RAW, JOB_ID => '_last'));

-- Season files sit at <season>/<POS>_season.csv — one directory level up, which
-- is what separates them from the weekly pattern above. They have no
-- PlayerOpponent column, so column 5 onward shifts by one; NULL is substituted.
COPY INTO OFFENSE_SEASON_RAW
FROM (
    SELECT
        $1, $2, $3, $4, NULL,
        $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19,
        $20, $21, $22, $23, $24, $25, $26, $27, $28,
        METADATA$FILENAME,
        METADATA$FILE_ROW_NUMBER,
        CURRENT_TIMESTAMP()
    FROM @RAW.FANTASY_STAGE
)
PATTERN = '.*[0-9]{4}/(QB|RB|WR|TE)_season[.]csv([.]gz)?'
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFL_CSV)
ON_ERROR = CONTINUE
FORCE = TRUE;

COPY INTO K_SEASON_RAW
FROM (
    SELECT
        $1, $2, $3, $4, NULL,
        $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
        METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @RAW.FANTASY_STAGE
)
PATTERN = '.*[0-9]{4}/K_season[.]csv([.]gz)?'
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFL_CSV)
ON_ERROR = CONTINUE
FORCE = TRUE;

COPY INTO DEFENSE_SEASON_RAW
FROM (
    SELECT
        $1, $2, $3, $4, NULL,
        $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19,
        $20, $21, $22, $23,
        METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @RAW.FANTASY_STAGE
)
PATTERN = '.*[0-9]{4}/(DB|LB|DL)_season[.]csv([.]gz)?'
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFL_CSV)
ON_ERROR = CONTINUE
FORCE = TRUE;
