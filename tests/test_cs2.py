"""CS2 evidence foundation tests on generated, non-gameplay media."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from autoclip.db import store
from autoclip.db.models import Job, Source, new_id
from autoclip.pipeline import ffmpeg
from autoclip.pipeline.cs2.candidates import (
    deduplicate_candidates,
    generate_candidates,
    group_evidence,
    normalize_interval,
)
from autoclip.pipeline.cs2.models import CS2Candidate, CS2Settings, EvidenceRecord, RegionOfInterest
from autoclip.pipeline.cs2.sampling import extract_evidence
from autoclip.pipeline.runner import PipelineRunner


def _make_video(path: Path, *, duration_s: int, audio: str | None, black: bool = False) -> Path:
    video_filter = (
        f"color=c=black:s=640x360:r=24:d={duration_s}"
        if black
        else f"testsrc2=size=640x360:rate=24:duration={duration_s}"
    )
    command = [
        ffmpeg.ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        video_filter,
    ]
    if audio == "silence":
        command += ["-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono:d={duration_s}"]
    elif audio == "tone":
        command += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=16000:duration={duration_s}",
        ]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio is not None:
        command += ["-c:a", "aac", "-shortest"]
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True, text=True)
    return path


def _evidence(
    event_id: str,
    start: float,
    end: float,
    kind: str,
    confidence: float,
    *,
    intensity: float = 0.0,
) -> EvidenceRecord:
    return EvidenceRecord(
        event_id=event_id,
        start_s=start,
        end_s=end,
        evidence_type=kind,
        detection_confidence=confidence,
        supporting_metadata={"intensity": intensity},
        explanation="Test observation; not a confirmed game event.",
        detector_version="test-1",
    )


class TestEvidenceContracts:
    def test_evidence_round_trips_with_future_evidence_types(self) -> None:
        evidence = _evidence("e1", 2.5, 3.0, "future_detector_signal", 0.7)

        loaded = EvidenceRecord.model_validate_json(evidence.model_dump_json())

        assert loaded == evidence
        assert loaded.evidence_type == "future_detector_signal"

    def test_timestamp_intervals_are_clamped_to_source(self) -> None:
        assert normalize_interval(-2, 3, 10) == (0.0, 3)
        assert normalize_interval(8, 14, 10) == (8, 10)
        assert normalize_interval(10, 12, 10) == (10, 10)
        assert normalize_interval(5, 4, 10) is None

    def test_regions_and_sampling_settings_are_validated(self) -> None:
        assert RegionOfInterest(name="kill_feed", x=0.65, y=0, width=0.35, height=0.25)
        with pytest.raises(ValueError, match="fit within"):
            RegionOfInterest(x=0.8, width=0.4)
        with pytest.raises(ValueError, match="min_candidate_s must be no greater"):
            CS2Settings(min_candidate_s=59, max_candidate_s=20)


class TestCandidateGeneration:
    @staticmethod
    def _selected_kill(timestamp: float, victim: str, event_id: str) -> EvidenceRecord:
        return EvidenceRecord(
            event_id=event_id,
            start_s=timestamp,
            end_s=timestamp,
            evidence_type="kill_feed_change",
            detection_confidence=0.9,
            supporting_metadata={
                "attacker_name": "thakurshaaab",
                "victim_name": victim,
                "selected_player_role": "attacker",
                "attribution_confidence": 1.0,
                "attribution_confirmed": True,
            },
            explanation="Attacker-side attribution in a deterministic test fixture.",
            detector_version="test-1",
        )

    @staticmethod
    def _round_end(timestamp: float) -> EvidenceRecord:
        return EvidenceRecord(
            event_id=f"round-{timestamp}",
            start_s=timestamp,
            end_s=timestamp,
            evidence_type="round_transition",
            detection_confidence=0.9,
            supporting_metadata={"banner_match": "round_winner"},
            explanation="Detected round result.",
            detector_version="test-1",
        )

    def test_groups_related_evidence_and_separates_confidence_from_quality(self) -> None:
        records = [
            _evidence("k1", 10, 10, "kill_feed_change", 0.9, intensity=0.8),
            _evidence("k2", 16, 16, "kill_feed_change", 0.8, intensity=0.4),
            _evidence("a", 10, 11, "audio_activity", 0.9, intensity=0.8),
            _evidence("v", 16, 17, "visual_activity", 0.7, intensity=0.4),
            _evidence("later", 45, 47, "round_transition", 0.8, intensity=0.2),
        ]
        settings = CS2Settings(context_before_s=3, context_after_s=4, merge_gap_s=6)

        candidates = generate_candidates(records, duration_s=60, settings=settings)

        assert len(candidates) == 2
        assert candidates[0].category == "multi_kill_sequence_potential"
        assert candidates[0].supporting_evidence_ids == ["k1", "k2", "a", "v"]
        assert candidates[0].start_s == 6
        assert candidates[0].end_s == 21
        assert 0 <= candidates[0].detection_confidence <= 1
        assert candidates[0].quality_score != round(candidates[0].detection_confidence * 100)
        assert "does not confirm" in candidates[0].selection_reason

    def test_source_duration_bounds_and_empty_evidence(self) -> None:
        settings = CS2Settings(context_before_s=10, context_after_s=10, min_candidate_s=15)
        candidates = generate_candidates(
            [_evidence("edge", 9, 10, "round_transition", 0.8)],
            duration_s=10,
            settings=settings,
        )
        assert len(candidates) == 1
        assert candidates[0].start_s == 0
        assert candidates[0].end_s == 10
        assert generate_candidates([], duration_s=10, settings=settings) == []

    def test_heavy_overlap_keeps_highest_quality_candidate(self) -> None:
        low = CS2Candidate(
            start_s=0,
            end_s=20,
            category="a",
            detection_confidence=0.9,
            quality_score=30,
            supporting_evidence_ids=["a"],
            selection_reason="low",
        )
        high = CS2Candidate(
            start_s=2,
            end_s=19,
            category="b",
            detection_confidence=0.5,
            quality_score=60,
            supporting_evidence_ids=["b"],
            selection_reason="high",
        )
        distant = CS2Candidate(
            start_s=40,
            end_s=55,
            category="c",
            detection_confidence=0.5,
            quality_score=50,
            supporting_evidence_ids=["c"],
            selection_reason="distant",
        )

        result = deduplicate_candidates([low, high, distant])

        assert [item.selection_reason for item in result] == ["high", "distant"]

    def test_group_evidence_uses_normalized_intervals(self) -> None:
        groups = group_evidence(
            [_evidence("a", 8, 12, "a", 0.5), _evidence("b", 13, 20, "b", 0.5)],
            merge_gap_s=1,
            duration_s=10,
        )
        assert len(groups) == 1
        assert (groups[0].start_s, groups[0].end_s) == (8, 10)

    def test_player_focused_candidates_require_confirmed_attacker_attribution(self) -> None:
        selected = EvidenceRecord(
            event_id="selected-kill",
            start_s=20,
            end_s=20,
            evidence_type="kill_feed_change",
            detection_confidence=0.9,
            supporting_metadata={
                "attacker_name": "thakurshaaab",
                "victim_name": "enemy",
                "selected_player_role": "attacker",
                "attribution_confidence": 1.0,
                "attribution_confirmed": True,
            },
            explanation="OCR positions support selected-player attacker attribution.",
            detector_version="test-1",
        )
        unrelated = selected.model_copy(
            update={
                "event_id": "other-kill",
                "start_s": 70,
                "end_s": 70,
                "supporting_metadata": {
                    "attacker_name": "teammate",
                    "victim_name": "opponent",
                    "selected_player_role": "unrelated",
                    "attribution_confidence": 1.0,
                    "attribution_confirmed": True,
                },
            }
        )
        settings = CS2Settings(
            local_player_name="thakurshaaab", context_before_s=2, context_after_s=3
        )

        candidates = generate_candidates([selected, unrelated], duration_s=100, settings=settings)

        assert len(candidates) == 1
        assert candidates[0].start_s == 13
        assert candidates[0].end_s == 28
        assert candidates[0].category == "player_kill_potential"
        assert "thakurshaaab" in candidates[0].selection_reason

    def test_player_focused_mode_returns_empty_without_confirmed_player_kills(self) -> None:
        unrelated = EvidenceRecord(
            event_id="other-kill",
            start_s=20,
            end_s=20,
            evidence_type="kill_feed_change",
            detection_confidence=0.98,
            supporting_metadata={
                "selected_player_role": "unrelated",
                "attribution_confidence": 1.0,
            },
            explanation="Another player got the kill.",
            detector_version="test-1",
        )
        uncertain = unrelated.model_copy(
            update={
                "event_id": "uncertain-kill",
                "start_s": 40,
                "end_s": 40,
                "supporting_metadata": {
                    "selected_player_role": "possible_attacker",
                    "attribution_confidence": 0.6,
                },
            }
        )

        assert (
            generate_candidates(
                [unrelated, uncertain],
                duration_s=100,
                settings=CS2Settings(local_player_name="thakurshaaab"),
            )
            == []
        )

    def test_activity_support_does_not_inflate_player_attribution_confidence(self) -> None:
        kill = self._selected_kill(20, "enemy-a", "selected-kill").model_copy(
            update={"detection_confidence": 0.55}
        )
        support = [
            _evidence("audio", 20, 21, "audio_activity", 0.99, intensity=1.0),
            _evidence("visual", 20, 21, "visual_activity", 0.99, intensity=1.0),
        ]

        settings = CS2Settings(local_player_name="thakurshaaab")
        baseline = generate_candidates([kill], duration_s=60, settings=settings)[0]
        candidate = generate_candidates(
            [kill, *support],
            duration_s=60,
            settings=settings,
        )[0]

        assert candidate.detection_confidence == 0.55
        assert candidate.quality_score > baseline.quality_score

    def test_round_boundary_separates_selected_player_kill_sequences(self) -> None:
        evidence = [
            self._selected_kill(35, "enemy-a", "a1"),
            self._selected_kill(38, "enemy-b", "a2"),
            self._round_end(40),
            self._selected_kill(41, "enemy-c", "b1"),
            self._selected_kill(43, "enemy-d", "b2"),
        ]
        candidates = generate_candidates(
            evidence,
            duration_s=100,
            settings=CS2Settings(
                local_player_name="thakurshaaab",
                context_before_s=2,
                context_after_s=2,
                min_candidate_s=5,
                max_candidate_s=30,
            ),
        )

        assert len(candidates) == 2
        assert all(item.category == "player_double_kill_potential" for item in candidates)
        assert candidates[0].end_s <= 40
        assert candidates[1].start_s >= 40

    def test_potential_ace_requires_distinct_victims_and_detected_round_end(self) -> None:
        kills = [
            self._selected_kill(time, f"enemy-{index}", f"kill-{index}")
            for index, time in enumerate((10, 11, 12, 13, 14))
        ]
        round_end = self._round_end(15)
        settings = CS2Settings(
            local_player_name="thakurshaaab",
            context_before_s=1,
            context_after_s=1,
            min_candidate_s=5,
            max_candidate_s=30,
        )

        candidates = generate_candidates([*kills, round_end], duration_s=60, settings=settings)

        assert len(candidates) == 1
        assert candidates[0].category == "potential_ace"
        repeated_victim = kills[0].model_copy(
            update={"event_id": "repeat", "start_s": 14, "end_s": 14}
        )
        unsupported = generate_candidates(
            [*kills[:4], repeated_victim, round_end], duration_s=60, settings=settings
        )
        assert unsupported[0].category == "player_multi_kill_sequence_potential"

    def test_triple_sequence_without_round_boundary_is_not_labeled_triple_kill(self) -> None:
        kills = [
            self._selected_kill(time, f"enemy-{index}", f"kill-{index}")
            for index, time in enumerate((10, 12, 14))
        ]
        candidates = generate_candidates(
            kills,
            duration_s=60,
            settings=CS2Settings(
                local_player_name="thakurshaaab",
                context_before_s=1,
                context_after_s=1,
                min_candidate_s=5,
                max_candidate_s=30,
            ),
        )

        assert len(candidates) == 1
        assert candidates[0].category == "player_multi_kill_sequence_potential"
        assert "round boundary was not established" in candidates[0].selection_reason


class TestSamplingAndPipeline:
    def test_missing_audio_sampling_cache_and_source_metadata(self, tmp_path: Path) -> None:
        video = _make_video(tmp_path / "silent-video.mp4", duration_s=12, audio=None)
        settings = CS2Settings(
            frame_interval_s=2,
            visual_change_threshold=0.08,
            ocr_enabled=False,
            diagnostics_enabled=True,
            diagnostics_max_crops=2,
        )
        progress: list[float] = []

        first = extract_evidence(
            video,
            tmp_path / "cache",
            duration_s=12,
            has_video=True,
            has_audio=False,
            width=640,
            height=360,
            fps=24,
            settings=settings,
            on_progress=progress.append,
        )
        second = extract_evidence(
            video,
            tmp_path / "cache",
            duration_s=12,
            has_video=True,
            has_audio=False,
            width=640,
            height=360,
            fps=24,
            settings=settings,
        )
        manifest = json.loads((tmp_path / "cache" / "sampling.json").read_text())
        diagnostic_dir = tmp_path / "cache" / "diagnostics"
        diagnostic_frames = [
            json.loads(line)
            for line in (diagnostic_dir / "frames.jsonl").read_text().splitlines()
        ]
        diagnostic_summary = json.loads((diagnostic_dir / "summary.json").read_text())

        assert first.sampled_frames >= 5
        assert progress == sorted(progress)
        assert progress[-1] < 1.0
        assert first.cache_bytes > 0
        assert second.cache_hit is True
        assert second.sampled_frames == first.sampled_frames
        assert manifest["media_metadata"] == {"width": 640, "height": 360, "fps": 24}
        assert all(item.evidence_type != "audio_activity" for item in first.evidence)
        assert len(diagnostic_frames) == first.sampled_frames
        assert all(
            frame["kill_feed_ocr_skip_reason"] == ["ocr_disabled"]
            for frame in diagnostic_frames
        )
        assert diagnostic_summary["crop_count"] <= 2

    def test_silent_audio_and_static_video_produce_no_activity_evidence(
        self, tmp_path: Path
    ) -> None:
        video = _make_video(tmp_path / "all-silent.mp4", duration_s=8, audio="silence", black=True)
        result = extract_evidence(
            video,
            tmp_path / "cache",
            duration_s=8,
            has_video=True,
            has_audio=True,
            settings=CS2Settings(frame_interval_s=1, ocr_enabled=False),
        )
        assert result.sampled_frames >= 6
        assert result.evidence == []

    def test_cancellation_is_honored_before_extraction(self, tmp_path: Path) -> None:
        with pytest.raises(ffmpeg.Cancelled):
            extract_evidence(
                tmp_path / "does-not-exist.mp4",
                tmp_path / "cache",
                duration_s=20,
                has_video=True,
                has_audio=False,
                cancelled=lambda: True,
            )

    def test_cs2_job_bypasses_transcription_and_provider(
        self, tmp_path: Path, initialised_db, monkeypatch
    ) -> None:
        video = _make_video(tmp_path / "local.mp4", duration_s=18, audio="tone")
        source = store.create_source(
            Source(
                id=new_id(),
                type="upload",
                path=str(video),
                title="synthetic fixture",
                filename=video.name,
                duration_s=18,
                width=640,
                height=360,
                fps=24,
                has_audio=True,
                has_video=True,
            )
        )
        job = store.create_job(
            Job(
                id=new_id(),
                source_id=source.id,
                provider="",
                settings={"mode": "cs2", "cs2": {"frame_interval_s": 2}},
            )
        )

        def forbidden(*_args, **_kwargs):
            raise AssertionError("CS2 mode must not invoke transcript mode")

        monkeypatch.setattr("autoclip.pipeline.runner.build_provider", forbidden)
        monkeypatch.setattr(PipelineRunner, "_stage_transcribe", forbidden)
        from autoclip.pipeline.cs2.candidates import generate_candidates
        from autoclip.pipeline.cs2.detector import CS2AnalysisResult, candidate_to_clip

        event = _evidence("kill-a", 8, 8, "kill_feed_change", 0.84)
        candidate = generate_candidates([event], duration_s=18)[0]
        fake_result = CS2AnalysisResult(
            clips=[candidate_to_clip(candidate, job.id)],
            evidence=[event],
            candidates=[candidate],
            sampled_frames=9,
            cache_bytes=1200,
            cache_hit=False,
            extraction_s=0.1,
            ocr_calls=3,
            ocr_s=0.04,
            candidate_generation_s=0.001,
            total_s=0.15,
        )
        monkeypatch.setattr("autoclip.pipeline.runner.analyze_cs2", lambda *_a, **_kw: fake_result)
        runner = PipelineRunner(job, source)

        clips = asyncio.run(runner.run())

        assert clips
        assert store.get_transcript(job.id) is None
        loaded = store.list_clips(job.id)
        assert loaded[0].category.endswith("_potential")
        assert 0 <= loaded[0].confidence <= 1
        assert loaded[0].evidence_ids
        assert (runner.workspace.root / "cs2" / "metrics.json").is_file()

    def test_legacy_job_defaults_to_transcript_mode(self, tmp_path: Path, initialised_db) -> None:
        video = _make_video(tmp_path / "legacy.mp4", duration_s=3, audio=None)
        source = store.create_source(
            Source(id=new_id(), type="upload", path=str(video), duration_s=3, has_audio=False)
        )
        job = store.create_job(Job(id=new_id(), source_id=source.id))
        assert PipelineRunner(job, source).mode == "transcript"
