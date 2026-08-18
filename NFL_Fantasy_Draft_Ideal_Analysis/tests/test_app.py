"""Smoke-test the dashboard against the bundled snapshot.

`AppTest` executes the real script, so this catches the failure mode that unit
tests miss: a query or widget that only breaks once Streamlit runs it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP = PROJECT_ROOT / "app" / "streamlit_app.py"
SNAPSHOT = PROJECT_ROOT / "data" / "snapshot"

pytestmark = pytest.mark.skipif(
    not (SNAPSHOT / "ideal_team.parquet").exists(),
    reason="no snapshot; run `python -m pipeline.export_marts --season 2025`",
)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch):
    """Force the snapshot backend, so the test never needs a warehouse."""
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    for variable in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    return streamlit_testing.AppTest.from_file(str(APP), default_timeout=180)


def test_app_runs_on_the_snapshot(app) -> None:
    run = app.run()
    assert not run.exception
    assert "bundled snapshot" in run.caption[-1].value


def test_standard_is_the_default_and_all_three_modes_are_offered(app) -> None:
    run = app.run()
    scoring = run.radio[0]
    assert scoring.value == "standard"
    assert scoring.options == ["Standard", "Half PPR", "Full PPR"]


def test_switching_scoring_mode_reranks(app) -> None:
    standard = app.run()
    before = "".join(element.value for element in standard.markdown)
    full_ppr = standard.radio[0].set_value("full_ppr").run()
    assert not full_ppr.exception
    after = "".join(element.value for element in full_ppr.markdown)
    assert after != before, "receptions should move the WR/TE boards"
