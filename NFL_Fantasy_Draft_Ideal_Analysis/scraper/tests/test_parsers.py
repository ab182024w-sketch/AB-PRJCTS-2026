"""Parser tests against committed fixtures — real captured Sleeper responses,
trimmed. No test here touches the network (README §9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from waiver.models import KNOWN_POSITIONS
from waiver.sources.sleeper import POSITION_GROUPS, parse_players, parse_trending

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def test_trending_add_fixture_parses() -> None:
    entries = parse_trending(fixture("sleeper_trending_add.json"))
    assert len(entries) == 25
    assert all(entry.count >= 0 for entry in entries)
    assert all(entry.external_player_id for entry in entries)


def test_trending_drop_fixture_parses() -> None:
    entries = parse_trending(fixture("sleeper_trending_drop.json"))
    assert len(entries) == 25


def test_trending_fails_loudly_on_empty_payload() -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        parse_trending([])


def test_trending_fails_loudly_on_wrong_shape() -> None:
    with pytest.raises(ValueError, match="non-empty list"):
        parse_trending({"player_id": "1", "count": 2})
    with pytest.raises(ValueError, match="missing player_id/count"):
        parse_trending([{"player": "1"}])


def test_players_fixture_parses() -> None:
    entries = parse_players(fixture("sleeper_players_trimmed.json"))
    by_id = {entry.external_player_id: entry for entry in entries}
    assert by_id["7528"].full_name == "Najee Harris"
    assert by_id["7528"].team == "NYG"
    assert by_id["7528"].position == "RB"
    # free agents keep a NULL team rather than inventing one
    assert by_id["3321"].team is None


def test_players_positions_group_into_the_known_set() -> None:
    entries = parse_players(fixture("sleeper_players_trimmed.json"))
    assert {entry.position for entry in entries if entry.position} <= set(KNOWN_POSITIONS)
    # Sleeper's granular defense positions collapse into hvpkod's groups
    by_id = {entry.external_player_id: entry for entry in entries}
    assert by_id["8842"].position == "DB"  # CB
    assert by_id["995"].position == "DB"   # FS


def test_team_defenses_fall_back_to_their_id_as_name() -> None:
    entries = parse_players(fixture("sleeper_players_trimmed.json"))
    defense = next(entry for entry in entries if entry.position == "DEF")
    assert defense.full_name == "CLE"


def test_players_keep_ids_narrows_to_the_trending_ids() -> None:
    keep = {"7528", "2505"}
    entries = parse_players(fixture("sleeper_players_trimmed.json"), keep_ids=keep)
    assert {entry.external_player_id for entry in entries} == keep


def test_players_fails_loudly_when_no_id_matches() -> None:
    with pytest.raises(ValueError, match="id shape changed"):
        parse_players(fixture("sleeper_players_trimmed.json"), keep_ids={"nope"})


def test_players_fails_loudly_on_empty_payload() -> None:
    with pytest.raises(ValueError, match="non-empty dict"):
        parse_players({})


def test_position_groups_only_produce_known_positions() -> None:
    assert set(POSITION_GROUPS.values()) <= set(KNOWN_POSITIONS)
