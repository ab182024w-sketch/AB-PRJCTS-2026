"""Ideal Fantasy Team — the Phase 2 dashboard (README §7, §8).

Run it:

    streamlit run app/streamlit_app.py

It reads Snowflake when credentials are present and a bundled Parquet snapshot
otherwise (`app/data.py`), so the same file runs in Streamlit in Snowflake, on a
container host, and on a laptop with no warehouse.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import charts
import queries
from data import get_backend, run_query
from render import CSS, Column, board_table, slot_heading

MODES = {"standard": "Standard", "half_ppr": "Half PPR", "full_ppr": "Full PPR"}
SLOT_ORDER = ("QB", "RB", "WR", "TE", "K", "DEF")

BOARD_COLUMNS = [
    Column("player_name", "Player", "{}"),
    Column("total_pts", "Points"),
    Column("pts_per_game", "Per game"),
    Column("games_played", "G", "{:.0f}"),
    Column("playoff_pts", "Playoff pts", detail=True),
    Column("stddev_pts", "Std dev", detail=True),
    Column("floor_pts", "Floor", detail=True),
    Column("ceiling_pts", "Ceiling", detail=True),
    Column("weeks_above_threshold", "Startable wks", "{:.0f}", detail=True),
    Column("last_4_pts_per_game", "Last 4/g", detail=True),
]

RANKING_COLUMNS = [
    Column("player_name", "Player", "{}"),
    Column("total_pts", "Points"),
    Column("pts_per_game", "Per game"),
    Column("games_played", "G", "{:.0f}"),
    Column("playoff_pts", "Playoff pts", detail=True),
    Column("stddev_pts", "Std dev", detail=True),
    Column("floor_pts", "Floor", detail=True),
    Column("ceiling_pts", "Ceiling", detail=True),
    Column("last_4_pts_per_game", "Last 4/g", detail=True),
]

DEFENSE_COLUMNS = [
    Column("team", "Defense", "{}"),
    Column("total_pts", "Points"),
    Column("idp_pts", "Playmaking"),
    Column("points_allowed_pts", "Pts allowed"),
    Column("yards_allowed_pts", "Yds allowed"),
    Column("avg_points_allowed", "PA/wk", detail=True),
    Column("avg_yards_allowed", "YA/wk", "{:.0f}", detail=True),
    Column("playoff_pts", "Playoff pts", detail=True),
    Column("stddev_pts", "Std dev", detail=True),
]


def base_rules(mode: str) -> dict[str, float]:
    frame = run_query(queries.SCORING_RULES, mode=mode)
    return {row.stat: float(row.points_per_unit) for row in frame.itertuples()}


def active_rules(mode: str) -> dict[str, float] | None:
    """The user's edited rules, or None when they match the shipped ones."""
    edited = st.session_state.get("custom_rules", {}).get(mode)
    if not edited:
        return None
    if edited == base_rules(mode):
        return None
    return edited


def sidebar_controls(seasons: list[int]) -> tuple[int, str, bool]:
    """Controls live at the top, not in the sidebar — Streamlit's sidebar is
    cramped on a phone and the mode toggle is the most-used control here."""
    with st.container():
        left, middle, right = st.columns([1, 2, 1])
        season = left.selectbox("Season", seasons, index=0)
        mode = middle.radio(
            "Scoring",
            list(MODES),
            format_func=lambda key: MODES[key],
            horizontal=True,
            index=0,
        )
        detail = right.toggle("More detail", value=False, help="Show consistency columns")
    return int(season), str(mode), bool(detail)


