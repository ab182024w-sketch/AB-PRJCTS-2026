"""SQL the dashboard runs.

Every statement here is written to execute unchanged on both backends the app
supports (Snowflake, and DuckDB over an exported snapshot), so the two never
drift: the object names are placeholders filled in by `data.py`, and the
dialect stays inside the intersection of the two engines.

The custom-rules board reimplements `sql/50_ideal_team.sql` as a parameterized
query rather than re-scoring in pandas. With the shipped rule values it
reproduces `MARTS.IDEAL_TEAM` row for row, which `tests/test_custom_board.py`
asserts — scoring truth stays SQL-side (README §8).
"""

from __future__ import annotations

SLOT_DEPTH: tuple[tuple[str, int], ...] = (
    ("QB", 10),
    ("RB", 25),
    ("WR", 25),
    ("TE", 25),
    ("K", 1),
    ("DEF", 5),
)

IDP_POSITIONS = ("DB", "LB", "DL")


def quote(value: object) -> str:
    """A single-quoted SQL literal. Every string these templates interpolate
    goes through here, including ones that today can only come from the marts."""
    return "'" + str(value).replace("'", "''") + "'"


SEASONS = """
SELECT DISTINCT season
FROM {ideal_team}
ORDER BY season DESC
"""

IDEAL_TEAM = """
SELECT slot, slot_rank, player_id, player_name, team, total_pts, games_played,
       pts_per_game, stddev_pts, floor_pts, ceiling_pts, weeks_above_threshold,
       playoff_pts, last_4_pts_per_game
FROM {ideal_team}
WHERE season = {season} AND scoring_mode = '{mode}'
ORDER BY slot, slot_rank
"""

PLAYER_SEASON = """
SELECT player_id, player_name, pos, team, total_pts, pts_per_game, games_played,
       pass_pts, rush_pts, rec_pts, misc_pts, kick_pts, def_pts,
       stddev_pts, cv_pts, floor_pts, ceiling_pts, best_week_pts, worst_week_pts,
       weeks_above_threshold, playoff_pts, last_4_pts_per_game
FROM {agg}
WHERE season = {season} AND scoring_mode = '{mode}'
ORDER BY total_pts DESC
"""

PLAYER_WEEKS = """
SELECT week, is_playoff, opponent, is_away, is_bye,
       pass_pts, rush_pts, rec_pts, misc_pts, kick_pts, def_pts, total_pts
FROM {fct}
WHERE season = {season} AND scoring_mode = '{mode}' AND player_id = '{player_id}'
ORDER BY week
"""

TEAM_DEFENSE = """
SELECT team, total_pts, idp_pts, points_allowed_pts, yards_allowed_pts,
       weeks_played, pts_per_week, avg_points_allowed, avg_yards_allowed,
       stddev_pts, best_week_pts, worst_week_pts, playoff_pts
FROM {team_def}
WHERE season = {season} AND scoring_mode = '{mode}'
ORDER BY total_pts DESC
"""

TEAM_DEFENSE_WEEKS = """
SELECT week, is_playoff, team, idp_pts, points_allowed_pts, yards_allowed_pts,
       total_pts, points_allowed, yards_allowed
FROM {team_def_week}
WHERE season = {season} AND scoring_mode = '{mode}'
ORDER BY week
"""

SCORING_RULES = """
SELECT stat, points_per_unit, component
FROM {rules}
WHERE scoring_mode = '{mode}'
ORDER BY component, stat
"""


def _values_clause(rules: dict[str, float]) -> str:
    rows = ", ".join(
        f"({quote(stat)}, {float(points)})" for stat, points in sorted(rules.items())
    )
    return f"(VALUES {rows}) AS r(stat, points_per_unit)"


