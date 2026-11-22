"""Deterministic unit tests for event reconstruction and highlight tags."""

from __future__ import annotations

import pytest
from autoclip.cs2demo.analyze import analyze_player
from autoclip.cs2demo.detection_models import DetectionSettings
from autoclip.cs2demo.models import NORMALIZED_SCHEMA_VERSION, DemoPlayer, ParsedDemo
from autoclip.cs2demo.reconstruct import reconstruct

SELECTED = "steamid:1000"


def _players(team_sizes: tuple[int, int] = (5, 5)) -> list[DemoPlayer]:
    return [
        DemoPlayer(
            session_id=f"session-{index}",
            steamid=1000 + index,
            name=f"p{index}",
            team_number=2 if index < team_sizes[0] else 3,
        )
        for index in range(sum(team_sizes))
    ]


def _death(
    tick: int,
    attacker: int | None,
    victim: int,
    *,
    weapon: str = "ak47",
    headshot: bool = False,
    penetrated: int = 0,
    thrusmoke: bool = False,
    noscope: bool = False,
    attackerblind: bool = False,
    warmup: bool = False,
) -> dict:
    return {
        "tick": tick,
        "attacker_steamid": str(attacker) if attacker is not None else "",
        "attacker_name": f"p{attacker - 1000}" if attacker else "",
        "user_steamid": str(victim),
        "user_name": f"p{victim - 1000}",
        "assister_steamid": "",
        "weapon": weapon,
        "headshot": headshot,
        "penetrated": penetrated,
        "thrusmoke": thrusmoke,
        "noscope": noscope,
        "attackerblind": attackerblind,
        "assistedflash": False,
        "warmup": warmup,
    }


def _states(
    players: list[DemoPlayer],
    ticks: list[int],
    dead_by_tick: dict[int, set[int]] | None = None,
    *,
    alive_by_tick: dict[int, set[int]] | None = None,
    warmup_ticks: set[int] | None = None,
    scores_by_tick: dict[int, dict[int, int]] | None = None,
) -> list[dict]:
    dead_by_tick = dead_by_tick or {}
    warmup_ticks = warmup_ticks or set()
    rows = []
    for tick in ticks:
        alive = alive_by_tick.get(tick) if alive_by_tick else None
        dead = dead_by_tick.get(tick, set())
        for player in players:
            rows.append(
                {
                    "tick": tick,
                    "steamid": player.steamid,
                    "name": player.name,
                    "team_num": player.team_number,
                    "is_alive": (
                        player.steamid in alive if alive is not None else player.steamid not in dead
                    ),
                    "is_warmup_period": tick in warmup_ticks,
                    "round_in_progress": True,
                    "total_rounds_played": 1,
                    "is_bomb_planted": False,
                    "round_win_reason": 0,
                    "team_rounds_total": 0,
                }
            )
            if scores_by_tick and tick in scores_by_tick:
                rows[-1]["team_rounds_total"] = scores_by_tick[tick].get(
                    int(player.team_number or 0), 0
                )
    return rows


def _demo(
    deaths: list[dict],
    *,
    team_sizes: tuple[int, int] = (5, 5),
    starts: list[int] | None = None,
    ends: list[int] | None = None,
    player_states: list[dict] | None = None,
    tick_rate: float | None = 64.0,
) -> ParsedDemo:
    players = _players(team_sizes)
    starts = starts or [100]
    ends = ends or [300]
    event_rows = {
        "round_freeze_end": [{"tick": tick} for tick in starts],
        "round_officially_ended": [{"tick": tick} for tick in ends],
        "player_death": deaths,
    }
    if player_states is None:
        ticks = sorted({starts[0], *[int(row["tick"]) for row in deaths], *ends})
        dead_by_tick = {}
        dead: set[int] = set()
        for row in sorted(deaths, key=lambda value: value["tick"]):
            dead = set(dead)
            dead.add(int(row["user_steamid"]))
            dead_by_tick[int(row["tick"])] = dead
        player_states = _states(players, ticks, dead_by_tick)
    return ParsedDemo(
        content_sha256="a" * 64,
        parser_version="0.42.2",
        schema_version=NORMALIZED_SCHEMA_VERSION,
        header={"map_name": "de_test"},
        players=players,
        event_names=list(event_rows),
        events=event_rows,
        player_states=player_states,
        tick_rate=tick_rate,
    )


def _result(demo: ParsedDemo, settings: DetectionSettings | None = None):
    return analyze_player(demo, SELECTED, settings)


def _tags(result) -> set[str]:
    return {tag for highlight in result.highlights for tag in highlight.tags}


