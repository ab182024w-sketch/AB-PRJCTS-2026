# NFL Fantasy Draft — Ideal Team Analysis

Scope definition for the Snowflake-based fantasy football optimizer. **No code exists yet — this document defines what will be built, in what order, and what decisions still need to be made.**

---

## 1. Goal

Given season-long CSV exports of NFL player statistics (one file per position group), load them into Snowflake, compute fantasy points from the raw stat lines, and return the **ideal fantasy team**:

| Slot | Count | Notes |
| --- | --- | --- |
| QB | 10 | Ranked by total fantasy points |
| RB | 10 | Ranked by total fantasy points |
| WR | 10 | Ranked by total fantasy points |
| TE | 10 | Ranked by total fantasy points |
| K | 1 | **Deferred to v1.5** — the current CSVs carry no kicking stats (see §2) |
| DEF | 5 | Team defenses, built by aggregating individual `LB` + `DB` + `DL` players up to their `Team` |

The output is a ranked "ideal team" board — the players who *actually* produced the most, which doubles as a draft-value reference and a season-in-review.

---

## 2. Source Data

Raw inputs are CSV files, one per position group, all sharing the same header:

```
PlayerName  PlayerId  Pos  Team  PlayerOpponent
PassingYDS  PassingTD  PassingInt
RushingYDS  RushingTD
ReceivingRec  ReceivingYDS  ReceivingTD
RetTD  FumTD  2PT  Fum
```

### Column semantics

| Column | Type | Meaning |
| --- | --- | --- |
| `PlayerName` | STRING | Display name; **not** unique — do not use as a key |
| `PlayerId` | STRING | Stable per-player identifier; the natural key |
| `Pos` | STRING | `QB`, `RB`, `WR`, `TE`, `K`, `LB`, `DB`, `DL` |
| `Team` | STRING | Player's NFL team abbreviation |
| `PlayerOpponent` | STRING | Opponent for the row — see the grain question in §3 |
| `PassingYDS/TD/Int` | NUMBER | Passing production and interceptions thrown |
| `RushingYDS/TD` | NUMBER | Rushing production |
| `ReceivingRec/YDS/TD` | NUMBER | Receptions, receiving yards, receiving TDs |
| `RetTD` | NUMBER | Return touchdowns (kick/punt) |
| `FumTD` | NUMBER | Touchdowns scored on a fumble recovery |
| `2PT` | NUMBER | Two-point conversions |
| `Fum` | NUMBER | Fumbles lost |

### Known data gaps and the v1 decision

Separate kicker and defense files exist but are not in scope yet. **v1 ranks strictly on the columns above**; the richer files land in v1.5 (§7).

1. **No kicking columns.** There is no `FGM`, `FGA`, `XPM`, or field-goal distance bucket in the header. **v1 therefore omits the K slot entirely** rather than fabricating a kicker ranking from unrelated stats. v1.5 adds a kicker CSV (`FGM_0_39 / FGM_40_49 / FGM_50+ / XPM / FGMiss`) and restores the single-kicker slot.
2. **No team-defense columns.** Sacks, interceptions caught, fumbles recovered, safeties, and points allowed are not present. **v1 ranks defenses only on what is available** — `RetTD`, `FumTD`, and any offensive-style production recorded for `LB`/`DB`/`DL` players — rolled up by `Team`. This is a weak proxy and the resulting top 5 should be treated as provisional; v1.5 replaces it with a real defensive CSV (`Sack / Int / FR / Safety / PtsAllowed / YdsAllowed`).
3. **`PassingInt` is interceptions thrown** (a negative for the passer). Interceptions *caught* by a defender are a different, absent stat.

The scoring-rules table (§4) and the slot configuration are data, not hard-coded SQL, specifically so that v1.5 adds rows and files rather than rewriting the pipeline.

---

## 3. Grain

**Confirmed: one row per player per game.** `PlayerId` alone is therefore *not* unique in the source — the grain is `(PlayerId, game)`, with `PlayerOpponent` identifying the game.

Consequences that shape the whole pipeline:

- **Season totals are a `GROUP BY PlayerId` aggregation**, not a direct read. `FCT_PLAYER_SCORING` scores each row at game grain; a separate `AGG_PLAYER_SEASON` sums to the season level, and the ideal-team ranking reads from the season aggregate.
- **Per-game and consistency metrics come for free** and are worth computing, since the whole point of an "ideal team" board is separating volume from reliability:
  - `games_played` — count of rows per player
  - `pts_per_game` — `total_pts / games_played`
  - `stddev_pts` and coefficient of variation — boom/bust measure
  - `floor` / `ceiling` — e.g. 20th and 80th percentile game via `PERCENTILE_CONT`
  - `weeks_above_threshold` — count of startable games (position-specific cutoff)
  - `best_game` / `worst_game`
