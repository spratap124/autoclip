"""Local CS2 demo upload and parse endpoints."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from .. import paths
from ..config import load as load_settings
from ..cs2demo.adapter import CS2_DEMO_MAGIC, DemoParseError
from ..cs2demo.analyze import analyze_player
from ..cs2demo.cache import require_cached
from ..cs2demo.detection_models import DetectionSettings
from ..cs2demo.models import ParsedDemo
from ..cs2demo.queue import queue
from ..cs2demo.reconstruct import reconstruct
from ..db import store
from ..db.models import DemoFile, DemoParseJob, new_id
from .schemas import DemoMatchOut, DemoParseJobOut, DemoUploadOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/demos", tags=["cs2-demos"])
UPLOAD_CHUNK = 4 * 1024 * 1024


@router.post("/upload", response_model=DemoUploadOut, status_code=201)
async def upload_demo(file: UploadFile = File(...)) -> DemoUploadOut:
    """Stream a local CS2 demo to disk, validate its magic, and deduplicate it."""
    filename = _safe_filename(file.filename or "match.dem")
    if Path(filename).suffix.lower() != ".dem":
        raise HTTPException(status_code=415, detail="Upload a CS2 .dem file.")

    demo_id = new_id()
    destination_dir = paths.demo_media_dir(demo_id)
    destination_dir.mkdir(parents=True, exist_ok=False)
    settings = load_settings().cs2_demo
    digest = hashlib.sha256()
    total = 0
    magic = bytearray()
    staging_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=destination_dir, prefix="upload-", suffix=".part"
        ) as staging:
            staging_path = Path(staging.name)
            while chunk := await file.read(UPLOAD_CHUNK):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Demo exceeds the configured {settings.max_upload_bytes}-byte limit."
                        ),
                    )
                if shutil.disk_usage(destination_dir).free < len(chunk):
                    raise HTTPException(
                        status_code=507, detail="Not enough disk space for this upload."
                    )
                if len(magic) < len(CS2_DEMO_MAGIC):
                    magic.extend(chunk[: len(CS2_DEMO_MAGIC) - len(magic)])
                digest.update(chunk)
                staging.write(chunk)
            staging.flush()
        if total == 0:
            raise HTTPException(status_code=400, detail="The uploaded demo is empty.")
        if bytes(magic) != CS2_DEMO_MAGIC:
            detail = (
                "The uploaded file is Zstandard-compressed; extract the .dem before uploading."
                if bytes(magic).startswith(b"\x28\xb5\x2f\xfd")
                else "Invalid demo signature. Expected a CS2 Source 2 PBDEMS2 file."
            )
            raise HTTPException(status_code=415, detail=detail)

        content_sha256 = digest.hexdigest()
        existing = await asyncio.to_thread(store.get_demo_by_hash, content_sha256)
        if existing is not None and Path(existing.file_path).is_file():
            return DemoUploadOut.of(existing, duplicate=True)

        final_path = destination_dir / "source.dem"
        staging_path.replace(final_path)
        demo = DemoFile(
            id=demo_id,
            content_sha256=content_sha256,
            file_path=str(final_path),
            original_filename=filename,
            size_bytes=total,
        )
        try:
            await asyncio.to_thread(store.create_demo, demo)
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        return DemoUploadOut.of(demo, duplicate=False)
    except HTTPException:
        raise
    except OSError as exc:
        log.exception("Failed to store CS2 demo upload")
        raise HTTPException(status_code=500, detail="Could not store the uploaded demo.") from exc
    finally:
        if staging_path is not None:
            staging_path.unlink(missing_ok=True)
        await file.close()
        if destination_dir.exists() and not any(destination_dir.iterdir()):
            destination_dir.rmdir()


@router.get("/{demo_id}", response_model=DemoUploadOut)
async def get_demo(demo_id: str) -> DemoUploadOut:
    demo = await asyncio.to_thread(store.get_demo, demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found.")
    return DemoUploadOut.of(demo)


@router.post("/{demo_id}/parse", response_model=DemoParseJobOut, status_code=202)
async def parse_uploaded_demo(demo_id: str) -> DemoParseJobOut:
    demo = await asyncio.to_thread(store.get_demo, demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found.")
    job = DemoParseJob(id=new_id(), demo_id=demo.id)
    await asyncio.to_thread(store.create_demo_job, job)
    queue.notify()
    return DemoParseJobOut.of(job)


@router.get("/jobs/{job_id}", response_model=DemoParseJobOut)
async def get_demo_parse_job(job_id: str) -> DemoParseJobOut:
    job = await asyncio.to_thread(store.get_demo_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Demo parse job not found.")
    return DemoParseJobOut.of(job)


@router.post("/jobs/{job_id}/cancel", response_model=DemoParseJobOut)
async def cancel_demo_parse_job(job_id: str) -> DemoParseJobOut:
    job = await asyncio.to_thread(store.get_demo_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Demo parse job not found.")
    if not queue.cancel(job_id):
        raise HTTPException(status_code=409, detail=f"Demo parse job is already {job.status}.")
    updated = await asyncio.to_thread(store.get_demo_job, job_id)
    return DemoParseJobOut.of(updated or job)


@router.get("/{demo_id}/match", response_model=DemoMatchOut)
async def get_parsed_match(demo_id: str) -> DemoMatchOut:
    demo = await asyncio.to_thread(store.get_demo, demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found.")
    try:
        parsed = await asyncio.to_thread(require_cached, demo.content_sha256)
    except DemoParseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _match_out(demo.id, parsed, cache_hit=True)


@router.get("/{demo_id}/players")
async def get_demo_players(demo_id: str) -> dict:
    demo, parsed = await _load_parsed_demo(demo_id)
    reconstruction = await asyncio.to_thread(reconstruct, parsed)
    valid = [kill for kill in reconstruction.kills if kill.valid_enemy_kill]
    output = []
    for player in reconstruction.players:
        player_id = str(player["player_id"])
        kills = [kill for kill in valid if kill.attacker_id == player_id]
        deaths = [kill for kill in valid if kill.victim_id == player_id]
        assists = [kill for kill in valid if kill.assister_id == player_id]
        output.append(
            {
                **player,
                "kills": len(kills),
                "deaths": len(deaths),
                "assists": len(assists),
                "adr": None,
                "selectable": player.get("steamid") is not None,
            }
        )
    return {"demo_id": demo.id, "players": output, "warnings": list(parsed.warnings)}


@router.get("/{demo_id}/rounds")
async def get_demo_rounds(demo_id: str, player_id: str | None = None) -> dict:
    demo, parsed = await _load_parsed_demo(demo_id)
    reconstruction = await asyncio.to_thread(reconstruct, parsed)
    rounds = []
    for item in reconstruction.rounds:
        kills = [kill for kill in reconstruction.kills if kill.round_id == item.round_id]
        rounds.append(
            {
                "round_id": item.round_id,
                "number": item.number,
                "start_tick": item.start_tick,
                "active_start_tick": item.active_start_tick,
                "end_tick": item.end_tick,
                "complete": item.complete,
                "winner_team": item.winner_team,
                "end_reason": item.end_reason,
                "outcome_evidence": item.outcome_evidence,
                "selected_player_kills": [
                    kill.event_id
                    for kill in kills
                    if player_id is not None and kill.attacker_id == player_id
                ],
                "kill_count": len(kills),
                "source_event_ids": list(item.source_event_ids),
            }
        )
    return {"demo_id": demo.id, "rounds": rounds, "warnings": list(reconstruction.warnings)}


@router.get("/{demo_id}/analysis/{player_id}")
async def get_player_analysis(
    demo_id: str,
    player_id: str,
    tag: str | None = None,
    round_number: int | None = Query(default=None, ge=1),
    minimum_score: int = Query(default=0, ge=0),
    sort: Literal["score", "chronological", "kills", "duration"] = "score",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    demo, parsed = await _load_parsed_demo(demo_id)
    settings = _detection_settings(load_settings().cs2_demo)
    settings_hash = hashlib.sha256(
        json.dumps(settings.__dict__, sort_keys=True).encode()
    ).hexdigest()
    result = await asyncio.to_thread(
        store.get_demo_analysis, demo.id, player_id, settings_hash
    )
    if result is None:
        try:
            analysis = await asyncio.to_thread(analyze_player, parsed, player_id, settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = {
            "demo_sha256": analysis.demo_sha256,
            "player_id": analysis.player_id,
            "highlights": [item.to_dict() for item in analysis.highlights],
            "warnings": list(analysis.warnings),
            "settings_hash": settings_hash,
        }
        await asyncio.to_thread(store.put_demo_analysis, demo.id, player_id, settings_hash, result)
    highlights = list(result["highlights"])
    if tag:
        selected_tags = {value.strip() for value in tag.split(",") if value.strip()}
        if "special_kills" in selected_tags:
            selected_tags.remove("special_kills")
            selected_tags.update(
                {
                    "wallbang",
                    "no_scope",
                    "through_smoke",
                    "blind_kill",
                    "knife_kill",
                    "awp_multi_kill",
                }
            )
        highlights = [
            item for item in highlights if selected_tags.intersection(item["tags"])
        ]
    if round_number is not None:
        highlights = [item for item in highlights if item["round_number"] == round_number]
    highlights = [item for item in highlights if item["score"] >= minimum_score]
    if sort == "chronological":
        highlights.sort(key=lambda item: (item["start_tick"], item["highlight_id"]))
    elif sort == "kills":
        highlights.sort(key=lambda item: (-item["kill_count"], -item["score"], item["start_tick"]))
    elif sort == "duration":
        highlights.sort(
            key=lambda item: (
                -(item["end_tick"] - item["start_tick"]),
                -item["score"],
                item["start_tick"],
            )
        )
    else:
        highlights.sort(key=lambda item: (-item["score"], item["start_tick"]))
    total = len(highlights)
    return {
        "demo_id": demo.id,
        "player_id": player_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "highlights": highlights[offset : offset + limit],
        "warnings": result["warnings"],
        "settings_hash": settings_hash,
    }


@router.get("/{demo_id}/analysis/{player_id}/export/{export_format}")
async def export_player_analysis(
    demo_id: str,
    player_id: str,
    export_format: Literal["json", "csv", "timestamps"],
) -> Response:
    result = await get_player_analysis(
        demo_id,
        player_id,
        tag=None,
        round_number=None,
        minimum_score=0,
        sort="score",
        limit=500,
        offset=0,
    )
    while len(result["highlights"]) < result["total"]:
        next_page = await get_player_analysis(
            demo_id,
            player_id,
            tag=None,
            round_number=None,
            minimum_score=0,
            sort="score",
            limit=500,
            offset=len(result["highlights"]),
        )
        if not next_page["highlights"]:
            break
        result["highlights"].extend(next_page["highlights"])
    highlights = result["highlights"]
    if export_format == "json":
        return Response(
            json.dumps(result, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="cs2-highlights.json"'},
        )
    output = io.StringIO(newline="")
    if export_format == "csv":
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "highlight_id",
                "player_id",
                "round_number",
                "tags",
                "score",
                "start_tick",
                "end_tick",
                "start_seconds",
                "end_seconds",
                "source_event_ids",
            ],
        )
        writer.writeheader()
        for item in highlights:
            writer.writerow(
                {
                    "highlight_id": item["highlight_id"],
                    "player_id": result["player_id"],
                    "round_number": item["round_number"],
                    "tags": ",".join(item["tags"]),
                    "score": item["score"],
                    "start_tick": item["start_tick"],
                    "end_tick": item["end_tick"],
                    "start_seconds": item["start_seconds"],
                    "end_seconds": item["end_seconds"],
                    "source_event_ids": ",".join(item["source_event_ids"]),
                }
            )
        media_type, filename = "text/csv", "cs2-highlights.csv"
    else:
        for item in highlights:
            output.write(
                f"{result['player_id']}  Round {item['round_number']}  tick {item['start_tick']}-"
                f"{item['end_tick']}  {' + '.join(item['tags'])}\n"
            )
        media_type, filename = "text/plain; charset=utf-8", "cs2-highlights.txt"
    return Response(
        output.getvalue(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{demo_id}", status_code=204)
async def delete_demo(demo_id: str) -> None:
    active_jobs = await asyncio.to_thread(store.list_demo_jobs, demo_id=demo_id, limit=1000)
    if any(job.status in ("queued", "running") for job in active_jobs):
        raise HTTPException(
            status_code=409,
            detail="Cancel active parsing before deleting this demo.",
        )
    demo = await asyncio.to_thread(store.delete_demo, demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found.")
    await asyncio.to_thread(shutil.rmtree, paths.demo_media_dir(demo_id), True)
    if re.fullmatch(r"[0-9a-f]{64}", demo.content_sha256):
        cache_dir = paths.root() / "cache" / "cs2demo" / demo.content_sha256
        await asyncio.to_thread(shutil.rmtree, cache_dir, True)


def _match_out(demo_id: str, parsed: ParsedDemo, *, cache_hit: bool = True) -> DemoMatchOut:
    reconstruction = reconstruct(parsed)
    final_tick = max(reconstruction.state_by_tick, default=-1)
    final_states = reconstruction.state_by_tick.get(final_tick, {})
    score_values: dict[int, set[int]] = {}
    for item in final_states.values():
        team = item.get("team_num")
        score = item.get("team_rounds_total")
        if team is not None and score is not None:
            score_values.setdefault(int(team), set()).add(int(score))
    scores = {
        str(team): next(iter(team_scores))
        for team, team_scores in score_values.items()
        if len(team_scores) == 1
    }
    return DemoMatchOut(
        demo_id=demo_id,
        content_sha256=parsed.content_sha256,
        parser_version=parsed.parser_version,
        schema_version=parsed.schema_version,
        header=parsed.header,
        players=[player.__dict__ for player in parsed.players],
        event_counts={name: len(rows) for name, rows in parsed.events.items()},
        warnings=list(dict.fromkeys([*parsed.warnings, *reconstruction.warnings])),
        round_count=len(reconstruction.rounds),
        match_score=scores,
        duration_s=None,
        match_type="unknown",
        cache_hit=cache_hit,
    )


async def _load_parsed_demo(demo_id: str) -> tuple[DemoFile, ParsedDemo]:
    demo = await asyncio.to_thread(store.get_demo, demo_id)
    if demo is None:
        raise HTTPException(status_code=404, detail="Demo not found.")
    try:
        parsed = await asyncio.to_thread(require_cached, demo.content_sha256)
    except DemoParseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return demo, parsed


def _detection_settings(settings: object) -> DetectionSettings:
    return DetectionSettings(
        tick_rate_override=getattr(settings, "tick_rate_override", None),
        pre_roll_s=getattr(settings, "pre_roll_s", 4.0),
        post_roll_s=getattr(settings, "post_roll_s", 5.0),
        won_clutch_post_roll_s=getattr(settings, "won_clutch_post_roll_s", 8.0),
        min_clip_duration_s=getattr(settings, "min_clip_duration_s", 8.0),
        max_clip_duration_s=getattr(settings, "max_clip_duration_s", 120.0),
        fast_multi_kill_s=getattr(settings, "fast_multi_kill_s", 5.0),
        headshot_streak_kills=getattr(settings, "headshot_streak_kills", 2),
        strict_clutches=getattr(settings, "strict_clutches", True),
    )


def _safe_filename(value: str) -> str:
    # Normalize both Windows and POSIX separators before taking a basename.
    name = Path(value.replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable() and ch not in "/\\")
    return name[:150] or "match.dem"
