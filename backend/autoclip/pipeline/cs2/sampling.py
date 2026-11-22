"""One-pass FFmpeg sampling and cached, bounded-memory signal extraction."""

from __future__ import annotations

import json
import logging
import math
import shutil
import sys
import time
import wave
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from .. import ffmpeg
from .diagnostics import DiagnosticWriter, round_association
from .models import CS2Settings, EvidenceRecord
from .recognition import (
    DETECTOR_VERSION,
    KillFeedTracker,
    RoundTransitionTracker,
    crop_region,
    read_lines,
)

log = logging.getLogger(__name__)
SAMPLE_RATE = 16_000


@dataclass
class SamplingResult:
    evidence: list[EvidenceRecord]
    sampled_frames: int
    cache_bytes: int
    cache_hit: bool
    extraction_s: float
    ocr_calls: int = 0
    ocr_s: float = 0.0


def extract_evidence(
    source: Path,
    cache_dir: Path,
    *,
    duration_s: float,
    has_video: bool,
    has_audio: bool,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    settings: CS2Settings | None = None,
    cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> SamplingResult:
    """Extract low-resolution samples and features once, then reuse the cache.

    A single FFmpeg invocation writes sampled JPEGs and (when present) one
    mono 16 kHz WAV. This avoids repeated source decoding and keeps working
    memory bounded; frame files are read one at a time for feature extraction.
    """
    settings = settings or CS2Settings()
    if duration_s <= 0:
        raise ValueError("source duration must be greater than zero")
    if cancelled and cancelled():
        raise ffmpeg.Cancelled("CS2 analysis cancelled.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "sampling.json"
    frames_dir = cache_dir / "frames"
    audio_path = cache_dir / "audio.wav"
    media_metadata = {"width": width, "height": height, "fps": fps}
    sampling_signature, feature_signature = _signatures(
        source, duration_s, has_video, has_audio, settings, media_metadata
    )

    cached = _read_cache(manifest_path, sampling_signature, frames_dir, audio_path, has_audio)
    if cached is not None:
        records, frame_count = cached
        evidence = _read_feature_cache(cache_dir / "features.json", feature_signature)
        ocr_calls = 0
        ocr_s = 0.0
        if evidence is None or settings.diagnostics_enabled:
            evidence, ocr_calls, ocr_s = _build_evidence(
                records,
                frames_dir,
                audio_path,
                duration_s=duration_s,
                has_audio=has_audio,
                settings=settings,
                diagnostics_dir=cache_dir / "diagnostics" if settings.diagnostics_enabled else None,
                cancelled=cancelled,
                on_progress=_map_progress(on_progress, 0.04, 0.94),
            )
            _write_feature_cache(cache_dir / "features.json", feature_signature, evidence)
        elif on_progress:
            on_progress(0.97)
        return SamplingResult(
            evidence, frame_count, _tree_size(cache_dir), True, 0.0, ocr_calls, ocr_s
        )

    shutil.rmtree(frames_dir, ignore_errors=True)
    audio_path.unlink(missing_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(source)]
    if has_video:
        sample_width = min(settings.frame_width, width or settings.frame_width)
        args += [
            "-map",
            "0:v:0",
            "-vf",
            f"fps=1/{settings.frame_interval_s:g},scale={sample_width}:-2",
            "-q:v",
            "5",
            str(frames_dir / "frame_%06d.jpg"),
        ]
    if has_audio:
        args += [
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]

    started = time.perf_counter()
    try:
        ffmpeg.run(
            args,
            total_duration_s=duration_s,
            on_progress=_map_progress(on_progress, 0.0, 0.30),
            cancelled=cancelled,
        )
    except ffmpeg.Cancelled:
        shutil.rmtree(frames_dir, ignore_errors=True)
        audio_path.unlink(missing_ok=True)
        raise
    extraction_s = time.perf_counter() - started

    frame_paths = sorted(frames_dir.glob("frame_*.jpg"))
    records = [
        {"timestamp_s": min(index * settings.frame_interval_s, duration_s), "filename": path.name}
        for index, path in enumerate(frame_paths)
    ]
    manifest = {
        "detector_version": DETECTOR_VERSION,
        "signature": sampling_signature,
        "frames": records,
        "audio_available": bool(has_audio and audio_path.exists()),
        "frame_count": len(records),
        "media_metadata": media_metadata,
    }
    _write_json_atomic(manifest_path, manifest)
    evidence, ocr_calls, ocr_s = _build_evidence(
        records,
        frames_dir,
        audio_path,
        duration_s=duration_s,
        has_audio=has_audio,
        settings=settings,
        diagnostics_dir=cache_dir / "diagnostics" if settings.diagnostics_enabled else None,
        cancelled=cancelled,
        on_progress=_map_progress(on_progress, 0.30, 0.94),
    )
    _write_feature_cache(cache_dir / "features.json", feature_signature, evidence)
    return SamplingResult(
        evidence, len(records), _tree_size(cache_dir), False, extraction_s, ocr_calls, ocr_s
    )


def _signatures(
    source: Path,
    duration_s: float,
    has_video: bool,
    has_audio: bool,
    settings: CS2Settings,
    media_metadata: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    stat = source.stat()
    base = {
        "source_path": str(source.resolve()),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "duration_s": round(duration_s, 3),
        "has_video": has_video,
        "has_audio": has_audio,
        "media_metadata": media_metadata,
    }
    sampling = base | {
        "frame_interval_s": settings.frame_interval_s,
        "frame_width": settings.frame_width,
    }
    features = base | {"settings": settings.model_dump(mode="json")}
    return sampling, features


def _map_progress(
    callback: Callable[[float], None] | None, start: float, end: float
) -> Callable[[float], None] | None:
    if callback is None:
        return None
    return lambda fraction: callback(start + (end - start) * max(0.0, min(1.0, fraction)))


def _read_cache(
    path: Path,
    signature: dict[str, Any],
    frames_dir: Path,
    audio_path: Path,
    has_audio: bool,
) -> tuple[list[dict[str, Any]], int] | None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        frames = manifest["frames"]
        if manifest.get("signature") != signature:
            return None
        if any(not (frames_dir / record["filename"]).is_file() for record in frames):
            return None
        if has_audio and not audio_path.is_file():
            return None
        return frames, len(frames)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _build_evidence(
    frames: list[dict[str, Any]],
    frames_dir: Path,
    audio_path: Path,
    *,
    duration_s: float,
    has_audio: bool,
    settings: CS2Settings,
    diagnostics_dir: Path | None,
    cancelled: Callable[[], bool] | None,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[list[EvidenceRecord], int, float]:
    evidence: list[EvidenceRecord] = []
    ocr_calls = 0
    ocr_seconds = 0.0
    diagnostic = (
        DiagnosticWriter(
            diagnostics_dir,
            max_crops=settings.diagnostics_max_crops,
            frame_count=len(frames),
        )
        if settings.diagnostics_enabled and diagnostics_dir is not None
        else None
    )
    if cancelled and cancelled():
        raise ffmpeg.Cancelled("CS2 analysis cancelled.")
    if len(frames) > 1:
        kill_tracker = KillFeedTracker(local_player_name=settings.local_player_name)
        round_tracker = RoundTransitionTracker()
        previous = None
        previous_kill_roi = None
        previous_time = 0.0
        next_round_ocr_s = 0.0
        progress_stride = max(1, len(frames) // 50)
        for index, item in enumerate(frames):
            if cancelled and cancelled():
                raise ffmpeg.Cancelled("CS2 analysis cancelled.")
            path = frames_dir / item["filename"]
            color = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if color is None:
                log.warning("Could not decode cached sample %s", path)
                if diagnostic:
                    diagnostic.write_frame(
                        {"timestamp_s": item["timestamp_s"], "frame_decoded": False},
                        None,
                        index=index,
                    )
                continue
            image = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
            current_time = min(float(item["timestamp_s"]), duration_s)
            if previous is not None:
                difference = _roi_difference(previous, image, settings)
                ratio = difference / 255.0
                if ratio >= settings.visual_change_threshold:
                    evidence.append(
                        EvidenceRecord(
                            event_id=f"vis_{index:06d}",
                            start_s=previous_time,
                            end_s=current_time,
                            evidence_type="visual_activity",
                            detection_confidence=round(min(0.95, ratio / 0.6), 3),
                            supporting_metadata={
                                "mean_abs_difference": round(difference, 2),
                                "normalized_change": round(ratio, 4),
                                "sample_interval_s": round(current_time - previous_time, 3),
                            },
                            explanation=(
                                "Sampled gameplay frames changed noticeably; this is visual "
                                "activity evidence, not a confirmed game event."
                            ),
                            detector_version=DETECTOR_VERSION,
                        )
                    )

            frame_diagnostic: dict[str, Any] = {
                "timestamp_s": current_time,
                "frame_decoded": True,
                "kill_feed_roi": settings.kill_feed_roi.model_dump(mode="json"),
                "kill_feed_region_processed": settings.ocr_enabled,
                "kill_feed_rows_gate": None,
                "kill_feed_roi_change": None,
                "kill_feed_change_threshold": settings.kill_feed_change_threshold,
                "kill_feed_change_gate": None,
                "pending_confirmation_before_frame": bool(kill_tracker.pending),
                "kill_feed_ocr_invoked": False,
                "kill_feed_ocr_skip_reason": None,
                "kill_feed_rows": [],
                "round_ocr_eligible_time": current_time >= next_round_ocr_s,
                "round_banner_color_gate": None,
                "round_ocr_invoked": False,
                "round_ocr_text": [],
                "round_event_emitted": False,
            }
            kill_crop = None
            if settings.ocr_enabled:
                kill_crop = crop_region(color, settings.kill_feed_roi)
                kill_gray = cv2.cvtColor(kill_crop, cv2.COLOR_BGR2GRAY)
                roi_change = (
                    255.0
                    if previous_kill_roi is None
                    else float(cv2.absdiff(previous_kill_roi, kill_gray).mean())
                )
                pending_confirmation = bool(kill_tracker.pending)
                rows_gate = _has_kill_feed_rows(kill_crop)
                change_gate = roi_change >= settings.kill_feed_change_threshold
                frame_diagnostic.update(
                    {
                        "kill_feed_rows_gate": rows_gate,
                        "kill_feed_roi_change": round(roi_change, 3),
                        "kill_feed_change_gate": change_gate,
                        "pending_confirmation_before_frame": pending_confirmation,
                    }
                )
                if rows_gate and (change_gate or pending_confirmation):
                    frame_diagnostic["kill_feed_ocr_invoked"] = True
                    ocr_started = time.perf_counter()
                    # Kill-feed glyphs are small and sit beside high-contrast
                    # weapon icons. A larger Otsu-preprocessed block pass has
                    # been measurably clearer than sparse-text PSM 11 here.
                    lines = read_lines(kill_crop, psm=6, scale=4.5)
                    ocr_seconds += time.perf_counter() - ocr_started
                    ocr_calls += 1
                    evidence.extend(kill_tracker.observe(current_time, lines))
                    frame_diagnostic["kill_feed_rows"] = kill_tracker.last_diagnostics
                else:
                    failed = []
                    if not rows_gate:
                        failed.append("dark_row_gate")
                    if not change_gate and not pending_confirmation:
                        failed.append("roi_change_gate")
                    frame_diagnostic["kill_feed_ocr_skip_reason"] = failed

                round_eligible = current_time >= next_round_ocr_s
                round_color_gate = (
                    _has_round_banner_color(color, settings.round_banner_roi)
                    if round_eligible
                    else None
                )
                frame_diagnostic.update(
                    {
                        "round_ocr_eligible_time": round_eligible,
                        "round_banner_color_gate": round_color_gate,
                    }
                )
                if round_eligible and round_color_gate:
                    round_crop = crop_region(color, settings.round_banner_roi)
                    ocr_started = time.perf_counter()
                    round_lines = read_lines(round_crop, psm=6)
                    ocr_seconds += time.perf_counter() - ocr_started
                    ocr_calls += 1
                    frame_diagnostic["round_ocr_invoked"] = True
                    frame_diagnostic["round_ocr_text"] = [
                        {"text": line.text, "confidence": line.confidence} for line in round_lines
                    ]
                    round_event = round_tracker.observe(current_time, round_lines)
                    if round_event is not None:
                        evidence.append(round_event)
                        frame_diagnostic["round_event_emitted"] = True
                        frame_diagnostic["round_event_id"] = round_event.event_id
                    next_round_ocr_s = current_time + settings.round_ocr_interval_s
            else:
                frame_diagnostic["kill_feed_ocr_skip_reason"] = ["ocr_disabled"]

            previous_kill_roi = cv2.cvtColor(
                crop_region(color, settings.kill_feed_roi), cv2.COLOR_BGR2GRAY
            )
            previous = image
            previous_time = current_time
            if diagnostic:
                round_ends = sorted(
                    record.start_s
                    for record in evidence
                    if record.evidence_type == "round_transition"
                    and record.detection_confidence >= 0.7
                )
                frame_diagnostic["round_association"] = round_association(
                    current_time, round_ends
                )
                for row in frame_diagnostic["kill_feed_rows"]:
                    row["round_association"] = frame_diagnostic["round_association"]
                diagnostic.write_frame(frame_diagnostic, kill_crop, index=index)
            if on_progress and ((index + 1) % progress_stride == 0 or index + 1 == len(frames)):
                on_progress((index + 1) / len(frames))

    if has_audio and audio_path.is_file():
        evidence.extend(_audio_evidence(audio_path, duration_s, settings))
    if on_progress:
        on_progress(1.0)
    if diagnostic:
        diagnostic.close()
    return evidence, ocr_calls, ocr_seconds


def _has_kill_feed_rows(crop) -> bool:
    """Cheap dark-band gate avoids invoking OCR on ordinary scenery changes."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    dark_fraction = (gray < 72).mean(axis=1)
    return bool((dark_fraction > 0.55).sum() >= 5)


def _has_round_banner_color(image, roi) -> bool:
    """Gate center OCR on a wide red banner edge seen in CS2 round-result HUDs."""
    crop = crop_region(image, roi)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    red = (
        ((hsv[:, :, 0] <= 20) | (hsv[:, :, 0] >= 165)) & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 50)
    )
    row_fraction = red.mean(axis=1)
    red_rows = int((row_fraction >= 0.25).sum())
    height = red.shape[0]
    banner_rows = 2 <= red_rows <= round(height * 0.65)
    broad_tint = red.mean() >= 0.65
    return bool(red.mean() >= 0.002 and (banner_rows or broad_tint))


def _roi_difference(first, second, settings: CS2Settings) -> float:
    import cv2

    height, width = first.shape[:2]
    differences: list[float] = []
    for roi in settings.regions:
        x1 = min(width, round(roi.x * width))
        y1 = min(height, round(roi.y * height))
        x2 = min(width, max(x1 + 1, round((roi.x + roi.width) * width)))
        y2 = min(height, max(y1 + 1, round((roi.y + roi.height) * height)))
        delta = cv2.absdiff(first[y1:y2, x1:x2], second[y1:y2, x1:x2])
        differences.append(float(delta.mean()))
    return max(differences, default=0.0)


def _audio_evidence(
    audio_path: Path, duration_s: float, settings: CS2Settings
) -> list[EvidenceRecord]:
    evidence: list[EvidenceRecord] = []
    try:
        with wave.open(str(audio_path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                return evidence
            samples_per_window = max(1, round(settings.audio_window_s * audio.getframerate()))
            window_index = 0
            while raw := audio.readframes(samples_per_window):
                values = array("h")
                values.frombytes(raw)
                if sys.byteorder != "little":
                    values.byteswap()
                if not values:
                    break
                square_mean = sum(value * value for value in values) / len(values)
                rms = math.sqrt(square_mean) / 32768.0
                level_dbfs = 20 * math.log10(max(rms, 1e-8))
                start = window_index * settings.audio_window_s
                end = min(duration_s, start + len(values) / audio.getframerate())
                if level_dbfs >= settings.audio_activity_threshold_dbfs and end > start:
                    intensity = max(
                        0.0, min(1.0, (level_dbfs - settings.audio_activity_threshold_dbfs) / 30)
                    )
                    evidence.append(
                        EvidenceRecord(
                            event_id=f"aud_{window_index:06d}",
                            start_s=start,
                            end_s=end,
                            evidence_type="audio_activity",
                            detection_confidence=round(min(0.95, 0.25 + intensity * 0.65), 3),
                            supporting_metadata={
                                "rms_dbfs": round(level_dbfs, 2),
                                "intensity": round(intensity, 3),
                                "window_s": round(end - start, 3),
                            },
                            explanation=(
                                "Audio level exceeded the configurable activity threshold; "
                                "loudness alone does not confirm a highlight."
                            ),
                            detector_version=DETECTOR_VERSION,
                        )
                    )
                window_index += 1
    except (OSError, wave.Error) as exc:
        log.warning("Could not read cached audio activity track: %s", exc)
    return evidence


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_feature_cache(path: Path, signature: dict[str, Any]) -> list[EvidenceRecord] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("signature") != signature
            or payload.get("detector_version") != DETECTOR_VERSION
        ):
            return None
        return [EvidenceRecord.model_validate(item) for item in payload["evidence"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_feature_cache(
    path: Path, signature: dict[str, Any], evidence: list[EvidenceRecord]
) -> None:
    _write_json_atomic(
        path,
        {
            "detector_version": DETECTOR_VERSION,
            "signature": signature,
            "evidence": [record.model_dump(mode="json") for record in evidence],
        },
    )


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
