"""Phase 0 — download hvpkod/NFL-Data player CSVs into a local season/week tree.

Usage:
    python -m pipeline.download --season 2025 --weeks 1-18
    python -m pipeline.download --season 2026 --weeks 5 --put-to-stage

Phase 1.6 adds the nflverse team-results feed (README §5a) behind --nflverse:
the league-wide schedule (points allowed) and team-week offensive splits
(yards allowed), which the player-stat source does not carry.

Idempotent: a file is re-downloaded only when its content differs from what is
already on disk, and writes go through a temp file + atomic rename, so an
interrupted run never leaves a half-written CSV behind.

The Snowflake PUT step is isolated in `put_to_stage` and is skipped unless
credentials are configured and `--put-to-stage` is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests

RAW_BASE = "https://raw.githubusercontent.com/hvpkod/NFL-Data/main/NFL-data-Players"
NFLVERSE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DB", "LB", "DL")
DEFAULT_SEASON = 2025
DEFAULT_WEEKS = range(1, 19)
USER_AGENT = "AB-PRJCTS-2026 NFL_Fantasy_Draft_Ideal_Analysis (+https://github.com/ab182024w-sketch/AB-PRJCTS-2026)"

SNOWFLAKE_ENV_VARS = (
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_SCHEMA",
    "SNOWFLAKE_WAREHOUSE",
)


@dataclass
class FileResult:
    relative_path: str
    status: str  # downloaded | unchanged | missing | error
    rows: int = 0
    bytes: int = 0
    detail: str = ""


@dataclass
class RunReport:
    season: int
    weeks: list[int]
    results: list[FileResult] = field(default_factory=list)

    @property
    def by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    @property
    def total_rows(self) -> int:
        return sum(r.rows for r in self.results)

    def to_dict(self) -> dict:
        return {
            "season": self.season,
            "weeks": self.weeks,
            "summary": self.by_status,
            "total_data_rows": self.total_rows,
            "files": [vars(r) for r in self.results],
        }


def parse_weeks(spec: str) -> list[int]:
    """Parse a week spec such as "1-18", "3", or "1,2,5-7"."""
    weeks: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            weeks.extend(range(int(start), int(end) + 1))
        else:
            weeks.append(int(part))
    return sorted(set(weeks))


def _targets(season: int, weeks: list[int], include_season_files: bool) -> list[str]:
    paths = [f"{season}/{week}/{pos}.csv" for week in weeks for pos in POSITIONS]
    if include_season_files:
        paths += [f"{season}/{pos}_season.csv" for pos in POSITIONS]
    return paths


def _nflverse_targets(season: int) -> list[tuple[str, str]]:
    """(url, local path) for the two Phase 1.6 assets (README §5a).

    `games.csv` is league-wide and covers every season in one file, so it is
    stored outside the per-season tree; the team-week file is per season.
    """
    return [
        (f"{NFLVERSE_BASE}/schedules/games.csv", "nflverse/games.csv"),
        (
            f"{NFLVERSE_BASE}/stats_team/stats_team_week_{season}.csv",
            f"nflverse/{season}/stats_team_week_{season}.csv",
        ),
    ]


def _row_count(content: bytes) -> int:
    text = content.decode("utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return max(len(lines) - 1, 0)  # minus header


def fetch(session: requests.Session, relative_path: str, timeout: float) -> bytes | None:
    """Return file bytes, or None when the source has no such file (HTTP 404)."""
    response = session.get(f"{RAW_BASE}/{relative_path}", timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def write_if_changed(destination: Path, content: bytes) -> bool:
    """Atomically write `content`; return True when the file actually changed."""
    if destination.exists():
        existing = destination.read_bytes()
        if hashlib.sha256(existing).digest() == hashlib.sha256(content).digest():
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.write_bytes(content)
    temp.replace(destination)
    return True


def download(
    data_dir: Path,
    season: int = DEFAULT_SEASON,
    weeks: list[int] | None = None,
    include_season_files: bool = True,
    timeout: float = 30.0,
) -> RunReport:
    weeks = weeks if weeks is not None else list(DEFAULT_WEEKS)
    report = RunReport(season=season, weeks=weeks)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    for relative_path in _targets(season, weeks, include_season_files):
        destination = data_dir / relative_path
        try:
            content = fetch(session, relative_path, timeout)
        except requests.RequestException as exc:
            report.results.append(FileResult(relative_path, "error", detail=str(exc)))
            continue
        if content is None:
            report.results.append(FileResult(relative_path, "missing", detail="404 at source"))
            continue
        changed = write_if_changed(destination, content)
        report.results.append(
            FileResult(
                relative_path,
                "downloaded" if changed else "unchanged",
                rows=_row_count(content),
                bytes=len(content),
            )
        )
    return report


def download_nflverse(
    data_dir: Path,
    season: int,
    timeout: float = 60.0,
) -> list[FileResult]:
    """Download the nflverse team-results assets. Same idempotency as `download`.

    A season that has not started yet has no `stats_team_week_{season}.csv`
    release asset, which surfaces as `missing` rather than as an error — the
    2026 refresh will hit exactly that until week 1 is played.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    results: list[FileResult] = []
    for url, relative_path in _nflverse_targets(season):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 404:
                results.append(FileResult(relative_path, "missing", detail="404 at source"))
                continue
            response.raise_for_status()
        except requests.RequestException as exc:
            results.append(FileResult(relative_path, "error", detail=str(exc)))
            continue
        changed = write_if_changed(data_dir / relative_path, response.content)
        results.append(
            FileResult(
                relative_path,
                "downloaded" if changed else "unchanged",
                rows=_row_count(response.content),
                bytes=len(response.content),
            )
        )
    return results


