"""Export the marts to a local Parquet snapshot the dashboard can serve.

This is the public-hosting path from README §8: the season is finished by the
time anyone browses it, so a public app can ship the data instead of a Snowflake
service account. It is also how the app runs with no warehouse at all.

    python -m pipeline.export_marts --season 2025

Writes `data/snapshot/*.parquet`, one file per object named exactly as
`app/data.py` expects. `data/` is gitignored; the snapshot is a build artifact,
rebuilt whenever the season data refreshes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from pipeline.run_sql import connect

# Object → the columns the app reads, narrowed to one season where the object
# has one. The staging table is included because the custom-scoring board
# re-scores from raw stat lines rather than from precomputed points.
EXPORTS = {
    "ideal_team": "SELECT * FROM FANTASY.MARTS.IDEAL_TEAM WHERE season = {season}",
    "agg": "SELECT * FROM FANTASY.MARTS.AGG_PLAYER_SEASON WHERE season = {season}",
    "fct": "SELECT * FROM FANTASY.MARTS.FCT_PLAYER_SCORING WHERE season = {season}",
    "team_def": "SELECT * FROM FANTASY.MARTS.FCT_TEAM_DEFENSE WHERE season = {season}",
    "team_def_week": (
        "SELECT * FROM FANTASY.MARTS.FCT_TEAM_DEFENSE_WEEK WHERE season = {season}"
    ),
    "rules": "SELECT * FROM FANTASY.MARTS.SCORING_RULES",
    "stg": (
        "SELECT season, week, is_playoff, player_id, player_name, pos, team, "
        "opponent, is_away, is_bye, stat, value "
        "FROM FANTASY.STAGING.STG_PLAYER_WEEK WHERE season = {season}"
    ),
}


def export(out_dir: Path, season: int) -> list[tuple[str, int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, int]] = []
    connection = connect()
    try:
        cursor = connection.cursor()
        try:
            for name, template in EXPORTS.items():
                cursor.execute(template.format(season=int(season)))
                columns = [column[0].lower() for column in cursor.description]
                frame = pd.DataFrame(cursor.fetchall(), columns=columns)
                frame.to_parquet(out_dir / f"{name}.parquet", index=False)
                written.append((name, len(frame)))
        finally:
            cursor.close()
    finally:
        connection.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "snapshot",
    )
    args = parser.parse_args()

    for name, rows in export(args.out, args.season):
        print(f"{name:16} {rows:>8,} rows")
    print(f"\nsnapshot written to {args.out}")


if __name__ == "__main__":
    main()
