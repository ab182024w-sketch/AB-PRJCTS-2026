"""HTTP plumbing shared by every source (README §9).

One client, three obligations: identify the project in the User-Agent, stay
polite (robots.txt checked per host, a fixed delay between requests,
exponential backoff on 429/5xx), and persist the raw response before any
parsing — when a payload shape changes, the parser gets fixed and re-run
against history instead of losing the snapshot.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

USER_AGENT = (
    "AB-PRJCTS-2026 waiver scraper "
    "(+https://github.com/ab182024w-sketch/AB-PRJCTS-2026)"
)
REQUEST_DELAY_SECONDS = 1.0
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4
DEFAULT_RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "waiver_raw"


class RobotsDisallowedError(RuntimeError):
    """The host's robots.txt forbids the URL — the fetch is refused, not retried."""


class Fetcher:
    def __init__(self, raw_dir: Path | None = None, delay: float = REQUEST_DELAY_SECONDS):
        self.raw_dir = raw_dir if raw_dir is not None else DEFAULT_RAW_DIR
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser(f"{host}/robots.txt")
            # An unreachable robots.txt reads as empty, which allows everything —
            # the standard interpretation, and requests to the API itself will
            # still fail visibly if the host is actually down.
            try:
                parser.read()
            except OSError:
                parser.parse([])
            self._robots[host] = parser
        return parser.can_fetch(USER_AGENT, url)

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def get_json(self, url: str) -> object:
        if not self._robots_allows(url):
            raise RobotsDisallowedError(f"robots.txt disallows {url}")
        backoff = 2.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._pace()
            response = self.session.get(url, timeout=30)
            if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                time.sleep(backoff)
                backoff *= 2
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"unreachable retry loop for {url}")

    def persist_raw(self, source: str, name: str, payload: object, scraped_at: datetime) -> Path:
        """Gzip the payload under raw_dir keyed by source and fetch timestamp."""
        stamp = scraped_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = self.raw_dir / source
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stamp}_{name}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path
