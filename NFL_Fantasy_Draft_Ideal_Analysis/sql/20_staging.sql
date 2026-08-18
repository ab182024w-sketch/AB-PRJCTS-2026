-- 20_staging.sql — STG_PLAYER_WEEK: typed, cleaned, path-parsed, unioned onto
-- the common tall/EAV shape (README §3, §5).
-- NOT YET EXECUTED: authored without a Snowflake account.
--
-- Shape: (season, week, player_id, pos, team, opponent, is_away, is_bye,
--         is_playoff, stat, value). One row per stat per player per week.
-- The tall layout is what makes scoring a join against SCORING_RULES rather
-- than a wide expression repeated once per mode.

USE WAREHOUSE FANTASY_WH;
USE DATABASE FANTASY;
USE SCHEMA STAGING;

-- Team abbreviation normalization. hvpkod's 2025 files were checked and are
-- already clean (32 abbreviations + 'FA'), so this table is currently an
-- identity map; it exists because the Phase 1.6 nflverse join needs LA -> LAR,
-- and because older seasons are not guaranteed to be as tidy (README §5, §5a).
CREATE TABLE IF NOT EXISTS TEAM_ALIAS (
    raw_team    VARCHAR NOT NULL PRIMARY KEY,
    team        VARCHAR NOT NULL
);

MERGE INTO TEAM_ALIAS t
USING (
    SELECT * FROM VALUES
        ('JAC', 'JAX'), ('JAX', 'JAX'),
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

-- Header-level attributes, deduplicated on the true grain (season, week,
-- player_id), keeping the last loaded row (README §3).
CREATE OR REPLACE VIEW STG_PLAYER_WEEK_HEADER AS
WITH all_rows AS (
    SELECT player_name, player_id, pos, team, player_opponent,
           source_total_points, source_rank, source_file, source_file_row, loaded_at
    FROM RAW.OFFENSE_RAW
    UNION ALL
    SELECT player_name, player_id, pos, team, player_opponent,
           source_total_points, source_rank, source_file, source_file_row, loaded_at
    FROM RAW.K_RAW
    UNION ALL
    SELECT player_name, player_id, pos, team, player_opponent,
           source_total_points, source_rank, source_file, source_file_row, loaded_at
    FROM RAW.DEFENSE_RAW
),
parsed AS (
    SELECT
        -- season and week live ONLY in the file path (README §2). A NULL here
        -- would silently collapse the grain, so 99_tests.sql asserts on it.
        TRY_TO_NUMBER(REGEXP_SUBSTR(source_file, '([0-9]{4})/[0-9]{1,2}/[A-Z]{1,2}\\.csv', 1, 1, 'e', 1)) AS season,
        TRY_TO_NUMBER(REGEXP_SUBSTR(source_file, '[0-9]{4}/([0-9]{1,2})/[A-Z]{1,2}\\.csv', 1, 1, 'e', 1)) AS week,
        TRIM(player_id)                                     AS player_id,
        TRIM(player_name)                                   AS player_name,
        UPPER(TRIM(pos))                                    AS pos,
        COALESCE(a.team, UPPER(TRIM(r.team)))               AS team,
        UPPER(TRIM(r.player_opponent))  = 'BYE'             AS is_bye,
        STARTSWITH(TRIM(r.player_opponent), '@')            AS is_away,
        CASE
            WHEN UPPER(TRIM(r.player_opponent)) = 'BYE' THEN NULL
            ELSE COALESCE(o.team, UPPER(LTRIM(TRIM(r.player_opponent), '@')))
        END                                                 AS opponent,
        TRY_TO_DOUBLE(r.source_total_points)                AS source_total_points,
        TRY_TO_NUMBER(r.source_rank)                        AS source_rank,
        r.source_file,
        r.source_file_row,
        r.loaded_at
    FROM all_rows r
    LEFT JOIN TEAM_ALIAS a ON a.raw_team = UPPER(TRIM(r.team))
    LEFT JOIN TEAM_ALIAS o ON o.raw_team = UPPER(LTRIM(TRIM(r.player_opponent), '@'))
)
SELECT
    season,
    week,
    week BETWEEN 15 AND 18 AS is_playoff,   -- flagged, never excluded (README §3)
    player_id,
    player_name,
    pos,
    team,
    opponent,
    is_away,
    is_bye,
    source_total_points,
    source_rank,
    source_file,
    source_file_row,
    loaded_at
FROM parsed
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY season, week, player_id
    ORDER BY loaded_at DESC, source_file_row DESC
) = 1;

