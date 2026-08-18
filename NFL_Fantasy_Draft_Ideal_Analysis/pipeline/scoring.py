"""Shared definitions for the tall stat shape and the scoring rules (README §2, §4).

These constants are the Python mirror of `sql/20_staging.sql` (column renames)
and `sql/30_scoring.sql` (the `SCORING_RULES` seed). The local harness exists to
verify these numbers against real data before the SQL is ever run, so the two
must be kept in sync; the stat names below are exactly the ones the SQL uses.
"""

from __future__ import annotations

SCORING_MODES = ("standard", "half_ppr", "full_ppr")

OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")
KICKER_POSITIONS = ("K",)
DEFENSE_POSITIONS = ("DB", "LB", "DL")
ALL_POSITIONS = OFFENSE_POSITIONS + KICKER_POSITIONS + DEFENSE_POSITIONS

# Source column -> snake_case stat name. Renaming happens at the staging
# boundary so no downstream SQL needs quoted identifiers for `2PT`,
# `FgMade_0-19` or `FanPtsAgainst-pts` (README §5).
OFFENSE_STAT_COLUMNS = {
    "PassingYDS": "passing_yds",
    "PassingTD": "passing_td",
    "PassingInt": "passing_int",
    "RushingYDS": "rushing_yds",
    "RushingTD": "rushing_td",
    "ReceivingRec": "receiving_rec",
    "ReceivingYDS": "receiving_yds",
    "ReceivingTD": "receiving_td",
    "RetTD": "ret_td",
    "FumTD": "fum_td",
    "2PT": "two_pt",
    "Fum": "fum",
    # loaded but never scored — matchup difficulty and usage/opportunity
    "FanPtsAgainst-pts": "fan_pts_against_pts",
    "TouchCarries": "touch_carries",
    "TouchReceptions": "touch_receptions",
    "Touches": "touches",
    "TargetsReceptions": "targets_receptions",
    "Targets": "targets",
    "ReceptionPercentage": "reception_percentage",
    "RzTarget": "rz_target",
    "RzTouch": "rz_touch",
    "RzG2G": "rz_g2g",
}

KICKER_STAT_COLUMNS = {
    "PatMade": "pat_made",
    "PatMissed": "pat_missed",
    "FgMade_0-19": "fg_made_0_19",
    "FgMade_20-29": "fg_made_20_29",
    "FgMade_30-39": "fg_made_30_39",
    "FgMade_40-49": "fg_made_40_49",
    "FgMade_50": "fg_made_50",
    "FgMiss_0-19": "fg_miss_0_19",
    "FgMiss_20-29": "fg_miss_20_29",
    "FgMiss_30-39": "fg_miss_30_39",
}

DEFENSE_STAT_COLUMNS = {
    "TacklesSck": "tackles_sck",
    "TurnoverInt": "turnover_int",
    "TurnoverFrcFum": "turnover_frc_fum",
    "TurnoverFumRec": "turnover_fum_rec",
    "ScoreIntTd": "score_int_td",
    "ScoreFumTd": "score_fum_td",
    "ScoreBlkTd": "score_blk_td",
    "ScoreSaf": "score_saf",
    "ScoreDef2ptRet": "score_def_2pt_ret",
    "Blk": "blk",
    # loaded but deliberately unscored — IDP categories (README §4)
    "TacklesTot": "tackles_tot",
    "TacklesAst": "tackles_ast",
    "TacklesTfl": "tackles_tfl",
    "PDef": "pdef",
    "QBHit": "qb_hit",
    "ReturnIntYds": "return_int_yds",
    "ReturnFumYds": "return_fum_yds",
}

# stat -> {mode: points_per_unit}. Receptions are the only mode-dependent rule.
OFFENSE_RULES: dict[str, dict[str, float]] = {
    "passing_yds": {"standard": 0.04, "half_ppr": 0.04, "full_ppr": 0.04},
    "passing_td": {"standard": 4, "half_ppr": 4, "full_ppr": 4},
    "passing_int": {"standard": -2, "half_ppr": -2, "full_ppr": -2},
    "rushing_yds": {"standard": 0.1, "half_ppr": 0.1, "full_ppr": 0.1},
    "rushing_td": {"standard": 6, "half_ppr": 6, "full_ppr": 6},
    "receiving_rec": {"standard": 0.0, "half_ppr": 0.5, "full_ppr": 1.0},
    "receiving_yds": {"standard": 0.1, "half_ppr": 0.1, "full_ppr": 0.1},
    "receiving_td": {"standard": 6, "half_ppr": 6, "full_ppr": 6},
    "ret_td": {"standard": 6, "half_ppr": 6, "full_ppr": 6},
    "fum_td": {"standard": 6, "half_ppr": 6, "full_ppr": 6},
    "two_pt": {"standard": 2, "half_ppr": 2, "full_ppr": 2},
    "fum": {"standard": -2, "half_ppr": -2, "full_ppr": -2},
}

KICKER_RULES: dict[str, float] = {
    "pat_made": 1,
    "pat_missed": -1,
    "fg_made_0_19": 3,
    "fg_made_20_29": 3,
    "fg_made_30_39": 3,
    "fg_made_40_49": 4,
    "fg_made_50": 5,
    "fg_miss_0_19": -1,
    "fg_miss_20_29": -1,
    "fg_miss_30_39": -1,
}

DEFENSE_RULES: dict[str, float] = {
    "tackles_sck": 1,
    "turnover_int": 2,
    "turnover_fum_rec": 2,
    "turnover_frc_fum": 1,
    "score_saf": 2,
    "score_int_td": 6,
    "score_fum_td": 6,
    "score_blk_td": 6,
    "blk": 2,
    "score_def_2pt_ret": 2,
}

# Which scoring component each stat rolls up into, for the broken-out
# `pass_pts` / `rush_pts` / ... columns on FCT_PLAYER_SCORING (README §5).
STAT_COMPONENT: dict[str, str] = {
    **{s: "pass_pts" for s in ("passing_yds", "passing_td", "passing_int")},
    **{s: "rush_pts" for s in ("rushing_yds", "rushing_td")},
    **{s: "rec_pts" for s in ("receiving_rec", "receiving_yds", "receiving_td")},
    **{s: "misc_pts" for s in ("ret_td", "fum_td", "two_pt", "fum")},
    **{s: "kick_pts" for s in KICKER_RULES},
    **{s: "def_pts" for s in DEFENSE_RULES},
}

PLAYOFF_WEEKS = (15, 16, 17, 18)

# Startable-game cutoffs for `weeks_above_threshold` (README §3).
POSITION_THRESHOLDS = {"QB": 18.0, "RB": 12.0, "WR": 12.0, "TE": 10.0, "K": 8.0}

# Slot depth for the ideal-team board (README §1) — a parameter, not hard-coded SQL.
SLOT_DEPTH = {"QB": 10, "RB": 25, "WR": 25, "TE": 25, "K": 1, "DEF": 5}


def rules_rows() -> list[tuple[str, str, float]]:
    """(scoring_mode, stat, points_per_unit) — the exact SCORING_RULES seed."""
    rows: list[tuple[str, str, float]] = []
    for stat, by_mode in OFFENSE_RULES.items():
        for mode in SCORING_MODES:
            rows.append((mode, stat, float(by_mode[mode])))
    for rules in (KICKER_RULES, DEFENSE_RULES):
        for stat, points in rules.items():
            for mode in SCORING_MODES:
                rows.append((mode, stat, float(points)))
    return rows
