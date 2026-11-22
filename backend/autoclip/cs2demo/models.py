"""Parser-independent normalized demo contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

NORMALIZED_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class DemoPlayer:
    session_id: str
    steamid: int | None
    name: str | None
    team_number: int | None
    name_history: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedDemo:
    content_sha256: str
    parser_version: str
    schema_version: int
    header: dict[str, str]
    players: list[DemoPlayer]
    event_names: list[str]
    events: dict[str, list[dict[str, Any]]]
    player_states: list[dict[str, Any]] = field(default_factory=list)
    tick_rate: float | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "players": [asdict(player) for player in self.players],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ParsedDemo:
        return cls(
            content_sha256=str(value["content_sha256"]),
            parser_version=str(value["parser_version"]),
            schema_version=int(value["schema_version"]),
            header=dict(value.get("header") or {}),
            players=[
                DemoPlayer(
                    **(player | {"name_history": tuple(player.get("name_history", ()))})
                )
                for player in value.get("players", [])
            ],
            event_names=list(value.get("event_names", [])),
            events={k: list(v) for k, v in (value.get("events") or {}).items()},
            player_states=list(value.get("player_states", [])),
            tick_rate=value.get("tick_rate"),
            warnings=list(value.get("warnings", [])),
        )