def ideal_team_page(season: int, mode: str, detail: bool) -> None:
    rules = active_rules(mode)
    if rules is None:
        board = run_query(queries.IDEAL_TEAM, season=season, mode=mode)
    else:
        st.info(
            "Custom league rules are active — this board is recomputed from the raw "
            "stat lines, not from the precomputed `MARTS.IDEAL_TEAM`.",
            icon="⚙️",
        )
        board = run_query(queries.custom_board(rules, season))

    st.caption(
        f"{MODES[mode]} · {season} · 10 QB / 25 RB / 25 WR / 25 TE / 1 K / 5 DEF. "
        "Playoff points are weeks 15–18, already included in the season total."
    )

    for slot in SLOT_ORDER:
        group = board[board["slot"] == slot]
        if group.empty:
            continue
        leader = group.iloc[0]
        st.markdown(
            slot_heading(slot, f'{len(group)} deep · best: {leader["player_name"]}'),
            unsafe_allow_html=True,
        )
        st.markdown(
            board_table(
                group,
                [column for column in BOARD_COLUMNS if column.key in group.columns],
                show_detail=detail,
                pos_column=None,
                rank_column="slot_rank",
            ),
            unsafe_allow_html=True,
        )


def rankings_page(season: int, mode: str, detail: bool) -> None:
    players = run_query(queries.PLAYER_SEASON, season=season, mode=mode)
    with st.expander("Filters", expanded=False):
        left, right = st.columns(2)
        positions = right.multiselect(
            "Positions", sorted(players["pos"].unique()), default=["QB", "RB", "WR", "TE"]
        )
        teams = right.multiselect("Teams", sorted(players["team"].dropna().unique()))
        search = left.text_input("Player name contains", "")
        max_games = int(players["games_played"].max())
        min_games = left.slider("Minimum games", 0, max_games, 0)
        sort_by = left.selectbox(
            "Sort by",
            ["total_pts", "pts_per_game", "playoff_pts", "last_4_pts_per_game", "stddev_pts"],
            format_func=lambda key: key.replace("_", " "),
        )
        limit = right.slider("Rows", 10, 300, 50, step=10)

    filtered = players[players["games_played"] >= min_games]
    if positions:
        filtered = filtered[filtered["pos"].isin(positions)]
    if teams:
        filtered = filtered[filtered["team"].isin(teams)]
    if search:
        filtered = filtered[filtered["player_name"].str.contains(search, case=False, na=False)]
    filtered = filtered.sort_values(sort_by, ascending=False).head(limit)

    st.caption(f"{len(filtered)} of {len(players)} players · {MODES[mode]} · {season}")
    st.markdown(
        board_table(filtered, RANKING_COLUMNS, show_detail=detail, rank_column=None),
        unsafe_allow_html=True,
    )


def player_page(season: int, mode: str) -> None:
    players = run_query(queries.PLAYER_SEASON, season=season, mode=mode)
    labels = {
        f'{row.player_name} · {row.pos} · {row.team}': row.player_id
        for row in players.itertuples()
    }
    choice = st.selectbox("Player", list(labels), index=0)
    player_id = labels[choice]
    record = players[players["player_id"] == player_id].iloc[0]

    columns = st.columns(4)
    columns[0].metric("Season points", f'{record["total_pts"]:.1f}')
    columns[1].metric("Per game", f'{record["pts_per_game"]:.1f}')
    columns[2].metric("Games", int(record["games_played"]))
    columns[3].metric("Playoff points", f'{record["playoff_pts"]:.1f}')

    weeks = run_query(queries.PLAYER_WEEKS, season=season, mode=mode, player_id=player_id)
    played = weeks[~weeks["is_bye"].astype(bool)]
    st.plotly_chart(
        charts.weekly_breakdown(played), width="stretch", config={"displayModeBar": False}
    )

    detail_columns = [
        Column("week", "Week", "{:.0f}"),
        Column("opponent", "Opp", "{}"),
        Column("total_pts", "Points"),
        Column("pass_pts", "Pass", detail=True),
        Column("rush_pts", "Rush", detail=True),
        Column("rec_pts", "Rec", detail=True),
        Column("misc_pts", "Misc", detail=True),
    ]
    display = played.copy()
    display["week"] = display.apply(
        lambda row: f'{int(row["week"])}★' if row["is_playoff"] else str(int(row["week"])),
        axis=1,
    )
    display["week"] = display["week"].astype(str)
    st.markdown(
        board_table(
            display,
            [Column(column.key, column.label, "{}" if column.key == "week" else column.fmt, column.detail)
             for column in detail_columns],
            show_detail=True,
            pos_column=None,
        ),
        unsafe_allow_html=True,
    )
    st.caption("★ marks a fantasy playoff week (15–18); it still counts in the season total.")


