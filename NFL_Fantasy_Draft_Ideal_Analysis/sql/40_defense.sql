-- 40_defense.sql — FCT_TEAM_DEFENSE: DB+LB+DL rolled up to the team, per week
-- then per season (README §4, §5).
-- Executed end-to-end against Snowflake (FANTASY database, 2025 season).
--
-- Limitation stated up front: points allowed and yards allowed are team-game
-- outcomes and are absent from a player-stat source, so this ranks defensive
-- *playmaking*. The tiers arrive in Phase 1.6 from nflverse (README §5a).
-- Tackles/TFL/PDef/QBHit are excluded on purpose — tackle volume correlates
-- with a defense being on the field, i.e. with being bad (README §4).

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA MARTS;

CREATE OR REPLACE TABLE FCT_TEAM_DEFENSE_WEEK AS
SELECT
    season,
    week,
    is_playoff,
    team,
    scoring_mode,
    SUM(def_pts)                    AS total_pts,
    COUNT(DISTINCT player_id)       AS defenders,
    COUNT_IF(NOT is_bye)            AS active_defender_rows
FROM FCT_PLAYER_SCORING
WHERE pos IN ('DB', 'LB', 'DL')
  AND team <> 'FA'          -- unsigned players carry no team attribution (README §3)
  AND NOT is_bye
GROUP BY season, week, is_playoff, team, scoring_mode;

CREATE OR REPLACE TABLE FCT_TEAM_DEFENSE AS
SELECT
    season,
    team,
    scoring_mode,
    SUM(total_pts)                                          AS total_pts,
    COUNT(DISTINCT week)                                    AS weeks_played,
    SUM(total_pts) / NULLIF(COUNT(DISTINCT week), 0)        AS pts_per_week,
    STDDEV_SAMP(total_pts)                                  AS stddev_pts,
    MAX(total_pts)                                          AS best_week_pts,
    MIN(total_pts)                                          AS worst_week_pts,
    SUM(IFF(is_playoff, total_pts, 0))                      AS playoff_pts
FROM FCT_TEAM_DEFENSE_WEEK
GROUP BY season, team, scoring_mode;
