"""Execute the pipeline's SQL files against Snowflake, statement by statement.

    python -m pipeline.run_sql 20_staging.sql 30_scoring.sql
    python -m pipeline.run_sql --season 2026 --all

Snowsight runs a worksheet as a script; the connector does not, so the files
have to be split. `split_statements` handles the quoting and `$$` bodies that a
naive `str.split(';')` gets wrong.

Connection comes from the environment (SNOWFLAKE_ACCOUNT / _USER / _PASSWORD or
_PRIVATE_KEY_PATH, optional _ROLE / _WAREHOUSE). Nothing is ever printed that
could contain a credential.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Order matters: each file assumes the previous one's objects exist. 05 is
# optional (the Snowsight git mount) and is deliberately not in the run.
PIPELINE_FILES = (
    "00_setup.sql",
    "10_raw.sql",
    "15_team_results.sql",
    "20_staging.sql",
    "30_scoring.sql",
    "35_season_agg.sql",
    "40_defense.sql",
    "50_ideal_team.sql",
    "60_waiver.sql",
    "99_tests.sql",
)


def connect():
    import snowflake.connector  # lazy: the download path has no Snowflake dep

    args = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
    }
    if os.environ.get("SNOWFLAKE_PASSWORD"):
        args["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        args["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
    for key, var in (("role", "SNOWFLAKE_ROLE"), ("warehouse", "SNOWFLAKE_WAREHOUSE")):
        if os.environ.get(var):
            args[key] = os.environ[var]
    return snowflake.connector.connect(**args)


def statements(sql: str):
    """Yield executable statements, skipping comment-only ones.

    A statement whose every line is a `--` comment is legal in a worksheet but
    reaches the connector as an empty string, which errors with "Empty SQL
    statement" — so it is dropped here rather than sent.
    """
    from snowflake.connector.util_text import split_statements

    for raw, _ in split_statements(io.StringIO(sql)):
        body = " ".join(
            line for line in raw.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body and body != ";":
            yield raw.strip(), body


def run(cursor, files, season: int | None, rows_shown: int = 8) -> int:
    if season is not None:
        # Read by 50_ideal_team.sql via GETVARIABLE, so a refresh can target one
        # season without editing SQL.
        cursor.execute(f"SET target_season = {int(season)}")
    for name in files:
        path = SQL_DIR / name
        print(f"\n===== {name} =====", flush=True)
        for statement, body in statements(path.read_text()):
            head = body[:110]
            try:
                rows = cursor.execute(statement).fetchmany(rows_shown)
            except Exception as exc:  # noqa: BLE001 — the message is what matters
                print(f"FAIL: {head}\n  {type(exc).__name__}: {exc}", flush=True)
                return 1
            print(f"ok  : {head}", flush=True)
            for row in rows:
                print(f"      {row}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="SQL file names under sql/")
    parser.add_argument("--all", action="store_true", help=f"run the full pipeline: {', '.join(PIPELINE_FILES)}")
    parser.add_argument("--season", type=int, help="value for the target_season SQL variable")
    args = parser.parse_args(argv)

    files = list(PIPELINE_FILES) if args.all else args.files
    if not files:
        parser.error("pass SQL file names or --all")

    connection = connect()
    try:
        return run(connection.cursor(), files, args.season)
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
