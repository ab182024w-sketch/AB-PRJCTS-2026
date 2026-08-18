# NFL Fantasy Draft — Ideal Team Analysis

Scope definition for the Snowflake-based fantasy football optimizer. **No code exists yet — this document defines what will be built, in what order, and what decisions still need to be made.**

Source data: [hvpkod/NFL-Data](https://github.com/hvpkod/NFL-Data), 2025 season (2026 once it starts).

---

## 1. Goal

Load per-week NFL player statistics into Snowflake, compute fantasy points from the raw stat lines under all three PPR modes, and return the **ideal fantasy team**:

| Slot | Count | Notes |
| --- | --- | --- |
| QB | 10 | Ranked by season fantasy points |
| RB | 25 | Ranked by season fantasy points — deeper than a starting lineup, to cover bench/flex |
| WR | 25 | Ranked by season fantasy points — deeper than a starting lineup, to cover bench/flex |
| TE | 25 | Ranked by season fantasy points — deeper than a starting lineup, to cover bench/flex |
| K | 1 | Single highest-scoring kicker, from `K.csv` field-goal/PAT stats |
| DEF | 5 | **Team** defenses — `DB` + `LB` + `DL` players aggregated up to their `Team` (confirmed: team level, not individual defenders) |

RB/WR/TE run 25 deep because real rosters carry a bench and a flex, so the useful board extends well past the starters; QB stays at 10 since only one starts. Slot depth is a parameter of the final query, not hard-coded, so these counts can change without touching anything upstream.

The output is a ranked "ideal team" board — the players who *actually* produced the most, which doubles as a draft-value reference and a season-in-review. The board is produced three times, once per scoring mode (§4).

---

## 2. Source Data

### Origin

**[hvpkod/NFL-Data](https://github.com/hvpkod/NFL-Data)** (MIT licensed), directory `NFL-data-Players/`. Seasons **2015 through 2025** are present; **2025 is the working season**, and the pipeline re-points at 2026 once that season starts.

Path convention — note that **the week is encoded in the directory name, not in a column**:

```
NFL-data-Players/{season}/{week}/{POS}.csv            # per-week actuals      e.g. 2025/18/QB.csv
NFL-data-Players/{season}/{week}/projected/{POS}_projected.csv
NFL-data-Players/{season}/{POS}_season.csv            # pre-aggregated season totals
```

`{POS}` ∈ `QB`, `RB`, `WR`, `TE`, `K`, `DB`, `LB`, `DL`. Each file is also published as `.json`; the CSVs are the input.

Ingestion loads `{season}/{week}/{POS}.csv` for all weeks and derives `season` and `week` from the path (Snowflake `METADATA$FILENAME` is parsed for both). `{POS}_season.csv` is **not** the input — it is used only as an independent reconciliation target (§6).

### Three distinct schemas

The earlier assumption that all files share one header is wrong. There are three:

**A. Offensive — `QB`, `RB`, `WR`, `TE`**

```
PlayerName, PlayerId, Pos, Team, PlayerOpponent,
PassingYDS, PassingTD, PassingInt,
RushingYDS, RushingTD,
ReceivingRec, ReceivingYDS, ReceivingTD,
RetTD, FumTD, 2PT, Fum,
FanPtsAgainst-pts,
TouchCarries, TouchReceptions, Touches,
TargetsReceptions, Targets, ReceptionPercentage,
RzTarget, RzTouch, RzG2G,
Rank, TotalPoints
```

**B. Kicker — `K`**

```
PlayerName, PlayerId, Pos, Team, PlayerOpponent,
PatMade, PatMissed,
FgMade_0-19, FgMade_20-29, FgMade_30-39, FgMade_40-49, FgMade_50,
FgMiss_0-19, FgMiss_20-29, FgMiss_30-39,
Rank, TotalPoints
```

**C. Defensive (IDP) — `DB`, `LB`, `DL`**

```
PlayerName, PlayerId, Pos, Team, PlayerOpponent,
TacklesTot, TacklesAst, TacklesSck, TacklesTfl,
TurnoverInt, TurnoverFrcFum, TurnoverFumRec,
ScoreIntTd, ScoreFumTd, ScoreBlkTd, ScoreSaf, ScoreDef2ptRet,
Blk, PDef, QBHit,
ReturnIntYds, ReturnFumYds,
Rank, TotalPoints
```

### Column semantics

| Column | Meaning |
| --- | --- |
| `PlayerName` | Display name; **not** unique — do not use as a key |
| `PlayerId` | Stable per-player identifier (an NFL/GSIS-style id); the natural key |
| `Pos` | One of the eight position codes above |
| `Team` | NFL team abbreviation; `FA` appears for unsigned players |
| `PlayerOpponent` | Opponent for that week, `@XXX` when away, **`Bye` on bye weeks** |
| `PassingInt` | Interceptions *thrown* (a negative for the passer) — distinct from `TurnoverInt`, which is interceptions *caught* by a defender |
| `TacklesSck` | Sacks (fractional values are expected — half-sacks) |
| `TacklesTfl` | Tackles for loss |
| `PDef` / `QBHit` | Passes defensed / QB hits |
| `FanPtsAgainst-pts` | Fantasy points the opponent's defense has allowed to this position — a matchup-difficulty indicator, **not** the player's own production |
| `Touches`, `Targets`, `ReceptionPercentage` | Usage/opportunity volume |
| `RzTarget`, `RzTouch`, `RzG2G` | Red-zone and goal-to-go opportunity |
| `Rank`, `TotalPoints` | **The source's own fantasy scoring and ranking** — see below |

### Notes that change the design

1. **Kicker and defensive stats are fully available.** The previously deferred Phase 1.5 is therefore folded into Phase 1: the K slot ships in v1, and team defenses are built from real defensive events (sacks, interceptions, forced/recovered fumbles, safeties, defensive TDs) rather than a `RetTD`/`FumTD` proxy.
2. **Team defense is assembled from IDP stats, not a DST feed.** Summing `DB`+`LB`+`DL` to the team gives every standard DST *event* category, but **points allowed and yards allowed are absent** — those are team-game outcomes, not player stats. Most real DST scoring is dominated by the points-allowed tier, so this ranking measures *defensive playmaking* and will differ from a league's DST ranking. This is a genuine modeling limitation, stated up front. Points-allowed tiers can be added later from a team-level game-results source.
3. **`TotalPoints` is the source's scoring, not ours.** Its rule set is unknown and its PPR mode unstated, so it is **not** used for ranking. It is loaded anyway and used as a reconciliation signal: our computed points under one of the three modes should track it closely, and a large systematic divergence means a scoring bug or a misunderstood column.
4. **Empty strings mean zero, not missing.** The CSVs leave unused stat cells blank (a QB's `ReceivingYDS`, a bye-week row's everything). These are legitimately zero for scoring, but they must be distinguished from a *parse failure* — see §5.
5. **Bye weeks appear as rows** with `PlayerOpponent = 'Bye'` and no stats. They must be excluded from `games_played` or every per-game average is silently deflated.
6. **Projections exist** (`projected/{POS}_projected.csv`, with `PlayerWeekProjectedPts`, `ProjectedRank`, and `ProjectionDiff`). Out of scope for v1 — this project ranks actual production — but they are the natural input for a later "who outperformed their projection" view.
7. **Ten prior seasons (2015–2024) are available** in the identical layout. v1 targets one season; multi-season is a later addition that costs a `season` predicate rather than a redesign, which is why `season` is carried as a column from ingestion onward.

---

## 3. Grain

**One row per player per week.** `PlayerId` alone is not unique; the true grain is **`(season, week, PlayerId)`**, where `season` and `week` come from the file path (§2) rather than from a column.

Consequences that shape the whole pipeline:

- **Season totals are an aggregation, not a direct read.** `FCT_PLAYER_SCORING` scores each row at week grain; `AGG_PLAYER_SEASON` groups to `(season, PlayerId, scoring_mode)`, and the ideal-team ranking reads from that aggregate. The source's own `{POS}_season.csv` is used to *verify* this roll-up, never to replace it (§6).
- **Per-game and consistency metrics come for free** and are worth computing, since the whole point of an "ideal team" board is separating volume from reliability:
  - `games_played` — count of non-bye rows per player
  - `pts_per_game` — `total_pts / games_played`
  - `stddev_pts` and coefficient of variation — boom/bust measure
  - `floor` / `ceiling` — e.g. 20th and 80th percentile game via `PERCENTILE_CONT`
  - `weeks_above_threshold` — count of startable games (position-specific cutoff)
  - `best_week` / `worst_week`
  - `last_4_pts_per_game` — late-season form, now possible because weeks are ordered
- **Ranking is on season `total_pts`** (the stated requirement), with `pts_per_game` shown alongside so a high-scoring player who simply played more games is distinguishable from a genuinely better one. A minimum-games filter is a parameter, defaulting to no filter for the headline board.

### Grain resolutions

- **Week ordering is available after all.** Because `week` is parsed from the directory name, games *can* be ordered — so trend, streak, last-N-weeks, and week-by-week charting are all in scope, and the Phase 2 per-game chart has a real x-axis. This was previously listed as a blocker; the directory layout resolves it.
- **Deduplication keys on `(season, week, PlayerId)`.** With a real week, the earlier hazard (a division rival appearing twice, indistinguishable without a week) disappears. Exact duplicates within that key indicate a double load and are dropped, keeping the last-loaded row.
- **Bye rows are excluded from `games_played`.** They exist in the data as `PlayerOpponent = 'Bye'` with no stats. Counting them would deflate every per-game average. Zero-stat rows for players who were active but did not produce *are* counted — that is a real zero.
- **`Team = 'FA'` rows** (unsigned players) carry no meaningful team attribution and are excluded from the team-defense roll-up.

---

## 4. Scoring Rules

**All three PPR modes are canonical.** Standard (0 PPR), half-PPR (0.5), and full PPR (1.0) are each first-class outputs — not one default with variants. Every mart carries `scoring_mode` as a column, so the ideal-team board exists three times over and the three can be diffed directly (the RB/WR/TE ordering is where they diverge; QB and K are essentially unaffected).

Scoring lives in a `SCORING_RULES` table keyed by `(scoring_mode, stat_column, points_per_unit)`, joined to the fact table — not hard-coded in SQL. Adding a league's custom rules is then an `INSERT`, and the three modes are three sets of rows rather than three queries.

### Offensive

| Event | Column | Standard | Half | Full |
| --- | --- | --- | --- | --- |
| Passing yard | `PassingYDS` | 0.04 | 0.04 | 0.04 |
| Passing TD | `PassingTD` | 4 | 4 | 4 |
| Interception thrown | `PassingInt` | −2 | −2 | −2 |
| Rushing yard | `RushingYDS` | 0.1 | 0.1 | 0.1 |
| Rushing TD | `RushingTD` | 6 | 6 | 6 |
| **Reception** | `ReceivingRec` | **0** | **0.5** | **1.0** |
| Receiving yard | `ReceivingYDS` | 0.1 | 0.1 | 0.1 |
| Receiving TD | `ReceivingTD` | 6 | 6 | 6 |
| Return TD | `RetTD` | 6 | 6 | 6 |
| Fumble-recovery TD | `FumTD` | 6 | 6 | 6 |
| Two-point conversion | `2PT` | 2 | 2 | 2 |
| Fumble lost | `Fum` | −2 | −2 | −2 |

### Kicker (identical across modes)

| Event | Column | Points |
| --- | --- | --- |
| PAT made | `PatMade` | 1 |
| PAT missed | `PatMissed` | −1 |
| FG 0–19 | `FgMade_0-19` | 3 |
| FG 20–29 | `FgMade_20-29` | 3 |
| FG 30–39 | `FgMade_30-39` | 3 |
| FG 40–49 | `FgMade_40-49` | 4 |
| FG 50+ | `FgMade_50` | 5 |
| FG missed | `FgMiss_*` | −1 |

Distance-tiered field goals are exactly why the source's bucketed columns are useful; a flat 3-points-per-FG rule would understate long-range kickers.

### Team defense (identical across modes)

Computed per defensive player, then summed to the team (§5).

| Event | Column | Points |
| --- | --- | --- |
| Sack | `TacklesSck` | 1 |
| Interception | `TurnoverInt` | 2 |
| Fumble recovered | `TurnoverFumRec` | 2 |
| Forced fumble | `TurnoverFrcFum` | 1 |
| Safety | `ScoreSaf` | 2 |
| Defensive TD | `ScoreIntTd` + `ScoreFumTd` + `ScoreBlkTd` | 6 |
| Blocked kick | `Blk` | 2 |
| Defensive 2pt return | `ScoreDef2ptRet` | 2 |

**Points allowed and yards allowed are not in the source** (§2), so their tier bonuses — usually the largest single component of real DST scoring — are absent. They are recoverable from a second feed; see §5a for the confirmed plan. Tackles, TFL, passes defensed, and QB hits are deliberately **excluded** from team-defense scoring: they are IDP-league categories, and including them would make the ranking a measure of tackle volume (i.e. of a defense being on the field a lot, which correlates with being *bad*) rather than of defensive playmaking. They remain available in the fact table for a possible future IDP mode.

### Points-allowed / yards-allowed tiers (Phase 1.6 — §5a)

Once the team-results feed lands, these two tiers are added to the DEF score. Standard values:

| Points allowed | Points | | Yards allowed | Points |
| --- | --- | --- | --- | --- |
| 0 | 10 | | under 100 | 5 |
| 1–6 | 7 | | 100–199 | 3 |
| 7–13 | 4 | | 200–299 | 2 |
| 14–20 | 1 | | 300–349 | 0 |
| 21–27 | 0 | | 350–399 | -1 |
| 28–34 | -1 | | 400–449 | -3 |
| 35+ | -4 | | 450+ | -5 |

Both are **per week**, then summed — a tier bonus averaged over a season would be meaningless. They are added as ordinary rows in `SCORING_RULES` keyed on a bucketed stat name, so nothing downstream changes.

## 5. Snowflake Architecture

A layered warehouse, staged so each step is independently re-runnable.

```
hvpkod/NFL-Data CSVs  (season/week/POS.csv)
  → Snowflake internal stage       (@FANTASY_STAGE, mirroring the season/week paths)
  → RAW.OFFENSE_RAW | K_RAW | DEFENSE_RAW   (3 schemas §2; VARCHAR + file/row lineage)
  → STAGING.STG_PLAYER_WEEK        (typed, cleaned, season/week parsed from path, unioned)
  → MARTS.FCT_PLAYER_SCORING       (points per player per week per scoring_mode)
  → MARTS.AGG_PLAYER_SEASON        (season totals + per-game/consistency metrics)
  → MARTS.FCT_TEAM_DEFENSE         (DB+LB+DL rolled up to Team, per week then season)
  → MARTS.IDEAL_TEAM               (final ranked roster, one board per scoring_mode)
```

Three RAW tables rather than one, because the three source schemas share only their five key columns. `STG_PLAYER_WEEK` is the union that reconciles them onto a common `(season, week, PlayerId, Pos, Team, opponent, stat, value)` shape — a **tall/EAV layout**, which is what allows `SCORING_RULES` to be a simple join on `stat` instead of a wide expression repeated three times per mode.

### Layer responsibilities

**Stage / RAW**
- Named file format: CSV, header skipped, `FIELD_OPTIONALLY_ENCLOSED_BY='"'`, `EMPTY_FIELD_AS_NULL = TRUE`.
- Everything lands as `VARCHAR`; `METADATA$FILENAME` and `METADATA$FILE_ROW_NUMBER` are retained — the filename is not just lineage here, it is **the source of `season` and `week`** (§2).
- Load with `COPY INTO ... ON_ERROR = CONTINUE`, then review the rejected rows rather than silently discarding them.
- Column names containing hyphens (`FgMade_0-19`, `FanPtsAgainst-pts`) and leading digits (`2PT`) require quoted identifiers; they are renamed to snake_case at the staging boundary so downstream SQL never needs quoting.

**STAGING**
- Parse `season` and `week` from `METADATA$FILENAME` with a regex, and assert both are non-null — an unparsed path would silently collapse the grain.
- Cast numerics with `TRY_TO_NUMBER`; a failed cast surfaces as `NULL` and is **counted**. Because blank cells legitimately mean zero (§2), the count is the only thing separating "no production" from "parse bug": `COALESCE(stat, 0)` is applied only after that count is asserted to be zero.
- Normalize team abbreviations (e.g. `JAC`/`JAX`, `LA`/`LAR`, `WSH`/`WAS`) via a mapping table; strip the leading `@` from `PlayerOpponent` into a separate `is_away` flag.
- Trim/upper-case `Pos`; assert it is in the known eight.
- Flag `PlayerOpponent = 'Bye'` rows as `is_bye` rather than deleting them — they are evidence the week loaded correctly.
- Deduplicate on `(season, week, PlayerId)`, keeping the last loaded row.

**MARTS**
- `FCT_PLAYER_SCORING`: **one row per `(season, week, PlayerId, scoring_mode)`**, with each component broken out (`pass_pts`, `rush_pts`, `rec_pts`, `kick_pts`, `def_pts`, `misc_pts`) alongside `total_pts`, so a surprising ranking can be explained rather than merely trusted. The source's own `TotalPoints` is carried alongside for reconciliation (§6).
- `AGG_PLAYER_SEASON`: `GROUP BY (season, PlayerId, scoring_mode)` — season `total_pts`, `games_played` (bye rows excluded), `pts_per_game`, `stddev_pts`, floor/ceiling percentiles, and `weeks_above_threshold` (§3). This is what the ideal-team ranking reads from.
- `FCT_TEAM_DEFENSE`: `SUM` of `DB` + `LB` + `DL` defensive scoring grouped by `(season, week, Team)`, then rolled to the season. This is what "joining LB, DB, DL" means in practice, and the top 5 teams are the defense picks. Excludes `Team = 'FA'`. See §4 for why tackle volume is deliberately not scored.
- `IDEAL_TEAM`: `QUALIFY ROW_NUMBER() OVER (PARTITION BY scoring_mode, slot ORDER BY season_total_pts DESC) <= n` per slot, unioned into a single roster board, carrying `games_played` and `pts_per_game` as context columns.

### Warehouse / cost notes
- An `XSMALL` warehouse with auto-suspend at 60s is more than sufficient for a single season of player-week rows.
- All marts are views or small tables; no clustering keys are warranted at this data volume.

---

## 5a. Team Results Feed (Phase 1.6)

hvpkod is a player-stat source and has no team or DST file — verified: `NFL-data-Players/2025/18/` contains only `QB/RB/WR/TE/K/DB/LB/DL`. Points allowed and yards allowed therefore need a second source. **[nflverse-data](https://github.com/nflverse/nflverse-data) covers both**, is free, versioned, and needs no scraping or API key.

| Need | Asset | Notes |
| --- | --- | --- |
| Points allowed | `schedules/games.csv` | One row per game with `season, week, home_team, away_team, home_score, away_score`. Points allowed = the opponent's score. 2025 verified present. |
| Yards allowed | `stats_team/stats_team_week_{season}.csv` | One row per team per week with `opponent_team` and full offensive splits (`passing_yards`, `rushing_yards`, …). Yards allowed for team X in week W = the *opponent's* offensive yards in that row. |

Both are stable release-asset URLs of the form `https://github.com/nflverse/nflverse-data/releases/download/{tag}/{file}`, so Phase 0's downloader handles them with no new machinery. Parquet is also published if CSV parsing becomes the bottleneck.

**Integration:**

```
nflverse games.csv + stats_team_week.csv
  → RAW.TEAM_GAME_RAW
  → STAGING.STG_TEAM_WEEK        (season, week, team, opponent, points_allowed, yards_allowed)
  → joins FCT_TEAM_DEFENSE on (season, week, team)
```

The join key is `(season, week, team)`, which the existing team-defense fact already has — so this is an added left join plus two `SCORING_RULES` rows, not a re-model.

**The one real gotcha:** abbreviations nearly match but not exactly. Checked against 2025 week 18: the only conflict is the Rams — hvpkod uses `LAR`, nflverse uses `LA`. hvpkod additionally has `FA` (free agents), which has no team-results counterpart and is already excluded from defensive rollups (§3). The existing normalization mapping table (§5) absorbs both; the join must be asserted to produce exactly 32 teams per week so a silent abbreviation drift never quietly zeroes out a defense's tier bonus.

**Also unlocked by the same feed**, at no extra ingestion cost: home/away splits, opponent strength (`FanPtsAgainst-pts` is already in the offensive files but is unvalidated), rest days, and separating playoff weeks from the regular season.

---

## 6. Data Quality Checks

Run as assertions after each load; a failure blocks promotion to MARTS.

- `season` and `week` parsed from every filename; zero unparsed rows. `week` is within 1–18.
- `(season, week, PlayerId)` is unique.
- `PlayerId` and `Pos` are never null; `Pos` is in the allowed eight.
- **Every `(season, week)` combination is present** for all eight position files — a missing directory is a silently incomplete season, which would quietly under-count a player's totals.
- `games_played` per player is between 0 and 18 once bye rows are excluded.
- Cast-failure count is zero (§5) — the check that distinguishes blank-means-zero from a parse bug.
- No impossible values: negative yardage where impossible, `PassingYDS > 700` in one week, sacks not a multiple of 0.5.
- Every `Team` and `PlayerOpponent` resolves to a known abbreviation (allowing `FA` and `Bye`).
- **Reconciliation against `{POS}_season.csv`:** summing our per-week rows to the season must match the source's own pre-aggregated season file stat-for-stat. This is a genuinely independent check on the ingestion — it catches missing weeks and double loads that internal consistency checks cannot.
- **Reconciliation against `TotalPoints`:** our computed points should track the source's own scoring closely under at least one of the three modes. A large systematic gap means a scoring bug or a misread column (§2).

---

## 7. Deliverables

**Phase 0 — Data acquisition**
- A small Python script that pulls `NFL-data-Players/2025/{1..18}/{POS}.csv` from the source repo (plus `{POS}_season.csv` for reconciliation) into a local `data/` tree mirroring the season/week layout, then `PUT`s them to the Snowflake stage. Re-runnable and idempotent, since the 2026 season will re-run it weekly.

**Phase 1 — Warehouse and rankings (this project's core)**
1. `sql/00_setup.sql` — database, schemas, warehouse, file formats, stage.
2. `sql/10_raw.sql` — the three RAW tables and `COPY INTO` loads, with `season`/`week` from `METADATA$FILENAME`.
3. `sql/20_staging.sql` — typed/cleaned staging, path parsing, and the union onto the common tall shape.
4. `sql/30_scoring.sql` — `SCORING_RULES` seeded with all three modes, plus `FCT_PLAYER_SCORING`.
5. `sql/35_season_agg.sql` — `AGG_PLAYER_SEASON` season totals and per-game/consistency metrics.
6. `sql/40_defense.sql` — `FCT_TEAM_DEFENSE` roll-up.
7. `sql/50_ideal_team.sql` — the final ideal-team query.
8. `sql/99_tests.sql` — the data-quality assertions from §6, including both reconciliations.
9. Documented output: the ideal team board (10 QB / 25 RB / 25 WR / 25 TE / 1 K / 5 DEF) exported to CSV **once per scoring mode** and committed as a reference result.

**Phase 1.5 — Season refresh for 2026**
- The 2025 files are complete and static; the 2026 season re-runs Phase 0 weekly against `NFL-data-Players/2026/{week}/`.
- `season` is already a column throughout, so this is a parameter change plus a scheduled job — not a rebuild. Multi-season comparison (2015–2024 are available in the identical layout) becomes a `WHERE season IN (...)` at that point.

**Phase 1.6 — Team results feed (§5a)**
- `sql/15_team_results.sql` + a Phase 0 downloader addition for the two nflverse assets, then `STG_TEAM_WEEK` and two extra `SCORING_RULES` rows for the points-allowed and yards-allowed tiers.
- Deliberately sequenced *after* Phase 1 rather than inside it: the DEF board is usable without it, and keeping the second source separate means a broken upstream release never blocks the core rankings.

**Phase 2 — Front end (future)** — stack recommendation in §8
- A web UI to browse the ideal team and the underlying rankings, not just a static CSV.
- Views: a roster board grouped by slot; a sortable/filterable player table (position, team, PPR mode, min games); a player detail page with the scoring breakdown (`pass/rush/rec/kick/def`) and a **week-by-week chart**; a team-defense comparison view.
- Interactive scoring: a three-way PPR mode selector and editable point values that re-rank live, so the "ideal team" can be recomputed under league-specific rules.
- Design intent: dark-mode-first, position-color-coded, fast — the table is the product, so sorting and filtering must feel instant.
- **Mobile-readable from day one** (§8, "Mobile"). This is a hard requirement for the web app, and separate from the native apps in Phase 4.

**Phase 3 — Waiver-wire tracking scraper (future)** — stack recommendation in §9
- A scheduled Python scraper that pulls current rostered-percentage / add-drop trend data and merges it against the computed rankings, surfacing players who score well but are widely available.
- Outputs a "waiver targets" view: high season/per-game points, low roster percentage, favorable upcoming opponent.
- Snapshots are stored as a time series so week-over-week roster-percentage *movement* is visible, not only the latest state.

**Phase 4 — iOS / Android beta (very distant — target: end of the 2026–27 season at the earliest)**
- Explicitly gated behind Phases 1–3 being complete and *working*. Feature-correctness on the web comes first; a native app that wraps a half-finished pipeline is two problems instead of one.
- Interim answer: the responsive web app (§8) covers phone use. A native app is only worth building for what the web cannot do — push notifications for waiver targets, offline access to the rankings, and a home-screen presence during draft season.
- Sequencing note: shipping the web app as an installable PWA is a cheap intermediate step that delivers a home-screen icon and offline caching without an app-store release, and is the sensible thing to try before committing to native.
- Stack thinking, to be revisited when the phase actually starts: a single cross-platform codebase (React Native/Expo or Flutter) rather than two native ones, reading the same API the web app uses — which is a further reason the front end should talk to a documented API layer rather than embedding queries, once it outgrows Streamlit.
- App-store review, Apple developer enrollment, and TestFlight/Play Console beta distribution are calendar overhead that has to be planned for separately from build effort.

---

## 8. Recommended Front-End Stack

Everything here is Python-first so the UI and the pipeline share one language and one set of Snowflake credentials.

### Recommendation: Streamlit for v1

**Streamlit**, ideally deployed as **Streamlit in Snowflake (SiS)**.

Why it fits this project specifically:
- The product *is* a set of ranked tables plus a few charts. That is precisely Streamlit's sweet spot, and it is roughly a few hundred lines rather than a separate front-end codebase.
- Running inside Snowflake means no separate hosting, no credential plumbing, no data egress — the app queries the marts directly and inherits Snowflake's auth and role-based access.
- The interactive scoring requirement (PPR toggle, editable point values, re-rank live) is a slider/number-input bound to a parameterized query. In Streamlit that is a handful of widgets; in a hand-rolled SPA it is a state-management project.
- Pure Python means the same person maintaining the SQL maintains the UI.

Supporting libraries:

| Concern | Choice | Reason |
| --- | --- | --- |
| Snowflake connectivity | `snowflake-snowpark-python` (or `snowflake-connector-python` outside SiS) | Native session inside SiS; DataFrame API keeps filtering pushed down to the warehouse |
| Data frames | `pandas`, or `polars` if data volume grows | Season-scale data is small; `pandas` is sufficient |
| Tables | `st.dataframe` with `column_config` | Built-in sorting, number/progress-bar formatting, and pinned columns — no grid library needed |
| Charts | `plotly` via `st.plotly_chart` | Interactive hover/zoom for per-game scoring and the stacked `pass/rush/rec/misc` breakdown |
| Caching | `st.cache_data(ttl=...)` | Keeps the warehouse from being re-queried on every widget interaction |
| Config | `st.secrets` / Snowflake secrets | No credentials in the repo |

Rough shape:

```python
# app.py
ppr = st.sidebar.select_slider("PPR", options=[0.0, 0.5, 1.0], value=1.0)
min_games = st.sidebar.slider("Minimum games", 0, 17, 0)

board = session.call("MARTS.IDEAL_TEAM", ppr, min_games).to_pandas()

for slot, group in board.groupby("SLOT"):
    st.subheader(slot)
    st.dataframe(group, column_config={"PTS_PER_GAME": st.column_config.NumberColumn(format="%.1f")})
```

Re-ranking stays in SQL (a parameterized view or stored procedure); Streamlit only passes parameters and renders. This keeps a single source of scoring truth rather than reimplementing the point math in Python.

### Upgrade path, only if Streamlit is outgrown

Move to **FastAPI + React** when one of these becomes true — not before:
- The design demands custom layout and interactions Streamlit cannot express.
- A public dashboard needs per-user accounts, saved leagues, or rate limiting beyond what a single Streamlit process handles (going public alone does *not* require this — see "Making the dashboard public" below).
- Sub-second interaction on large tables matters (Streamlit re-runs the script on each interaction).

That stack: **FastAPI** (Python, matches the rest of the project) + `snowflake-connector-python` with a connection pool, `pydantic` response models, **React + TypeScript + Vite**, **TanStack Table** for the sortable/filterable grid, **Tailwind** + **shadcn/ui** for the dark-mode-first design, and **Recharts** for charts. Deploy the API on any container host and the front end as static files.

**Explicitly not recommended:** Dash (heavier than Streamlit for equivalent output here) and Jupyter/Voilà (notebook semantics leak into the UI). A plain static export is also insufficient because live re-ranking is a stated requirement.

### Serving strategy

Query Snowflake directly. The marts are small, and the interactive PPR toggle needs live recomputation. Caching happens at the app layer (`st.cache_data`) plus Snowflake's own result cache; an intermediate export store would add staleness for no real benefit at this data volume — with one exception for public hosting, below.

### Mobile

The web dashboard must be **readable and usable on a phone** from the first release — this is a Phase 2 requirement, not a Phase 4 one.

What that means concretely for a table-heavy app:
- A wide ranking table does not shrink gracefully. On narrow viewports the board switches to a **card-per-player layout** — name, position, team, points, points-per-game — rather than a horizontally-scrolling grid.
- Column priority is explicit: rank, name, and total points always visible; usage/consistency columns hidden behind a "more detail" toggle.
- Controls (PPR mode, position filter, min games) move from a sidebar to a collapsible top sheet, since Streamlit's sidebar is cramped on mobile.
- Charts get a minimum touch-target size and are made scrollable rather than compressed.

Streamlit is responsive enough for this with `st.columns` breakpoints and `use_container_width=True` everywhere, but it needs deliberate layout work — the default wide-table rendering is not usable on a phone. If mobile ergonomics ever become the dominant constraint, that is a legitimate trigger for the FastAPI + React path above, where the layout is fully controllable.

### Making the dashboard public

Streamlit can absolutely serve a public, anyone-can-open dashboard — but **not** as Streamlit in Snowflake. SiS apps are gated behind Snowflake authentication and role grants, so every viewer needs a Snowflake login. Going public means running the *same app code* on a different host.

| Option | Public? | Cost | Notes |
| --- | --- | --- | --- |
| **Streamlit in Snowflake** | No — Snowflake login required | Warehouse compute only | Best for private/personal use; recommended default (§8) |
| **Streamlit Community Cloud** | Yes | Free | Deploys from a GitHub repo; simplest path to a public URL. Resource-limited and apps sleep when idle |
| **Container host** (Cloud Run, Render, Fly.io) | Yes | Low, usage-based | Full control, custom domain, scale-to-zero. `streamlit run` in a container |

The app code is identical across all three; only the Snowflake connection and secrets handling differ. Start on SiS, and moving public later is a deployment change, not a rewrite — which is a further reason to keep scoring logic in SQL rather than in the UI layer.

**Connecting a public app to Snowflake safely.** Outside SiS there is no inherited identity, so the app authenticates as one service account shared by every visitor. That account must be:
- **Read-only and narrowly scoped** — a dedicated role with `SELECT` on the `MARTS` views only; no access to `RAW`, `STAGING`, or any other database.
- **Key-pair authenticated**, with the private key in the host's secret store (`st.secrets`, or the platform's secret manager) — never in the repo. Note that Streamlit Community Cloud requires a public GitHub repo, so secret hygiene is not optional there.
- **Backed by a dedicated `XSMALL` warehouse** with `AUTO_SUSPEND = 60`, a resource monitor, and a statement timeout. Anonymous traffic is anonymous compute spend; the resource monitor is the thing that stops a scraper or a bored visitor from running up a bill.

**Cheaper alternative: ship the data with the app.** Since v1 ranks a completed season rather than live in-season data, the marts can be exported to a Parquet/DuckDB file and bundled with the deployment. The app then queries the local file, public traffic never touches Snowflake, cost is effectively zero, and there is no service account to leak. Filtering and PPR re-ranking still work — DuckDB runs the same SQL locally. The tradeoff is a rebuild-and-redeploy step whenever the data refreshes, which is acceptable at weekly or seasonal cadence and only becomes wrong if the data starts updating live.

**Recommendation:** SiS privately first; when going public, Streamlit Community Cloud with the bundled DuckDB export. Add the read-only Snowflake service account only once the dashboard genuinely needs live data.

---

## 9. Recommended Scraper Stack (Python)

### Libraries

| Concern | Choice | Reason |
| --- | --- | --- |
| HTTP | `httpx` (or `requests`) with a `Retry`/backoff wrapper | Connection pooling, timeouts, HTTP/2; set a descriptive `User-Agent` identifying the project |
| HTML parsing | `selectolax` | Substantially faster than BeautifulSoup and sufficient for CSS-selector extraction |
| Fallback parsing | `beautifulsoup4` + `lxml` | Only where messy markup needs its leniency |
| Tables | `pandas.read_html` | One-liner when the source is already a clean HTML table |
| JS-rendered pages | `playwright` (sync API) | Many fantasy sites render rosters client-side; use only where a static fetch genuinely fails, since it is far slower |
| Validation | `pydantic` | Reject malformed scrapes at the boundary instead of loading garbage into RAW |
| Robots compliance | `urllib.robotparser` | Checked before each fetch |
| Rate limiting | `tenacity` for retries + a fixed inter-request delay | Exponential backoff on 429/5xx |
| Snowflake load | `snowflake-connector-python` `write_pandas`, or `PUT` + `COPY INTO` | Same RAW → STAGING → MARTS path as the CSVs |
| Scheduling | GitHub Actions cron for v1; Snowflake Tasks or Prefect/Dagster if it grows | A weekly/daily scrape does not justify an orchestrator yet |
| Dependencies | `uv` with `pyproject.toml` | Fast, reproducible lockfile |
| Testing | `pytest` + `vcrpy` or saved HTML fixtures | Parser tests must not hit the network |

### Design rules

- **Prefer an official/public API over scraping** wherever one exists — it is more stable and unambiguously permitted. Scrape only as a fallback.
- **Check `robots.txt` and each site's terms of service before adding a source**, rate-limit politely (roughly one request per second, single-threaded), and never scrape behind a login.
- **Persist the raw response** (gzipped HTML/JSON keyed by source and fetch timestamp) before parsing. When a site's markup changes, the parser can be fixed and re-run against history instead of losing the data.
- **Append-only snapshots.** Every scrape inserts rows stamped with `scraped_at`; nothing is overwritten. Roster-percentage *movement* is the actual signal for waiver decisions, and it only exists if history is kept.
- **Parsers are pure functions** from HTML to validated records, tested against committed fixtures so they can be verified offline.
- **Fail loudly.** A selector that matches zero rows raises rather than silently loading an empty snapshot.

### Proposed layout

```
scraper/
  pyproject.toml
  src/waiver/
    fetch.py       # httpx client, robots check, rate limiting, raw response caching
    sources/       # one module per site: parse(html) -> list[PlayerRosterPct]
    models.py      # pydantic schemas
    load.py        # write validated snapshots to RAW.WAIVER_SNAPSHOT_RAW
    cli.py         # `waiver scrape --source X --dry-run`
  tests/fixtures/  # saved HTML for offline parser tests
```

The resulting `MARTS.WAIVER_TARGETS` view joins the latest snapshot to `AGG_PLAYER_SEASON` on `PlayerId` and surfaces high points-per-game at low roster percentage, with the week-over-week delta as a trend indicator. Player-name matching across sources will need an alias/crosswalk table — `PlayerId` is unlikely to be shared with an external site.

---

## 10. Out of Scope (for now)

- Live in-season projections or forecasting models — this ranks *actual* production.
- Trade analyzers, auction values, and keeper/dynasty valuation.
- Multi-season history and aging curves — the data is available (2015–2024) and `season` is carried throughout, but v1 targets one season.
- The source's `projected/` files and actual-vs-projection analysis (§2).
- IDP scoring (tackles, TFL, passes defensed) as a ranked category; those columns are loaded but not scored (§4).
- Head-to-head league simulation or playoff odds.
- Any automated roster moves against a real league platform.

---

## 11. Decisions and Open Questions

### Resolved

| Question | Decision |
| --- | --- |
| Data source | [hvpkod/NFL-Data](https://github.com/hvpkod/NFL-Data), `NFL-data-Players/{season}/{week}/{POS}.csv`, MIT licensed (§2) |
| Season coverage / refresh | 2025 is the working season and is complete; the pipeline re-points at 2026 when that season starts and refreshes weekly. 2015–2024 also available (§7 Phase 1.5) |
| Grain and week ordering | One row per player per week; `season`/`week` parsed from the file path, so weeks *are* ordered and trend analysis is in scope (§3) |
| Canonical scoring mode | **All three.** Standard, half-PPR, and full PPR are each first-class; `scoring_mode` is a column on every mart (§4) |
| "Top 5 Defenses" | **Team** defenses — `DB`+`LB`+`DL` aggregated to `Team`, not individual defenders (§1, §5) |
| Kicker slot | In scope for v1; `K.csv` has PAT and distance-bucketed field goals (§2, §4) |
| Mobile | Responsive web from the first release; native iOS/Android is a distant Phase 4 (§7, §8) |
| Points allowed / yards allowed | **Yes, addable.** hvpkod has no team file, but nflverse-data supplies both; scheduled as Phase 1.6 (§5a) |

### Still open

1. **Which offensive-yards definition counts as "yards allowed"** — nflverse's team-week splits let you include or exclude sack yardage and return yardage, and leagues differ. Minor, but it moves defenses across tier boundaries.
2. **Which scoring mode headlines the UI** when only one board can be shown at a time (all three are computed regardless).
3. **Kicker and IDP point values** are league-dependent; the defaults in §4 should be checked against the actual league's settings.
4. **Playoff weeks.** Whether weeks 15–18 should be separable from the regular season for "who won championships" views.
