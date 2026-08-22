"""Load a validated snapshot into FANTASY.RAW (append-only, README §9).

The tables are created by `sql/60_waiver.sql` — run the pipeline once before
the first scrape. Nothing here deletes or updates: every run appends rows
stamped with the run's single `scraped_at`, because movement between snapshots
is the signal and it only exists if history accumulates.
"""

from __future__ import annotations

import os

from waiver.models import TrendSnapshot

TREND_INSERT = """
INSERT INTO FANTASY.RAW.WAIVER_TREND_RAW
    (scraped_at, source, kind, lookback_hours, external_player_id, trend_count, loaded_at)
SELECT column1, column2, column3, column4, column5, column6, CURRENT_TIMESTAMP()
FROM VALUES (%s, %s, %s, %s, %s, %s)
"""

PLAYERS_INSERT = """
INSERT INTO FANTASY.RAW.WAIVER_PLAYERS_RAW
    (scraped_at, source, external_player_id, full_name, team, position, active, loaded_at)
SELECT column1, column2, column3, column4, column5, column6, column7, CURRENT_TIMESTAMP()
FROM VALUES (%s, %s, %s, %s, %s, %s, %s)
"""


def connect():
    import snowflake.connector  # lazy: --dry-run needs no Snowflake dependency

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
    args.setdefault("warehouse", "FANTASY_WH")
    return snowflake.connector.connect(**args)


def load(snapshot: TrendSnapshot) -> dict[str, int]:
    """Append the snapshot; returns rows written per table."""
    stamp = snapshot.scraped_at.replace(tzinfo=None)  # TIMESTAMP_NTZ column, UTC
    trend_rows = [
        (stamp, snapshot.source, kind, snapshot.lookback_hours, e.external_player_id, e.count)
        for kind, entries in (("add", snapshot.adds), ("drop", snapshot.drops))
        for e in entries
    ]
    player_rows = [
        (stamp, snapshot.source, p.external_player_id, p.full_name, p.team, p.position, p.active)
        for p in snapshot.players
    ]
    connection = connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.executemany(TREND_INSERT, trend_rows)
            cursor.executemany(PLAYERS_INSERT, player_rows)
        finally:
            cursor.close()
    finally:
        connection.close()
    return {"WAIVER_TREND_RAW": len(trend_rows), "WAIVER_PLAYERS_RAW": len(player_rows)}
