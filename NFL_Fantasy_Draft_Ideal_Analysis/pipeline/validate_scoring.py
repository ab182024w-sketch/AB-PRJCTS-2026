"""Local verification harness for the Phase 1 scoring logic (pandas only).

This is a *verification tool*, not a second pipeline: it runs the same tall-shape
+ SCORING_RULES-join logic the Snowflake SQL runs, over the CSVs Phase 0
downloaded, so the scoring rules and the reconciliations can be checked without
a Snowflake account.

    python -m pipeline.validate_scoring --season 2025
    python -m pipeline.validate_scoring --season 2025 --top 10 --out reference/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from pipeline.scoring import (
    DEFENSE_POSITIONS,
    DEFENSE_RULES,
    DEFENSE_STAT_COLUMNS,
    KICKER_POSITIONS,
    KICKER_RULES,
    KICKER_STAT_COLUMNS,
    OFFENSE_POSITIONS,
    OFFENSE_RULES,
    OFFENSE_STAT_COLUMNS,
    PLAYOFF_WEEKS,
    POSITION_THRESHOLDS,
    SCORING_MODES,
    SLOT_DEPTH,
    STAT_COMPONENT,
    rules_rows,
)

KEY_COLUMNS = ["PlayerName", "PlayerId", "Pos", "Team", "PlayerOpponent"]
WEEK_FILE_RE = re.compile(r"(?P<season>\d{4})/(?P<week>\d{1,2})/(?P<pos>[A-Z]{1,2})\.csv$")


def _column_map(pos: str) -> dict[str, str]:
    if pos in OFFENSE_POSITIONS:
        return OFFENSE_STAT_COLUMNS
    if pos in KICKER_POSITIONS:
        return KICKER_STAT_COLUMNS
    return DEFENSE_STAT_COLUMNS


def load_player_weeks(data_dir: Path, season: int) -> tuple[pd.DataFrame, int]:
    """Return the tall STG_PLAYER_WEEK equivalent plus the cast-failure count."""
    frames: list[pd.DataFrame] = []
    cast_failures = 0

    for csv_path in sorted((data_dir / str(season)).rglob("*.csv")):
        match = WEEK_FILE_RE.search(csv_path.as_posix())
        if not match:  # {POS}_season.csv — reconciliation only, never an input
            continue
        week = int(match["week"])
        pos = match["pos"]
        raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False, na_values=[""])
        if raw.empty:
            continue

        stat_columns = {c: n for c, n in _column_map(pos).items() if c in raw.columns}
        frame = raw[KEY_COLUMNS + list(stat_columns)].rename(columns=stat_columns)
        tall = frame.melt(id_vars=KEY_COLUMNS, var_name="stat", value_name="raw_value")

        value = pd.to_numeric(tall["raw_value"], errors="coerce")
        # A blank cell legitimately means zero; a *non-blank* cell that fails to
        # parse is a bug and must be counted before COALESCE hides it (README §5).
        cast_failures += int((tall["raw_value"].notna() & value.isna()).sum())
        tall["value"] = value.fillna(0.0)

        opponent = tall["PlayerOpponent"].fillna("")
        tall["season"] = season
        tall["week"] = week
        tall["is_playoff"] = week in PLAYOFF_WEEKS
        tall["is_away"] = opponent.str.startswith("@")
        tall["is_bye"] = opponent.str.upper() == "BYE"
        tall["opponent"] = opponent.str.lstrip("@").replace({"Bye": None, "BYE": None})
        frames.append(tall.drop(columns=["raw_value", "PlayerOpponent"]))

    if not frames:
        raise SystemExit(f"no week files found under {data_dir / str(season)} — run pipeline.download first")

    tall = pd.concat(frames, ignore_index=True)
    return tall.rename(columns={"PlayerId": "player_id", "PlayerName": "player_name", "Pos": "pos", "Team": "team"}), cast_failures


def score(tall: pd.DataFrame) -> pd.DataFrame:
    """FCT_PLAYER_SCORING: one row per (season, week, player_id, scoring_mode)."""
    rules = pd.DataFrame(rules_rows(), columns=["scoring_mode", "stat", "points_per_unit"])
    rules["component"] = rules["stat"].map(STAT_COMPONENT)

    scored = tall.merge(rules, on="stat", how="inner")
    scored["points"] = scored["value"] * scored["points_per_unit"]

    grouped = scored.groupby(
        ["season", "week", "player_id", "player_name", "pos", "team", "scoring_mode", "is_bye", "is_playoff", "component"],
        dropna=False,
        observed=True,
    )["points"].sum().reset_index()

    wide = grouped.pivot_table(
        index=["season", "week", "player_id", "player_name", "pos", "team", "scoring_mode", "is_bye", "is_playoff"],
        columns="component",
        values="points",
        fill_value=0.0,
    ).reset_index()
    wide.columns.name = None
    for component in ("pass_pts", "rush_pts", "rec_pts", "misc_pts", "kick_pts", "def_pts"):
        if component not in wide.columns:
            wide[component] = 0.0
    wide["total_pts"] = wide[["pass_pts", "rush_pts", "rec_pts", "misc_pts", "kick_pts", "def_pts"]].sum(axis=1)
    return wide


def aggregate_season(scored: pd.DataFrame) -> pd.DataFrame:
    """AGG_PLAYER_SEASON: totals plus per-game and consistency metrics."""
    played = scored[~scored["is_bye"]].copy()
    played["threshold"] = played["pos"].map(POSITION_THRESHOLDS).fillna(0.0)
    played["above_threshold"] = played["total_pts"] >= played["threshold"]
    last4 = played[played["week"] > played["week"].max() - 4]

    agg = played.groupby(["season", "player_id", "player_name", "pos", "scoring_mode"], observed=True).agg(
        team=("team", "last"),
        total_pts=("total_pts", "sum"),
        games_played=("week", "nunique"),
        stddev_pts=("total_pts", "std"),
        best_week=("total_pts", "max"),
        worst_week=("total_pts", "min"),
        floor_pts=("total_pts", lambda s: s.quantile(0.20)),
        ceiling_pts=("total_pts", lambda s: s.quantile(0.80)),
        weeks_above_threshold=("above_threshold", "sum"),
        playoff_pts=("total_pts", "sum"),
    ).reset_index()

    playoff = played[played["is_playoff"]].groupby(["season", "player_id", "scoring_mode"], observed=True)["total_pts"].sum()
    agg["playoff_pts"] = agg.set_index(["season", "player_id", "scoring_mode"]).index.map(playoff).fillna(0.0)
    agg["pts_per_game"] = agg["total_pts"] / agg["games_played"].replace(0, pd.NA)
    agg["cv"] = agg["stddev_pts"] / agg["pts_per_game"]
    l4 = last4.groupby(["season", "player_id", "scoring_mode"], observed=True)["total_pts"].mean()
    agg["last_4_pts_per_game"] = agg.set_index(["season", "player_id", "scoring_mode"]).index.map(l4)
    return agg.round(2)


def team_defense(scored: pd.DataFrame) -> pd.DataFrame:
    """FCT_TEAM_DEFENSE: DB+LB+DL summed to the team, season grain.

    Excludes FA rows (no team attribution) and bye rows, so `weeks` is games
    played (17), not calendar weeks (18).
    """
    defense = scored[
        scored["pos"].isin(DEFENSE_POSITIONS)
        & (scored["team"] != "FA")
        & ~scored["is_bye"]
    ]
    return (
        defense.groupby(["season", "team", "scoring_mode"], observed=True)
        .agg(total_pts=("def_pts", "sum"), weeks=("week", "nunique"))
        .reset_index()
        .assign(pts_per_week=lambda d: (d["total_pts"] / d["weeks"]).round(2))
        .round(2)
    )


def ideal_team(agg: pd.DataFrame, defense: pd.DataFrame, depth: dict[str, int]) -> pd.DataFrame:
    """IDEAL_TEAM: top-N per slot per scoring mode, unioned into one board."""
    boards = []
    for slot, n in depth.items():
        if slot == "DEF":
            board = (
                defense.sort_values(["total_pts", "team"], ascending=[False, True])
                .groupby("scoring_mode", observed=True)
                .head(n)
                .assign(slot="DEF", player_name=lambda d: d["team"] + " D/ST", player_id=None,
                        games_played=lambda d: d["weeks"], pts_per_game=lambda d: d["pts_per_week"])
            )
        else:
            board = (
                agg[agg["pos"] == slot]
                .sort_values(["total_pts", "player_id"], ascending=[False, True])
                .groupby("scoring_mode", observed=True)
                .head(n)
                .assign(slot=slot)
            )
        boards.append(board[["scoring_mode", "slot", "player_name", "team", "total_pts", "games_played", "pts_per_game"]])
    board = pd.concat(boards, ignore_index=True)
    board["slot_rank"] = board.groupby(["scoring_mode", "slot"], observed=True)["total_pts"].rank(method="first", ascending=False).astype(int)
    return board.sort_values(["scoring_mode", "slot", "slot_rank"]).reset_index(drop=True)


def load_source_points(data_dir: Path, season: int) -> pd.DataFrame:
    """The source's own `TotalPoints`, at week grain — reconciliation only."""
    source = []
    for csv_path in sorted((data_dir / str(season)).rglob("*.csv")):
        match = WEEK_FILE_RE.search(csv_path.as_posix())
        if not match:
            continue
        raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False, na_values=[""])
        if raw.empty:
            continue
        source.append(
            pd.DataFrame({
                "week": int(match["week"]),
                "player_id": raw["PlayerId"],
                "pos": raw["Pos"],
                "source_pts": pd.to_numeric(raw["TotalPoints"], errors="coerce").fillna(0.0),
            })
        )
    return pd.concat(source, ignore_index=True)


