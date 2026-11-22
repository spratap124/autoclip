"""Deterministic reconstruction of rounds, player identity, kills, and state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .models import ParsedDemo


@dataclass(frozen=True)
class RoundWindow:
    round_id: str
    number: int
    start_tick: int
    active_start_tick: int
    end_tick: int | None
    complete: bool
    source_event_ids: tuple[str, ...]
    end_reason: int | None = None
    outcome_tick: int | None = None
    winner_team: int | None = None
    outcome_evidence: str | None = None


@dataclass(frozen=True)
class KillEvent:
    event_id: str
    tick: int
    round_id: str
    attacker_id: str | None
    victim_id: str | None
    attacker_team: int | None
    victim_team: int | None
    assister_id: str | None
    weapon: str | None
    headshot: bool
    penetrated: int | None
    through_smoke: bool
    no_scope: bool
    attacker_blind: bool
    flash_assist: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def valid_enemy_kill(self) -> bool:
        return (
            self.attacker_id is not None
            and self.victim_id is not None
            and self.attacker_id != self.victim_id
            and self.attacker_team is not None
            and self.victim_team is not None
            and self.attacker_team != self.victim_team
        )


@dataclass(frozen=True)
class Reconstruction:
    players: tuple[dict[str, Any], ...]
    rounds: tuple[RoundWindow, ...]
    kills: tuple[KillEvent, ...]
    state_by_tick: dict[int, dict[str, dict[str, Any]]]
    warnings: tuple[str, ...]


def reconstruct(parsed: ParsedDemo) -> Reconstruction:
    """Build parser-independent event objects without I/O or parser access."""
    warnings: list[str] = []
    state_by_tick: dict[int, dict[str, dict[str, Any]]] = {}
    for row in parsed.player_states:
        tick = _int(row.get("tick"))
        steamid = _int(row.get("steamid"))
        if tick is None or steamid is None:
            continue
        state_by_tick.setdefault(tick, {})[f"steamid:{steamid}"] = {
            "steamid": steamid,
            "team_num": _int(row.get("team_num")),
            "is_alive": _bool_or_none(row.get("is_alive")),
            "is_warmup_period": _bool_or_none(row.get("is_warmup_period")),
            "round_in_progress": _bool_or_none(row.get("round_in_progress")),
            "total_rounds_played": _int(row.get("total_rounds_played")),
            "is_bomb_planted": _bool_or_none(row.get("is_bomb_planted")),
            "round_win_reason": _int(row.get("round_win_reason")),
            "team_rounds_total": _int(row.get("team_rounds_total")),
        }

    rounds = _round_windows(parsed, state_by_tick, warnings)
    player_records = [
        {
            "player_id": f"steamid:{player.steamid}"
            if player.steamid is not None
            else player.session_id,
            "session_id": player.session_id,
            "steamid": player.steamid,
            "name": player.name,
            "name_history": player.name_history,
            "team_number": player.team_number,
        }
        for player in parsed.players
    ]
    if any(player.steamid is None for player in parsed.players):
        warnings.append(
            "Some roster players lack SteamID64; event attribution for them may be unavailable."
        )

    raw_deaths = parsed.events.get("player_death", [])
    kills: list[KillEvent] = []
    seen: set[tuple[Any, ...]] = set()
    for ordinal, row in enumerate(raw_deaths):
        tick = _int(row.get("tick"))
        if tick is None:
            continue
        round_window = _round_for_tick(rounds, tick, active_only=True)
        if round_window is None:
            continue
        state = state_by_tick.get(tick, {})
        if any(item.get("is_warmup_period") is True for item in state.values()):
            continue
        attacker = _identity(row.get("attacker_steamid"))
        victim = _identity(row.get("user_steamid"))
        attacker_state = state.get(attacker or "", {})
        victim_state = state.get(victim or "", {})
        attacker_team = _int(attacker_state.get("team_num"))
        victim_team = _int(victim_state.get("team_num"))
        weapon = _optional_string(row.get("weapon"))
        key = (tick, attacker, victim, weapon)
        if key in seen:
            continue
        seen.add(key)
        event_id = hashlib.sha256(
            f"{parsed.content_sha256}:{round_window.round_id}:{tick}:{attacker}:{victim}:{ordinal}".encode()
        ).hexdigest()[:24]
        kills.append(
            KillEvent(
                event_id=event_id,
                tick=tick,
                round_id=round_window.round_id,
                attacker_id=attacker,
                victim_id=victim,
                attacker_team=attacker_team,
                victim_team=victim_team,
                assister_id=_identity(row.get("assister_steamid")),
                weapon=weapon,
                headshot=bool(row.get("headshot", False)),
                penetrated=_int(row.get("penetrated")),
                through_smoke=bool(row.get("thrusmoke", False)),
                no_scope=bool(row.get("noscope", False)),
                attacker_blind=bool(row.get("attackerblind", False)),
                flash_assist=bool(row.get("assistedflash", False)),
                raw=row,
            )
        )
    if raw_deaths and not kills:
        warnings.append("No player_death rows could be assigned to a known round boundary.")
    return Reconstruction(
        players=tuple(player_records),
        rounds=tuple(rounds),
        kills=tuple(sorted(kills, key=lambda item: (item.tick, item.event_id))),
        state_by_tick=state_by_tick,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _round_windows(
    parsed: ParsedDemo,
    state_by_tick: dict[int, dict[str, dict[str, Any]]],
    warnings: list[str],
) -> list[RoundWindow]:
    events = parsed.events
    # Freeze-end marks the actionable round state. In the observed real demo,
    # round_prestart precedes some freeze-end ticks by thousands of ticks and
    # must not be used as the round's lower event boundary.
    preferred = ["round_freeze_end", "round_start", "round_prestart", "round_poststart"]
    start_name = next((name for name in preferred if events.get(name)), None)
    if start_name is None:
        warnings.append("No round-start signals were found; round classification is unavailable.")
        return []
    starts = sorted(
        {tick for row in events[start_name] if (tick := _int(row.get("tick"))) is not None}
    )
    if not starts:
        warnings.append("Round-start events did not include usable ticks.")
        return []
    # Freeze-end marks the action start when present; prestart remains the
    # outer clip boundary to leave room for a later pre-roll.
    active_starts = sorted(
        {
            tick
            for row in events.get("round_freeze_end", [])
            if (tick := _int(row.get("tick"))) is not None
        }
    )
    if not active_starts:
        active_starts = starts
        warnings.append("round_freeze_end is unavailable; round starts use the next best event.")
    end_markers = [
        (tick, _event_id(parsed, name, row))
        for name in ("round_end", "round_officially_ended")
        for row in events.get(name, [])
        if (tick := _int(row.get("tick"))) is not None
    ]
    rounds: list[RoundWindow] = []
    for index, start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else None
        active = next(
            (
                tick
                for tick in active_starts
                if tick >= start and (next_start is None or tick < next_start)
            ),
            start,
        )
        markers = [
            marker
            for marker in end_markers
            if marker[0] >= start and (next_start is None or marker[0] < next_start)
        ]
        end = max((marker[0] for marker in markers), default=None)
        if end is None and next_start is not None:
            end = next_start - 1
        if not markers and next_start is None:
            warnings.append(f"Round {index + 1} has no verified end boundary.")
        boundary_ids = {
            _event_id(parsed, start_name, row)
            for row in events[start_name]
            if _int(row.get("tick")) == start
        }
        boundary_ids.update(
            _event_id(parsed, "round_freeze_end", row)
            for row in events.get("round_freeze_end", [])
            if _int(row.get("tick")) == active
        )
        boundary_ids.update(event_id for _, event_id in markers)
        outcome_tick = (
            end
            if markers
            else active_starts[index + 1]
            if next_start is not None
            else None
        )
        winner_team, outcome_evidence = _round_winner_from_scoreboard(
            active,
            outcome_tick,
            state_by_tick,
        )
        end_reason = _round_end_reason(outcome_tick, state_by_tick)
        if next_start is not None:
            boundary_ids.update(
                _event_id(parsed, "round_freeze_end", row)
                for row in events.get("round_freeze_end", [])
                if _int(row.get("tick")) == active_starts[index + 1]
            )
        rounds.append(
            RoundWindow(
                round_id=f"round-{index + 1:03d}",
                number=index + 1,
                start_tick=start,
                active_start_tick=active,
                end_tick=end,
                complete=end is not None and (bool(markers) or next_start is not None),
                source_event_ids=tuple(sorted(boundary_ids)),
                end_reason=end_reason,
                outcome_tick=outcome_tick,
                winner_team=winner_team,
                outcome_evidence=outcome_evidence,
            )
        )
    return rounds


def _round_for_tick(
    rounds: list[RoundWindow] | tuple[RoundWindow, ...], tick: int, *, active_only: bool
) -> RoundWindow | None:
    for round_window in rounds:
        start = round_window.active_start_tick if active_only else round_window.start_tick
        end = round_window.end_tick
        if tick >= start and (end is None or tick <= end):
            return round_window
    return None


def _identity(value: Any) -> str | None:
    number = _int(value)
    return f"steamid:{number}" if number and number > 0 else None


def _event_id(parsed: ParsedDemo, event_name: str, row: dict[str, Any]) -> str:
    tick = _int(row.get("tick"))
    digest = hashlib.sha256(
        f"{parsed.content_sha256}:{event_name}:{tick}:{sorted(row.items())}".encode()
    ).hexdigest()[:24]
    return f"event:{digest}"


def _round_winner_from_scoreboard(
    start_tick: int,
    outcome_tick: int | None,
    state_by_tick: dict[int, dict[str, dict[str, Any]]],
) -> tuple[int | None, str | None]:
    if outcome_tick is None:
        return None, None
    start_state = state_by_tick.get(start_tick, {})
    outcome_state = state_by_tick.get(outcome_tick, {})
    start_scores = _team_scores(start_state)
    outcome_scores = _team_scores(outcome_state)
    if len(start_scores) < 2 or set(start_scores) != set(outcome_scores):
        return None, None
    # A side swap changes the team-number-to-roster mapping. Do not compare
    # scores across that transition without a stable team identity source.
    shared_players = set(start_state) & set(outcome_state)
    if any(
        start_state[player].get("team_num") != outcome_state[player].get("team_num")
        for player in shared_players
    ):
        return None, None
    deltas = {
        team: outcome_scores[team] - start_scores[team]
        for team in start_scores
    }
    winners = [team for team, delta in deltas.items() if delta == 1]
    if len(winners) != 1 or any(delta not in (0, 1) for delta in deltas.values()):
        return None, None
    if sum(deltas.values()) != 1:
        return None, None
    return winners[0], "one team's round score increased by one"


def _team_scores(state: dict[str, dict[str, Any]]) -> dict[int, int]:
    values: dict[int, set[int]] = {}
    for row in state.values():
        team = _int(row.get("team_num"))
        score = _int(row.get("team_rounds_total"))
        if team is not None and score is not None:
            values.setdefault(team, set()).add(score)
    if not values or any(len(scores) != 1 for scores in values.values()):
        return {}
    return {team: next(iter(scores)) for team, scores in values.items()}


def _round_end_reason(
    outcome_tick: int | None,
    state_by_tick: dict[int, dict[str, dict[str, Any]]],
) -> int | None:
    if outcome_tick is None:
        return None
    reasons = {
        reason
        for row in state_by_tick.get(outcome_tick, {}).values()
        if (reason := _int(row.get("round_win_reason"))) is not None and reason > 0
    }
    return next(iter(reasons)) if len(reasons) == 1 else None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
