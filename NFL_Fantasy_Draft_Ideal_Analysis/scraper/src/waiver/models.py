"""Typed schemas at the scrape boundary (README §9).

Validation happens here, before anything reaches Snowflake: a malformed scrape
is rejected as a whole rather than loading garbage into RAW. Parsers return
these models, never raw dicts.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# The eight positions the hvpkod feed carries, which is what the crosswalk can
# ever hope to match. Sleeper is more granular on defense; sources map into
# these groups before the models are built.
KNOWN_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DB", "LB", "DL", "DEF")


class TrendingEntry(BaseModel):
    """One player's add or drop count from a trending feed."""

    model_config = ConfigDict(frozen=True)

    external_player_id: str = Field(min_length=1)
    count: int = Field(ge=0)


class DirectoryEntry(BaseModel):
    """One player from the source's player directory — the identity half of the
    name+team+position crosswalk to hvpkod player_ids."""

    model_config = ConfigDict(frozen=True)

    external_player_id: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    team: str | None = None
    position: str | None = None
    active: bool = True


class TrendSnapshot(BaseModel):
    """Everything one scrape run produced, stamped once so every row of the run
    shares the same `scraped_at` (append-only snapshots, README §9)."""

    source: str
    scraped_at: datetime
    lookback_hours: int = Field(gt=0)
    adds: list[TrendingEntry]
    drops: list[TrendingEntry]
    players: list[DirectoryEntry]
