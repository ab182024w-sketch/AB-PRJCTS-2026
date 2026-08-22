"""`waiver scrape --source sleeper [--dry-run]` (README §9).

Dry-run does everything except touch Snowflake: fetch, persist raw gzip,
parse, validate, and print what would load — so the scrape path can be proven
without credentials.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from waiver.fetch import Fetcher
from waiver.load import load
from waiver.sources import SOURCES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="waiver", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scrape = commands.add_parser("scrape", help="fetch a source and load it into RAW")
    scrape.add_argument("--source", choices=sorted(SOURCES), default="sleeper")
    scrape.add_argument("--lookback-hours", type=int, default=24)
    scrape.add_argument("--dry-run", action="store_true", help="fetch and validate, skip the Snowflake load")
    scrape.add_argument("--raw-dir", type=Path, default=None, help="where raw gzipped responses are kept")
    args = parser.parse_args(argv)

    fetcher = Fetcher(raw_dir=args.raw_dir)
    snapshot = SOURCES[args.source].scrape(fetcher, lookback_hours=args.lookback_hours)
    print(
        f"{args.source} @ {snapshot.scraped_at.isoformat()} "
        f"(lookback {snapshot.lookback_hours}h): "
        f"{len(snapshot.adds)} adds, {len(snapshot.drops)} drops, "
        f"{len(snapshot.players)} directory rows; raw saved under {fetcher.raw_dir}"
    )
    if args.dry_run:
        print("dry run: nothing loaded")
        return 0
    for table, rows in load(snapshot).items():
        print(f"loaded {rows:>5} rows into RAW.{table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
