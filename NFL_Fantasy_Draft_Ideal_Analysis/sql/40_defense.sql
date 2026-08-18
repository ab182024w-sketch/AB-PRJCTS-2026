-- 40_defense.sql — FCT_TEAM_DEFENSE: DB+LB+DL rolled up to the team, per week
-- then per season (README §4, §5, §5a).
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season).
--
-- Three components, kept separate so a ranking can be explained:
--   idp_pts             playmaking from the hvpkod IDP files (sacks, turnovers,
--                       defensive scores) — Phase 1
--   points_allowed_pts  banded bonus from the game's final score  — Phase 1.6
--   yards_allowed_pts   banded bonus from the opponent's net yards — Phase 1.6
-- Tackles/TFL/PDef/QBHit are excluded on purpose — tackle volume correlates
-- with a defense being on the field, i.e. with being bad (README §4).

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA MARTS;

CREATE OR REPLACE TABLE FCT_TEAM_DEFENSE_WEEK AS
WITH idp AS (
    SELECT
        season,
        week,
        is_playoff,
        team,
        scoring_mode,
        SUM(def_pts)                    AS idp_pts,
        COUNT(DISTINCT player_id)       AS defenders,
        COUNT_IF(NOT is_bye)            AS active_defender_rows
    FROM FCT_PLAYER_SCORING
    WHERE pos IN ('DB', 'LB', 'DL')
      AND team <> 'FA'      -- unsigned players carry no team attribution (README §3)
      AND NOT is_bye
    GROUP BY season, week, is_playoff, team, scoring_mode
),
-- Team results are scored once per team-week and then attached to all three
-- modes: the tiers do not vary by mode (README §5a).
team_week AS (
    SELECT
        w.season,
        w.week,
        w.team,
        w.points_allowed,
        w.yards_allowed,
        pt.points   AS points_allowed_pts,
        yt.points   AS yards_allowed_pts
    FROM STAGING.STG_TEAM_WEEK w
    LEFT JOIN DEF_TIERS pt
           ON pt.metric = 'points_allowed'
          AND w.points_allowed >= pt.lower_bound
          AND (pt.upper_bound IS NULL OR w.points_allowed < pt.upper_bound)
    LEFT JOIN DEF_TIERS yt
           ON yt.metric = 'yards_allowed'
          AND w.yards_allowed >= yt.lower_bound
          AND (yt.upper_bound IS NULL OR w.yards_allowed < yt.upper_bound)
    -- The fantasy season is weeks 1-18; nflverse carries the real postseason
    -- through week 22, which has no counterpart in the player feed (README §2).
    WHERE w.season_type = 'REG'
)
SELECT
    i.season,
    i.week,
    i.is_playoff,
    i.team,
    i.scoring_mode,
    i.idp_pts,
    -- A missing team-week row must not quietly become a bonus, and must not
    -- wipe out the IDP points either: COALESCE to 0 and let 99_tests.sql assert
    -- that the join covers every team-week it should.
    COALESCE(t.points_allowed_pts, 0)                               AS points_allowed_pts,
    COALESCE(t.yards_allowed_pts, 0)                                AS yards_allowed_pts,
    i.idp_pts
        + COALESCE(t.points_allowed_pts, 0)
        + COALESCE(t.yards_allowed_pts, 0)                          AS total_pts,
    t.points_allowed,
    t.yards_allowed,
    t.team IS NOT NULL                                              AS has_team_result,
    i.defenders,
    i.active_defender_rows
FROM idp i
LEFT JOIN team_week t
       ON t.season = i.season AND t.week = i.week AND t.team = i.team;

CREATE OR REPLACE TABLE FCT_TEAM_DEFENSE AS
SELECT
    season,
    team,
    scoring_mode,
    SUM(total_pts)                                          AS total_pts,
    SUM(idp_pts)                                            AS idp_pts,
    SUM(points_allowed_pts)                                 AS points_allowed_pts,
    SUM(yards_allowed_pts)                                  AS yards_allowed_pts,
    COUNT(DISTINCT week)                                    AS weeks_played,
    COUNT_IF(has_team_result)                               AS weeks_with_team_result,
    SUM(total_pts) / NULLIF(COUNT(DISTINCT week), 0)        AS pts_per_week,
    AVG(points_allowed)                                     AS avg_points_allowed,
    AVG(yards_allowed)                                      AS avg_yards_allowed,
    STDDEV_SAMP(total_pts)                                  AS stddev_pts,
    MAX(total_pts)                                          AS best_week_pts,
    MIN(total_pts)                                          AS worst_week_pts,
    SUM(IFF(is_playoff, total_pts, 0))                      AS playoff_pts
FROM FCT_TEAM_DEFENSE_WEEK
GROUP BY season, team, scoring_mode;
