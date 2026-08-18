-- 30_scoring.sql — SCORING_RULES (all three modes) and FCT_PLAYER_SCORING
-- (README §4, §5).
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season). The
-- identical rule set is mirrored in pipeline/scoring.py and was verified against
-- the real 2025 CSVs by pipeline/validate_scoring.py — keep the two in sync.

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA MARTS;

CREATE TABLE IF NOT EXISTS SCORING_RULES (
    scoring_mode        VARCHAR NOT NULL,   -- standard | half_ppr | full_ppr
    stat                VARCHAR NOT NULL,   -- matches STAGING.STG_PLAYER_WEEK.stat
    points_per_unit     FLOAT   NOT NULL,
    component           VARCHAR NOT NULL,   -- pass_pts | rush_pts | rec_pts | misc_pts | kick_pts | def_pts
    PRIMARY KEY (scoring_mode, stat)
);

-- Adding a league's custom rules is an INSERT, not a new query.
MERGE INTO SCORING_RULES t
USING (
    SELECT * FROM VALUES
        -- offensive: identical across modes except the reception
        ('standard','passing_yds',       0.04,'pass_pts'),
        ('half_ppr','passing_yds',       0.04,'pass_pts'),
        ('full_ppr','passing_yds',       0.04,'pass_pts'),
        ('standard','passing_td',        4,   'pass_pts'),
        ('half_ppr','passing_td',        4,   'pass_pts'),
        ('full_ppr','passing_td',        4,   'pass_pts'),
        ('standard','passing_int',      -2,   'pass_pts'),
        ('half_ppr','passing_int',      -2,   'pass_pts'),
        ('full_ppr','passing_int',      -2,   'pass_pts'),
        ('standard','rushing_yds',       0.1, 'rush_pts'),
        ('half_ppr','rushing_yds',       0.1, 'rush_pts'),
        ('full_ppr','rushing_yds',       0.1, 'rush_pts'),
        ('standard','rushing_td',        6,   'rush_pts'),
        ('half_ppr','rushing_td',        6,   'rush_pts'),
        ('full_ppr','rushing_td',        6,   'rush_pts'),
        ('standard','receiving_rec',     0.0, 'rec_pts'),   -- the only mode-dependent rule
        ('half_ppr','receiving_rec',     0.5, 'rec_pts'),
        ('full_ppr','receiving_rec',     1.0, 'rec_pts'),
        ('standard','receiving_yds',     0.1, 'rec_pts'),
        ('half_ppr','receiving_yds',     0.1, 'rec_pts'),
        ('full_ppr','receiving_yds',     0.1, 'rec_pts'),
        ('standard','receiving_td',      6,   'rec_pts'),
        ('half_ppr','receiving_td',      6,   'rec_pts'),
        ('full_ppr','receiving_td',      6,   'rec_pts'),
        ('standard','ret_td',            6,   'misc_pts'),
        ('half_ppr','ret_td',            6,   'misc_pts'),
        ('full_ppr','ret_td',            6,   'misc_pts'),
        ('standard','fum_td',            6,   'misc_pts'),
        ('half_ppr','fum_td',            6,   'misc_pts'),
        ('full_ppr','fum_td',            6,   'misc_pts'),
        ('standard','two_pt',            2,   'misc_pts'),
        ('half_ppr','two_pt',            2,   'misc_pts'),
        ('full_ppr','two_pt',            2,   'misc_pts'),
        ('standard','fum',              -2,   'misc_pts'),
        ('half_ppr','fum',              -2,   'misc_pts'),
        ('full_ppr','fum',              -2,   'misc_pts'),
        -- kicker: identical across modes; distance-tiered on purpose
        ('standard','pat_made',          1,   'kick_pts'),
        ('half_ppr','pat_made',          1,   'kick_pts'),
        ('full_ppr','pat_made',          1,   'kick_pts'),
        ('standard','pat_missed',       -1,   'kick_pts'),
        ('half_ppr','pat_missed',       -1,   'kick_pts'),
        ('full_ppr','pat_missed',       -1,   'kick_pts'),
        ('standard','fg_made_0_19',      3,   'kick_pts'),
        ('half_ppr','fg_made_0_19',      3,   'kick_pts'),
        ('full_ppr','fg_made_0_19',      3,   'kick_pts'),
        ('standard','fg_made_20_29',     3,   'kick_pts'),
        ('half_ppr','fg_made_20_29',     3,   'kick_pts'),
        ('full_ppr','fg_made_20_29',     3,   'kick_pts'),
        ('standard','fg_made_30_39',     3,   'kick_pts'),
        ('half_ppr','fg_made_30_39',     3,   'kick_pts'),
        ('full_ppr','fg_made_30_39',     3,   'kick_pts'),
        ('standard','fg_made_40_49',     4,   'kick_pts'),
        ('half_ppr','fg_made_40_49',     4,   'kick_pts'),
        ('full_ppr','fg_made_40_49',     4,   'kick_pts'),
        ('standard','fg_made_50',        5,   'kick_pts'),
        ('half_ppr','fg_made_50',        5,   'kick_pts'),
        ('full_ppr','fg_made_50',        5,   'kick_pts'),
        ('standard','fg_miss_0_19',     -1,   'kick_pts'),
        ('half_ppr','fg_miss_0_19',     -1,   'kick_pts'),
        ('full_ppr','fg_miss_0_19',     -1,   'kick_pts'),
        ('standard','fg_miss_20_29',    -1,   'kick_pts'),
        ('half_ppr','fg_miss_20_29',    -1,   'kick_pts'),
        ('full_ppr','fg_miss_20_29',    -1,   'kick_pts'),
        ('standard','fg_miss_30_39',    -1,   'kick_pts'),
        ('half_ppr','fg_miss_30_39',    -1,   'kick_pts'),
        ('full_ppr','fg_miss_30_39',    -1,   'kick_pts'),
        -- team defense: identical across modes. Tackles/TFL/PDef/QBHit are
        -- deliberately absent — see README §4.
        ('standard','tackles_sck',       1,   'def_pts'),
        ('half_ppr','tackles_sck',       1,   'def_pts'),
        ('full_ppr','tackles_sck',       1,   'def_pts'),
        ('standard','turnover_int',      2,   'def_pts'),
        ('half_ppr','turnover_int',      2,   'def_pts'),
        ('full_ppr','turnover_int',      2,   'def_pts'),
        ('standard','turnover_fum_rec',  2,   'def_pts'),
        ('half_ppr','turnover_fum_rec',  2,   'def_pts'),
        ('full_ppr','turnover_fum_rec',  2,   'def_pts'),
        ('standard','turnover_frc_fum',  1,   'def_pts'),
        ('half_ppr','turnover_frc_fum',  1,   'def_pts'),
        ('full_ppr','turnover_frc_fum',  1,   'def_pts'),
        ('standard','score_saf',         2,   'def_pts'),
        ('half_ppr','score_saf',         2,   'def_pts'),
        ('full_ppr','score_saf',         2,   'def_pts'),
        ('standard','score_int_td',      6,   'def_pts'),
        ('half_ppr','score_int_td',      6,   'def_pts'),
        ('full_ppr','score_int_td',      6,   'def_pts'),
        ('standard','score_fum_td',      6,   'def_pts'),
        ('half_ppr','score_fum_td',      6,   'def_pts'),
        ('full_ppr','score_fum_td',      6,   'def_pts'),
        ('standard','score_blk_td',      6,   'def_pts'),
        ('half_ppr','score_blk_td',      6,   'def_pts'),
        ('full_ppr','score_blk_td',      6,   'def_pts'),
        ('standard','blk',               2,   'def_pts'),
        ('half_ppr','blk',               2,   'def_pts'),
        ('full_ppr','blk',               2,   'def_pts'),
        ('standard','score_def_2pt_ret', 2,   'def_pts'),
        ('half_ppr','score_def_2pt_ret', 2,   'def_pts'),
        ('full_ppr','score_def_2pt_ret', 2,   'def_pts')
    AS v(scoring_mode, stat, points_per_unit, component)
) s
   ON t.scoring_mode = s.scoring_mode AND t.stat = s.stat