-- The tall fact. OBJECT_CONSTRUCT drops NULL entries, so blank cells simply do
-- not produce a row — which is the same thing as zero for a SUM, and keeps the
-- table roughly a third smaller. `value_text` is carried so 99_tests.sql can
-- count cast failures: a NULL `value` next to a non-NULL `value_text` is a
-- parse bug, and is the only thing separating that from "no production"
-- (README §2 note 4, §5).
CREATE OR REPLACE TABLE STG_PLAYER_WEEK AS
WITH offense AS (
    SELECT
        source_file, source_file_row, player_id, loaded_at,
        OBJECT_CONSTRUCT(
            'passing_yds', passing_yds, 'passing_td', passing_td, 'passing_int', passing_int,
            'rushing_yds', rushing_yds, 'rushing_td', rushing_td,
            'receiving_rec', receiving_rec, 'receiving_yds', receiving_yds, 'receiving_td', receiving_td,
            'ret_td', ret_td, 'fum_td', fum_td, 'two_pt', two_pt, 'fum', fum,
            'fan_pts_against_pts', fan_pts_against_pts,
            'touch_carries', touch_carries, 'touch_receptions', touch_receptions, 'touches', touches,
            'targets_receptions', targets_receptions, 'targets', targets,
            'reception_percentage', reception_percentage,
            'rz_target', rz_target, 'rz_touch', rz_touch, 'rz_g2g', rz_g2g
        ) AS stats
    FROM RAW.OFFENSE_RAW
),
kicker AS (
    SELECT
        source_file, source_file_row, player_id, loaded_at,
        OBJECT_CONSTRUCT(
            'pat_made', pat_made, 'pat_missed', pat_missed,
            'fg_made_0_19', fg_made_0_19, 'fg_made_20_29', fg_made_20_29,
            'fg_made_30_39', fg_made_30_39, 'fg_made_40_49', fg_made_40_49,
            'fg_made_50', fg_made_50,
            'fg_miss_0_19', fg_miss_0_19, 'fg_miss_20_29', fg_miss_20_29,
            'fg_miss_30_39', fg_miss_30_39
        ) AS stats
    FROM RAW.K_RAW
),
defense AS (
    SELECT
        source_file, source_file_row, player_id, loaded_at,
        OBJECT_CONSTRUCT(
            'tackles_sck', tackles_sck, 'turnover_int', turnover_int,
            'turnover_frc_fum', turnover_frc_fum, 'turnover_fum_rec', turnover_fum_rec,
            'score_int_td', score_int_td, 'score_fum_td', score_fum_td,
            'score_blk_td', score_blk_td, 'score_saf', score_saf,
            'score_def_2pt_ret', score_def_2pt_ret, 'blk', blk,
            -- loaded but deliberately unscored (README §4)
            'tackles_tot', tackles_tot, 'tackles_ast', tackles_ast, 'tackles_tfl', tackles_tfl,
            'pdef', pdef, 'qb_hit', qb_hit,
            'return_int_yds', return_int_yds, 'return_fum_yds', return_fum_yds
        ) AS stats
    FROM RAW.DEFENSE_RAW
),
unioned AS (
    SELECT * FROM offense
    UNION ALL SELECT * FROM kicker
    UNION ALL SELECT * FROM defense
)
SELECT
    h.season,
    h.week,
    h.is_playoff,
    h.player_id,
    h.player_name,
    h.pos,
    h.team,
    h.opponent,
    h.is_away,
    h.is_bye,
    f.key::VARCHAR                  AS stat,
    f.value::VARCHAR                AS value_text,
    TRY_TO_DOUBLE(f.value::VARCHAR) AS value,
    h.source_total_points,
    h.source_file
FROM unioned u
JOIN STG_PLAYER_WEEK_HEADER h
  ON h.source_file = u.source_file
 AND h.source_file_row = u.source_file_row
 AND h.player_id = u.player_id,
LATERAL FLATTEN(input => u.stats) f;
