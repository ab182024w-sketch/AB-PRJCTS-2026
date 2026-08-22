-- 60_waiver.sql — Phase 3 waiver-wire layer (README §7, §9).
--
--   scraper (waiver scrape)  ->  RAW.WAIVER_TREND_RAW / RAW.WAIVER_PLAYERS_RAW
--   crosswalk + typing       ->  STAGING.STG_WAIVER_*, STAGING.WAIVER_XWALK
--   dashboard reads          ->  MARTS.WAIVER_TREND, MARTS.WAIVER_TARGETS
--
-- The RAW tables are append-only on purpose: week-over-week *movement* is the
-- waiver signal, and it only exists if every scrape's rows survive, stamped
-- with their run's scraped_at. Nothing below ever deletes from RAW; the
-- staging/mart objects are rebuilt from the full history on every run, so the
-- file is idempotent without touching the snapshots.
--
-- What the source provides: Sleeper publishes add/drop counts (how many of its
-- leagues added or dropped a player in a lookback window), not roster
-- percentage — no keyless source whose terms permit automated access carries
-- roster percentage (README §12). Columns are therefore counts, and "low
-- roster percentage" in the targets view becomes "currently being added",
-- which is the same availability signal read from the other side: a player
-- with a large add count is by definition available in many leagues.

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA RAW;

SET target_season = COALESCE(GETVARIABLE('TARGET_SEASON')::NUMBER, 2025);

-- ==================================================================== RAW ===
-- IF NOT EXISTS, never REPLACE: replacing would destroy the history that the
-- movement metrics are built from.
CREATE TABLE IF NOT EXISTS WAIVER_TREND_RAW (
    scraped_at          TIMESTAMP_NTZ   NOT NULL,   -- one stamp per scrape run (UTC)
    source              VARCHAR         NOT NULL,   -- 'sleeper'
    kind                VARCHAR         NOT NULL,   -- 'add' | 'drop'
    lookback_hours      NUMBER          NOT NULL,
    external_player_id  VARCHAR         NOT NULL,   -- the source's id, not hvpkod's
    trend_count         NUMBER          NOT NULL,
    loaded_at           TIMESTAMP_NTZ
);

-- The identity half of each scrape: name/team/position for every id the
-- trending feeds referenced. Also append-only — a player's team at scrape
-- time matters for the crosswalk, and teams change.
CREATE TABLE IF NOT EXISTS WAIVER_PLAYERS_RAW (
    scraped_at          TIMESTAMP_NTZ   NOT NULL,
    source              VARCHAR         NOT NULL,
    external_player_id  VARCHAR         NOT NULL,
    full_name           VARCHAR         NOT NULL,
    team                VARCHAR,                    -- NULL for free agents
    position            VARCHAR,                    -- already grouped to hvpkod's eight + DEF
    active              BOOLEAN,
    loaded_at           TIMESTAMP_NTZ
);

-- ================================================================ STAGING ===
USE SCHEMA STAGING;

-- Latest identity per external id. A view: the raw table is small and the
-- freshest team/position should win without a rebuild step.
CREATE OR REPLACE VIEW STG_WAIVER_PLAYER AS
SELECT
    source,
    external_player_id,
    full_name,
    -- Same alias table both feeds use, so 'OAK'-style historical codes cannot
    -- diverge from the player feed's abbreviations.
    COALESCE(a.team, UPPER(TRIM(p.team)))   AS team,
    position                                AS pos,
    active,
    scraped_at                              AS directory_scraped_at
FROM RAW.WAIVER_PLAYERS_RAW p
LEFT JOIN TEAM_ALIAS a ON a.raw_team = UPPER(TRIM(p.team))
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY source, external_player_id
    ORDER BY scraped_at DESC
) = 1;

-- One row per scrape per player, add and drop pivoted side by side. MAX is
-- safe because (scraped_at, source, kind, external_player_id) is the raw
-- grain — 99_tests.sql asserts that.
CREATE OR REPLACE VIEW STG_WAIVER_TREND AS
SELECT
    source,
    scraped_at,
    external_player_id,
    MAX(lookback_hours)                                   AS lookback_hours,
    COALESCE(MAX(IFF(kind = 'add',  trend_count, NULL)), 0) AS adds,
    COALESCE(MAX(IFF(kind = 'drop', trend_count, NULL)), 0) AS drops
FROM RAW.WAIVER_TREND_RAW
GROUP BY source, scraped_at, external_player_id;

