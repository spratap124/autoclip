"""Parser-independent highlight output contracts and deterministic settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class DetectionSettings:
    tick_rate_override: float | None = None
    pre_roll_s: float = 4.0
    post_roll_s: float = 5.0
    won_clutch_post_roll_s: float = 8.0
    min_clip_duration_s: float = 8.0
    max_clip_duration_s: float = 120.0
    fast_multi_kill_s: float = 5.0
    headshot_streak_kills: int = 2
    strict_clutches: bool = True
    score_weights: dict[str, int] = field(
        default_factory=lambda: {
            "2k": 15,
            "3k": 25,
            "4k": 35,
            "5k": 45,
            "clutch_1v1": 10,
            "clutch_1v2": 20,
            "clutch_1v3": 30,
            "clutch_1v4": 40,
            "clutch_1v5": 50,
            "clutch_won": 20,
            "headshot": 2,
            "headshot_streak": 5,
            "wallbang": 5,
            "no_scope": 8,
            "through_smoke": 5,
            "blind_kill": 5,
            "knife_kill": 6,
            "awp_multi": 5,
            "opening_kill": 5,
            "round_winning_elimination": 8,
            "fast_multi_kill": 10,
        }
    )


@dataclass(frozen=True)
class ClutchRecord:
    clutch_id: str
    round_id: str
    start_tick: int
    end_tick: int | None
    player_id: str
    opponents_alive: int
    teammates_alive: int
    outcome: Literal["won", "lost", "unknown"]
    kill_event_ids: tuple[str, ...]
    evidence_complete: bool
    outcome_reason: str
    source_event_ids: tuple[str, ...] = ()
    bomb_state: str = "unknown"


@dataclass(frozen=True)
class Highlight:
    highlight_id: str
    demo_sha256: str
    player_id: str
    round_id: str
    round_number: int
    tags: tuple[str, ...]
    score: int
    score_breakdown: dict[str, int]
    start_tick: int
    end_tick: int
    start_seconds: float | None
    end_seconds: float | None
    kill_count: int
    kill_event_ids: tuple[str, ...]
    kills: tuple[dict[str, Any], ...]
    source_event_ids: tuple[str, ...] = ()
    clutch: ClutchRecord | None = None
    data_quality_warnings: tuple[str, ...] = ()
    timing_warning: str | None = None
    detection_version: str = "cs2-demo-1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerAnalysis:
    demo_sha256: str
    player_id: str
    highlights: tuple[Highlight, ...]
    warnings: tuple[str, ...]