def unscored_weeks(source_df: pd.DataFrame) -> list[int]:
    """Weeks where the source published stats but left every TotalPoints at 0.

    2025 week 18 is such a week. Including it would make the reconciliation look
    catastrophically broken when nothing is wrong with our scoring at all.
    """
    totals = source_df.groupby("week")["source_pts"].sum()
    return sorted(totals[totals == 0].index.tolist())


def reconcile_total_points(scored: pd.DataFrame, source_df: pd.DataFrame, skip_weeks: list[int]) -> pd.DataFrame:
    merged = scored.merge(source_df, on=["week", "player_id", "pos"], how="inner")
    merged = merged[~merged["week"].isin(skip_weeks)]
    merged["diff"] = (merged["total_pts"] - merged["source_pts"]).round(3)
    return (
        merged.groupby(["scoring_mode", "pos"], observed=True)
        .agg(
            rows=("diff", "size"),
            mean_diff=("diff", "mean"),
            median_diff=("diff", "median"),
            pct_within_0_1=("diff", lambda s: (s.abs() <= 0.1).mean() * 100),
            max_abs_diff=("diff", lambda s: s.abs().max()),
        )
        .round(3)
        .reset_index()
    )


def run_checks(tall: pd.DataFrame, agg: pd.DataFrame, cast_failures: int, source_df: pd.DataFrame) -> pd.DataFrame:
    """The README §6 assertions, run locally — the same checks as sql/99_tests.sql."""
    header = tall.drop_duplicates(["season", "week", "player_id"])
    weeks = sorted(header["week"].unique())
    positions_per_week = tall.groupby("week")["pos"].nunique()
    games = agg[agg["scoring_mode"] == "standard"]["games_played"]
    sacks = tall.loc[tall["stat"] == "tackles_sck", "value"]
    checks = [
        ("season_week_parsed", int(header["week"].isna().sum() + (~header["week"].between(1, 18)).sum())),
        ("grain_unique", int(tall.duplicated(["season", "week", "player_id", "stat"]).sum())),
        ("key_columns_valid", int(header["player_id"].isna().sum() + (~header["pos"].isin(list(OFFENSE_POSITIONS + KICKER_POSITIONS + DEFENSE_POSITIONS))).sum())),
        ("all_positions_present_every_week", int((positions_per_week < 8).sum())),
        ("no_cast_failures", cast_failures),
        ("games_played_in_range", int(((games < 0) | (games > 18)).sum())),
        ("passing_yds_under_700", int((tall.loc[tall["stat"] == "passing_yds", "value"] > 700).sum())),
        ("sacks_are_half_multiples", int(((sacks * 2) % 1 != 0).sum())),
        ("bye_rows_present", 0 if header["is_bye"].any() else 1),
        ("weeks_covered_1_18", 0 if weeks == list(range(1, 19)) else 1),
    ]
    return pd.DataFrame(checks, columns=["check", "failures"]).assign(
        status=lambda d: d["failures"].map(lambda n: "pass" if n == 0 else "FAIL")
    )


