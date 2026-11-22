"""Pure, deterministic selected-player highlight analysis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .detection_models import (
    ClutchRecord,
    DetectionSettings,
    Highlight,
    PlayerAnalysis,
)
from .models import ParsedDemo
from .reconstruct import KillEvent, Reconstruction, RoundWindow, reconstruct
from .scoring import score_highlight


@dataclass
class _Play:
    round_window: RoundWindow
    kills: list[KillEvent]
    clutch: ClutchRecord | None = None


def analyze_player(
    parsed: ParsedDemo,
    player_id: str,
    settings: DetectionSettings | None = None,
) -> PlayerAnalysis:
    """Return every supported highlight for one stable player ID.

    `player_id` must be `steamid:<SteamID64>` or a roster session ID. Player
    names are never used to join events because duplicate names and name
    changes make them ambiguous.
    """
    settings = settings or DetectionSettings()
    reconstructed = reconstruct(parsed)
    players_by_id = {str(player["player_id"]): player for player in reconstructed.players}
    if player_id not in players_by_id:
        raise ValueError("Selected player is not present in the parsed demo roster.")
    if not player_id.startswith("steamid:"):
        raise ValueError(
            "This demo player has no SteamID64; event identity cannot be verified safely."
        )

    warnings = list(parsed.warnings) + list(reconstructed.warnings)
    if not parsed.player_states:
        warnings.append(
            "No player-state evidence is cached; clutch classifications are unavailable."
        )
    valid_match_kills = [kill for kill in reconstructed.kills if kill.valid_enemy_kill]
    player_kills = [kill for kill in valid_match_kills if kill.attacker_id == player_id]
    player_kills_by_round: dict[str, list[KillEvent]] = {}
    for kill in player_kills:
        player_kills_by_round.setdefault(kill.round_id, []).append(kill)
    rounds_by_id = {round_window.round_id: round_window for round_window in reconstructed.rounds}
    clutches, clutch_warnings = _find_clutches(
        parsed,
        reconstructed,
        player_id,
        settings,
        valid_match_kills,
    )
    warnings.extend(clutch_warnings)
    plays: list[_Play] = []
    for round_id, kills in player_kills_by_round.items():
        round_window = rounds_by_id[round_id]
        # Round membership is verified; a universal event-gap for separating
        # plays is not. Keep every valid selected-player kill in one round
        # group so a missing tick rate cannot make a multikill disappear.
        plays.append(
            _Play(round_window, sorted(kills, key=lambda item: (item.tick, item.event_id)))
        )
    for clutch in clutches:
        round_window = rounds_by_id.get(clutch.round_id)
        if round_window is None:
            continue
        clutch_kills = [kill for kill in player_kills if kill.event_id in clutch.kill_event_ids]
        overlap = [
            play
            for play in plays
            if play.round_window.round_id == clutch.round_id
            and any(kill.event_id in clutch.kill_event_ids for kill in play.kills)
        ]
        if overlap:
            target = overlap[0]
            for extra in overlap[1:]:
                target.kills.extend(extra.kills)
                plays.remove(extra)
            target.kills.extend(kill for kill in clutch_kills if kill not in target.kills)
            target.clutch = clutch
        else:
            plays.append(_Play(round_window, clutch_kills, clutch))

    opening_ids: set[str] = set()
    for round_window in reconstructed.rounds:
        round_kills = [kill for kill in valid_match_kills if kill.round_id == round_window.round_id]
        if round_kills:
            opening_ids.add(min(round_kills, key=lambda item: (item.tick, item.event_id)).event_id)

    highlights: list[Highlight] = []
    player_names = {key: value.get("name") for key, value in players_by_id.items()}
    for play in plays:
        kills = sorted(
            {kill.event_id: kill for kill in play.kills}.values(),
            key=lambda item: (item.tick, item.event_id),
        )
        tags = _tags_for_play(
            play,
            kills,
            opening_ids,
            reconstructed,
            parsed,
            player_id,
            settings,
        )
        if not tags:
            continue
        highlight = _build_highlight(
            parsed,
            player_id,
            play,
            kills,
            tags,
            player_names,
            settings,
        )
        highlights.append(highlight)
    highlights.sort(key=lambda item: (-item.score, item.start_tick, item.highlight_id))
    return PlayerAnalysis(
        demo_sha256=parsed.content_sha256,
        player_id=player_id,
        highlights=tuple(highlights),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _tags_for_play(
    play: _Play,
    kills: list[KillEvent],
    opening_ids: set[str],
    reconstruction: Reconstruction,
    parsed: ParsedDemo,
    player_id: str,
    settings: DetectionSettings,
) -> set[str]:
    tags: set[str] = set()
    victims = {kill.victim_id for kill in kills if kill.victim_id is not None}
    count = len(victims)
    if count >= 2:
        label_count = min(count, 5)
        tags.update({"multi_kill", f"{label_count}K"})
        if label_count == 5 and _verified_ace(play.round_window, kills, player_id, reconstruction):
            tags.add("ace")
    if play.clutch is not None:
        tags.add(f"clutch_1v{play.clutch.opponents_alive}")
        tags.add(f"clutch_{play.clutch.outcome}")
    if any(kill.event_id in opening_ids for kill in kills):
        tags.add("opening_kill")
    if any(
        _round_winning_kill(kill, play.round_window, player_id, reconstruction) for kill in kills
    ):
        tags.add("round_winning_elimination")
    if any(kill.headshot for kill in kills):
        tags.add("headshot")
    if _headshot_streak(kills, settings.headshot_streak_kills) and any(
        kill.headshot for kill in kills
    ):
        tags.add("headshot_streak")
    if any((kill.penetrated or 0) > 0 for kill in kills):
        tags.add("wallbang")
    if any(kill.no_scope for kill in kills):
        tags.add("no_scope")
    if any(kill.through_smoke for kill in kills):
        tags.add("through_smoke")
    if any(kill.attacker_blind for kill in kills):
        tags.add("blind_kill")
    if any((kill.weapon or "").lower().startswith("knife") for kill in kills):
        tags.add("knife_kill")
    awp_kills = [kill for kill in kills if (kill.weapon or "").lower() == "awp"]
    if len(awp_kills) >= 2:
        tags.add("awp_multi_kill")
    tick_rate = settings.tick_rate_override or parsed.tick_rate
    if (
        tick_rate
        and len(kills) >= 2
        and any(
            (right.tick - left.tick) / tick_rate <= settings.fast_multi_kill_s
            for left, right in zip(kills, kills[1:], strict=False)
        )
    ):
        tags.add("fast_multi_kill")
    return tags


def _find_clutches(
    parsed: ParsedDemo,
    reconstruction: Reconstruction,
    player_id: str,
    settings: DetectionSettings,
    valid_kills: list[KillEvent],
) -> tuple[list[ClutchRecord], list[str]]:
    required_ids = {
        str(player["player_id"])
        for player in reconstruction.players
        if player.get("steamid") is not None
    }
    warnings: list[str] = []
    if not parsed.player_states or player_id not in required_ids:
        return [], [
            "Clutch attempts were not classified because stable player-state evidence is missing."
        ]
    attempts: list[ClutchRecord] = []
    for round_window in reconstruction.rounds:
        active: dict[str, object] | None = None
        round_ticks = sorted(
            tick
            for tick in reconstruction.state_by_tick
            if tick >= round_window.active_start_tick
            and (round_window.end_tick is None or tick <= round_window.end_tick)
        )
        for tick in round_ticks:
            states = reconstruction.state_by_tick[tick]
            state_complete = required_ids.issubset(states) and all(
                states[identity].get("is_alive") is not None
                and states[identity].get("team_num") is not None
                for identity in required_ids
            )
            if active is not None and not state_complete:
                active["complete"] = False
            if settings.strict_clutches and not state_complete:
                continue
            if player_id not in states or states[player_id].get("is_alive") is None:
                continue
            selected = states[player_id]
            if selected.get("is_warmup_period") is True:
                continue
            team = selected.get("team_num")
            if team is None:
                continue
            team_alive = {
                identity
                for identity in (required_ids & states.keys())
                if states[identity].get("team_num") == team
                and states[identity].get("is_alive") is True
            }
            opponent_alive = {
                identity
                for identity in (required_ids & states.keys())
                if states[identity].get("team_num") != team
                and states[identity].get("is_alive") is True
            }
            if active is None:
                if (
                    selected.get("is_alive") is True
                    and team_alive == {player_id}
                    and opponent_alive
                ):
                    active = {
                        "start": tick,
                        "opponents": len(opponent_alive),
                        "team": team,
                        "source": {f"state:{tick}"},
                        "complete": state_complete,
                        "outcome": "unknown",
                        "end": None,
                        "reason": "round ended without a complete win/loss state",
                        "bomb_state": (
                            "planted"
                            if selected.get("is_bomb_planted") is True
                            else "not_planted"
                            if selected.get("is_bomb_planted") is False
                            else "unknown"
                        ),
                    }
                continue
            active["source"].add(f"state:{tick}")
            bomb_planted = selected.get("is_bomb_planted")
            if bomb_planted is True:
                active["bomb_state"] = "planted"
            elif bomb_planted is False and active.get("bomb_state") == "planted":
                active["bomb_state"] = "resolved"
            elif bomb_planted is False and active.get("bomb_state") == "unknown":
                active["bomb_state"] = "not_planted"
            if selected.get("is_alive") is False:
                active["outcome"] = "lost"
                active["end"] = tick
                active["reason"] = "selected player death observed in player state"
            elif not opponent_alive and team_alive:
                active["outcome"] = "won"
                active["end"] = tick
                active["reason"] = "all opposing players eliminated while selected player survived"
            elif tick == round_window.outcome_tick and round_window.winner_team is not None:
                active["outcome"] = (
                    "won" if team == round_window.winner_team else "lost"
                )
                active["end"] = tick
                active["reason"] = (
                    "round scoreboard advanced for the clutch player's team"
                    if team == round_window.winner_team
                    else "round scoreboard advanced for the opposing team"
                )
            if active["end"] is not None:
                attempts.append(
                    _clutch_record(
                        round_window,
                        player_id,
                        active,
                        [
                            kill
                            for kill in valid_kills
                            if kill.attacker_id == player_id
                            and kill.round_id == round_window.round_id
                            and int(active["start"]) <= kill.tick <= int(active["end"])
                        ],
                        evidence_complete=bool(active["complete"]),
                    )
                )
                active = None
                # The player may enter another clutch after a teammate respawn
                # in nonstandard modes, so continue scanning deterministically.
        if active is not None:
            outcome_state = reconstruction.state_by_tick.get(
                round_window.outcome_tick or round_window.end_tick or -1, {}
            )
            outcome_player = outcome_state.get(player_id)
            if outcome_player is not None:
                active["source"].add(f"state:{round_window.outcome_tick or round_window.end_tick}")
                if outcome_player.get("is_alive") is False:
                    active["outcome"] = "lost"
                    active["reason"] = "selected player death observed at round outcome"
                elif (
                    outcome_player.get("is_alive") is True
                    and round_window.winner_team is not None
                    and outcome_player.get("team_num") is not None
                ):
                    active["outcome"] = (
                        "won"
                        if outcome_player["team_num"] == round_window.winner_team
                        else "lost"
                    )
                    active["reason"] = "round scoreboard outcome and survivor state"
            active["end"] = round_window.outcome_tick or round_window.end_tick
            attempts.append(
                _clutch_record(
                    round_window,
                    player_id,
                    active,
                    [
                        kill
                        for kill in valid_kills
                        if kill.attacker_id == player_id
                        and kill.round_id == round_window.round_id
                        and kill.tick >= int(active["start"])
                    ],
                    evidence_complete=bool(active["complete"]) and round_window.complete,
                )
            )
    if settings.strict_clutches and not attempts and not reconstruction.state_by_tick:
        warnings.append("No complete player-state snapshots exist to prove clutch survivor counts.")
    return attempts, warnings


def _clutch_record(
    round_window: RoundWindow,
    player_id: str,
    active: dict[str, object],
    kills: list[KillEvent],
    *,
    evidence_complete: bool,
) -> ClutchRecord:
    start = int(active["start"])
    end_value = active.get("end")
    end = int(end_value) if end_value is not None else None
    return ClutchRecord(
        clutch_id=hashlib.sha256(
            f"{round_window.round_id}:{player_id}:{start}".encode()
        ).hexdigest()[:24],
        round_id=round_window.round_id,
        start_tick=start,
        end_tick=end,
        player_id=player_id,
        opponents_alive=int(active["opponents"]),
        teammates_alive=1,
        outcome=active["outcome"],
        kill_event_ids=tuple(kill.event_id for kill in kills),
        evidence_complete=evidence_complete,
        outcome_reason=str(active["reason"]),
        source_event_ids=tuple(sorted(active["source"])),
        bomb_state=str(active.get("bomb_state", "unknown")),
    )


def _verified_ace(
    round_window: RoundWindow,
    kills: list[KillEvent],
    player_id: str,
    reconstruction: Reconstruction,
) -> bool:
    state = reconstruction.state_by_tick.get(round_window.active_start_tick, {})
    selected = state.get(player_id)
    if selected is None or selected.get("is_alive") is not True:
        return False
    required = {
        str(player["player_id"])
        for player in reconstruction.players
        if player.get("steamid") is not None
    }
    if not required or not required.issubset(state):
        return False
    team = selected.get("team_num")
    if team is None:
        return False
    all_team_ids = {
        identity
        for identity in required
        if state[identity].get("team_num") == team
    }
    all_opponent_ids = {
        identity
        for identity in required
        if state[identity].get("team_num") != team
    }
    opponent_ids = {
        identity
        for identity, item in state.items()
        if item.get("team_num") != team and item.get("is_alive") is True
    }
    teammate_ids = {
        identity
        for identity, item in state.items()
        if item.get("team_num") == team and item.get("is_alive") is True
    }
    victims = {kill.victim_id for kill in kills if kill.victim_id is not None}
    return (
        len(all_team_ids) == 5
        and len(all_opponent_ids) == 5
        and len(teammate_ids) == 5
        and len(opponent_ids) == 5
        and victims == opponent_ids
    )


def _round_winning_kill(
    kill: KillEvent,
    round_window: RoundWindow,
    player_id: str,
    reconstruction: Reconstruction,
) -> bool:
    if kill.attacker_id != player_id:
        return False
    states = reconstruction.state_by_tick.get(kill.tick, {})
    selected = states.get(player_id)
    if selected is None or selected.get("team_num") is None:
        return False
    team = selected["team_num"]
    required = {
        str(player["player_id"])
        for player in reconstruction.players
        if player.get("steamid") is not None
    }
    if not required.issubset(states):
        return False
    start_state = reconstruction.state_by_tick.get(round_window.active_start_tick, {})
    if not required.issubset(start_state):
        return False
    start_team = start_state.get(player_id, {}).get("team_num")
    if start_team is None:
        return False
    if sum(item.get("team_num") == start_team for item in start_state.values()) != 5:
        return False
    if sum(item.get("team_num") != start_team for item in start_state.values()) != 5:
        return False
    opponents = [
        states[identity] for identity in required if states[identity].get("team_num") != team
    ]
    teammates = [
        states[identity] for identity in required if states[identity].get("team_num") == team
    ]
    return (
        bool(opponents)
        and all(item.get("is_alive") is False for item in opponents)
        and any(item.get("is_alive") is True for item in teammates)
    )


def _headshot_streak(kills: list[KillEvent], threshold: int) -> bool:
    run = 0
    for kill in sorted(kills, key=lambda item: item.tick):
        run = run + 1 if kill.headshot else 0
        if run >= threshold:
            return True
    return False


def _build_highlight(
    parsed: ParsedDemo,
    player_id: str,
    play: _Play,
    kills: list[KillEvent],
    tags: set[str],
    player_names: dict[str, str | None],
    settings: DetectionSettings,
) -> Highlight:
    if not kills and play.clutch is None:
        raise ValueError("A highlight must have at least one supporting kill or clutch event.")
    event_ticks = [kill.tick for kill in kills]
    if play.clutch is not None:
        event_ticks.append(play.clutch.start_tick)
        if play.clutch.end_tick is not None:
            event_ticks.append(play.clutch.end_tick)
    anchor_start = min(event_ticks)
    anchor_end = max(event_ticks)
    tick_rate = settings.tick_rate_override or parsed.tick_rate
    timing_warnings: list[str] = []
    if tick_rate:
        pre_ticks = round(settings.pre_roll_s * tick_rate)
        post_s = (
            settings.won_clutch_post_roll_s
            if play.clutch is not None and play.clutch.outcome == "won"
            else settings.post_roll_s
        )
        post_ticks = round(post_s * tick_rate)
    else:
        pre_ticks = post_ticks = 0
        timing_warnings.append(
            "Tick rate is unavailable; seconds and second-based padding are not derived."
        )
    start_tick = max(play.round_window.start_tick, anchor_start - pre_ticks)
    max_round_tick = play.round_window.end_tick
    end_tick = anchor_end + post_ticks
    if max_round_tick is not None:
        end_tick = min(max_round_tick, end_tick)
    if end_tick < anchor_end:
        end_tick = anchor_end
    if tick_rate:
        minimum_ticks = round(settings.min_clip_duration_s * tick_rate)
        missing = max(0, minimum_ticks - (end_tick - start_tick))
        extend_after = min(
            missing,
            max(0, (max_round_tick - end_tick) if max_round_tick is not None else missing),
        )
        end_tick += extend_after
        missing -= extend_after
        start_tick = max(play.round_window.start_tick, start_tick - missing)
        if (end_tick - start_tick) < minimum_ticks:
            timing_warnings.append(
                "Round boundaries prevent the configured minimum duration from being reached."
            )
    if tick_rate and (end_tick - start_tick) / tick_rate > settings.max_clip_duration_s:
        timing_warnings.append(
            "The complete event span exceeds the configured maximum duration; "
            "the event span is preserved without truncation."
        )
    if tick_rate and settings.tick_rate_override is not None:
        timing_warnings.append(
            "Seconds use the configured tick-rate override and represent demo time, not video time."
        )
    timing_warning = "; ".join(dict.fromkeys(timing_warnings)) or None
    score, breakdown = score_highlight(kills, tags, play.clutch, settings)
    kill_dicts = tuple(
        {
            "event_id": kill.event_id,
            "tick": kill.tick,
            "attacker_id": kill.attacker_id,
            "attacker_name": kill.raw.get("attacker_name")
            or player_names.get(kill.attacker_id or ""),
            "victim_id": kill.victim_id,
            "victim_name": kill.raw.get("user_name") or player_names.get(kill.victim_id or ""),
            "assister_id": kill.assister_id,
            "weapon": kill.weapon,
            "headshot": kill.headshot,
            "penetrated": kill.penetrated,
            "through_smoke": kill.through_smoke,
            "no_scope": kill.no_scope,
            "attacker_blind": kill.attacker_blind,
            "flash_assist": kill.flash_assist,
            "source_fields": kill.raw,
        }
        for kill in kills
    )
    kill_ids = tuple(kill.event_id for kill in kills)
    source_ids = set(kill_ids)
    source_ids.update(play.round_window.source_event_ids)
    if play.clutch is not None:
        source_ids.update(play.clutch.source_event_ids)
    highlight_id = hashlib.sha256(
        "|".join(
            [
                parsed.content_sha256,
                player_id,
                play.round_window.round_id,
                *sorted(source_ids),
                *sorted(tags),
            ]
        ).encode()
    ).hexdigest()[:24]
    return Highlight(
        highlight_id=highlight_id,
        demo_sha256=parsed.content_sha256,
        player_id=player_id,
        round_id=play.round_window.round_id,
        round_number=play.round_window.number,
        tags=tuple(sorted(tags, key=_tag_sort_key)),
        score=score,
        score_breakdown=breakdown,
        start_tick=start_tick,
        end_tick=max(start_tick, end_tick),
        start_seconds=round(start_tick / tick_rate, 3) if tick_rate else None,
        end_seconds=round(end_tick / tick_rate, 3) if tick_rate else None,
        kill_count=len({kill.victim_id for kill in kills if kill.victim_id is not None}),
        kill_event_ids=kill_ids,
        kills=kill_dicts,
        source_event_ids=tuple(sorted(source_ids)),
        clutch=play.clutch,
        data_quality_warnings=tuple(
            (["Round end boundary is incomplete."] if not play.round_window.complete else [])
            + (
                ["Clutch state or outcome is incomplete."]
                if play.clutch is not None and not play.clutch.evidence_complete
                else []
            )
        ),
        timing_warning=timing_warning,
    )


def _tag_sort_key(tag: str) -> tuple[int, str]:
    preferred = {
        "ace": 0,
        "5K": 1,
        "4K": 2,
        "3K": 3,
        "2K": 4,
        "multi_kill": 5,
        "clutch_won": 6,
        "clutch_lost": 7,
    }
    return preferred.get(tag, 20), tag
