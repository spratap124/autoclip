"""Optional invariants against a caller-supplied local real CS2 demo."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from autoclip.cs2demo.analyze import analyze_player
from autoclip.cs2demo.cache import parse_cached
from autoclip.cs2demo.reconstruct import reconstruct

DEMO_PATH = os.environ.get("AUTOCLIP_TEST_DEMO_PATH")
pytestmark = pytest.mark.skipif(
    not DEMO_PATH,
    reason="Set AUTOCLIP_TEST_DEMO_PATH to a local, uncompressed CS2 .dem file.",
)


def test_local_demo_event_provenance_and_tick_invariants() -> None:
    assert DEMO_PATH is not None
    parsed, cache_hit = parse_cached(Path(DEMO_PATH))
    assert parsed.players
    reconstruction = reconstruct(parsed)
    round_ids = {item.round_id for item in reconstruction.rounds}
    rounds_by_id = {item.round_id: item for item in reconstruction.rounds}
    kill_ids = [kill.event_id for kill in reconstruction.kills]
    assert len(kill_ids) == len(set(kill_ids))
    assert all(kill.round_id in round_ids for kill in reconstruction.kills)
    assert all(
        kill.tick >= rounds_by_id[kill.round_id].active_start_tick
        and (
            rounds_by_id[kill.round_id].end_tick is None
            or kill.tick <= rounds_by_id[kill.round_id].end_tick
        )
        for kill in reconstruction.kills
    )
    selected = next((player for player in reconstruction.players if player.get("steamid")), None)
    if selected is not None:
        analysis = analyze_player(parsed, str(selected["player_id"]))
        known_kills = set(kill_ids)
        for highlight in analysis.highlights:
            assert set(highlight.kill_event_ids) <= known_kills
            assert set(highlight.kill_event_ids) <= set(highlight.source_event_ids)
            assert highlight.end_tick >= highlight.start_tick
            if parsed.tick_rate is None:
                assert highlight.start_seconds is None and highlight.end_seconds is None
    # This checks parser/cache and internal provenance only. It does not compare
    # against independent labels or claim detection accuracy.
    assert isinstance(cache_hit, bool)