def reconcile_season_files(tall: pd.DataFrame, data_dir: Path, season: int) -> pd.DataFrame:
    """Sum our per-week stats to the season and compare to `{POS}_season.csv`."""
    ours = tall.groupby(["player_id", "pos", "stat"], observed=True)["value"].sum().reset_index()
    rows = []
    for csv_path in sorted((data_dir / str(season)).glob("*_season.csv")):
        pos = csv_path.stem.split("_")[0]
        raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False, na_values=[""])
        stat_columns = {c: n for c, n in _column_map(pos).items() if c in raw.columns}
        frame = raw[["PlayerId", "Pos"] + list(stat_columns)].rename(columns=stat_columns)
        tallf = frame.melt(id_vars=["PlayerId", "Pos"], var_name="stat", value_name="value")
        tallf["value"] = pd.to_numeric(tallf["value"], errors="coerce").fillna(0.0)
        rows.append(tallf.rename(columns={"PlayerId": "player_id", "Pos": "pos"}))
    theirs = pd.concat(rows, ignore_index=True).groupby(["player_id", "pos", "stat"], observed=True)["value"].sum().reset_index()

    # Rate/among-week-only columns cannot be summed across weeks meaningfully.
    non_additive = {"reception_percentage", "fan_pts_against_pts", "rank"}
    merged = ours.merge(theirs, on=["player_id", "pos", "stat"], how="inner", suffixes=("_ours", "_theirs"))
    merged = merged[~merged["stat"].isin(non_additive)]
    merged["diff"] = (merged["value_ours"] - merged["value_theirs"]).round(3)
    return (
        merged.groupby(["pos", "stat"], observed=True)
        .agg(rows=("diff", "size"), mismatches=("diff", lambda s: int((s.abs() > 0.001).sum())), max_abs_diff=("diff", lambda s: s.abs().max()))
        .reset_index()
        .sort_values(["mismatches", "pos"], ascending=[False, True])
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--top", type=int, default=10, help="rows printed per position board")
    parser.add_argument("--out", type=Path, help="write the full ideal-team board CSVs here (one per mode)")
    args = parser.parse_args(argv)

    pd.set_option("display.width", 200)

    tall, cast_failures = load_player_weeks(args.data_dir, args.season)
    scored = score(tall)
    agg = aggregate_season(scored)
    defense = team_defense(scored)
    board = ideal_team(agg, defense, SLOT_DEPTH)

    print(f"== load ==\nplayer-week rows: {tall.groupby(['week', 'player_id']).ngroups}  tall stat rows: {len(tall)}")
    print(f"cast failures (non-blank cells that failed to parse): {cast_failures}")
    print(f"bye rows: {tall[tall['is_bye']].groupby(['week', 'player_id']).ngroups}")

    for mode in SCORING_MODES:
        print(f"\n===== scoring mode: {mode} =====")
        for slot in ("QB", "RB", "WR", "TE", "K", "DEF"):
            top = board[(board["scoring_mode"] == mode) & (board["slot"] == slot)].head(args.top)
            print(f"\n-- {slot} top {min(args.top, len(top))} --")
            print(top[["slot_rank", "player_name", "team", "total_pts", "games_played", "pts_per_game"]].to_string(index=False))

    source_df = load_source_points(args.data_dir, args.season)
    skipped = unscored_weeks(source_df)

    print("\n===== README §6 assertions =====")
    print(run_checks(tall, agg, cast_failures, source_df).to_string(index=False))

    print("\n===== reconciliation vs source TotalPoints =====")
    if skipped:
        print(f"weeks excluded (source published stats but left TotalPoints at 0): {skipped}")
    print(reconcile_total_points(scored, source_df, skipped).to_string(index=False))

    print("\n===== reconciliation vs {POS}_season.csv (per-stat sums) =====")
    season_recon = reconcile_season_files(tall, args.data_dir, args.season)
    print(season_recon.head(20).to_string(index=False))
    print(f"stats compared: {len(season_recon)}  stats with any mismatch: {int((season_recon['mismatches'] > 0).sum())}")

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for mode in SCORING_MODES:
            path = args.out / f"ideal_team_{args.season}_{mode}.csv"
            board[board["scoring_mode"] == mode].to_csv(path, index=False)
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