WHEN MATCHED THEN UPDATE SET points_per_unit = s.points_per_unit, component = s.component
WHEN NOT MATCHED THEN INSERT (scoring_mode, stat, points_per_unit, component)
     VALUES (s.scoring_mode, s.stat, s.points_per_unit, s.component);

-- Team-defense tiers (README §4, §5a, Phase 1.6). These are not per-unit rates
-- — 14 points allowed is not 14× the value of 1 — so they are a banded lookup
-- rather than SCORING_RULES rows: one bonus per team per week, awarded by which
-- band the game's outcome falls in. Bands are half-open [lo, hi) with hi NULL
-- meaning "and above", so no gap or overlap is possible.
-- Identical across all three modes, which is why scoring_mode is absent: PPR
-- changes what a reception is worth, not what a shutout is worth.
CREATE TABLE IF NOT EXISTS DEF_TIERS (
    metric      VARCHAR NOT NULL,   -- points_allowed | yards_allowed
    lower_bound NUMBER  NOT NULL,
    upper_bound NUMBER,             -- exclusive; NULL = unbounded
    points      FLOAT   NOT NULL,
    PRIMARY KEY (metric, lower_bound)
);

MERGE INTO DEF_TIERS t
USING (
    SELECT * FROM VALUES
        ('points_allowed',   0,    1,  10),   -- shutout
        ('points_allowed',   1,    7,   7),
        ('points_allowed',   7,   14,   4),
        ('points_allowed',  14,   21,   1),
        ('points_allowed',  21,   28,   0),
        ('points_allowed',  28,   35,  -1),
        ('points_allowed',  35, NULL,  -4),
        ('yards_allowed',    0,  100,   5),
        ('yards_allowed',  100,  200,   3),
        ('yards_allowed',  200,  300,   2),
        ('yards_allowed',  300,  350,   0),
        ('yards_allowed',  350,  400,  -1),
        ('yards_allowed',  400,  450,  -3),
        ('yards_allowed',  450, NULL,  -5)
    AS v(metric, lower_bound, upper_bound, points)
) s
   ON t.metric = s.metric AND t.lower_bound = s.lower_bound
