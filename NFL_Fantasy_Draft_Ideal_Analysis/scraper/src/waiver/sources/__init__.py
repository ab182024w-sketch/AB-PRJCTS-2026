"""One module per source. Each exposes `scrape(fetcher, lookback_hours) -> TrendSnapshot`
and pure `parse_*` functions the offline tests exercise against fixtures."""

from __future__ import annotations

from waiver.sources import sleeper

SOURCES = {"sleeper": sleeper}
