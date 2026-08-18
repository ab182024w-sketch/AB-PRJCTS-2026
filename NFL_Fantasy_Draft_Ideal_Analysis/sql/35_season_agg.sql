-- 35_season_agg.sql — AGG_PLAYER_SEASON: season totals plus per-game and
-- consistency metrics (README §3).
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season).

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA MARTS;

-- Startable-game cutoffs for weeks_above_threshold. A table, not a CASE
-- expression, so a league can retune them without editing SQL.
CREATE TABLE IF NOT EXISTS POSITION_THRESHOLDS (
    pos         VARCHAR NOT NULL PRIMARY KEY,
    threshold   FLOAT   NOT NULL
);

MERGE INTO POSITION_THRESHOLDS t
USING (
    SELECT * FROM VALUES
        ('QB', 18.0), ('RB', 12.0), ('WR', 12.0), ('TE', 10.0), ('K', 8.0),
        ('DB', 6.0),  ('LB', 6.0),  ('DL', 6.0)
    AS v(pos, threshold)
) s ON t.pos = s.pos
WHEN MATCHED THEN UPDATE SET threshold = s.threshold
WHEN NOT MATCHED THEN INSERT (pos, threshold) VALUES (s.pos, s.threshold);

CREATE OR REPLACE TABLE AGG_PLAYER_SEASON AS
WITH played AS (
    -- Bye rows are excluded here and only here: counting them would deflate
    -- every per-game average (README §3).
    SELECT f.*, COALESCE(t.threshold, 0) AS threshold
    FROM FCT_PLAYER_SCORING f
    LEFT JOIN POSITION_THRESHOLDS t ON t.pos = f.pos
    WHERE NOT f.is_bye
),
last_week AS (
    SELECT season, MAX(week) AS max_week FROM played GROUP BY season
)
SELECT
    p.season,
    p.player_id,
    ANY_VALUE(p.player_name)                                                AS player_name,
    p.pos,
    MAX_BY(p.team, p.week)                                                  AS team,
    p.scoring_mode,
    SUM(p.total_pts)                                                        AS total_pts,
    SUM(p.pass_pts)                                                         AS pass_pts,
    SUM(p.rush_pts)                                                         AS rush_pts,
    SUM(p.rec_pts)                                                          AS rec_pts,
    SUM(p.misc_pts)                                                         AS misc_pts,
    SUM(p.kick_pts)                                                         AS kick_pts,
    SUM(p.def_pts)                                                          AS def_pts,
    COUNT(DISTINCT p.week)                                                  AS games_played,
    SUM(p.total_pts) / NULLIF(COUNT(DISTINCT p.week), 0)                    AS pts_per_game,
    STDDEV_SAMP(p.total_pts)                                                AS stddev_pts,
    STDDEV_SAMP(p.total_pts)
        / NULLIF(SUM(p.total_pts) / NULLIF(COUNT(DISTINCT p.week), 0), 0)   AS cv_pts,
    PERCENTILE_CONT(0.20) WITHIN GROUP (ORDER BY p.total_pts)               AS floor_pts,
    PERCENTILE_CONT(0.80) WITHIN GROUP (ORDER BY p.total_pts)               AS ceiling_pts,
    MAX(p.total_pts)                                                        AS best_week_pts,
    MIN(p.total_pts)                                                        AS worst_week_pts,
    MAX_BY(p.week, p.total_pts)                                             AS best_week,
    MIN_BY(p.week, p.total_pts)                                             AS worst_week,
    COUNT_IF(p.total_pts >= p.threshold)                                    AS weeks_above_threshold,
    SUM(IFF(p.is_playoff, p.total_pts, 0))                                  AS playoff_pts,
    AVG(IFF(p.week > lw.max_week - 4, p.total_pts, NULL))                   AS last_4_pts_per_game,
    SUM(p.source_total_points)                                              AS source_total_points
FROM played p
JOIN last_week lw ON lw.season = p.season
GROUP BY p.season, p.player_id, p.pos, p.scoring_mode;