- **Ranking is on season `total_pts`** (the stated requirement), with `pts_per_game` shown alongside so a high-scoring player who simply played more games is distinguishable from a genuinely better one. A minimum-games filter is a parameter, defaulting to no filter for the headline board.

### Grain follow-ups

- **No `Week` column is present in the header.** Games can be counted and aggregated, but they cannot be *ordered*, so trend, streak, and last-N-weeks analysis is unavailable until a `Week` or date column is added to the export. Adding one is cheap and unlocks the Phase 2 week-by-week chart.
- **Duplicate `(PlayerId, PlayerOpponent)` pairs are expected** for division opponents played twice. Without a `Week`, those two games are indistinguishable — so deduplication must *not* key on `(PlayerId, PlayerOpponent)` or it will silently delete real games. See §5.
- **Bye weeks and DNPs:** presence of zero-stat rows vs. missing rows decides whether `games_played` means games active or games on roster. To be verified against the loaded row counts.

---

## 4. Scoring Rules

Default scoring, configurable via a scoring-rules table rather than hard-coded in SQL:

| Event | Points |
| --- | --- |
| Passing yard | 0.04 (1 per 25) |
| Passing TD | 4 |
| Interception thrown | −2 |
| Rushing yard | 0.1 (1 per 10) |
| Rushing TD | 6 |
| Reception | 1.0 (full PPR) — set to 0.5 for half-PPR, 0 for standard |
| Receiving yard | 0.1 |
| Receiving TD | 6 |
| Return TD | 6 |
| Fumble-recovery TD | 6 |
| Two-point conversion | 2 |
| Fumble lost | −2 |

PPR mode is a parameter, not a rewrite: results will be produced for standard / half / full PPR so the rankings can be compared.

---

## 5. Snowflake Architecture

A layered warehouse, staged so each step is independently re-runnable.

```
CSV files
  → Snowflake internal stage  (@FANTASY_STAGE)
  → RAW.PLAYER_STATS_RAW      (all columns as VARCHAR, plus file/row lineage)
  → STAGING.STG_PLAYER_STATS  (typed, cleaned, deduped)
  → MARTS.FCT_PLAYER_SCORING  (fantasy points per player per GAME)
  → MARTS.AGG_PLAYER_SEASON   (season totals + per-game/consistency metrics)
  → MARTS.DIM_TEAM_DEFENSE    (LB+DB+DL rolled up to Team, season level)
  → MARTS.IDEAL_TEAM          (final ranked roster)
```

### Layer responsibilities

**Stage / RAW**
- Named file format: CSV, header skipped, `FIELD_OPTIONALLY_ENCLOSED_BY='"'`, `NULL_IF=('','NA','-')`.
- Everything lands as `VARCHAR`; `METADATA$FILENAME` and `METADATA$FILE_ROW_NUMBER` are retained so any bad value can be traced back to a line in a file.
- Load with `COPY INTO ... ON_ERROR = CONTINUE`, then review the rejected rows rather than silently discarding them.

**STAGING**
- Cast numerics with `TRY_TO_NUMBER`; a failed cast surfaces as `NULL` and is counted, not hidden.
- `COALESCE(stat, 0)` only *after* the cast-failure count is checked — otherwise a parse bug looks like a zero-production week.
- Normalize team abbreviations (e.g. `JAC`/`JAX`, `LA`/`LAR`, `WSH`/`WAS`) via a mapping table.
- Trim/upper-case `Pos`; assert it is in the known set.
- **Deduplicate on the full row hash, not on `(PlayerId, PlayerOpponent)`.** Rows are per-game and a team plays each division opponent twice, so keying on the opponent would delete legitimate games. With no `Week` column (§3), only an exact-duplicate-row collapse is safe — and even that is risky if a player posts an identical stat line twice, so duplicates are *reported for review* rather than dropped automatically.

**MARTS**
- `FCT_PLAYER_SCORING`: **one row per player per game**, with each scoring component broken out (`pass_pts`, `rush_pts`, `rec_pts`, `misc_pts`) alongside `total_pts`, so a surprising ranking can be explained rather than merely trusted.
- `AGG_PLAYER_SEASON`: `GROUP BY PlayerId` over the fact table — season `total_pts`, `games_played`, `pts_per_game`, `stddev_pts`, floor/ceiling percentiles, and `weeks_above_threshold` (§3). This is what the ideal-team ranking reads from.
- `DIM_TEAM_DEFENSE`: `SUM` of `LB` + `DB` + `DL` production grouped by `Team`; this is what "joining LB, DB, DL" means in practice, and the top 5 teams by that total are the defense picks. In v1 the only meaningful inputs are `RetTD` and `FumTD` (§2), so the roll-up is deliberately thin and swaps in real defensive stats at v1.5 without changing its interface.
- `IDEAL_TEAM`: `QUALIFY ROW_NUMBER() OVER (PARTITION BY slot ORDER BY season_total_pts DESC) <= n` per slot, unioned into a single roster board, carrying `games_played` and `pts_per_game` as context columns.

