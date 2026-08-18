-- 15_team_results.sql — the nflverse team-results feed (README §5a, Phase 1.6).
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season).
--
-- Why a second source at all: points allowed and yards allowed are team-game
-- outcomes, and a player-stat file cannot contain them — yet they are the
-- largest component of D/ST scoring in every standard league. Without this the
-- DEF board ranks playmaking only (README §5a).
--
--   nflverse games.csv            -> RAW.TEAM_GAME_RAW  -> points allowed
--   nflverse stats_team_week.csv  -> RAW.TEAM_WEEK_RAW  -> yards allowed
--   both                          -> STAGING.STG_TEAM_WEEK, joined by
--                                    40_defense.sql on (season, week, team)
--
-- Loading is by column NAME, not position: the team-week release carries ~138
-- columns and nflverse does not promise their order across seasons, so a
-- positional COPY would silently shift a season from now. INFER_SCHEMA builds a
-- per-season scratch table matching the file, MATCH_BY_COLUMN_NAME fills it,
-- and only the handful of columns this project uses is copied into the
-- persistent table — which therefore keeps a stable shape whatever nflverse
-- adds upstream.

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA RAW;

SET target_season = COALESCE(GETVARIABLE('TARGET_SEASON')::NUMBER, 2025);

-- ================================================================ SCHEDULE ===
-- games.csv is one league-wide file covering every season, so it is staged at
-- nflverse/games.csv rather than under a season directory.
CREATE TABLE IF NOT EXISTS TEAM_GAME_RAW (
    game_id     VARCHAR,
    season      NUMBER,
    game_type   VARCHAR,    -- REG, WC, DIV, CON, SB
    week        NUMBER,
    away_team   VARCHAR,
    away_score  NUMBER,
    home_team   VARCHAR,
    home_score  NUMBER,
    source_file VARCHAR,
    loaded_at   TIMESTAMP_NTZ
);

CREATE OR REPLACE TEMPORARY TABLE GAMES_LOAD
    USING TEMPLATE (
        SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*))
        FROM TABLE(INFER_SCHEMA(
            LOCATION    => '@RAW.FANTASY_STAGE/nflverse/games.csv',
            FILE_FORMAT => 'RAW.FF_NFLVERSE_CSV',
            IGNORE_CASE => TRUE          -- else the columns are quoted lowercase
        ))
    );

COPY INTO GAMES_LOAD
FROM @RAW.FANTASY_STAGE/nflverse/games.csv
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFLVERSE_CSV)
MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
FORCE = TRUE;

DELETE FROM TEAM_GAME_RAW WHERE season = $target_season;

-- Unplayed games are in the file from the schedule release onward with NULL
-- scores; they are not results and must not become a 0-0 shutout.
INSERT INTO TEAM_GAME_RAW
SELECT
    game_id, season, game_type, week,
    away_team, away_score, home_team, home_score,
    'nflverse/games.csv', CURRENT_TIMESTAMP()
FROM GAMES_LOAD
WHERE season = $target_season
  AND home_score IS NOT NULL
  AND away_score IS NOT NULL;

-- =============================================================== TEAM WEEK ===
CREATE TABLE IF NOT EXISTS TEAM_WEEK_RAW (
    season          NUMBER,
    week            NUMBER,
    season_type     VARCHAR,    -- REG or POST
    game_id         VARCHAR,
    team            VARCHAR,
    opponent_team   VARCHAR,
    passing_yards   FLOAT,
    sack_yards_lost FLOAT,      -- negative in the source
    rushing_yards   FLOAT,
    source_file     VARCHAR,
    loaded_at       TIMESTAMP_NTZ
);

SET stmt = $$
CREATE OR REPLACE TEMPORARY TABLE TEAM_WEEK_LOAD
    USING TEMPLATE (
        SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*))
        FROM TABLE(INFER_SCHEMA(
            LOCATION    => '@RAW.FANTASY_STAGE/nflverse/$$ || $target_season || $$/',
            FILE_FORMAT => 'RAW.FF_NFLVERSE_CSV',
            IGNORE_CASE => TRUE
        ))
    )$$;
