"""The board renders its own HTML, so the mobile contract is tested here."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import render

COLUMNS = [
    render.Column("player_name", "Player", "{}"),
    render.Column("total_pts", "Points"),
    render.Column("stddev_pts", "Std dev", detail=True),
]


@pytest.fixture
def frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "slot_rank": 1,
                "player_name": "Josh Allen",
                "pos": "QB",
                "team": "BUF",
                "total_pts": 356.62,
                "stddev_pts": 8.4,
            },
            {
                "slot_rank": 2,
                "player_name": "Ken <script>",
                "pos": "QB",
                "team": "LAR",
                "total_pts": 342.375,
                "stddev_pts": None,
            },
        ]
    )


def test_detail_columns_are_hidden_until_asked_for(frame: pd.DataFrame) -> None:
    assert "Std dev" not in render.board_table(frame, COLUMNS, show_detail=False)
    assert "Std dev" in render.board_table(frame, COLUMNS, show_detail=True)


def test_every_cell_carries_its_mobile_label(frame: pd.DataFrame) -> None:
    """Card mode drops the header row, so a cell without `data-label` renders as
    an unlabelled number on a phone."""
    html = render.board_table(frame, COLUMNS, show_detail=True, rank_column="slot_rank")
    assert html.count("<td") == html.count("data-label=")
    assert 'data-label="Rank"' in html


def test_player_names_cannot_inject_markup(frame: pd.DataFrame) -> None:
    html = render.board_table(frame, COLUMNS, show_detail=False)
    assert "<script>" not in html
    assert "Ken &lt;script&gt;" in html


def test_missing_values_render_as_a_dash(frame: pd.DataFrame) -> None:
    assert "—" in render.board_table(frame, COLUMNS, show_detail=True)


def test_rank_column_falls_back_to_row_order(frame: pd.DataFrame) -> None:
    html = render.board_table(
        frame.drop(columns=["slot_rank"]), COLUMNS, show_detail=False, rank_column="slot_rank"
    )
    assert '<td class="rank left" data-label="Rank">1</td>' in html
    assert '<td class="rank left" data-label="Rank">2</td>' in html