def test_round_scoped_two_kill_and_three_kill_classification() -> None:
    two = _demo([_death(110, 1000, 1005), _death(130, 1000, 1006)])
    three = _demo([_death(110, 1000, 1005), _death(130, 1000, 1006), _death(150, 1000, 1007)])

    assert "2K" in _tags(_result(two))
    assert "3K" in _tags(_result(three))


def test_four_kill_and_verified_ace_classification() -> None:
    four = _demo([_death(110 + 10 * i, 1000, 1005 + i) for i in range(4)])
    ace = _demo([_death(110 + 10 * i, 1000, 1005 + i) for i in range(5)])

    assert "4K" in _tags(_result(four))
    assert "5K" in _tags(_result(ace))
    assert "ace" in _tags(_result(ace))


def test_five_kills_in_nonstandard_six_opponent_round_is_not_ace() -> None:
    demo = _demo(
        [_death(110 + 10 * i, 1000, 1004 + i) for i in range(5)],
        team_sizes=(4, 6),
    )

    assert "5K" in _tags(_result(demo))
    assert "ace" not in _tags(_result(demo))


def test_ace_requires_five_roster_players_per_team_not_only_five_alive() -> None:
    players = _players(team_sizes=(6, 5))
    dead_teammate = 1005
    victims = [1006, 1007, 1008, 1009, 1010]
    ticks = [100, 110, 120, 130, 140, 150, 300]
    dead_by_tick = {100: {dead_teammate}}
    dead: set[int] = {dead_teammate}
    for tick, victim in zip(ticks[1:-1], victims, strict=True):
        dead = {*dead, victim}
        dead_by_tick[tick] = dead
    states = _states(players, ticks, dead_by_tick)
    demo = _demo(
        [_death(tick, 1000, victim) for tick, victim in zip(ticks[1:-1], victims, strict=True)],
        team_sizes=(6, 5),
        player_states=states,
    )

    assert "5K" in _tags(_result(demo))
    assert "ace" not in _tags(_result(demo))


def test_kills_across_rounds_are_never_merged() -> None:
    deaths = [_death(110, 1000, 1005), _death(210, 1000, 1006)]
    demo = _demo(deaths, starts=[100, 200], ends=[180, 280])
    result = _result(demo)

    assert "2K" not in _tags(result)
    assert {item.round_number for item in result.highlights} == {1, 2}


def test_duplicate_rows_teamkill_suicide_world_and_warmup_are_excluded() -> None:
    valid = _death(110, 1000, 1005)
    deaths = [
        valid,
        valid.copy(),
        _death(120, 1000, 1001),  # team kill
        _death(130, 1000, 1000),  # suicide
        _death(140, None, 1005),  # world kill
        _death(150, 1000, 1006, warmup=True),
    ]
    states = _states(
        _players(),
        [100, 110, 120, 130, 140, 150, 300],
        warmup_ticks={150},
    )
    demo = _demo(deaths, player_states=states)
    reconstruction = reconstruct(demo)
    valid_events = [kill for kill in reconstruction.kills if kill.valid_enemy_kill]

    assert len(valid_events) == 1
    assert valid_events[0].attacker_id == SELECTED


def test_kill_indicators_and_opening_kill_tags() -> None:
    demo = _demo(
        [
            _death(
                110,
                1000,
                1005,
                weapon="awp",
                headshot=True,
                penetrated=1,
                thrusmoke=True,
                noscope=True,
                attackerblind=True,
            ),
            _death(120, 1000, 1006, weapon="awp", headshot=True),
        ]
    )
    tags = _tags(_result(demo, DetectionSettings(tick_rate_override=64)))

    assert {
        "2K",
        "opening_kill",
        "headshot",
        "headshot_streak",
        "wallbang",
        "through_smoke",
        "no_scope",
        "blind_kill",
        "awp_multi_kill",
        "fast_multi_kill",
    } <= tags


def test_fast_multi_kill_detects_a_rapid_pair_inside_a_long_round() -> None:
    demo = _demo(
        [_death(110, 1000, 1005), _death(111, 1000, 1006), _death(900, 1000, 1007)]
    )

    assert "fast_multi_kill" in _tags(
        _result(demo, DetectionSettings(tick_rate_override=64, fast_multi_kill_s=1))
    )


def test_knife_kill_tag_and_wallbang_requires_positive_penetration() -> None:
    knife = _demo([_death(110, 1000, 1005, weapon="knife_karambit")])
    no_wallbang = _demo([_death(110, 1000, 1005, penetrated=0)])

    assert "knife_kill" in _tags(_result(knife))
    assert "wallbang" not in _tags(_result(no_wallbang))


