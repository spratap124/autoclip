"""Single-worker queue for isolated local demo parsing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import load as load_settings
from ..db import store
from ..db.models import DemoParseJob, utcnow
from .adapter import DemoParseCancelled, DemoParseError, validate_demo_file
from .cache import parse_cached

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DemoQueueStatus:
    running_job_id: str | None
    queued: int


class DemoParseQueue:
    """Keeps demo parsing off FastAPI's event loop and supports cancellation."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._wakeup: asyncio.Event | None = None
        self._running_job_id: str | None = None
        self._cancel_requested: set[str] = set()
        self._stopping = False

    async def start(self) -> None:
        self._requeue_interrupted()
        self._stopping = False
        self._wakeup = asyncio.Event()
        self._task = asyncio.create_task(self._worker(), name="autoclip-cs2-demo-queue")
        self.notify()

    async def stop(self) -> None:
        self._stopping = True
        if self._running_job_id:
            self._cancel_requested.add(self._running_job_id)
        self.notify()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._wakeup = None

    def notify(self) -> None:
        if self._wakeup is not None:
            self._wakeup.set()

    def enqueue(self, job: DemoParseJob) -> None:
        store.create_demo_job(job)
        self.notify()

    def cancel(self, job_id: str) -> bool:
        job = store.get_demo_job(job_id)
        if job is None or job.status in ("done", "failed", "cancelled"):
            return False
        self._cancel_requested.add(job_id)
        if job.status == "queued":
            store.update_demo_job(job_id, status="cancelled", current_stage="cancelled")
            demo = store.get_demo(job.demo_id)
            if demo is not None:
                store.update_demo_parse(demo.id, status="cancelled")
        self.notify()
        return True

    def status(self) -> DemoQueueStatus:
        queued = len([job for job in store.list_demo_jobs(limit=200) if job.status == "queued"])
        return DemoQueueStatus(self._running_job_id, queued)

    def _requeue_interrupted(self) -> None:
        for job in store.list_demo_jobs(limit=200):
            if job.status == "running":
                store.update_demo_job(job.id, status="queued", current_stage="queued")
                store.update_demo_parse(job.demo_id, status="uploaded")

    async def _worker(self) -> None:
        wakeup = self._wakeup
        assert wakeup is not None
        while not self._stopping:
            job = await asyncio.to_thread(store.next_queued_demo_job)
            if job is None:
                wakeup.clear()
                await wakeup.wait()
                continue
            await self._run_one(job)

    async def _run_one(self, job: DemoParseJob) -> None:
        demo = await asyncio.to_thread(store.get_demo, job.demo_id)
        if demo is None:
            store.update_demo_job(
                job.id,
                status="failed",
                current_stage="failed",
                error="Demo record not found.",
                finished_at=utcnow(),
            )
            return
        self._running_job_id = job.id
        store.update_demo_job(
            job.id,
            status="running",
            current_stage="validate",
            progress=0,
            started_at=utcnow(),
            error=None,
        )
        store.update_demo_parse(demo.id, status="parsing")
        try:
            validate_demo_file(
                Path(demo.file_path),
                max_bytes=load_settings().cs2_demo.max_upload_bytes,
            )
            store.update_demo_job(job.id, current_stage="parse")
            settings = load_settings().cs2_demo
            parsed, _cache_hit = await asyncio.to_thread(
                parse_cached,
                Path(demo.file_path),
                timeout_s=settings.parser_timeout_s,
                memory_limit_mb=settings.parser_memory_limit_mb,
                cancelled=lambda: job.id in self._cancel_requested,
            )
            if job.id in self._cancel_requested:
                raise DemoParseCancelled("Demo parsing was cancelled.")
            store.update_demo_parse(
                demo.id,
                status="parsed",
                parser_version=parsed.parser_version,
                schema_version=parsed.schema_version,
                warnings=parsed.warnings,
            )
            store.update_demo_job(
                job.id,
                status="done",
                current_stage="complete",
                progress=1,
                finished_at=utcnow(),
            )
        except DemoParseCancelled:
            store.update_demo_parse(demo.id, status="cancelled")
            store.update_demo_job(
                job.id, status="cancelled", current_stage="cancelled", finished_at=utcnow()
            )
        except DemoParseError as exc:
            store.update_demo_parse(demo.id, status="failed")
            store.update_demo_job(
                job.id,
                status="failed",
                current_stage="failed",
                error=str(exc),
                finished_at=utcnow(),
            )
        except Exception:
            log.exception("Unexpected CS2 demo parse failure for job %s", job.id)
            store.update_demo_parse(demo.id, status="failed")
            store.update_demo_job(
                job.id,
                status="failed",
                current_stage="failed",
                error="Demo parsing failed unexpectedly.",
                finished_at=utcnow(),
            )
        finally:
            self._running_job_id = None
            self._cancel_requested.discard(job.id)


queue = DemoParseQueue()
