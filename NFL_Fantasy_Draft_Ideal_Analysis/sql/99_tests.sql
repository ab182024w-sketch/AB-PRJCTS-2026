-- 99_tests.sql — the README §6 data-quality assertions.
-- Executed against Snowflake on the 2025 season: all nine error-level checks
-- return 0 failures, and the two warn-level reconciliations fire as expected
-- (the source's own inconsistencies, quantified in README §12).
--
-- Every check writes one row into TEST_RESULTS. A failure blocks promotion to
-- MARTS; the two reconciliations are warn-level because the *source itself* is
-- internally inconsistent in known, measured ways.

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA STAGING;

CREATE OR REPLACE TABLE TEST_RESULTS (
    run_at          TIMESTAMP_NTZ,
    check_name      VARCHAR,
    severity        VARCHAR,   -- error | warn
    failures        NUMBER,
    detail          VARCHAR
);

INSERT INTO TEST_RESULTS (run_at, check_name, severity, failures, detail)

-- season/week parsed from every filename, week within 1-18
SELECT CURRENT_TIMESTAMP(), 'season_week_parsed', 'error', COUNT(*),
       'rows whose filename did not yield a season/week in 1-18'
FROM STG_PLAYER_WEEK_HEADER
WHERE season IS NULL OR week IS NULL OR week NOT BETWEEN 1 AND 18

UNION ALL
-- (season, week, player_id) is unique
SELECT CURRENT_TIMESTAMP(), 'grain_unique', 'error', COUNT(*),
       'duplicate (season, week, player_id) keys'
FROM (
    SELECT season, week, player_id
    FROM STG_PLAYER_WEEK_HEADER
    GROUP BY season, week, player_id
    HAVING COUNT(*) > 1
)

UNION ALL
-- player_id / pos never null, pos in the known eight
SELECT CURRENT_TIMESTAMP(), 'key_columns_valid', 'error', COUNT(*),
       'null player_id/pos or unknown pos'
FROM STG_PLAYER_WEEK_HEADER
WHERE player_id IS NULL OR pos IS NULL
   OR pos NOT IN ('QB', 'RB', 'WR', 'TE', 'K', 'DB', 'LB', 'DL')

UNION ALL
-- every (season, week) has all eight position files
SELECT CURRENT_TIMESTAMP(), 'all_positions_present_every_week', 'error', COUNT(*),
       'season/week combinations missing at least one position file'
FROM (
    SELECT season, week
    FROM STG_PLAYER_WEEK_HEADER
    GROUP BY season, week
    HAVING COUNT(DISTINCT pos) < 8
)

UNION ALL
-- cast failures: a non-blank cell that did not parse. Blank means zero, so this
-- count is the ONLY thing distinguishing "no production" from a parse bug.
-- Measured locally on 2025: 0.
SELECT CURRENT_TIMESTAMP(), 'no_cast_failures', 'error', COUNT(*),
       'non-blank stat cells that failed TRY_TO_DOUBLE'
FROM STG_PLAYER_WEEK
WHERE value_text IS NOT NULL AND value IS NULL

UNION ALL
-- games_played between 0 and 18 once bye rows are excluded
SELECT CURRENT_TIMESTAMP(), 'games_played_in_range', 'error', COUNT(*),
       'players with games_played outside 0-18'
FROM (
    SELECT player_id, COUNT(DISTINCT week) AS games_played
    FROM STG_PLAYER_WEEK_HEADER
    WHERE NOT is_bye
    GROUP BY season, player_id
    HAVING games_played > 18
)

UNION ALL
-- impossible values
SELECT CURRENT_TIMESTAMP(), 'no_impossible_values', 'error', COUNT(*),
       'implausible stat values (see predicate)'
FROM STG_PLAYER_WEEK
WHERE (stat = 'passing_yds'   AND value > 700)
   OR (stat = 'tackles_sck'   AND MOD(value * 2, 1) <> 0)   -- sacks come in halves
   OR (stat IN ('receiving_rec', 'targets', 'touches', 'pat_made') AND value < 0)
   OR (stat LIKE 'fg_made_%'  AND value < 0)

UNION ALL
-- every team and opponent resolves to a known abbreviation (FA / Bye allowed)
SELECT CURRENT_TIMESTAMP(), 'team_abbreviations_known', 'error', COUNT(*),
       'unmapped team or opponent abbreviations'
FROM STG_PLAYER_WEEK_HEADER h
WHERE (h.team     IS NOT NULL AND h.team     NOT IN (SELECT team FROM TEAM_ALIAS))
   OR (h.opponent IS NOT NULL AND h.opponent NOT IN (SELECT team FROM TEAM_ALIAS))

UNION ALL
-- bye rows must exist; their absence means the bye marker changed shape
SELECT CURRENT_TIMESTAMP(), 'bye_rows_present', 'error',
       IFF(COUNT_IF(is_bye) = 0, 1, 0),
       'no PlayerOpponent = Bye rows found at all'
FROM STG_PLAYER_WEEK_HEADER

UNION ALL
-- ---------------------------------------------------------------------------
-- Reconciliation 1 — against the source's own TotalPoints (README §6).
-- Local finding, and the reason this is warn-level and week-filtered:
--   * The source's implied ruleset is ~half-PPR: computed half_ppr points match
--     TotalPoints exactly for ~94% of offensive player-weeks.
--   * Weeks where the source published stats but left TotalPoints at 0 (all of
--     2025 week 18) are excluded — otherwise the check fails on source data.
--   * Defensive rows diverge by design: the source scores tackles, we do not.
SELECT CURRENT_TIMESTAMP(), 'reconcile_total_points_half_ppr', 'warn', COUNT(*),
       'offensive/kicker player-weeks where half_ppr points differ from source TotalPoints by > 0.1'
FROM MARTS.FCT_PLAYER_SCORING f
WHERE f.scoring_mode = 'half_ppr'
  AND f.pos IN ('QB', 'RB', 'WR', 'TE', 'K')
  AND f.source_total_points IS NOT NULL
  AND f.week NOT IN (
        SELECT week FROM MARTS.FCT_PLAYER_SCORING
        GROUP BY season, week
        HAVING SUM(COALESCE(source_total_points, 0)) = 0
      )
  AND ABS(f.total_pts - f.source_total_points) > 0.1

UNION ALL
-- ---------------------------------------------------------------------------
-- Reconciliation 2 — our per-week sums against {POS}_season.csv (README §6).
-- Local finding: the source's own season file is NOT an exact sum of its weekly
-- files (≈39 of 141 position/stat pairs differ, always by tiny amounts, mostly
-- in tackle and target counts). So this is a warn with a tolerance, not the
-- stat-for-stat equality the README assumed. Scoring-relevant stats are the
-- ones that matter; a large diff there is a real ingestion bug.
SELECT CURRENT_TIMESTAMP(), 'reconcile_season_files', 'warn', COUNT(*),
       'player/stat pairs where our weekly sum differs from {POS}_season.csv by > 1 unit'
FROM (
    SELECT
        w.player_id, w.stat,
        SUM(w.value)                    AS ours,
        ANY_VALUE(s.season_value)       AS theirs
    FROM STG_PLAYER_WEEK w
    JOIN (
        SELECT player_id, 'passing_yds' AS stat, TRY_TO_DOUBLE(passing_yds) AS season_value FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'passing_td',    TRY_TO_DOUBLE(passing_td)    FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'passing_int',   TRY_TO_DOUBLE(passing_int)   FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'rushing_yds',   TRY_TO_DOUBLE(rushing_yds)   FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'rushing_td',    TRY_TO_DOUBLE(rushing_td)    FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'receiving_rec', TRY_TO_DOUBLE(receiving_rec) FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'receiving_yds', TRY_TO_DOUBLE(receiving_yds) FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'receiving_td',  TRY_TO_DOUBLE(receiving_td)  FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'fum',           TRY_TO_DOUBLE(fum)           FROM RAW.OFFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'pat_made',      TRY_TO_DOUBLE(pat_made)      FROM RAW.K_SEASON_RAW
        UNION ALL SELECT player_id, 'fg_made_40_49', TRY_TO_DOUBLE(fg_made_40_49) FROM RAW.K_SEASON_RAW
        UNION ALL SELECT player_id, 'fg_made_50',    TRY_TO_DOUBLE(fg_made_50)    FROM RAW.K_SEASON_RAW
        UNION ALL SELECT player_id, 'tackles_sck',   TRY_TO_DOUBLE(tackles_sck)   FROM RAW.DEFENSE_SEASON_RAW
        UNION ALL SELECT player_id, 'turnover_int',  TRY_TO_DOUBLE(turnover_int)  FROM RAW.DEFENSE_SEASON_RAW
    ) s
      ON s.player_id = w.player_id AND s.stat = w.stat
    GROUP BY w.player_id, w.stat
    HAVING ABS(SUM(w.value) - ANY_VALUE(s.season_value)) > 1
);

-- Promotion gate.
SELECT check_name, severity, failures, detail
FROM TEST_RESULTS
WHERE run_at = (SELECT MAX(run_at) FROM TEST_RESULTS)
ORDER BY IFF(severity = 'error', 0, 1), failures DESC;

-- Fails the script when any error-level check found rows.
SELECT
    CASE
        WHEN SUM(IFF(severity = 'error', failures, 0)) > 0
        THEN 1 / 0   -- deliberate divide-by-zero: aborts the run
        ELSE 0
    END AS error_level_failures
FROM TEST_RESULTS
WHERE run_at = (SELECT MAX(run_at) FROM TEST_RESULTS);