WHEN MATCHED THEN UPDATE SET upper_bound = s.upper_bound, points = s.points
WHEN NOT MATCHED THEN INSERT (metric, lower_bound, upper_bound, points)
     VALUES (s.metric, s.lower_bound, s.upper_bound, s.points);

-- One row per (season, week, player_id, scoring_mode), with each component
-- broken out so a surprising ranking can be explained rather than trusted.
-- Built from the header cross-joined to the modes and LEFT JOINed to the stats,
-- so a player who suited up and produced nothing keeps a real 0.0 row instead
-- of disappearing (README §3: that is a real zero, not a missing week).
CREATE OR REPLACE TABLE FCT_PLAYER_SCORING AS
WITH modes AS (
    SELECT DISTINCT scoring_mode FROM SCORING_RULES
),
base AS (
    SELECT h.*, m.scoring_mode
    FROM STAGING.STG_PLAYER_WEEK_HEADER h
    CROSS JOIN modes m
)
SELECT
    b.season,
    b.week,
    b.is_playoff,
    b.player_id,
    b.player_name,
    b.pos,
    b.team,
    b.opponent,
    b.is_away,
    b.is_bye,
    b.scoring_mode,
    COALESCE(SUM(IFF(r.component = 'pass_pts', s.value * r.points_per_unit, 0)), 0) AS pass_pts,
    COALESCE(SUM(IFF(r.component = 'rush_pts', s.value * r.points_per_unit, 0)), 0) AS rush_pts,
    COALESCE(SUM(IFF(r.component = 'rec_pts',  s.value * r.points_per_unit, 0)), 0) AS rec_pts,
    COALESCE(SUM(IFF(r.component = 'misc_pts', s.value * r.points_per_unit, 0)), 0) AS misc_pts,
    COALESCE(SUM(IFF(r.component = 'kick_pts', s.value * r.points_per_unit, 0)), 0) AS kick_pts,
    COALESCE(SUM(IFF(r.component = 'def_pts',  s.value * r.points_per_unit, 0)), 0) AS def_pts,
    COALESCE(SUM(s.value * r.points_per_unit), 0)                                   AS total_pts,
    -- carried for reconciliation only; never an input to a ranking (README §2)
    b.source_total_points
FROM base b
LEFT JOIN STAGING.STG_PLAYER_WEEK s
       ON s.season = b.season AND s.week = b.week AND s.player_id = b.player_id
LEFT JOIN SCORING_RULES r
       ON r.stat = s.stat AND r.scoring_mode = b.scoring_mode
GROUP BY
    b.season, b.week, b.is_playoff, b.player_id, b.player_name, b.pos, b.team,
    b.opponent, b.is_away, b.is_bye, b.scoring_mode, b.source_total_points;
