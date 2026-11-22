"""Run opt-in local CS2 diagnostics against user-supplied labels.

Usage: python -m autoclip.pipeline.cs2.evaluate RECORDING --labels labels.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ...db.models import Source
from .. import ffmpeg
from .candidates import generate_candidates
from .diagnostics import classify_label_evidence, write_candidate_decisions
from .models import CS2Settings
from .sampling import extract_evidence


def evaluate_recording(recording: Path, labels_path: Path, output_dir: Path) -> Path:
    """Analyze one explicitly selected local recording and compare supplied labels."""
    recording = recording.expanduser().resolve()
    labels_path = labels_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(labels, dict) or not isinstance(labels.get("known_kills"), list):
        raise ValueError("labels.json must contain a known_kills array")
    player_name = str(labels.get("local_player_name", "")).strip()
    if not player_name:
        raise ValueError("labels.json must contain local_player_name")
    for event in labels["known_kills"]:
        if not isinstance(event, dict) or not isinstance(event.get("timestamp_s"), (int, float)):
            raise ValueError("each known_kills item requires numeric timestamp_s")

    info = ffmpeg.probe(recording)
    if not info.has_video:
        raise ValueError("recording must contain video")
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = CS2Settings(
        local_player_name=player_name,
        diagnostics_enabled=True,
        diagnostics_max_crops=int(labels.get("max_crops", 100)),
    )
    source = Source(
        id="local-diagnostic",
        type="upload",
        path=str(recording),
        filename=recording.name,
        title=recording.stem,
        duration_s=info.duration_s,
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        has_video=info.has_video,
    )
    sampling = extract_evidence(
        recording,
        output_dir / "samples",
        duration_s=source.duration_s,
        has_video=source.has_video,
        has_audio=source.has_audio,
        width=source.width,
        height=source.height,
        fps=source.fps,
        settings=settings,
    )
    candidates = generate_candidates(
        sampling.evidence,
        duration_s=source.duration_s,
        settings=settings,
    )
    diagnostic_dir = output_dir / "samples" / "diagnostics"
    candidate_path = diagnostic_dir / "candidate_decisions.jsonl"
    write_candidate_decisions(
        candidate_path,
        sampling.evidence,
        candidates,
        local_player_name=player_name,
        max_candidates=settings.max_candidates,
    )
    frame_records = _read_jsonl(diagnostic_dir / "frames.jsonl")
    decision_records = _read_jsonl(candidate_path)
    tolerance_s = float(labels.get("tolerance_s", 2.0))
    known_kills = []
    for label in labels["known_kills"]:
        diagnosis = classify_label_evidence(
            float(label["timestamp_s"]),
            frame_records,
            sampling.evidence,
            decision_records,
            tolerance_s=tolerance_s,
        )
        observed_roles = sorted(
            {
                str(event.supporting_metadata.get("selected_player_role"))
                for event in sampling.evidence
                if event.evidence_type == "kill_feed_change"
                and abs(event.start_s - float(label["timestamp_s"])) <= tolerance_s
            }
        )
        expected_role = label.get("expected_role")
        known_kills.append(
            {
                **{key: value for key, value in label.items() if key != "note"},
                "note": label.get("note"),
                "observed_roles_within_tolerance": observed_roles,
                "expected_role_observed": (
                    expected_role in observed_roles if expected_role is not None else None
                ),
                "diagnosis": diagnosis,
            }
        )
    sequences = _evaluate_sequences(
        labels.get("expected_multi_kill_sequences", []), known_kills
    )
    report = {
        "format_version": 1,
        "recording_name": recording.name,
        "local_player_name": player_name,
        "tolerance_s": tolerance_s,
        "sampled_frames": sampling.sampled_frames,
        "ocr_calls": sampling.ocr_calls,
        "evidence_count": len(sampling.evidence),
        "candidate_count": len(candidates),
        "diagnostics_dir": "samples/diagnostics",
        "known_kills": known_kills,
        "expected_multi_kill_sequences": sequences,
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        "accuracy_claim": None,
        "note": "Labels are user supplied; this report does not assert accuracy.",
    }
    report_path = output_dir / "evaluation.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


def _evaluate_sequences(sequences: Any, known_kills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(sequences, list):
        raise ValueError("expected_multi_kill_sequences must be an array")
    results = []
    for sequence in sequences:
        timestamps = sequence.get("kill_timestamps_s") if isinstance(sequence, dict) else None
        if not isinstance(timestamps, list) or not timestamps:
            raise ValueError("each sequence requires a non-empty kill_timestamps_s array")
        matched = []
        for timestamp in timestamps:
            event = min(
                known_kills,
                key=lambda item: abs(float(item["timestamp_s"]) - float(timestamp)),
                default=None,
            )
            matched.append(
                event
                if event is not None
                and abs(float(event["timestamp_s"]) - float(timestamp)) <= 0.001
                else None
            )
        candidate_id_sets = []
        for event in matched:
            ids = {
                item.get("candidate_rank")
                for item in (event or {}).get("diagnosis", {}).get("candidate_decisions", [])
                if item.get("candidate_decision") == "included_in_candidate"
            }
            candidate_id_sets.append(ids)
        shared = (
            set.intersection(*candidate_id_sets)
            if candidate_id_sets and all(candidate_id_sets)
            else set()
        )
        results.append(
            {
                "label": sequence.get("label"),
                "kill_timestamps_s": timestamps,
                "expected_count": sequence.get("expected_count", len(timestamps)),
                "all_labels_supplied": all(event is not None for event in matched),
                "same_candidate_ranks_for_all_detected_events": sorted(shared),
                "diagnoses": [event.get("diagnosis") if event else None for event in matched],
                "note": "Sequence labels are user supplied; no kill count is inferred.",
            }
        )
    return results


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path, help="local video file; no download is performed")
    parser.add_argument("--labels", required=True, type=Path, help="user-authored labels JSON")
    parser.add_argument("--output", type=Path, default=Path("cs2-diagnostic-output"))
    args = parser.parse_args()
    report = evaluate_recording(args.recording, args.labels, args.output)
    print(report)


if __name__ == "__main__":
    main()