def snowflake_configured() -> bool:
    return all(os.environ.get(var) for var in SNOWFLAKE_ENV_VARS) and bool(
        os.environ.get("SNOWFLAKE_PASSWORD") or os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    )


def put_to_stage(
    data_dir: Path,
    season: int,
    stage: str = "@FANTASY_STAGE",
) -> int:
    """PUT the downloaded CSVs onto the Snowflake internal stage, mirroring paths.

    Isolated from the download path on purpose: it is the only function that
    needs credentials, and it is never called unless `snowflake_configured()`.
    Returns the number of PUT statements issued.
    """
    import snowflake.connector  # imported lazily so the downloader has no hard dep

    connect_args = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
        "schema": os.environ["SNOWFLAKE_SCHEMA"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
    }
    if os.environ.get("SNOWFLAKE_ROLE"):
        connect_args["role"] = os.environ["SNOWFLAKE_ROLE"]
    if os.environ.get("SNOWFLAKE_PASSWORD"):
        connect_args["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        connect_args["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]

    sources = [data_dir / str(season)]
    nflverse = data_dir / "nflverse"
    if nflverse.exists():
        # Only this season's team-week file, plus the league-wide schedule.
        sources += [nflverse / str(season), nflverse / "games.csv"]

    paths: list[Path] = []
    for source in sources:
        if source.is_dir():
            paths += sorted(source.rglob("*.csv"))
        elif source.is_file():
            paths.append(source)

    statements = 0
    with snowflake.connector.connect(**connect_args) as connection:
        cursor = connection.cursor()
        for csv_path in paths:
            stage_path = f"{stage}/{csv_path.relative_to(data_dir).parent.as_posix()}"
            cursor.execute(
                f"PUT 'file://{csv_path}' {stage_path} "
                "AUTO_COMPRESS = TRUE OVERWRITE = TRUE"
            )
            statements += 1
    return statements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--weeks", default="1-18", help='week spec, e.g. "1-18" or "1,5-7"')
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data",
    )
    parser.add_argument("--no-season-files", action="store_true", help="skip {POS}_season.csv reconciliation files")
    parser.add_argument("--nflverse", action="store_true", help="also fetch the Phase 1.6 team-results feed (README §5a)")
    parser.add_argument("--put-to-stage", action="store_true", help="PUT the tree to the Snowflake stage after download")
    parser.add_argument("--stage", default="@FANTASY_STAGE")
    parser.add_argument("--report", type=Path, help="write the JSON run report here")
    args = parser.parse_args(argv)

    report = download(
        data_dir=args.data_dir,
        season=args.season,
        weeks=parse_weeks(args.weeks),
        include_season_files=not args.no_season_files,
    )

    if args.nflverse:
        report.results += download_nflverse(args.data_dir, args.season)

    counts = report.by_status
    print(f"season {report.season} weeks {report.weeks[0]}-{report.weeks[-1]} -> {args.data_dir}")
    for status in ("downloaded", "unchanged", "missing", "error"):
        if counts.get(status):
            print(f"  {status}: {counts[status]}")
    print(f"  data rows: {report.total_rows}")
    for result in report.results:
        if result.status in ("missing", "error"):
            print(f"  ! {result.relative_path}: {result.status} ({result.detail})")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report.to_dict(), indent=2) + "\n")

    if args.put_to_stage:
        if not snowflake_configured():
            print("  skipping PUT: Snowflake credentials are not configured")
        else:
            print(f"  PUT {put_to_stage(args.data_dir, args.season, args.stage)} files to {args.stage}")

    return 1 if counts.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
