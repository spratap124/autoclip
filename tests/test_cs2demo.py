"""Fast, synthetic checks for local demo validation and parsed-cache metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from autoclip.cs2demo.adapter import (
    CS2_DEMO_MAGIC,
    DemoParseCancelled,
    DemoParseError,
    parse_isolated,
    sha256_file,
    validate_demo_file,
)
from autoclip.cs2demo.cache import cache_path, load_cached
from autoclip.cs2demo.models import NORMALIZED_SCHEMA_VERSION, DemoPlayer, ParsedDemo


def test_validate_requires_cs2_source2_signature(tmp_path: Path) -> None:
    path = tmp_path / "demo.dem"
    path.write_bytes(CS2_DEMO_MAGIC + b"fixture")

    validate_demo_file(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"HL2DEMO\0legacy", "Legacy Source 1"),
        (b"\x28\xb5\x2f\xfdcompressed", "Zstandard-compressed"),
        (b"not a demo", "signature"),
    ],
)
def test_validate_rejects_unsupported_content(tmp_path: Path, payload: bytes, message: str) -> None:
    path = tmp_path / "demo.dem"
    path.write_bytes(payload)

    with pytest.raises(DemoParseError, match=message):
        validate_demo_file(path)


def test_validate_enforces_configured_size(tmp_path: Path) -> None:
    path = tmp_path / "demo.dem"
    path.write_bytes(CS2_DEMO_MAGIC + b"fixture")

    with pytest.raises(DemoParseError, match="upload limit"):
        validate_demo_file(path, max_bytes=8)


def test_sha256_streams_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.dem"
    path.write_bytes(b"abc")

    assert sha256_file(path, chunk_bytes=1) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_versioned_cache_round_trip_and_invalidation(tmp_path: Path) -> None:
    parsed = ParsedDemo(
        content_sha256="a" * 64,
        parser_version="0.42.2",
        schema_version=NORMALIZED_SCHEMA_VERSION,
        header={"map_name": "de_test"},
        players=[DemoPlayer("session-7", 7, "Player", 2)],
        event_names=["player_death"],
        events={"player_death": [{"tick": 12}]},
    )
    target = cache_path(parsed.content_sha256, root=tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(parsed.to_dict()), encoding="utf-8")

    loaded = load_cached(target, content_sha256=parsed.content_sha256)
    assert loaded == parsed
    assert load_cached(target, content_sha256="b" * 64) is None

    value = parsed.to_dict() | {"parser_version": "old"}
    target.write_text(json.dumps(value), encoding="utf-8")
    assert load_cached(target, content_sha256=parsed.content_sha256) is None


def test_cached_path_versions_parser_and_schema(tmp_path: Path) -> None:
    path = cache_path("a" * 64, root=tmp_path)
    assert f"parser-0.42.2-schema-{NORMALIZED_SCHEMA_VERSION}.json" in str(path)


def test_player_names_are_grouped_by_steamid_without_name_joining() -> None:
    from autoclip.cs2demo.adapter import _players

    class Frame:
        def to_dict(self, orient: str) -> list[dict]:
            assert orient == "records"
            return [
                {"steamid": 17, "name": "FirstName", "team_number": 2},
                {"steamid": 17, "name": "Renamed", "team_number": 2},
                {"steamid": 18, "name": "FirstName", "team_number": 3},
            ]

    players = _players(Frame())

    assert len(players) == 2
    assert players[0].steamid == 17
    assert players[0].name_history == ("FirstName", "Renamed")
    assert players[1].steamid == 18
    assert players[1].name == "FirstName"


def test_isolated_parser_honors_cancellation(tmp_path: Path, monkeypatch) -> None:
    import autoclip.cs2demo.adapter as adapter

    path = tmp_path / "demo.dem"
    path.write_bytes(CS2_DEMO_MAGIC + b"fixture")
    terminated: list[bool] = []

    class FakeProcess:
        pid = 123

    monkeypatch.setattr(adapter.subprocess, "Popen", lambda **_kwargs: FakeProcess())
    monkeypatch.setattr(adapter, "_terminate", lambda _process: terminated.append(True))

    with pytest.raises(DemoParseCancelled, match="cancelled"):
        parse_isolated(path, content_sha256="a" * 64, cancelled=lambda: True)
    assert terminated == [True]


def test_demo_database_migration_and_crud(initialised_db: int, tmp_path: Path) -> None:
    from autoclip.db import store
    from autoclip.db.models import DemoFile, new_id

    demo = DemoFile(
        id=new_id(),
        content_sha256="c" * 64,
        file_path=str(tmp_path / "match.dem"),
        original_filename="match.dem",
        size_bytes=8,
    )
    store.create_demo(demo)
    assert store.get_demo(demo.id) == demo
    assert store.get_demo_by_hash(demo.content_sha256) == demo
    store.update_demo_parse(demo.id, status="parsed", parser_version="0.42.2", warnings=["partial"])
    loaded = store.get_demo(demo.id)
    assert loaded is not None
    assert loaded.parse_status == "parsed"
    assert loaded.warnings == ["partial"]
    assert store.delete_demo(demo.id) == loaded
    assert store.get_demo(demo.id) is None
