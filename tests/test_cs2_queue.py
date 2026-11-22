"""CS2 jobs run through AutoClip's real background queue."""

from __future__ import annotations

import time
from threading import Event

import pytest
from autoclip import app as app_module
from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import Clip, Source, new_id
from autoclip.pipeline import ffmpeg
from autoclip.pipeline import runner as runner_module
from autoclip.pipeline.cs2 import CS2Candidate
from autoclip.pipeline.cs2.detector import CS2AnalysisResult
from fastapi.testclient import TestClient


def _source() -> Source:
    return store.create_source(
        Source(
            id=new_id(),
            type="upload",
            path="/tmp/cs2-queue-fixture.mp4",
            title="CS2 queue fixture",
            duration_s=45,
            width=1920,
            height=1080,
            fps=60,
        )
    )


def _result(job_id: str) -> CS2AnalysisResult:
    candidate = CS2Candidate(
        start_s=5,
        end_s=25,
        category="kill_feed_moment_potential",
        detection_confidence=0.8,
        quality_score=52,
        supporting_evidence_ids=["kill_test"],
        selection_reason="Potential gameplay moment supported by a new kill-feed row.",
    )
    clip = Clip(
        id=new_id(),
        job_id=job_id,
        start_s=candidate.start_s,
        end_s=candidate.end_s,
        title="Kill-feed moment potential",
        score=candidate.quality_score,
        category=candidate.category,
        confidence=candidate.detection_confidence,
        reason=candidate.selection_reason,
        evidence_ids=candidate.supporting_evidence_ids,
    )
    return CS2AnalysisResult(
        clips=[clip],
        evidence=[],
        candidates=[candidate],
        sampled_frames=10,
        cache_bytes=128,
        cache_hit=False,
        extraction_s=0.1,
        ocr_calls=2,
        ocr_s=0.05,
        candidate_generation_s=0.001,
        total_s=0.2,
    )


def _wait_for_terminal(client: TestClient, job_id: str, timeout_s: float = 5) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    pytest.fail(f"Job {job_id} did not reach a terminal state.")


def test_cs2_queue_reports_progress_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(app_module.ENV_NO_WORKER, raising=False)
    entered = Event()
    release = Event()

    def fake_analyze(source, *, job_id, on_progress, **_kwargs):
        on_progress(0.25)
        entered.set()
        assert release.wait(3)
        on_progress(0.8)
        return _result(job_id)

    monkeypatch.setattr(runner_module, "analyze_cs2", fake_analyze)
    with TestClient(create_app()) as client:
        source = _source()
        response = client.post(
            "/api/jobs", json={"source_id": source.id, "settings": {"mode": "cs2"}}
        )
        assert response.status_code == 201
        job_id = response.json()["id"]
        assert response.json()["provider"] == ""
        try:
            assert entered.wait(3)
            running = client.get(f"/api/jobs/{job_id}").json()
            assert running["status"] == "running"
            assert running["current_stage"] == "cs2_detect"
            assert 0 < running["progress"] < 1
        finally:
            release.set()

        done = _wait_for_terminal(client, job_id)
        assert done["status"] == "done"
        assert done["progress"] == 1
        assert len(client.get(f"/api/jobs/{job_id}/clips").json()) == 1


def test_cs2_queue_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(app_module.ENV_NO_WORKER, raising=False)

    def fail_analysis(*_args, **_kwargs):
        raise RuntimeError("synthetic detector failure")

    monkeypatch.setattr(runner_module, "analyze_cs2", fail_analysis)
    with TestClient(create_app()) as client:
        source = _source()
        job_id = client.post(
            "/api/jobs", json={"source_id": source.id, "settings": {"mode": "cs2"}}
        ).json()["id"]
        failed = _wait_for_terminal(client, job_id)

    assert failed["status"] == "failed"
    assert "synthetic detector failure" in failed["error"]


def test_cs2_queue_cancels_running_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(app_module.ENV_NO_WORKER, raising=False)
    entered = Event()

    def cancellable_analysis(_source, *, cancelled, **_kwargs):
        entered.set()
        deadline = time.monotonic() + 3
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        if cancelled():
            raise ffmpeg.Cancelled("CS2 analysis cancelled.")
        raise RuntimeError("cancellation callback was not set")

    monkeypatch.setattr(runner_module, "analyze_cs2", cancellable_analysis)
    with TestClient(create_app()) as client:
        source = _source()
        job_id = client.post(
            "/api/jobs", json={"source_id": source.id, "settings": {"mode": "cs2"}}
        ).json()["id"]
        assert entered.wait(3)
        response = client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 200
        cancelled = _wait_for_terminal(client, job_id)

    assert cancelled["status"] == "cancelled"
