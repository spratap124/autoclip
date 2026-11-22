"""API contracts for cached demo roster, analysis, timeline, and exports."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from autoclip import app as app_module
from autoclip import paths
from autoclip.app import create_app
from autoclip.cs2demo.adapter import PARSER_VERSION
from autoclip.cs2demo.models import NORMALIZED_SCHEMA_VERSION, DemoPlayer, ParsedDemo
from autoclip.db import store
from autoclip.db.models import DemoFile, new_id
from fastapi.testclient import TestClient


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def parsed_demo() -> tuple[str, str]:
    content_hash = "b" * 64
    demo_id = new_id()
    players = [
        DemoPlayer(f"session-{index}", 2000 + index, f"player-{index}", 2 if index < 5 else 3)
        for index in range(10)
    ]
    events = {
        "round_freeze_end": [{"tick": 100}],
        "round_officially_ended": [{"tick": 300}],
        "player_death": [
            {
                "tick": tick,
                "attacker_steamid": "2000",
                "attacker_name": "player-0",
                "user_steamid": str(victim),
                "user_name": f"player-{victim - 2000}",
                "assister_steamid": "",
                "weapon": "ak47",
                "headshot": False,
                "penetrated": 0,
                "thrusmoke": False,
                "noscope": False,
                "attackerblind": False,
                "assistedflash": False,
            }
            for tick, victim in ((110, 2005), (120, 2006))
        ],
    }
    ticks = [100, 110, 111, 120, 121, 300]
    state_rows = []
    for tick in ticks:
        dead = {2005} if tick >= 110 else set()
        if tick >= 120:
            dead.add(2006)
        for player in players:
            state_rows.append(
                {
                    "tick": tick,
                    "steamid": player.steamid,
                    "team_num": player.team_number,
                    "is_alive": player.steamid not in dead,
                    "is_warmup_period": False,
                    "round_in_progress": True,
                    "total_rounds_played": 1,
                    "is_bomb_planted": False,
                    "round_win_reason": 0,
                    "team_rounds_total": 0,
                }
            )
    parsed = ParsedDemo(
        content_sha256=content_hash,
        parser_version=PARSER_VERSION,
        schema_version=NORMALIZED_SCHEMA_VERSION,
        header={"map_name": "de_test"},
        players=players,
        event_names=list(events),
        events=events,
        player_states=state_rows,
        tick_rate=64,
    )
    demo = DemoFile(
        id=demo_id,
        content_sha256=content_hash,
        file_path="hidden/local/path.dem",
        original_filename="fixture.dem",
        size_bytes=1,
        parse_status="parsed",
    )
    store.create_demo(demo)
    cache = (
        paths.root()
        / "cache"
        / "cs2demo"
        / content_hash
        / f"parser-{PARSER_VERSION}-schema-{NORMALIZED_SCHEMA_VERSION}.json"
    )
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(parsed.to_dict()), encoding="utf-8")
    return demo_id, "steamid:2000"


def test_roster_analysis_filter_timeline_and_exports(
    client: TestClient, parsed_demo: tuple[str, str]
) -> None:
    demo_id, player_id = parsed_demo
    players = client.get(f"/api/demos/{demo_id}/players")
    assert players.status_code == 200
    assert len(players.json()["players"]) == 10
    assert players.json()["players"][0]["kills"] == 2

    analysis = client.get(f"/api/demos/{demo_id}/analysis/{player_id}")
    assert analysis.status_code == 200
    assert analysis.json()["total"] == 1
    highlight = analysis.json()["highlights"][0]
    assert "2K" in highlight["tags"]
    assert highlight["score"] == sum(highlight["score_breakdown"].values())
    assert highlight["kill_event_ids"]

    filtered = client.get(
        f"/api/demos/{demo_id}/analysis/{player_id}", params={"tag": "3K"}
    )
    assert filtered.json()["total"] == 0
    tag_or_filter = client.get(
        f"/api/demos/{demo_id}/analysis/{player_id}",
        params={"tag": "3K,opening_kill"},
    )
    assert tag_or_filter.json()["total"] == 1
    rounds = client.get(f"/api/demos/{demo_id}/rounds", params={"player_id": player_id})
    assert rounds.json()["rounds"][0]["selected_player_kills"] == highlight["kill_event_ids"]

    exported = client.get(f"/api/demos/{demo_id}/analysis/{player_id}/export/json")
    assert exported.status_code == 200
    assert exported.json()["highlights"][0]["highlight_id"] == highlight["highlight_id"]
    csv_export = client.get(f"/api/demos/{demo_id}/analysis/{player_id}/export/csv")
    assert "round_number" in csv_export.text
    timestamps = client.get(f"/api/demos/{demo_id}/analysis/{player_id}/export/timestamps")
    assert "Round 1" in timestamps.text


def test_analysis_rejects_unknown_player_and_delete_cleans_records(
    client: TestClient, parsed_demo: tuple[str, str]
) -> None:
    demo_id, _ = parsed_demo
    response = client.get(f"/api/demos/{demo_id}/analysis/steamid:999")
    assert response.status_code == 422
    assert client.delete(f"/api/demos/{demo_id}").status_code == 204
    assert store.get_demo(demo_id) is None
    assert client.get(f"/api/demos/{demo_id}/players").status_code == 404