### Warehouse / cost notes
- An `XSMALL` warehouse with auto-suspend at 60s is more than sufficient for a single season of player-week rows.
- All marts are views or small tables; no clustering keys are warranted at this data volume.

---

## 6. Data Quality Checks

Run as assertions after each load; a failure blocks promotion to MARTS.

- `PlayerId` is never null; `(PlayerId, PlayerOpponent)` appears at most twice (home/away against a division rival) — three or more occurrences indicate a duplicate load.
- `games_played` per player is between 1 and 17; anything higher means rows were double-loaded.
- `Pos` is in the allowed set; unmapped values are reported, not dropped.
- No negative yardage totals where impossible; flag implausible outliers (e.g. `PassingYDS > 700` in one game).
- Row count per position group is within an expected band vs. the previous load.
- Every `Team` and `PlayerOpponent` resolves to a known team abbreviation.
- Total fantasy points reconcile within tolerance against a hand-checked sample of ~10 known players.

---

## 7. Deliverables

**Phase 1 — Warehouse and rankings (this project's core)**
1. `sql/00_setup.sql` — database, schemas, warehouse, file format, stage.
2. `sql/10_raw.sql` — RAW tables and `COPY INTO` loads.
3. `sql/20_staging.sql` — typed/cleaned staging views.
4. `sql/30_scoring.sql` — scoring-rules table and `FCT_PLAYER_SCORING`.
5. `sql/35_season_agg.sql` — `AGG_PLAYER_SEASON` season totals and per-game/consistency metrics.
6. `sql/40_defense.sql` — `DIM_TEAM_DEFENSE` roll-up.
7. `sql/50_ideal_team.sql` — the final ideal-team query.
8. `sql/99_tests.sql` — the data-quality assertions from §6.
9. Documented output: the ideal team board (10 QB / 10 RB / 10 WR / 10 TE / 5 DEF — no K in v1) exported to CSV and committed as a reference result.

**Phase 1.5 — Kicker and real defensive stats**
- Load the separate kicker CSV; add field-goal-by-distance and extra-point rows to the scoring-rules table; restore the 1-K slot.
- Load the separate defensive CSV; rebuild `DIM_TEAM_DEFENSE` on sacks / interceptions / fumble recoveries / safeties / points allowed instead of the `RetTD`+`FumTD` proxy.
- Re-run the reference output and diff the top 5 defenses against the v1 result to quantify how misleading the proxy was.

**Phase 2 — Front end (future)**
- A web UI to browse the ideal team and the underlying rankings, not just a static CSV.
- Views: a roster board grouped by slot; a sortable/filterable player table (position, team, PPR mode, min games); a player detail page with the scoring breakdown (`pass/rush/rec/misc`) and week-by-week chart; a team-defense comparison view.
- Interactive scoring: PPR toggle and editable point values that re-rank live, so the "ideal team" can be recomputed under league-specific rules.
- Design intent: dark-mode-first, position-color-coded, responsive, fast — the table is the product, so sorting and filtering must feel instant.
- Serving approach TBD: either query Snowflake directly through a thin API layer, or export the marts to a small cached store the front end reads. Choice depends on whether the data updates in-season.

**Phase 3 — Waiver-wire tracking scraper (future)**
- A scheduled scraper that pulls current rostered-percentage / add-drop trend data and merges it against the computed rankings, surfacing players who score well but are widely available.
- Outputs a "waiver targets" view: high projected points, low roster percentage, favorable upcoming opponent.
- Requirements: respect each source's `robots.txt` and terms of service, rate-limit politely, cache raw responses, and store snapshots as time series so trends over weeks are visible rather than only the latest state.
- Scheduling via Snowflake Tasks or an external orchestrator; raw scrape payloads land in RAW with a load timestamp, then flow through the same staging → marts pattern.

---

## 8. Out of Scope (for now)

- Live in-season projections or forecasting models — this ranks *actual* production.
- Trade analyzers, auction values, and keeper/dynasty valuation.
- Multi-season history and aging curves.
- Head-to-head league simulation or playoff odds.
- Any automated roster moves against a real league platform.

---

## 9. Open Questions

1. Can a `Week` or date column be added to the export? Rows are per-game but unordered without it, which blocks trend analysis and makes duplicate detection imprecise. (§3)
2. Which scoring mode is canonical — standard, half-PPR, or full PPR?
3. Which season(s) do the current files cover, and will they be refreshed in-season?
4. Does "top 5 Defenses" mean the 5 best team defenses, or the top 5 individual defenders across `LB`/`DB`/`DL`? This document assumes team defenses.

**Resolved:**
- v1 ranks only on the columns present today — no kicker slot, defenses on the `RetTD`/`FumTD` proxy. The separate kicker and defensive files are deferred to Phase 1.5. (§2, §7)
- Rows are per-player-per-game; season totals are an explicit aggregation and per-game/consistency metrics are in scope. (§3)
