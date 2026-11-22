"""Transparent heuristic score calculation; never used to filter highlights."""

from __future__ import annotations

from .detection_models import ClutchRecord, DetectionSettings
from .reconstruct import KillEvent


def score_highlight(
    kills: list[KillEvent],
    tags: set[str],
    clutch: ClutchRecord | None,
    settings: DetectionSettings,
) -> tuple[int, dict[str, int]]:
    weights = settings.score_weights

    def weight(key: str) -> int:
        return int(weights.get(key, 0))
    breakdown: dict[str, int] = {}
    count = len({kill.victim_id for kill in kills if kill.victim_id is not None})
    count_key = {2: "2k", 3: "3k", 4: "4k", 5: "5k"}.get(min(count, 5))
    if count_key:
        breakdown[f"{count_key}_round_multikill"] = weight(count_key)
    if clutch is not None:
        clutch_key = f"clutch_1v{clutch.opponents_alive}"
        if clutch_key in weights:
            breakdown[clutch_key] = weight(clutch_key)
        if clutch.outcome == "won":
            breakdown["clutch_won"] = weight("clutch_won")
    headshots = sum(kill.headshot for kill in kills)
    if headshots:
        breakdown["headshots"] = headshots * weight("headshot")
    tag_weights = {
        "headshot_streak": "headshot_streak",
        "wallbang": "wallbang",
        "no_scope": "no_scope",
        "through_smoke": "through_smoke",
        "blind_kill": "blind_kill",
        "knife_kill": "knife_kill",
        "awp_multi_kill": "awp_multi",
        "opening_kill": "opening_kill",
        "round_winning_elimination": "round_winning_elimination",
        "fast_multi_kill": "fast_multi_kill",
    }
    for tag, weight_key in tag_weights.items():
        if tag in tags:
            breakdown[weight_key] = weight(weight_key)
    return sum(breakdown.values()), breakdown
