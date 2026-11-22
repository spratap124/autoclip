"""Bounded local diagnostics for CS2 sampling and label evaluation."""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from pathlib import Path
from typing import Any

import cv2

from .models import CS2Candidate, EvidenceRecord

MAX_CROP_BYTES = 16 * 1024 * 1024


def round_association(timestamp_s: float, round_ends: list[float]) -> dict[str, Any]:
    index = bisect_left(round_ends, timestamp_s)
    return {
        "detected_round_index": index,
        "previous_round_end_s": round_ends[index - 1] if index else None,
        "next_round_end_s": round_ends[index] if index < len(round_ends) else None,
        "boundary_detected": index < len(round_ends),
    }


class DiagnosticWriter:
    """Write one JSONL record per sampled frame and bounded representative crops."""

    def __init__(self, directory: Path, *, max_crops: int, frame_count: int) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.crop_dir = directory / "kill_feed_crops"
        self.crop_dir.mkdir(parents=True, exist_ok=True)
        # Replace only files owned by this diagnostic feature; never touch source,
        # sample, feature-cache, or user-export files.
        for path in self.crop_dir.glob("killfeed_*.jpg"):
            path.unlink(missing_ok=True)
        self.frames = (directory / "frames.jsonl").open("w", encoding="utf-8")
        self.max_crops = max_crops
        self.crop_stride = max(1, math.ceil(frame_count / max_crops)) if max_crops else 0
        self.crop_count = 0
        self.crop_bytes = 0
        self.frame_count = frame_count

    def write_frame(self, record: dict[str, Any], crop, *, index: int) -> None:
        if (
            crop is not None
            and self.crop_count < self.max_crops
            and index % self.crop_stride == 0
        ):
            ok, encoded = cv2.imencode(
                ".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 78]
            )
            if ok and self.crop_bytes + len(encoded) <= MAX_CROP_BYTES:
                name = f"killfeed_{index:06d}_{record['timestamp_s']:.3f}.jpg"
                (self.crop_dir / name).write_bytes(encoded.tobytes())
                self.crop_count += 1
                self.crop_bytes += len(encoded)
                record["kill_feed_crop"] = f"kill_feed_crops/{name}"
            else:
                record["kill_feed_crop"] = None
                record["crop_omitted"] = "per-run byte cap reached"
        self.frames.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if not self.frames.closed:
            self.frames.flush()
            self.frames.close()
        self.write_summary()

    def write_summary(self) -> None:
        (self.directory / "summary.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "sampled_frames": self.frame_count,
                    "crop_count": self.crop_count,
                    "crop_bytes": self.crop_bytes,
                    "crop_byte_cap": MAX_CROP_BYTES,
                    "crop_count_cap": self.max_crops,
                    "frames_file": "frames.jsonl",
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def write_candidate_decisions(
    path: Path,
    evidence: list[EvidenceRecord],
    candidates: list[CS2Candidate],
    *,
    local_player_name: str,
    max_candidates: int,
) -> None:
    """Explain whether each emitted kill observation became a final candidate."""
    if not path.parent.is_dir():
        return
    by_evidence = {
        event_id: (rank, candidate)
        for rank, candidate in enumerate(candidates, start=1)
        for event_id in candidate.supporting_evidence_ids
    }
    round_ends = sorted(
        record.start_s
        for record in evidence
        if record.evidence_type == "round_transition" and record.detection_confidence >= 0.7
    )
    rows: list[dict[str, Any]] = []
    for record in evidence:
        if record.evidence_type != "kill_feed_change":
            continue
        metadata = record.supporting_metadata
        role = metadata.get("selected_player_role")
        attribution = float(metadata.get("attribution_confidence", 0) or 0)
        selected = bool(local_player_name.strip())
        candidate_entry = by_evidence.get(record.event_id)
        candidate = candidate_entry[1] if candidate_entry else None
        if selected and role != "attacker":
            decision = "rejected_before_candidate_generation"
            reason = f"selected_player_role={role!r}; player mode requires attacker"
        elif selected and attribution < 0.9:
            decision = "rejected_before_candidate_generation"
            reason = f"attribution_confidence={attribution:.3f}; requires >= 0.9"
        elif candidate is not None:
            decision = "included_in_candidate"
            reason = None
        else:
            decision = "not_in_final_candidates"
            reason = (
                "anchor passed; candidate was removed by overlap deduplication or the "
                f"max_candidates={max_candidates} limit"
            )
        rows.append(
            {
                "event_id": record.event_id,
                "timestamp_s": record.start_s,
                "round_association": round_association(record.start_s, round_ends),
                "parsed_attacker": metadata.get("attacker_name"),
                "parsed_victim": metadata.get("victim_name"),
                "selected_player_role": role,
                "attribution_confidence": attribution,
                "candidate_decision": decision,
                "candidate_reason": reason,
                "candidate_rank": candidate_entry[0] if candidate_entry else None,
                "candidate_interval_s": (
                    [candidate.start_s, candidate.end_s] if candidate is not None else None
                ),
                "candidate_category": candidate.category if candidate is not None else None,
            }
        )
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def classify_label_evidence(
    timestamp_s: float,
    frame_records: list[dict[str, Any]],
    evidence: list[EvidenceRecord],
    candidate_decisions: list[dict[str, Any]],
    *,
    tolerance_s: float = 2.0,
) -> dict[str, Any]:
    """Summarize the furthest observed stage near a user-supplied kill timestamp."""
    nearby_frames = [
        item for item in frame_records if abs(item["timestamp_s"] - timestamp_s) <= tolerance_s
    ]
    nearby_rows = [
        row
        for frame in nearby_frames
        for row in frame.get("kill_feed_rows", [])
    ]
    nearby_events = [
        record
        for record in evidence
        if record.evidence_type == "kill_feed_change"
        and abs(record.start_s - timestamp_s) <= tolerance_s
    ]
    event_ids = {record.event_id for record in nearby_events}
    decisions = [item for item in candidate_decisions if item.get("event_id") in event_ids]
    if any(item.get("candidate_decision") == "included_in_candidate" for item in decisions):
        loss_stage = "candidate_generated"
    elif any(
        item.get("candidate_decision") == "rejected_before_candidate_generation"
        for item in decisions
    ):
        loss_stage = "selected_player_attribution_gate"
    elif nearby_events:
        loss_stage = "candidate_filtering_or_grouping"
    elif any(row.get("decision") == "confirmed_emitted" for row in nearby_rows):
        loss_stage = "event_persistence_or_matching"
    elif nearby_rows:
        loss_stage = "row_confirmation_or_parse"
    elif nearby_frames and not any(frame.get("kill_feed_ocr_invoked") for frame in nearby_frames):
        loss_stage = "pixel_gate_or_sampling"
    elif nearby_frames:
        loss_stage = "ocr_or_kill_feed_line_filter"
    else:
        loss_stage = "no_sample_within_tolerance"
    return {
        "known_timestamp_s": timestamp_s,
        "tolerance_s": tolerance_s,
        "loss_stage": loss_stage,
        "nearby_frames": len(nearby_frames),
        "ocr_frames": sum(bool(frame.get("kill_feed_ocr_invoked")) for frame in nearby_frames),
        "ocr_rows": nearby_rows,
        "kill_events": [record.model_dump(mode="json") for record in nearby_events],
        "candidate_decisions": decisions,
    }
