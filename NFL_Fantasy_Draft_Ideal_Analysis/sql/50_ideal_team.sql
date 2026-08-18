-- 50_ideal_team.sql — the ideal-team board, one per scoring mode (README §1, §5).
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season).
--
-- Slot depth is a parameter, not hard-coded: it lives in SLOT_DEPTH, so
-- changing 25 RB to 30 is an UPDATE, not a query edit.

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA MARTS;

CREATE TABLE IF NOT EXISTS SLOT_DEPTH (
    slot        VARCHAR NOT NULL PRIMARY KEY,
    depth       NUMBER  NOT NULL,
    sort_order  NUMBER  NOT NULL
);

MERGE INTO SLOT_DEPTH t
USING (
    SELECT * FROM VALUES
        ('QB', 10, 1),
        ('RB', 25, 2),
        ('WR', 25, 3),
        ('TE', 25, 4),
        ('K',   1, 5),
        ('DEF', 5, 6)
    AS v(slot, depth, sort_order)
) s ON t.slot = s.slot
WHEN MATCHED THEN UPDATE SET depth = s.depth, sort_order = s.sort_order
WHEN NOT MATCHED THEN INSERT (slot, depth, sort_order) VALUES (s.slot, s.depth, s.sort_order);

-- Minimum-games filter: a parameter defaulting to no filter for the headline
-- board (README §3). Set the session variable before running to apply one.
SET min_games = 0;
SET target_season = 2025;

CREATE OR REPLACE TABLE IDEAL_TEAM AS
WITH candidates AS (
    SELECT
        a.season,
        a.scoring_mode,
        a.pos                   AS slot,
        a.player_id,
        a.player_name,
        a.team,
        a.total_pts,
        a.games_played,
        a.pts_per_game,
        a.stddev_pts,
        a.floor_pts,
        a.ceiling_pts,
        a.weeks_above_threshold,
        a.playoff_pts,
        a.last_4_pts_per_game
    FROM AGG_PLAYER_SEASON a
    WHERE a.pos IN ('QB', 'RB', 'WR', 'TE', 'K')
      AND a.season = $target_season
      AND a.games_played >= $min_games

    UNION ALL

    -- Team defenses stand in for players: the slot's "player" is the unit.
    SELECT
        d.season,
        d.scoring_mode,
        'DEF'                   AS slot,
        d.team                  AS player_id,
        d.team || ' D/ST'       AS player_name,
        d.team,
        d.total_pts,
        d.weeks_played          AS games_played,
        d.pts_per_week          AS pts_per_game,
        d.stddev_pts,
        NULL                    AS floor_pts,
        NULL                    AS ceiling_pts,
        NULL                    AS weeks_above_threshold,
        d.playoff_pts,
        NULL                    AS last_4_pts_per_game
    FROM FCT_TEAM_DEFENSE d
    WHERE d.season = $target_season
)
SELECT
    c.season,
    c.scoring_mode,
    c.slot,
    ROW_NUMBER() OVER (PARTITION BY c.scoring_mode, c.slot ORDER BY c.total_pts DESC, c.player_id) AS slot_rank,
    c.player_id,
    c.player_name,
    c.team,
    ROUND(c.total_pts, 2)           AS total_pts,
    c.games_played,
    ROUND(c.pts_per_game, 2)        AS pts_per_game,
    ROUND(c.stddev_pts, 2)          AS stddev_pts,
    ROUND(c.floor_pts, 2)           AS floor_pts,
    ROUND(c.ceiling_pts, 2)         AS ceiling_pts,
    c.weeks_above_threshold,
    ROUND(c.playoff_pts, 2)         AS playoff_pts,
    ROUND(c.last_4_pts_per_game, 2) AS last_4_pts_per_game
FROM candidates c
JOIN SLOT_DEPTH d ON d.slot = c.slot
QUALIFY slot_rank <= d.depth
ORDER BY c.scoring_mode, d.sort_order, slot_rank;

-- Reference export, one file per scoring mode (README §7 deliverable 9).
-- COPY INTO @RAW.FANTASY_STAGE/exports/ideal_team_2025_standard.csv
-- FROM (SELECT * FROM IDEAL_TEAM WHERE scoring_mode = 'standard')
-- FILE_FORMAT = (TYPE = CSV, COMPRESSION = NONE, HEADER = TRUE)
-- SINGLE = TRUE OVERWRITE = TRUE;