EXECUTE IMMEDIATE $stmt;

SET stmt = $$
COPY INTO TEAM_WEEK_LOAD
FROM @RAW.FANTASY_STAGE/nflverse/$$ || $target_season || $$/
FILE_FORMAT = (FORMAT_NAME = RAW.FF_NFLVERSE_CSV)
MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
FORCE = TRUE$$;
EXECUTE IMMEDIATE $stmt;

DELETE FROM TEAM_WEEK_RAW WHERE season = $target_season;

INSERT INTO TEAM_WEEK_RAW
SELECT
    season, week, season_type, game_id, team, opponent_team,
    passing_yards, sack_yards_lost, rushing_yards,
    'nflverse/' || season || '/stats_team_week_' || season || '.csv',
    CURRENT_TIMESTAMP()
FROM TEAM_WEEK_LOAD;

-- ================================================================= STAGING ===
USE SCHEMA STAGING;

-- One row per team per week: what that defense gave up. Both sources are
-- team-perspective offense rows, so every metric here is the OPPONENT's line.
CREATE OR REPLACE TABLE STG_TEAM_WEEK AS
WITH game_sides AS (
    -- Each game contributes two rows, one per team's point of view.
    SELECT season, week, game_type, game_id,
           away_team AS raw_team, home_team AS raw_opponent,
           away_score AS points_scored, home_score AS points_allowed,
           FALSE AS is_home
    FROM RAW.TEAM_GAME_RAW
    UNION ALL
    SELECT season, week, game_type, game_id,
           home_team, away_team,
           home_score, away_score,
           TRUE
    FROM RAW.TEAM_GAME_RAW
),
team_yards AS (
    -- Net total yards, the figure a fantasy site means by "yards allowed":
    -- sack yardage is already negative in the source, so it adds.
    SELECT
        season, week, season_type, game_id,
        COALESCE(a.team, UPPER(TRIM(t.team)))          AS team,
        COALESCE(o.team, UPPER(TRIM(t.opponent_team))) AS opponent,
        COALESCE(passing_yards, 0)
            + COALESCE(sack_yards_lost, 0)
            + COALESCE(rushing_yards, 0)               AS net_yards
    FROM RAW.TEAM_WEEK_RAW t
    LEFT JOIN TEAM_ALIAS a ON a.raw_team = UPPER(TRIM(t.team))
    LEFT JOIN TEAM_ALIAS o ON o.raw_team = UPPER(TRIM(t.opponent_team))
),
scores AS (
    SELECT
        g.season,
        g.week,
        g.game_id,
        IFF(g.game_type = 'REG', 'REG', 'POST') AS season_type,
        COALESCE(a.team, UPPER(TRIM(g.raw_team)))     AS team,
        COALESCE(o.team, UPPER(TRIM(g.raw_opponent))) AS opponent,
        g.is_home,
        g.points_scored,
        g.points_allowed
    FROM game_sides g
    LEFT JOIN TEAM_ALIAS a ON a.raw_team = UPPER(TRIM(g.raw_team))
    LEFT JOIN TEAM_ALIAS o ON o.raw_team = UPPER(TRIM(g.raw_opponent))
)
SELECT
    s.season,
    s.week,
    -- Same convention as the player feed: weeks 15-18 are the fantasy
    -- postseason and are flagged, never excluded (README §3, §11).
    s.week BETWEEN 15 AND 18            AS is_playoff,
    s.season_type,
    s.game_id,
    s.team,
    s.opponent,
    s.is_home,
    s.points_scored,
    s.points_allowed,
    own.net_yards                       AS yards_gained,
    -- The opponent's offensive line IS this defense's yards allowed. A missing
    -- team-week row leaves this NULL rather than 0, so 99_tests.sql can tell a
    -- shutout apart from a failed join.
    opp.net_yards                       AS yards_allowed
FROM scores s
LEFT JOIN team_yards own
       ON own.season = s.season AND own.week = s.week AND own.team = s.team
LEFT JOIN team_yards opp
       ON opp.season = s.season AND opp.week = s.week AND opp.team = s.opponent;