def defense_page(season: int, mode: str, detail: bool) -> None:
    defenses = run_query(queries.TEAM_DEFENSE, season=season, mode=mode)
    st.caption(
        "Team defense is DB + LB + DL rolled up, plus the points-allowed and "
        "yards-allowed tiers from the nflverse feed (§5a). The tiers are identical "
        "in all three scoring modes."
    )
    st.markdown(
        board_table(defenses, DEFENSE_COLUMNS, show_detail=detail, pos_column=None, rank_column=None),
        unsafe_allow_html=True,
    )

    st.subheader("Value against what they gave up")
    st.plotly_chart(
        charts.defense_scatter(defenses, top=5),
        width="stretch",
        config={"displayModeBar": False},
    )

    team = st.selectbox("Weekly detail", defenses["team"].tolist())
    weeks = run_query(queries.TEAM_DEFENSE_WEEKS, season=season, mode=mode)
    st.plotly_chart(
        charts.defense_weekly(weeks[weeks["team"] == team]),
        width="stretch",
        config={"displayModeBar": False},
    )


def rules_page(mode: str) -> None:
    st.caption(
        "Edit the point values to match your league. The board is then recomputed "
        "from the weekly stat lines by the same SQL that builds the shipped one, so "
        "there is no second copy of the scoring math."
    )
    shipped = base_rules(mode)
    current = st.session_state.get("custom_rules", {}).get(mode, dict(shipped))

    components = run_query(queries.SCORING_RULES, mode=mode)
    edited: dict[str, float] = {}
    with st.form("rules"):
        for component, group in components.groupby("component"):
            st.markdown(f'**{component.replace("_pts", "").title()}**')
            columns = st.columns(3)
            for index, row in enumerate(group.itertuples()):
                edited[row.stat] = columns[index % 3].number_input(
                    row.stat,
                    value=float(current.get(row.stat, row.points_per_unit)),
                    step=0.1,
                    format="%.3f",
                )
        applied = st.form_submit_button("Apply to boards", type="primary")

    if applied:
        st.session_state.setdefault("custom_rules", {})[mode] = edited
        # Every tab is rendered in one pass, and the boards were drawn before this
        # form was submitted. Without the rerun the edited rules would not reach
        # them until the user's next interaction.
        st.rerun()

    stored = st.session_state.get("custom_rules", {}).get(mode)
    if stored is not None:
        changed = {stat: value for stat, value in stored.items() if value != shipped[stat]}
        if changed:
            st.success(f'{len(changed)} rule(s) changed: {", ".join(sorted(changed))}')
        else:
            st.info("Back to the shipped values — boards will use the precomputed tables.")

    if st.button("Reset to shipped values"):
        st.session_state.get("custom_rules", {}).pop(mode, None)
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Ideal Fantasy Team", page_icon="🏈", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("🏈 Ideal Fantasy Team")

    backend = get_backend()
    if backend.kind == "none":
        st.error(
            "No data source. Either set `SNOWFLAKE_ACCOUNT`/`SNOWFLAKE_USER`/"
            "`SNOWFLAKE_PASSWORD`, or build a local snapshot with "
            "`python -m pipeline.export_marts --season 2025`."
        )
        return

    seasons = run_query(queries.SEASONS)["season"].tolist()
    season, mode, detail = sidebar_controls([int(value) for value in seasons])

    board, rankings, player, defense, rules = st.tabs(
        ["Ideal team", "Rankings", "Player", "Defense", "League rules"]
    )
    with board:
        ideal_team_page(season, mode, detail)
    with rankings:
        rankings_page(season, mode, detail)
    with player:
        player_page(season, mode)
    with defense:
        defense_page(season, mode, detail)
    with rules:
        rules_page(mode)

    st.caption(f"Data source: {backend.label}")


if __name__ == "__main__":
    main()
