"""Sleeper — the chosen Phase 3 source.

Why Sleeper: a public JSON API with no key, whose documentation explicitly
grants free read-only use (docs.sleeper.com, "stay under 1000 API calls per
minute"), and whose robots.txt disallows nothing. Every keyless alternative
that carries true roster percentage forbids automated access — NFL.com robots
is `Disallow: *`, Fleaflicker disallows `/api/`, MyFantasyLeague disallows its
season paths — so per the README's own framing ("rostered-percentage /
add-drop trend data", §7) this source supplies the add/drop trend variant:
league-wide counts of how many Sleeper leagues added or dropped each player in
a lookback window. Availability *movement* is exactly the waiver signal; what
is lost without roster percentage is the absolute availability level, and that
deviation is documented in the README.

Sleeper's IDs are its own; the directory endpoint supplies name/team/position,
which is what the SQL crosswalk joins to hvpkod player_ids.
"""

from __future__ import annotations

from datetime import datetime, timezone

from waiver.fetch import Fetcher
from waiver.models import DirectoryEntry, TrendingEntry, TrendSnapshot

BASE = "https://api.sleeper.app/v1"
TRENDING_LIMIT = 200  # the feed tops out well below this; asking high loses nothing

# Sleeper is granular on defense (CB, FS, NT, ...); hvpkod groups into DB/LB/DL.
# Mapping here keeps the SQL crosswalk a plain equality join.
POSITION_GROUPS = {
    "QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE", "K": "K",
    "CB": "DB", "S": "DB", "SS": "DB", "FS": "DB", "DB": "DB",
    "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
    "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL",
    "DEF": "DEF",
}


def parse_trending(payload: object) -> list[TrendingEntry]:
    """Pure: trending JSON -> validated entries. Empty or misshapen input raises —
    a feed that suddenly matches nothing is an outage, not an empty snapshot."""
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"trending payload is not a non-empty list: {type(payload).__name__}")
    entries = []
    for item in payload:
        if not isinstance(item, dict) or "player_id" not in item or "count" not in item:
            raise ValueError(f"trending entry missing player_id/count: {item!r}")
        entries.append(
            TrendingEntry(external_player_id=str(item["player_id"]), count=int(item["count"]))
        )
    return entries


def parse_players(payload: object, keep_ids: set[str] | None = None) -> list[DirectoryEntry]:
    """Pure: the player directory -> validated identity rows.

    The full directory is ~12k rows of mostly-inactive players; `keep_ids`
    narrows it to the ids a trending feed actually referenced, so the loaded
    snapshot stays proportional to the signal.
    """
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"players payload is not a non-empty dict: {type(payload).__name__}")
    entries = []
    for external_id, player in payload.items():
        if keep_ids is not None and str(external_id) not in keep_ids:
            continue
        if not isinstance(player, dict):
            raise TypeError(f"player {external_id!r} is not an object")
        # Team defenses have no full_name; their id ("CLE") is the name.
        name = player.get("full_name") or str(external_id)
        raw_position = player.get("position")
        entries.append(
            DirectoryEntry(
                external_player_id=str(external_id),
                full_name=name,
                team=player.get("team"),
                position=POSITION_GROUPS.get(raw_position) if raw_position else None,
                active=bool(player.get("active", True)),
            )
        )
    if keep_ids is not None and not entries:
        raise ValueError("no directory entries matched the trending ids — id shape changed?")
    return entries


def scrape(fetcher: Fetcher, lookback_hours: int = 24) -> TrendSnapshot:
    scraped_at = datetime.now(timezone.utc)
    adds_raw = fetcher.get_json(
        f"{BASE}/players/nfl/trending/add?lookback_hours={lookback_hours}&limit={TRENDING_LIMIT}"
    )
    drops_raw = fetcher.get_json(
        f"{BASE}/players/nfl/trending/drop?lookback_hours={lookback_hours}&limit={TRENDING_LIMIT}"
    )
    players_raw = fetcher.get_json(f"{BASE}/players/nfl")
    fetcher.persist_raw("sleeper", "trending_add", adds_raw, scraped_at)
    fetcher.persist_raw("sleeper", "trending_drop", drops_raw, scraped_at)
    fetcher.persist_raw("sleeper", "players", players_raw, scraped_at)

    adds = parse_trending(adds_raw)
    drops = parse_trending(drops_raw)
    referenced = {entry.external_player_id for entry in adds + drops}
    players = parse_players(players_raw, keep_ids=referenced)
    return TrendSnapshot(
        source="sleeper",
        scraped_at=scraped_at,
        lookback_hours=lookback_hours,
        adds=adds,
        drops=drops,
        players=players,
    )
