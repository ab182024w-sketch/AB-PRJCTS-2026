"""Phase 3 waiver layer, tested offline.

Two halves. The SQL half runs the app's waiver queries on DuckDB against the
exported snapshot, the way the other tests exercise the ranking queries. The
UI half proves the "degrade, do not break" rule: with the waiver Parquet files
missing entirely, every tab still renders and the waiver ones show the
explicit empty state.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "app"))

import queries

import data

APP = PROJECT_ROOT / "app" / "streamlit_app.py"
SNAPSHOT = PROJECT_ROOT / "data" / "snapshot"
SEASON = 2025

pytestmark = pytest.mark.skipif(
    not (SNAPSHOT / "ideal_team.parquet").exists(),
    reason="no snapshot; run `python -m pipeline.export_marts --season 2025`",
)

waiver_snapshot = pytest.mark.skipif(
    not (SNAPSHOT / "waiver_targets.parquet").exists(),
    reason="snapshot has no waiver data; run the scraper, 60_waiver.sql, export",
)


@pytest.fixture(scope="module")
def connection():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(database=":memory:")
    for name in ("waiver_targets", "waiver_trend", "fct"):
        path = SNAPSHOT / f"{name}.parquet"
        if path.exists():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return con


@waiver_snapshot
def test_waiver_targets_query_runs_on_the_snapshot(connection) -> None:
    sql = queries.WAIVER_TARGETS.format(
        waiver_targets="waiver_targets", season=SEASON, mode="standard"
    )
    frame = connection.execute(sql).fetch_df()
    assert not frame.empty
    # the board's contract: production, movement, provenance
    for column in (
        "player_name", "pos", "team", "adds", "drops", "delta_adds",
        "total_pts", "pts_per_game", "games_played", "scraped_at", "match_status",
    ):
        assert column in frame.columns, column
    # matched rows carry season production; unmatched rows are present, not dropped
    matched = frame[frame["player_id"].notna()]
    assert not matched.empty
    assert matched["total_pts"].notna().all()
    assert set(frame["match_status"]) <= {"matched", "matched_by_team", "ambiguous", "unmatched"}


@waiver_snapshot
def test_waiver_trend_query_runs_on_the_snapshot(connection) -> None:
    row = connection.execute(
        "SELECT source, external_player_id FROM waiver_trend LIMIT 1"
    ).fetchone()
    sql = queries.WAIVER_TREND_PLAYER.format(
        waiver_trend="waiver_trend", source=row[0], external_player_id=row[1]
    )
    trend = connection.execute(sql).fetch_df()
    assert not trend.empty
    assert trend["scraped_at"].is_monotonic_increasing


@waiver_snapshot
def test_every_waiver_row_is_stamped(connection) -> None:
    for table in ("waiver_targets", "waiver_trend"):
        nulls = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE scraped_at IS NULL"
        ).fetchone()[0]
        assert nulls == 0, table


@pytest.fixture
def app_without_waiver_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The app pointed at a snapshot that predates Phase 3: every ranking file,
    no waiver files."""
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    for parquet in SNAPSHOT.glob("*.parquet"):
        if not parquet.name.startswith("waiver"):
            shutil.copy(parquet, tmp_path / parquet.name)
    for variable in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("FANTASY_SNAPSHOT_DIR", str(tmp_path))
    # Streamlit's caches are process-global: another test may already have
    # built the DuckDB connection over the full snapshot, so both cache layers
    # must be dropped for the redirected directory to take effect.
    data._duckdb_connection.clear()
    data.run_query.clear()
    yield streamlit_testing.AppTest.from_file(str(APP), default_timeout=180)
    data._duckdb_connection.clear()
    data.run_query.clear()


def test_waiver_tabs_degrade_without_waiver_data(app_without_waiver_data) -> None:
    run = app_without_waiver_data.run()
    assert not run.exception, "missing waiver data must never break the app"
    # the rankings still rendered
    assert "bundled snapshot" in run.caption[-1].value
    # and the waiver tabs show the explicit empty state
    empty_states = [element for element in run.info if "No waiver scrape" in element.value]
    assert len(empty_states) == 3, "all three waiver tabs show the empty state"
