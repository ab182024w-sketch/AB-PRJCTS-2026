"""Where the dashboard gets its data.

Two backends, one SQL dialect (see `queries.py`):

* **Snowflake** — the default. Inside Streamlit in Snowflake the active Snowpark
  session is reused, so there are no credentials to configure; outside it, the
  connector reads `st.secrets["snowflake"]` or the `SNOWFLAKE_*` environment
  variables.
* **DuckDB over a Parquet snapshot** — what `pipeline/export_marts.py` writes.
  This is the public-hosting path from README §8: the app queries a bundled file,
  anonymous traffic never reaches the warehouse, and there is no service account
  to leak. It is also what makes the app runnable, and testable, offline.

The backend is chosen by what is available, so the same code deploys to all
three targets in §8 unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

SNOWFLAKE_OBJECTS = {
    "ideal_team": "FANTASY.MARTS.IDEAL_TEAM",
    "agg": "FANTASY.MARTS.AGG_PLAYER_SEASON",
    "fct": "FANTASY.MARTS.FCT_PLAYER_SCORING",
    "team_def": "FANTASY.MARTS.FCT_TEAM_DEFENSE",
    "team_def_week": "FANTASY.MARTS.FCT_TEAM_DEFENSE_WEEK",
    "rules": "FANTASY.MARTS.SCORING_RULES",
    "stg": "FANTASY.STAGING.STG_PLAYER_WEEK",
    "waiver_targets": "FANTASY.MARTS.WAIVER_TARGETS",
    "waiver_trend": "FANTASY.MARTS.WAIVER_TREND",
}

SNAPSHOT_OBJECTS = {name: name for name in SNOWFLAKE_OBJECTS}

DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshot"


@dataclass(frozen=True)
class Backend:
    kind: str
    label: str
    objects: dict[str, str]


def _snapshot_dir() -> Path:
    return Path(os.environ.get("FANTASY_SNAPSHOT_DIR", DEFAULT_SNAPSHOT_DIR))


def _snapshot_available() -> bool:
    directory = _snapshot_dir()
    return directory.is_dir() and any(directory.glob("*.parquet"))


def _secrets() -> dict[str, str]:
    """`st.secrets["snowflake"]`, or nothing when no secrets file is deployed."""
    try:
        return dict(st.secrets["snowflake"])
    except (KeyError, FileNotFoundError):
        return {}


def _snowflake_configured() -> bool:
    if _secrets():
        return True
    return bool(os.environ.get("SNOWFLAKE_ACCOUNT") and os.environ.get("SNOWFLAKE_USER"))


@st.cache_resource(show_spinner=False)
def _snowpark_session():
    from snowflake.snowpark.context import get_active_session

    return get_active_session()


@st.cache_resource(show_spinner=False)
def _snowflake_connection():
    import snowflake.connector

    config = _secrets()
    for key, variable in (
        ("account", "SNOWFLAKE_ACCOUNT"),
        ("user", "SNOWFLAKE_USER"),
        ("password", "SNOWFLAKE_PASSWORD"),
        ("role", "SNOWFLAKE_ROLE"),
        ("warehouse", "SNOWFLAKE_WAREHOUSE"),
    ):
        if key not in config and os.environ.get(variable):
            config[key] = os.environ[variable]
    config.setdefault("warehouse", "FANTASY_WH")
    config.setdefault("client_session_keep_alive", True)
    return snowflake.connector.connect(**config)


@st.cache_resource(show_spinner=False)
def _duckdb_connection():
    import duckdb

    connection = duckdb.connect(database=":memory:")
    for name in SNAPSHOT_OBJECTS:
        path = _snapshot_dir() / f"{name}.parquet"
        if path.exists():
            connection.execute(
                f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')"
            )
    return connection


def _snowpark_available() -> bool:
    """True only when the app is running inside Streamlit in Snowflake, which
    hands it a session and therefore needs no credentials at all."""
    try:
        from snowflake.snowpark.exceptions import SnowparkSessionException
    except ImportError:
        return False
    try:
        _snowpark_session()
    except SnowparkSessionException:
        return False
    return True


def get_backend() -> Backend:
    """Snowflake when it is reachable, the bundled snapshot otherwise."""
    if _snowpark_available():
        return Backend("snowpark", "Streamlit in Snowflake", SNOWFLAKE_OBJECTS)
    if _snowflake_configured():
        return Backend("snowflake", "Snowflake", SNOWFLAKE_OBJECTS)
    if _snapshot_available():
        return Backend("duckdb", "bundled snapshot", SNAPSHOT_OBJECTS)
    return Backend("none", "no data source", SNOWFLAKE_OBJECTS)


@st.cache_data(ttl=3600, show_spinner=False)
def run_query(sql: str, **params: object) -> pd.DataFrame:
    """Run a `queries.py` template. Object names and query parameters are both
    filled here, so a template is never half-formatted by its caller."""
    backend = get_backend()
    # Every string parameter sits inside quotes in queries.py, so escaping the
    # quote character here is enough to keep a player name out of the grammar.
    safe = {
        key: value.replace("'", "''") if isinstance(value, str) else value
        for key, value in params.items()
    }
    statement = sql.format(**backend.objects, **safe)
    if backend.kind == "snowpark":
        frame = _snowpark_session().sql(statement).to_pandas()
    elif backend.kind == "snowflake":
        cursor = _snowflake_connection().cursor()
        try:
            cursor.execute(statement)
            columns = [column[0] for column in cursor.description]
            frame = pd.DataFrame(cursor.fetchall(), columns=columns)
        finally:
            cursor.close()
    elif backend.kind == "duckdb":
        frame = _duckdb_connection().execute(statement).fetch_df()
    else:
        raise RuntimeError(
            "No data source. Set SNOWFLAKE_* credentials, or run "
            "`python -m pipeline.export_marts` to build a local snapshot."
        )
    frame.columns = [str(column).lower() for column in frame.columns]
    return frame
