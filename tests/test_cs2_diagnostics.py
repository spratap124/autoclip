from __future__ import annotations

import json

import numpy as np
from autoclip.pipeline.cs2.diagnostics import (
    DiagnosticWriter,
    classify_label_evidence,
    write_candidate_decisions,
)
from autoclip.pipeline.cs2.models import CS2Candidate, EvidenceRecord


def _kill(event_id: str, timestamp: float, role: str, attribution: float) -> EvidenceRecord:
    return EvidenceRecord(
        event_id=event_id,
        start_s=timestamp,
        end_s=timestamp,
        evidence_type="kill_feed_change",
        detection_confidence=0.9,
        supporting_metadata={
            "attacker_name": "thakurshaaab" if role == "attacker" else "other",
            "victim_name": "enemy",
            "selected_player_role": role,
            "attribution_confidence": attribution,
        },
        explanation="Diagnostic test evidence.",
    )


def test_candidate_diagnostics_explain_anchor_rejection_and_inclusion(tmp_path) -> None:
    records = [_kill("kill-a", 10, "attacker", 1.0), _kill("kill-b", 12, "possible_attacker", 0.6)]
    candidate = CS2Candidate(
        start_s=4,
        end_s=22,
        category="player_double_kill_potential",
        detection_confidence=0.9,
        quality_score=32,
        supporting_evidence_ids=["kill-a"],
        selection_reason="test",
    )
    path = tmp_path / "candidate_decisions.jsonl"
    write_candidate_decisions(
        path, records, [candidate], local_player_name="thakurshaaab", max_candidates=20
    )
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["candidate_decision"] == "included_in_candidate"
    assert rows[0]["candidate_rank"] == 1
    assert rows[1]["candidate_decision"] == "rejected_before_candidate_generation"
    assert "attacker" in rows[1]["candidate_reason"]


def test_label_classifier_distinguishes_gate_loss_from_confirmation_loss() -> None:
    gate_result = classify_label_evidence(10, [{
        "timestamp_s": 10,
        "kill_feed_ocr_invoked": False,
        "kill_feed_ocr_skip_reason": ["dark_row_gate"],
    }], [], [])
    assert gate_result["loss_stage"] == "pixel_gate_or_sampling"

    row_result = classify_label_evidence(10, [{
        "timestamp_s": 10,
        "kill_feed_ocr_invoked": True,
        "kill_feed_rows": [{"decision": "pending_first_observation", "ocr_text": "target enemy"}],
    }], [], [])
    assert row_result["loss_stage"] == "row_confirmation_or_parse"

    rejected = classify_label_evidence(
        10,
        [{"timestamp_s": 10, "kill_feed_ocr_invoked": True, "kill_feed_rows": []}],
        [_kill("kill-victim", 10, "victim", 1.0)],
        [{"event_id": "kill-victim", "candidate_decision": "rejected_before_candidate_generation"}],
    )
    assert rejected["loss_stage"] == "selected_player_attribution_gate"


def test_diagnostic_writer_caps_crop_count_and_records_relative_path(tmp_path) -> None:
    writer = DiagnosticWriter(tmp_path, max_crops=1, frame_count=2)
    crop = np.zeros((8, 12, 3), dtype=np.uint8)
    writer.write_frame({"timestamp_s": 0.0}, crop, index=0)
    writer.write_frame({"timestamp_s": 0.5}, crop, index=1)
    writer.close()
    rows = [json.loads(line) for line in (tmp_path / "frames.jsonl").read_text().splitlines()]
    assert rows[0]["kill_feed_crop"].startswith("kill_feed_crops/")
    assert rows[1].get("kill_feed_crop") is None
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["crop_count"] == 1
    assert summary["crop_bytes"] <= summary["crop_byte_cap"]