def custom_board(rules: dict[str, float], season: int, tier_mode: str = "standard") -> str:
    """The ideal-team board recomputed from league-specific point values.

    `tier_mode` only selects which copy of the points/yards-allowed tier columns
    to read: those bands are the same in all three modes (README §5a).
    """
    slots = ", ".join(f"('{slot}', {depth})" for slot, depth in SLOT_DEPTH)
    idp = ", ".join(quote(pos) for pos in IDP_POSITIONS)
    return f"""
WITH rules AS (SELECT * FROM {_values_clause(rules)}),
weekly AS (
    SELECT s.season, s.week, s.is_playoff, s.player_id, s.pos,
           MIN(s.player_name) AS player_name,
           MIN(s.team)        AS team,
           SUM(COALESCE(s.value * r.points_per_unit, 0)) AS total_pts
    -- LEFT, so a week in which a player recorded only unscored stats is still a
    -- game played. An inner join here silently deflates every per-game average.
    FROM {{stg}} s
    LEFT JOIN rules r ON r.stat = s.stat
    WHERE s.season = {int(season)} AND NOT s.is_bye
    GROUP BY s.season, s.week, s.is_playoff, s.player_id, s.pos
),
player_season AS (
    SELECT season, player_id, pos,
           MIN(player_name)  AS player_name,
           MAX_BY(team, week) AS team,
           SUM(total_pts)     AS total_pts,
           COUNT(DISTINCT week) AS games_played,
           SUM(total_pts) / NULLIF(COUNT(DISTINCT week), 0) AS pts_per_game,
           STDDEV_SAMP(total_pts) AS stddev_pts,
           SUM(CASE WHEN is_playoff THEN total_pts ELSE 0 END) AS playoff_pts
    FROM weekly
    GROUP BY season, player_id, pos
),
idp_week AS (
    SELECT season, week, team, SUM(total_pts) AS idp_pts
    FROM weekly
    WHERE pos IN ({idp}) AND team <> 'FA'
    GROUP BY season, week, team
),
def_week AS (
    SELECT t.season, t.week, t.is_playoff, t.team,
           COALESCE(i.idp_pts, 0) + t.points_allowed_pts + t.yards_allowed_pts AS total_pts
    FROM {{team_def_week}} t
    LEFT JOIN idp_week i
           ON i.season = t.season AND i.week = t.week AND i.team = t.team
    WHERE t.season = {int(season)} AND t.scoring_mode = {quote(tier_mode)}
),
def_season AS (
    SELECT season, team,
           SUM(total_pts) AS total_pts,
           COUNT(*)       AS games_played,
           SUM(total_pts) / NULLIF(COUNT(*), 0) AS pts_per_game,
           STDDEV_SAMP(total_pts) AS stddev_pts,
           SUM(CASE WHEN is_playoff THEN total_pts ELSE 0 END) AS playoff_pts
    FROM def_week
    GROUP BY season, team
),
candidates AS (
    SELECT pos AS slot, player_id, player_name, team, total_pts, games_played,
           pts_per_game, stddev_pts, playoff_pts
    FROM player_season
    WHERE pos IN ('QB', 'RB', 'WR', 'TE', 'K')
    UNION ALL
    SELECT 'DEF' AS slot, team AS player_id, team || ' D/ST' AS player_name, team,
           total_pts, games_played, pts_per_game, stddev_pts, playoff_pts
    FROM def_season
),
depths AS (SELECT * FROM (VALUES {slots}) AS d(slot, depth))
SELECT c.slot,
       ROW_NUMBER() OVER (PARTITION BY c.slot ORDER BY c.total_pts DESC, c.player_id) AS slot_rank,
       c.player_id, c.player_name, c.team,
       ROUND(c.total_pts, 2)    AS total_pts,
       c.games_played,
       ROUND(c.pts_per_game, 2) AS pts_per_game,
       ROUND(c.stddev_pts, 2)   AS stddev_pts,
       ROUND(c.playoff_pts, 2)  AS playoff_pts
FROM candidates c
JOIN depths d ON d.slot = c.slot
QUALIFY slot_rank <= d.depth
ORDER BY c.slot, slot_rank
"""
