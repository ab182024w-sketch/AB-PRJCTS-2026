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

- **No `Week` column is present in the header.** Games can be counted and aggregated, but they cannot be *ordered*, so trend, streak, and last-N-weeks analysis is unavailable until a `Week` or date column is added to the export. Adding one is cheap and unlocks the Phase 2 per-game trend chart.
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

**Phase 2 — Front end (future)** — stack recommendation in §8
- A web UI to browse the ideal team and the underlying rankings, not just a static CSV.
- Views: a roster board grouped by slot; a sortable/filterable player table (position, team, PPR mode, min games); a player detail page with the scoring breakdown (`pass/rush/rec/misc`) and per-game chart; a team-defense comparison view.
- Interactive scoring: PPR toggle and editable point values that re-rank live, so the "ideal team" can be recomputed under league-specific rules.
- Design intent: dark-mode-first, position-color-coded, responsive, fast — the table is the product, so sorting and filtering must feel instant.

**Phase 3 — Waiver-wire tracking scraper (future)** — stack recommendation in §9
- A scheduled Python scraper that pulls current rostered-percentage / add-drop trend data and merges it against the computed rankings, surfacing players who score well but are widely available.
- Outputs a "waiver targets" view: high season/per-game points, low roster percentage, favorable upcoming opponent.
- Snapshots are stored as a time series so week-over-week roster-percentage *movement* is visible, not only the latest state.

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
- Public/multi-user access without Snowflake logins is needed.
- The design demands custom layout and interactions Streamlit cannot express.
- Sub-second interaction on large tables matters (Streamlit re-runs the script on each interaction).

That stack: **FastAPI** (Python, matches the rest of the project) + `snowflake-connector-python` with a connection pool, `pydantic` response models, **React + TypeScript + Vite**, **TanStack Table** for the sortable/filterable grid, **Tailwind** + **shadcn/ui** for the dark-mode-first design, and **Recharts** for charts. Deploy the API on any container host and the front end as static files.

**Explicitly not recommended:** Dash (heavier than Streamlit for equivalent output here) and Jupyter/Voilà (notebook semantics leak into the UI). A plain static export is also insufficient because live re-ranking is a stated requirement.

### Serving strategy

Query Snowflake directly. The marts are small, and the interactive PPR toggle needs live recomputation. Caching happens at the app layer (`st.cache_data`) plus Snowflake's own result cache; an intermediate export store would add staleness for no real benefit at this data volume.

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
- Multi-season history and aging curves.
- Head-to-head league simulation or playoff odds.
- Any automated roster moves against a real league platform.

---

## 11. Open Questions

1. Can a `Week` or date column be added to the export? Rows are per-game but unordered without it, which blocks trend analysis and makes duplicate detection imprecise. (§3)
2. Which scoring mode is canonical — standard, half-PPR, or full PPR?
3. Which season(s) do the current files cover, and will they be refreshed in-season?
4. Does "top 5 Defenses" mean the 5 best team defenses, or the top 5 individual defenders across `LB`/`DB`/`DL`? This document assumes team defenses.

**Resolved:**
- v1 ranks only on the columns present today — no kicker slot, defenses on the `RetTD`/`FumTD` proxy. The separate kicker and defensive files are deferred to Phase 1.5. (§2, §7)
- Rows are per-player-per-game; season totals are an explicit aggregation and per-game/consistency metrics are in scope. (§3)
