"""Plotly figures. Playoff weeks are shaded rather than split out (README §7)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COMPONENT_COLORS = {
    "pass_pts": "#ef476f",
    "rush_pts": "#06d6a0",
    "rec_pts": "#4cc9f0",
    "misc_pts": "#ffd166",
    "kick_pts": "#b892ff",
    "def_pts": "#f8961e",
}

COMPONENT_LABELS = {
    "pass_pts": "Passing",
    "rush_pts": "Rushing",
    "rec_pts": "Receiving",
    "misc_pts": "Misc",
    "kick_pts": "Kicking",
    "def_pts": "Defense",
}

PLAYOFF_START = 15
PLAYOFF_BAND = "rgba(255, 209, 102, 0.12)"

# The theme is chosen in the browser, so the server cannot know which one is
# active. Mid-grey ink and gridlines on a transparent canvas stay legible under
# both instead of committing the figures to a dark background.
NEUTRAL_INK = "#8b949e"
NEUTRAL_LINE = "rgba(128,128,128,0.3)"


def _layout(figure: go.Figure, height: int) -> go.Figure:
    figure.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        template="plotly",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": NEUTRAL_INK},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0},
        hovermode="x unified",
        dragmode=False,
    )
    axes = {
        "gridcolor": NEUTRAL_LINE,
        "zerolinecolor": NEUTRAL_LINE,
        "linecolor": NEUTRAL_LINE,
    }
    figure.update_xaxes(**axes)
    figure.update_yaxes(**axes)
    return figure


def _shade_playoffs(figure: go.Figure, weeks: pd.Series) -> None:
    if weeks.empty or weeks.max() < PLAYOFF_START:
        return
    figure.add_vrect(
        x0=PLAYOFF_START - 0.5,
        x1=float(weeks.max()) + 0.5,
        fillcolor=PLAYOFF_BAND,
        line_width=0,
        annotation_text="fantasy playoffs",
        annotation_position="top left",
        annotation_font_size=11,
    )


def weekly_breakdown(weeks: pd.DataFrame) -> go.Figure:
    """Weekly points, stacked by scoring component."""
    figure = go.Figure()
    for component, label in COMPONENT_LABELS.items():
        if component not in weeks.columns or weeks[component].abs().sum() == 0:
            continue
        figure.add_bar(
            x=weeks["week"],
            y=weeks[component],
            name=label,
            marker_color=COMPONENT_COLORS[component],
        )
    figure.update_layout(barmode="relative", xaxis_title="Week", yaxis_title="Points")
    figure.update_xaxes(dtick=1)
    _shade_playoffs(figure, weeks["week"])
    return _layout(figure, 340)


def defense_weekly(weeks: pd.DataFrame) -> go.Figure:
    """A defense's week, split into what it took away and what it gave up."""
    figure = go.Figure()
    for column, label, color in (
        ("idp_pts", "Playmaking (IDP)", "#06d6a0"),
        ("points_allowed_pts", "Points allowed", "#4cc9f0"),
        ("yards_allowed_pts", "Yards allowed", "#ef476f"),
    ):
        figure.add_bar(x=weeks["week"], y=weeks[column], name=label, marker_color=color)
    figure.update_layout(barmode="relative", xaxis_title="Week", yaxis_title="Points")
    figure.update_xaxes(dtick=1)
    _shade_playoffs(figure, weeks["week"])
    return _layout(figure, 340)


def defense_scatter(defenses: pd.DataFrame, top: int) -> go.Figure:
    """Fantasy value against points allowed — the two need not agree."""
    figure = go.Figure()
    figure.add_scatter(
        x=defenses["avg_points_allowed"],
        y=defenses["total_pts"],
        mode="markers+text",
        text=defenses["team"],
        textposition="top center",
        textfont={"size": 10},
        marker={
            "size": 11,
            "color": defenses["idp_pts"],
            "colorscale": "Teal",
            "colorbar": {"title": "IDP pts", "thickness": 10},
        },
        hovertemplate="%{text}<br>%{y:.0f} pts<br>%{x:.1f} allowed/wk<extra></extra>",
    )
    if len(defenses) > top:
        cutoff = float(defenses["total_pts"].nlargest(top).min())
        figure.add_hline(
            y=cutoff,
            line_dash="dot",
            line_color=NEUTRAL_LINE,
            annotation_text=f"top {top} cutoff",
            annotation_font_size=11,
        )
    figure.update_layout(
        xaxis_title="Points allowed per week", yaxis_title="Season fantasy points"
    )
    return _layout(figure, 420)


def waiver_player_trend(trend: pd.DataFrame, weeks: pd.DataFrame | None) -> go.Figure:
    """A waiver player's add/drop counts over scrape time, with their weekly
    fantasy points below when the crosswalk matched them.

    Two panels rather than one overlay: scrapes live on a wall-clock axis and
    fantasy points on a week-number axis, and the two only share an axis once
    in-season scrapes exist. Stacking keeps both readable in the meantime.
    """
    has_weeks = weeks is not None and not weeks.empty
    figure = make_subplots(
        rows=2 if has_weeks else 1,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.16,
        subplot_titles=(
            ("Adds and drops per scrape", "Weekly fantasy points")
            if has_weeks
            else ("Adds and drops per scrape",)
        ),
    )
    figure.add_scatter(
        x=trend["scraped_at"], y=trend["adds"], name="Adds",
        mode="lines+markers", line={"color": "#06d6a0"}, row=1, col=1,
    )
    figure.add_scatter(
        x=trend["scraped_at"], y=trend["drops"], name="Drops",
        mode="lines+markers", line={"color": "#ef476f"}, row=1, col=1,
    )
    if has_weeks:
        figure.add_bar(
            x=weeks["week"], y=weeks["total_pts"], name="Points",
            marker_color="#4cc9f0", row=2, col=1,
        )
        figure.update_xaxes(title_text="Week", dtick=1, row=2, col=1)
    figure.update_layout(hovermode="closest")
    return _layout(figure, 460 if has_weeks else 300)


def rank_comparison(frame: pd.DataFrame, label_column: str) -> go.Figure:
    """Season points as a horizontal bar, best at the top."""
    ordered = frame.iloc[::-1]
    figure = go.Figure(
        go.Bar(
            x=ordered["total_pts"],
            y=ordered[label_column],
            orientation="h",
            marker_color="#4cc9f0",
            hovertemplate="%{y}: %{x:.1f}<extra></extra>",
        )
    )
    figure.update_layout(xaxis_title="Season points", yaxis_title=None)
    return _layout(figure, max(280, 22 * len(frame)))
