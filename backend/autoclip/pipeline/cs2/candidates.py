"""Deterministic grouping, clipping, and scoring of normalized evidence."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from .models import CS2Candidate, CS2Settings, EvidenceRecord

_CANDIDATE_ANCHOR_TYPES = {
    "kill_feed_change",
    "round_transition",
    "voice_reaction",
    "speech_moment",
}


@dataclass(frozen=True)
class EvidenceGroup:
    start_s: float
    end_s: float
    evidence: tuple[EvidenceRecord, ...]


def normalize_interval(
    start_s: float, end_s: float, duration_s: float
) -> tuple[float, float] | None:
    """Clamp an interval into a source duration; return None when empty."""
    if duration_s <= 0 or end_s < start_s:
        return None
    start = min(max(start_s, 0.0), duration_s)
    end = min(max(end_s, 0.0), duration_s)
    return round(start, 3), round(end, 3)


def group_evidence(
    evidence: list[EvidenceRecord], *, merge_gap_s: float, duration_s: float
) -> list[EvidenceGroup]:
    """Merge nearby observations, while normalizing their times to the source."""
    normalized: list[EvidenceRecord] = []
    for record in evidence:
        interval = normalize_interval(record.start_s, record.end_s, duration_s)
        if interval is None:
            continue
        normalized.append(record.model_copy(update={"start_s": interval[0], "end_s": interval[1]}))
    normalized.sort(key=lambda item: (item.start_s, item.end_s, item.event_id))

    groups: list[EvidenceGroup] = []
    current: list[EvidenceRecord] = []
    start = end = 0.0
    for record in normalized:
        if current and record.start_s - end > merge_gap_s:
            groups.append(EvidenceGroup(start, end, tuple(current)))
            current = []
        if not current:
            start, end = record.start_s, record.end_s
        else:
            end = max(end, record.end_s)
        current.append(record)
    if current:
        groups.append(EvidenceGroup(start, end, tuple(current)))
    return groups


def generate_candidates(
    evidence: list[EvidenceRecord],
    *,
    duration_s: float,
    settings: CS2Settings | None = None,
) -> list[CS2Candidate]:
    """Create ranked, explainable candidate windows from weak/strong evidence."""
    settings = settings or CS2Settings()
    player_focused = bool(settings.local_player_name.strip())
    if player_focused:
        # Player-focused jobs may anchor only on conservatively attributed kills.
        # Round banners and unrelated kills can contribute context, never replace
        # selected-player action as the reason a clip exists.
        anchors = [
            item
            for item in evidence
            if item.evidence_type == "kill_feed_change"
            and item.supporting_metadata.get("selected_player_role") == "attacker"
            and item.supporting_metadata.get("attribution_confidence", 0) >= 0.9
        ]
    else:
        anchors = [item for item in evidence if item.evidence_type in _CANDIDATE_ANCHOR_TYPES]
    round_ends = sorted(
        {
            item.start_s
            for item in evidence
            if item.evidence_type == "round_transition" and item.detection_confidence >= 0.7
        }
    )
    if player_focused:
        by_round: dict[int, list[EvidenceRecord]] = {}
        for anchor in anchors:
            by_round.setdefault(bisect_left(round_ends, anchor.start_s), []).append(anchor)
        groups = [
            (round_id, group)
            for round_id, round_items in sorted(by_round.items())
            for group in group_evidence(
                round_items, merge_gap_s=settings.merge_gap_s, duration_s=duration_s
            )
        ]
    else:
        groups = [
            (None, group)
            for group in group_evidence(
                anchors, merge_gap_s=settings.merge_gap_s, duration_s=duration_s
            )
        ]
    candidates: list[CS2Candidate] = []

    for round_id, group in groups:
        lower_bound = round_ends[round_id - 1] if round_id else 0.0
        upper_bound = (
            round_ends[round_id]
            if round_id is not None and round_id < len(round_ends)
            else duration_s
        )
        start = max(lower_bound, group.start_s - settings.context_before_s)
        end = min(upper_bound, group.end_s + settings.context_after_s)
        desired = min(max(end - start, settings.min_candidate_s), settings.max_candidate_s)
        if end - start < desired:
            start, end = _expand_interval(start, end, desired, lower_bound, upper_bound)
        interval = normalize_interval(start, end, duration_s)
        if interval is None:
            continue

        support = list(group.evidence)
        for item in evidence:
            if item.evidence_type in _CANDIDATE_ANCHOR_TYPES:
                if (
                    player_focused
                    and item.evidence_type == "round_transition"
                    and round_id is not None
                    and item.start_s == upper_bound
                ):
                    support.append(item)
                continue
            if item.start_s <= group.end_s + settings.context_after_s and item.end_s >= (
                group.start_s - settings.context_before_s
            ):
                support.append(item)
        types = {item.evidence_type for item in support}
        # In selected-player mode, supporting audio or visual activity may raise
        # estimated quality but must not raise confidence in who got the kill.
        confidence_records = group.evidence if player_focused else tuple(support)
        confidence = _combined_confidence(tuple(confidence_records))
        quality = _quality_score(tuple(support), types)
        kill_count = sum(item.evidence_type == "kill_feed_change" for item in group.evidence)
        victim_names = [
            str(item.supporting_metadata.get("victim_name", "")).casefold().strip()
            for item in group.evidence
        ]
        ace_supported = (
            kill_count >= 5
            and round_id is not None
            and round_id < len(round_ends)
            and all(victim_names)
            and len(set(victim_names)) == kill_count
            and all(item.detection_confidence >= 0.7 for item in group.evidence)
        )
        round_bounded = round_id is not None and round_id < len(round_ends)
        category = _category(
            types,
            kill_count,
            player_focused=player_focused,
            round_bounded=round_bounded,
            ace_supported=ace_supported,
        )
        reason = _selection_reason(group.evidence, support)
        if player_focused:
            reason = _player_selection_reason(
                settings.local_player_name,
                kill_count,
                round_bounded=round_bounded,
                category=category,
            )
        candidates.append(
            CS2Candidate(
                start_s=interval[0],
                end_s=interval[1],
                category=category,
                detection_confidence=confidence,
                quality_score=quality,
                supporting_evidence_ids=list(dict.fromkeys(item.event_id for item in support)),
                selection_reason=reason,
            )
        )

    return deduplicate_candidates(candidates)[: settings.max_candidates]


def _expand_interval(
    start: float, end: float, desired: float, lower_bound: float, upper_bound: float
) -> tuple[float, float]:
    deficit = desired - (end - start)
    left_room = start - lower_bound
    right_room = upper_bound - end
    left = min(left_room, deficit / 2)
    right = min(right_room, deficit - left)
    left += min(left_room - left, deficit - left - right)
    return start - left, end + right


def deduplicate_candidates(
    candidates: list[CS2Candidate], *, iou_threshold: float = 0.5
) -> list[CS2Candidate]:
    """Keep the best candidate from heavily overlapping intervals."""
    kept: list[CS2Candidate] = []
    for candidate in sorted(candidates, key=lambda item: item.quality_score, reverse=True):
        if any(_interval_iou(candidate, prior) > iou_threshold for prior in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda item: (-item.quality_score, item.start_s))


def _interval_iou(first: CS2Candidate, second: CS2Candidate) -> float:
    intersection = max(0.0, min(first.end_s, second.end_s) - max(first.start_s, second.start_s))
    union = max(first.end_s, second.end_s) - min(first.start_s, second.start_s)
    return intersection / union if union > 0 else 0.0


def _combined_confidence(records: tuple[EvidenceRecord, ...]) -> float:
    # Confidence describes signal detection only, not whether the gameplay is a highlight.
    independent = {record.evidence_type for record in records}
    best_by_type = [
        max(r.detection_confidence for r in records if r.evidence_type == kind)
        for kind in independent
    ]
    return round(min(0.99, sum(best_by_type) / max(1, len(best_by_type))), 3)


def _quality_score(records: tuple[EvidenceRecord, ...], kinds: set[str]) -> int:
    # Repeated distinct feed changes rank above a lone round banner; confidence stays separate.
    kill_count = sum(record.evidence_type == "kill_feed_change" for record in records)
    intensity = max(
        (float(record.supporting_metadata.get("intensity", 0.0)) for record in records),
        default=0.0,
    )
    event_bonus = min(48, max(0, kill_count - 1) * 12)
    support_bonus = min(12, max(0, len(kinds) - 1) * 4)
    value = 20 + event_bonus + support_bonus + round(12 * max(0.0, min(1.0, intensity)))
    return min(85, max(0, value))


def _category(
    kinds: set[str],
    kill_count: int,
    *,
    player_focused: bool = False,
    round_bounded: bool = False,
    ace_supported: bool = False,
) -> str:
    if "kill_feed_change" in kinds:
        if player_focused:
            if kill_count == 1:
                return "player_kill_potential"
            if kill_count == 2:
                return "player_double_kill_potential"
            if kill_count == 3:
                return (
                    "player_triple_kill_potential"
                    if round_bounded
                    else "player_multi_kill_sequence_potential"
                )
            if kill_count == 4 and round_bounded:
                return "player_four_kill_round_potential"
            if ace_supported:
                return "potential_ace"
            return "player_multi_kill_sequence_potential"
        return "multi_kill_sequence_potential" if kill_count >= 2 else "kill_feed_moment_potential"
    if "round_transition" in kinds:
        return "round_transition_potential"
    if "voice_reaction" in kinds:
        return "voice_reaction_potential"
    if "speech_moment" in kinds:
        return "speech_moment_potential"
    if "audio_activity" in kinds and "visual_activity" in kinds:
        return "multi_signal_potential"
    if "audio_activity" in kinds:
        return "audio_activity_potential"
    if "visual_activity" in kinds:
        return "visual_activity_potential"
    return "unclassified_potential"


def _selection_reason(anchors: tuple[EvidenceRecord, ...], support: list[EvidenceRecord]) -> str:
    kill_count = sum(item.evidence_type == "kill_feed_change" for item in anchors)
    has_round = any(item.evidence_type == "round_transition" for item in anchors)
    other_types = sorted(
        {
            item.evidence_type
            for item in support
            if item.evidence_type not in _CANDIDATE_ANCHOR_TYPES
        }
    )
    if kill_count >= 2:
        summary = (
            f"Potential multi-kill sequence supported by {kill_count} distinct kill-feed changes; "
            "the feed does not confirm that one player made every kill."
        )
    elif kill_count == 1:
        summary = "Potential gameplay moment supported by a new kill-feed row."
    elif has_round:
        summary = "Potential round-transition moment supported by a winner banner."
    else:
        summary = "Potential gameplay moment supported by timestamped HUD evidence."
    return summary + (f" Additional signals: {', '.join(other_types)}." if other_types else "")


def _player_selection_reason(
    player_name: str, kill_count: int, *, round_bounded: bool, category: str
) -> str:
    if kill_count == 1:
        summary = "One high-confidence kill-feed row attributes the kill to the selected player."
    elif round_bounded:
        summary = (
            f"{kill_count} distinct kill-feed rows attribute kills to the selected player "
            "within one detected round."
        )
    else:
        summary = (
            f"{kill_count} distinct kill-feed rows attribute kills to the selected player "
            "within a short sequence; a round boundary was not established."
        )
    return f"{category.replace('_', ' ').title()} for {player_name!r}. {summary}"