def test_clutch_won_lost_and_unknown_require_alive_state() -> None:
    players = _players()
    all_ids = {player.steamid for player in players}
    selected_team = {1000, 1001, 1002, 1003, 1004}
    enemy_team = all_ids - selected_team
    won_states = _states(
        players,
        [100, 110, 120, 130, 140, 150, 160, 300],
        alive_by_tick={
            100: all_ids,
            110: {1000, *enemy_team},
            120: {1000, 1006, 1007, 1008, 1009},
            130: {1000, 1007, 1008, 1009},
            140: {1000, 1008, 1009},
            150: {1000, 1009},
            160: {1000},
            300: {1000},
        },
    )
    won = _demo(
        [_death(120 + 10 * i, 1000, 1005 + i) for i in range(5)],
        player_states=won_states,
    )
    won_result = _result(won)
    assert "clutch_1v5" in _tags(won_result)
    assert "clutch_won" in _tags(won_result)

    lost_states = _states(
        players,
        [100, 110, 120, 130, 300],
        alive_by_tick={
            100: all_ids,
            110: {1000, *enemy_team},
            120: {1000, 1006, 1007, 1008, 1009},
            130: {1006, 1007, 1008, 1009},
            300: set(),
        },
    )
    lost = _demo(
        [_death(130, 1005, 1000)],
        player_states=lost_states,
    )
    assert "clutch_lost" in _tags(_result(lost))

    unknown_states = _states(
        players,
        [100, 110, 300],
        alive_by_tick={100: all_ids, 110: {1000, *enemy_team}, 300: {1000, *enemy_team}},
    )
    unknown = _demo([], player_states=unknown_states)
    unknown_result = _result(unknown)
    assert "clutch_unknown" in _tags(unknown_result)
    assert "clutch_won" not in _tags(unknown_result)


def test_clutch_outcome_can_use_verified_round_score_for_bomb_result() -> None:
    players = _players()
    all_ids = {player.steamid for player in players}
    enemies = all_ids - {1000, 1001, 1002, 1003, 1004}
    states = _states(
        players,
        [100, 110, 300],
        alive_by_tick={100: all_ids, 110: {1000, *enemies}, 300: {1000, *enemies}},
        scores_by_tick={100: {2: 0, 3: 0}, 300: {2: 1, 3: 0}},
    )
    demo = _demo([], player_states=states)
    result = _result(demo)
    clutch_highlights = [
        highlight for highlight in result.highlights if "clutch_1v5" in highlight.tags
    ]

    assert len(clutch_highlights) == 1
    assert "clutch_won" in clutch_highlights[0].tags
    assert clutch_highlights[0].clutch is not None
    assert "scoreboard" in clutch_highlights[0].clutch.outcome_reason


def test_missing_state_prevents_unverified_clutch_claim() -> None:
    players = _players()
    incomplete = _states(players, [100, 110, 300])
    incomplete = [row for row in incomplete if row["steamid"] != 1009]
    demo = _demo([], player_states=incomplete)

    assert not any(tag.startswith("clutch_") for tag in _tags(_result(demo)))


@pytest.mark.parametrize("opponents_alive", [1, 2, 3, 4, 5])
def test_clutch_start_labels_supported_opponent_counts(opponents_alive: int) -> None:
    players = _players()
    all_ids = {player.steamid for player in players}
    enemies = sorted(all_ids - {1000, 1001, 1002, 1003, 1004})
    alive = {1000, *enemies[:opponents_alive]}
    states = _states(
        players,
        [100, 110, 300],
        alive_by_tick={100: all_ids, 110: alive, 300: alive},
    )

    assert f"clutch_1v{opponents_alive}" in _tags(_result(_demo([], player_states=states)))


def test_reanalysis_is_stable_and_every_score_has_breakdown() -> None:
    demo = _demo([_death(110, 1000, 1005), _death(120, 1000, 1006, headshot=True)])
    first = _result(demo)
    second = _result(demo)

    assert [item.highlight_id for item in first.highlights] == [
        item.highlight_id for item in second.highlights
    ]
    for highlight in first.highlights:
        assert highlight.score == sum(highlight.score_breakdown.values())
        assert set(highlight.kill_event_ids) <= set(highlight.source_event_ids)


def test_clip_window_uses_padding_minimum_and_preserves_event_span() -> None:
    demo = _demo(
        [_death(400, 1000, 1005), _death(420, 1000, 1006)],
        starts=[0],
        ends=[1000],
    )
    settings = DetectionSettings(
        pre_roll_s=1,
        post_roll_s=1,
        min_clip_duration_s=8,
        max_clip_duration_s=3,
    )
    highlight = _result(demo, settings).highlights[0]

    assert highlight.start_tick <= 400
    assert highlight.end_tick >= 420
    assert highlight.end_tick - highlight.start_tick >= 8 * 64
    assert "maximum duration" in (highlight.timing_warning or "")