-- ------------------------------------------------------------- CROSSWALK ---
-- Sleeper's ids share nothing with hvpkod's PlayerId, so identity is resolved
-- by normalized name + position, with team as the tie-breaker. Normalization
-- strips punctuation and generational suffixes because the two sources
-- disagree on exactly those ("D.J. Moore" / "DJ Moore", "Odell Beckham Jr.").
-- Every outcome is kept and labelled — ambiguous and unmatched rows surface in
-- the marts and the dashboard rather than disappearing (the task's rule:
-- report, do not silently drop).
CREATE OR REPLACE TABLE WAIVER_XWALK AS
WITH hv AS (
    -- One identity row per hvpkod player, on the team they were last seen with
    -- in the target season. Scoped to the target season because names recur
    -- across seasons and the marts the crosswalk feeds are season-filtered
    -- anyway.
    SELECT player_id, player_name, pos, team,
           UPPER(REGEXP_REPLACE(
               REGEXP_REPLACE(UPPER(player_name), '\\s+(JR|SR|II|III|IV|V)\\.?$', ''),
               '[^A-Z0-9]', ''
           )) AS norm_name
    FROM (
        SELECT player_id,
               MAX_BY(player_name, week) AS player_name,
               pos,
               MAX_BY(team, week)        AS team
        FROM STG_PLAYER_WEEK_HEADER
        WHERE season = $target_season
        GROUP BY player_id, pos
    )
),
sl AS (
    SELECT source, external_player_id, full_name, team, pos,
           UPPER(REGEXP_REPLACE(
               REGEXP_REPLACE(UPPER(full_name), '\\s+(JR|SR|II|III|IV|V)\\.?$', ''),
               '[^A-Z0-9]', ''
           )) AS norm_name
    FROM STG_WAIVER_PLAYER
),
by_name_pos AS (
    SELECT
        sl.source,
        sl.external_player_id,
        COUNT(hv.player_id)                                        AS candidates,
        -- With several same-name same-position players, the one on the same
        -- team is the match; MAX_BY's boolean puts it first.
        MAX_BY(hv.player_id,   IFF(hv.team = sl.team, 1, 0))       AS player_id,
        MAX_BY(hv.player_name, IFF(hv.team = sl.team, 1, 0))       AS hv_player_name,
        MAX(IFF(hv.team = sl.team, 1, 0))                          AS team_agrees
    FROM sl
    LEFT JOIN hv
           ON hv.norm_name = sl.norm_name
          AND hv.pos = sl.pos
    GROUP BY sl.source, sl.external_player_id
)
SELECT
    b.source,
    b.external_player_id,
    IFF(b.candidates = 0, NULL, b.player_id)          AS player_id,
    b.hv_player_name,
    CASE
        WHEN b.candidates = 0                        THEN 'unmatched'
        WHEN b.candidates = 1                        THEN 'matched'
        WHEN b.candidates > 1 AND b.team_agrees = 1  THEN 'matched_by_team'
        ELSE 'ambiguous'    -- several candidates, none on the scraped team
    END                                               AS match_status,
    b.candidates
FROM by_name_pos b;

-- ================================================================== MARTS ===
USE SCHEMA MARTS;

-- Full history, one row per scrape per player — the trend chart's table.
-- Season-agnostic: a scrape is a statement about *now*, and the app joins it
-- to whichever season is being viewed.
CREATE OR REPLACE TABLE WAIVER_TREND AS
SELECT
    t.source,
    t.scraped_at,
    t.lookback_hours,
    t.external_player_id,
    x.player_id,
    COALESCE(p.full_name, t.external_player_id) AS player_name,
    p.team,
    p.pos,
    t.adds,
    t.drops,
    t.adds - t.drops                            AS net_adds,
    COALESCE(x.match_status, 'unmatched')       AS match_status
FROM STAGING.STG_WAIVER_TREND t
LEFT JOIN STAGING.STG_WAIVER_PLAYER p
       ON p.source = t.source AND p.external_player_id = t.external_player_id
LEFT JOIN STAGING.WAIVER_XWALK x
       ON x.source = t.source AND x.external_player_id = t.external_player_id;

-- The board: the latest scrape joined to season production, all three scoring
-- modes side by side so the app's mode toggle stays a filter. LEFT joins
-- everywhere — a trending player with no season stats (rookie, IDP the
-- rankings do not carry, team defense) still appears, with NULL production and
-- a match_status that says why. delta_adds compares against the previous
-- scrape of the same source and lookback so "risers" is movement, not level.
--
-- next_opponent is NULL for now, deliberately: RAW.TEAM_GAME_RAW keeps played
-- games only (a schedule release with NULL scores is filtered at load,
-- 15_team_results.sql), so there is no unplayed-schedule row to read an
-- upcoming opponent from — and the working season is complete anyway. The
-- column keeps its place in the contract so the app does not change when a
-- 2026 schedule feed lands.
CREATE OR REPLACE TABLE WAIVER_TARGETS AS
WITH latest AS (
    SELECT *
    FROM STAGING.STG_WAIVER_TREND
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY source, external_player_id
        ORDER BY scraped_at DESC
    ) = 1
),
previous AS (
    SELECT source, external_player_id, scraped_at, adds, drops
    FROM STAGING.STG_WAIVER_TREND
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY source, external_player_id
        ORDER BY scraped_at DESC
    ) = 2
)
SELECT
    l.source,
    l.scraped_at,
    a.season,
    a.scoring_mode,
    l.external_player_id,
    x.player_id,
    COALESCE(a.player_name, p.full_name, l.external_player_id) AS player_name,
    COALESCE(a.pos, p.pos)                       AS pos,
    COALESCE(a.team, p.team)                     AS team,
    l.adds,
    l.drops,
    l.adds - l.drops                             AS net_adds,
    prev.scraped_at                              AS prev_scraped_at,
    l.adds  - prev.adds                          AS delta_adds,
    l.drops - prev.drops                         AS delta_drops,
    a.total_pts,
    a.pts_per_game,
    a.games_played,
    a.playoff_pts,
    a.last_4_pts_per_game,
    CAST(NULL AS VARCHAR)                        AS next_opponent,
    COALESCE(x.match_status, 'unmatched')        AS match_status
FROM latest l
LEFT JOIN STAGING.STG_WAIVER_PLAYER p
       ON p.source = l.source AND p.external_player_id = l.external_player_id
LEFT JOIN STAGING.WAIVER_XWALK x
       ON x.source = l.source AND x.external_player_id = l.external_player_id
LEFT JOIN previous prev
       ON prev.source = l.source AND prev.external_player_id = l.external_player_id
LEFT JOIN AGG_PLAYER_SEASON a
       ON a.player_id = x.player_id;
