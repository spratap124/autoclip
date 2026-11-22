"""Orchestrate local sampling, normalized evidence, and candidate generation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ...db.models import Clip, Source, new_id
from .. import ffmpeg
from .candidates import generate_candidates
from .diagnostics import write_candidate_decisions
from .models import CS2Candidate, CS2Settings, EvidenceRecord
from .sampling import SamplingResult, extract_evidence


@dataclass
class CS2AnalysisResult:
    clips: list[Clip]
    evidence: list[EvidenceRecord]
    candidates: list[CS2Candidate]
    sampled_frames: int
    cache_bytes: int
    cache_hit: bool
    extraction_s: float
    ocr_calls: int
    ocr_s: float
    candidate_generation_s: float
    total_s: float


def analyze(
    source: Source,
    *,
    job_id: str,
    cache_dir: Path,
    settings: CS2Settings | None = None,
    cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> CS2AnalysisResult:
    """Analyze without transcription, LLM provider construction, or uploads."""
    settings = settings or CS2Settings()
    if not source.has_video:
        raise ValueError("CS2 mode requires a video source")
    started = time.perf_counter()
    try:
        sampling: SamplingResult = extract_evidence(
            Path(source.path),
            cache_dir / "samples",
            duration_s=source.duration_s,
            has_video=source.has_video,
            has_audio=source.has_audio,
            width=source.width,
            height=source.height,
            fps=source.fps,
            settings=settings,
            cancelled=cancelled,
            on_progress=on_progress,
        )
    except ffmpeg.Cancelled:
        raise

    if cancelled and cancelled():
        raise ffmpeg.Cancelled("CS2 analysis cancelled.")

    _save_records(cache_dir / "evidence.json", sampling.evidence)
    candidate_started = time.perf_counter()
    candidates = generate_candidates(
        sampling.evidence,
        duration_s=source.duration_s,
        settings=settings,
    )
    if settings.diagnostics_enabled:
        write_candidate_decisions(
            cache_dir / "diagnostics" / "candidate_decisions.jsonl",
            sampling.evidence,
            candidates,
            local_player_name=settings.local_player_name,
            max_candidates=settings.max_candidates,
        )
    candidate_generation_s = time.perf_counter() - candidate_started
    _save_candidates(cache_dir / "candidates.json", candidates)
    clips = [candidate_to_clip(item, job_id) for item in candidates]
    if on_progress:
        on_progress(0.99)
    return CS2AnalysisResult(
        clips=clips,
        evidence=sampling.evidence,
        candidates=candidates,
        sampled_frames=sampling.sampled_frames,
        cache_bytes=sampling.cache_bytes,
        cache_hit=sampling.cache_hit,
        extraction_s=sampling.extraction_s,
        ocr_calls=sampling.ocr_calls,
        ocr_s=sampling.ocr_s,
        candidate_generation_s=candidate_generation_s,
        total_s=time.perf_counter() - started,
    )


def candidate_to_clip(candidate: CS2Candidate, job_id: str) -> Clip:
    """Adapt timestamps into AutoClip's review row without fake word indices."""
    return Clip(
        id=new_id(),
        job_id=job_id,
        start_s=candidate.start_s,
        end_s=candidate.end_s,
        start_word=0,
        end_word=0,
        title=candidate.category.replace("_", " ").title(),
        hook="",
        score=candidate.quality_score,
        reason=candidate.selection_reason,
        category=candidate.category,
        confidence=candidate.detection_confidence,
        evidence_ids=candidate.supporting_evidence_ids,
    )


def _save_records(path: Path, records: list[EvidenceRecord]) -> None:
    _write_json_atomic(path, [record.model_dump(mode="json") for record in records])


def _save_candidates(path: Path, candidates: list[CS2Candidate]) -> None:
    _write_json_atomic(path, [candidate.model_dump(mode="json") for candidate in candidates])


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
