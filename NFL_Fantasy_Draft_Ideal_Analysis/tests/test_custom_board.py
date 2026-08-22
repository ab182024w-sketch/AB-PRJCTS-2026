"""The custom-rules board must reduce to the shipped one.

`app/queries.custom_board` re-scores from `STG_PLAYER_WEEK` so a league can edit
point values, which means it is a second implementation of
`sql/50_ideal_team.sql` and could silently drift from it. Fed the shipped rule
values it has to return exactly `MARTS.IDEAL_TEAM`, so that is the test.

It runs against the Parquet snapshot, on DuckDB — no warehouse required:

    python -m pipeline.export_marts --season 2025
    pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "app"))

import queries

SNAPSHOT = PROJECT_ROOT / "data" / "snapshot"
SEASON = 2025
KEY = ["slot", "slot_rank"]


pytestmark = pytest.mark.skipif(
    not (SNAPSHOT / "ideal_team.parquet").exists(),
    reason="no snapshot; run `python -m pipeline.export_marts --season 2025`",
)


@pytest.fixture(scope="module")
def connection():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(database=":memory:")
    for name in ("ideal_team", "team_def_week", "stg", "rules", "agg", "fct"):
        con.execute(
            f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{SNAPSHOT / f'{name}.parquet'}')"
        )
    return con


def shipped_rules(connection, mode: str) -> dict[str, float]:
    rows = connection.execute(
        "SELECT stat, points_per_unit FROM rules WHERE scoring_mode = ?", [mode]
    ).fetchall()
    return {stat: float(points) for stat, points in rows}


@pytest.mark.parametrize("mode", ["standard", "half_ppr", "full_ppr"])
def test_custom_board_matches_ideal_team(connection, mode: str) -> None:
    rules = shipped_rules(connection, mode)
    assert rules, f"no shipped rules for {mode}"

    sql = queries.custom_board(rules, SEASON).format(
        stg="stg", fct="fct", team_def_week="team_def_week"
    )
    computed = connection.execute(sql).fetch_df().set_index(KEY).sort_index()
    expected = (
        connection.execute(
            "SELECT slot, slot_rank, player_id, player_name, team, total_pts, games_played "
            "FROM ideal_team WHERE season = ? AND scoring_mode = ?",
            [SEASON, mode],
        )
        .fetch_df()
        .set_index(KEY)
        .sort_index()
    )

    assert len(computed) == len(expected) == 91
    for column in ("player_id", "player_name", "team", "games_played"):
        mismatched = computed[column] != expected[column]
        assert not mismatched.any(), computed[mismatched].join(
            expected[mismatched], rsuffix="_expected"
        )
    assert (computed["total_pts"] - expected["total_pts"]).abs().max() < 0.011


@pytest.mark.parametrize("mode", ["standard", "half_ppr", "full_ppr"])
def test_custom_player_season_matches_the_season_agg(connection, mode: str) -> None:
    """The rankings board re-scores too, so it needs the same parity guarantee."""
    rules = shipped_rules(connection, mode)
    sql = queries.custom_player_season(rules, SEASON).format(stg="stg", fct="fct")
    computed = connection.execute(sql).fetch_df().set_index("player_id").sort_index()
    expected = (
        connection.execute(
            "SELECT player_id, player_name, pos, team, total_pts, games_played, "
            "playoff_pts, last_4_pts_per_game "
            "FROM agg WHERE season = ? AND scoring_mode = ?",
            [SEASON, mode],
        )
        .fetch_df()
        .set_index("player_id")
        .sort_index()
    )

    # Every player in the mart, including the ones who never recorded a stat.
    assert len(computed) == len(expected)
    for column in ("player_name", "pos", "team", "games_played"):
        mismatched = computed[column] != expected[column]
        assert not mismatched.any(), computed[mismatched].join(
            expected[mismatched], rsuffix="_expected"
        )
    for column in ("total_pts", "playoff_pts", "last_4_pts_per_game"):
        assert (computed[column] - expected[column]).abs().max() < 0.011, column


def test_slot_depths_match_the_readme() -> None:
    assert dict(queries.SLOT_DEPTH) == {
        "QB": 10,
        "RB": 25,
        "WR": 25,
        "TE": 25,
        "K": 1,
        "DEF": 5,
    }
