# Phase 1 — first live Snowflake run (2025 season)

Every `sql/*.sql` file was executed in order against a real Snowflake account,
statement by statement, on the 2025 season. This file records what the run
produced and what had to be fixed to get there.

## Sequence

```
python -m pipeline.download --season 2025 --weeks 1-18 --put-to-stage --stage '@RAW.FANTASY_STAGE'
sql/00_setup.sql  10_raw.sql  20_staging.sql  30_scoring.sql
sql/35_season_agg.sql  40_defense.sql  50_ideal_team.sql  99_tests.sql
```

`PUT` staged 152 gzipped CSVs under `@RAW.FANTASY_STAGE/2025/<week>/`.

## Row counts

| Object | Rows |
| --- | --- |
| `RAW.OFFENSE_RAW` | 18,575 |
| `RAW.K_RAW` | 1,035 |
| `RAW.DEFENSE_RAW` | 27,422 |
| `STAGING.STG_PLAYER_WEEK` | 76,359 |
| `MARTS.FCT_PLAYER_SCORING` | 141,096 |
| `MARTS.IDEAL_TEAM` | 273 (91 slots × 3 scoring modes) |

`VALIDATE(..., JOB_ID => '_last')` returned no rejected rows for any of the three
weekly loads.

## Data-quality results (`STAGING.TEST_RESULTS`)

All nine error-level checks returned 0 failures: `season_week_parsed`,
`grain_unique`, `key_columns_valid`, `all_positions_present_every_week`,
`no_cast_failures`, `games_played_in_range`, `no_impossible_values`,
`team_abbreviations_known`, `bye_rows_present`.

The two warn-level reconciliations fired as designed, at the magnitudes the
harness predicted: `reconcile_season_files` (1) and
`reconcile_total_points_half_ppr` (1,139 player-weeks). Both are properties of
the source, documented in README §12.

## Agreement with the local harness

All 273 `IDEAL_TEAM` rows match `reference/ideal_team_2025_*.csv` exactly on
player, team, points and games played, across all three scoring modes.

## Fixes the live run forced

1. **`COPY INTO` loaded 0 files.** The `PATTERN` values were anchored as
   `.*/[0-9]{4}/…`, which requires a directory *above* the season. Stage-relative
   paths start at the season (`2025/1/QB.csv.gz`), so nothing matched. Changed to
   `.*[0-9]{4}/…`.
2. **`DEFENSE_RAW` column count.** The IDP files have 24 columns, not 23, so the
   select list was one short of the table (`$24` and, for the season files,
   `$23`).
3. **`VALIDATE(..., '_last')` resolved to the wrong job.** `_last` means the last
   `COPY` in the *session*, so the three calls grouped at the end of the file all
   pointed at the final load. Each call now sits immediately after its own `COPY`.
4. **Season-file loads were not re-runnable.** Only the three weekly tables were
   truncated, so a second run appended duplicate season rows.

The harness had a matching bug the comparison exposed: team-defense `weeks`
counted bye rows, giving 18 games instead of 17. Tie-breaks in both boards are
now explicit (`player_id`, and `team` for defenses) so the ordering is stable.
