# Phases 1.5 + 1.6 — live Snowflake run

Phase 1.5 (any season, re-runnable) and Phase 1.6 (nflverse team results) were
executed against the same Snowflake account as Phase 1, then verified against
the local pandas harness. This records what the run produced.

## Sequence

```bash
python -m pipeline.download --season 2025 --weeks 1-18 --nflverse --put-to-stage
python -m pipeline.run_sql  --season 2025 --all

# the Phase 1.5 claim, tested rather than asserted: a second season, loaded
# after the first, must not disturb it
python -m pipeline.download --season 2024 --weeks 1-16 --nflverse --put-to-stage
python -m pipeline.run_sql  --season 2024 10_raw.sql 15_team_results.sql 20_staging.sql \
                                          30_scoring.sql 35_season_agg.sql 40_defense.sql
python -m pipeline.run_sql  --season 2025 50_ideal_team.sql 99_tests.sql
```

`run_sql.py --season N` sets the `TARGET_SEASON` session variable; every SQL file
reads it via `COALESCE(GETVARIABLE('TARGET_SEASON')::NUMBER, 2025)`, so the same
files serve 2024, 2025 and 2026 with no edits.

## Row counts (both seasons resident)

| Object | Rows |
| --- | --- |
| `RAW.OFFENSE_RAW` | 34,937 |
| `RAW.K_RAW` | 1,874 |
| `RAW.DEFENSE_RAW` | 51,975 |
| `RAW.TEAM_GAME_RAW` | 570 |
| `RAW.TEAM_WEEK_RAW` | 1,140 |
| `STAGING.STG_PLAYER_WEEK` | 142,742 |
| `STAGING.STG_TEAM_WEEK` | 1,140 (570 per season; 544 of the 2025 rows are `REG`) |
| `MARTS.FCT_PLAYER_SCORING` | 266,358 |
| `MARTS.FCT_TEAM_DEFENSE_WEEK` | 3,072 |
| `MARTS.IDEAL_TEAM` | 273 — 2025 only, because the board is built for `TARGET_SEASON` |

## Phase 1.5 — what was actually verified

- **Re-download is a no-op.** A second `--season 2025 --weeks 1-18 --nflverse`
  reported 153 unchanged / 1 rewritten, the rewrite being nflverse's `games.csv`,
  which is republished as results land.
- **Loading 2024 left 2025 alone.** Each `DELETE` is scoped to the target season
  (`WHERE source_file LIKE '%' || $target_season || '/%'`, and `WHERE season =
  $target_season` for the team tables), and every one of them deleted 0 rows on
  the 2024 pass. `STG_PLAYER_WEEK_HEADER` afterwards: 2024 → 41,754 rows, 2025 →
  47,032. Truncating, which is what Phase 1 did, would have wiped 2025.
- **A partial season is a normal outcome, not a failure.** hvpkod's 2024 tree
  stops at week 16; the downloader reported the 16 week-17/18 files as
  `missing (404 at source)` and loaded the rest. This is the same path an
  in-season 2026 refresh takes every week.
- **The board follows `--season`.** `IDEAL_TEAM` stayed 273 rows, all 2025, with
  2024 fully queryable in RAW/STAGING/MARTS underneath it.

## Phase 1.6 — team results

Both nflverse assets load through `INFER_SCHEMA` + `MATCH_BY_COLUMN_NAME`
rather than positionally: `stats_team_week_2025.csv` is ~138 columns wide and
gains columns between releases, so a positional `COPY` breaks on the next
upstream release (it did, during development — "number of columns in file (138)
does not match that of the corresponding table (9)").

2025, regular season: **544 team-weeks, 32 teams, weeks 1–18, zero missing
yards-allowed joins.** `LA` → `LAR` rewrote the Rams' 17 rows; `LAC` stayed the
Chargers.

### What the tiers changed

The DEF board is no longer a playmaking ranking. Scoring what a defense gave up
moves teams by double-digit ranks:

| Team | IDP pts | Tier pts | Total | IDP-only rank | Final rank |
| --- | --- | --- | --- | --- | --- |
| HOU | 144 | +50 | 194 | 1 | 1 |
| SEA | 129 | +51 | 180 | 4 | 2 |
| CLE | 132 | +34 | 166 | 3 | 3 |
| MIN | 122 | +36 | 158 | 8 | 4 |
| DEN | 113 | +43 | 156 | 12 | **5** |
| PIT | 138 | **−6** | 132 | 2 | 12 |
| CHI | 129 | **−17** | 112 | 4 | 16 |

Pittsburgh and Chicago are the point of the phase: both were top-4 on takeaways
and sacks while giving up enough points and yards to finish with *negative* team
value, which is exactly the distortion §5a predicted the IDP-only board would
have.

## Data quality

All **15** error-level checks in `99_tests.sql` return 0 failures — the nine from
Phase 1 plus six new ones for the team feed (`team_week_grain_unique`,
`team_week_team_count_plausible`, `team_week_abbreviations_known`,
`yards_allowed_present`, `def_tier_bands_total`, `defense_team_result_coverage`).

The two warn-level reconciliations return their Phase 1 baselines unchanged —
`reconcile_season_files` (1) and `reconcile_total_points_half_ppr` (1,139) — with
two seasons resident. Both had to be scoped to `$target_season` to stay there:
they join on `player_id`, so with 2024 loaded they were comparing a player's 2024
season file against his 2025 weekly rows, which inflated them to 2,236 and 2,066.

## Local ↔ Snowflake parity

`pipeline/validate_scoring.py` now loads the same two nflverse files, applies the
same bands from `pipeline.scoring.POINTS_ALLOWED_TIERS` / `YARDS_ALLOWED_TIERS`,
and adds them per week before the season roll-up — the order matters, since a
tier is one bonus per week, not a rate.

All **273** rows of `MARTS.IDEAL_TEAM` match the three reference CSVs exactly:
0 unmatched keys, 0 differing names, 0 point differences, including all 15 DEF
rows across the three scoring modes. The tiers are mode-independent, so the DEF
board is identical in standard, half-PPR and full PPR.
