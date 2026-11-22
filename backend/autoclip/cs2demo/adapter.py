"""Thin demoparser2 adapter: parser data in, stable JSON domain data out."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .models import NORMALIZED_SCHEMA_VERSION, DemoPlayer, ParsedDemo

log = logging.getLogger(__name__)
PARSER_VERSION = "0.42.2"
CS2_DEMO_MAGIC = b"PBDEMS2\0"
CORE_EVENTS = (
    "player_death",
    "round_freeze_end",
    "round_officially_ended",
    "round_start",
    "round_end",
    "round_prestart",
    "round_poststart",
    "cs_pre_restart",
    "player_spawn",
    "player_team",
    "player_disconnect",
    "bomb_planted",
    "bomb_defused",
    "bomb_exploded",
    "bomb_begindefuse",
    "bomb_beginplant",
    "bomb_dropped",
)


class DemoSource(Protocol):
    """Normalized boundary used by the detector, independent of parser APIs."""

    def parse(self, path: Path, content_sha256: str) -> ParsedDemo: ...


class DemoParseError(RuntimeError):
    """Controlled error raised when a demo cannot be validated or parsed."""


class DemoParseCancelled(DemoParseError):
    """Raised when a parse job is cancelled by its owner."""


def validate_demo_file(path: Path, *, max_bytes: int = 2_000_000_000) -> None:
    if not path.is_file():
        raise DemoParseError("Demo file does not exist.")
    size = path.stat().st_size
    if size == 0:
        raise DemoParseError("Demo file is empty.")
    if size > max_bytes:
        raise DemoParseError(f"Demo exceeds the configured {max_bytes}-byte upload limit.")
    with path.open("rb") as handle:
        magic = handle.read(len(CS2_DEMO_MAGIC))
    if magic != CS2_DEMO_MAGIC:
        if magic.startswith(b"\x28\xb5\x2f\xfd"):
            detail = "The file is Zstandard-compressed; extract the .dem file before uploading."
        elif magic.startswith(b"HL2DEMO"):
            detail = "Legacy Source 1 demos are unsupported; upload a CS2 Source 2 demo."
        else:
            detail = "The file does not have the CS2 Source 2 PBDEMS2 signature."
        raise DemoParseError(detail)


def sha256_file(path: Path, *, chunk_bytes: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def parse_demo(path: Path, content_sha256: str | None = None) -> ParsedDemo:
    """Parse a Source 2 demo using demoparser2 in-process.

    Production callers must use :func:`parse_isolated`; this function is kept
    narrow so the adapter can be probed directly and deterministically.
    """
    validate_demo_file(path)
    try:
        installed = importlib.metadata.version("demoparser2")
    except importlib.metadata.PackageNotFoundError as exc:
        raise DemoParseError("demoparser2 is not installed.") from exc
    if installed != PARSER_VERSION:
        raise DemoParseError(
            f"Expected demoparser2 {PARSER_VERSION}, found {installed}; "
            "refusing an incompatible parse."
        )

    try:
        from demoparser2 import DemoParser

        parser = DemoParser(str(path))
        header = parser.parse_header()
        event_names = parser.list_game_events()
        player_frame = parser.parse_player_info()
        players = _players(player_frame)
        requested = [name for name in CORE_EVENTS if name in event_names]
        raw_events: dict[str, list[dict[str, Any]]] = {name: [] for name in requested}
        if requested:
            for event_name, frame in parser.parse_events(requested):
                if event_name in raw_events:
                    raw_events[event_name] = _frame_records(frame)
        warnings: list[str] = []
        available_bomb_events = {
            name for name in requested if name in {"bomb_planted", "bomb_defused", "bomb_exploded"}
        }
        if not available_bomb_events:
            warnings.append("Bomb event streams are unavailable in this demo.")
        death_rows = raw_events.get("player_death", [])
        for column, label in (
            ("headshot", "headshot"),
            ("penetrated", "wall-penetration"),
            ("noscope", "no-scope"),
            ("thrusmoke", "through-smoke"),
            ("attackerblind", "attacker-blind"),
        ):
            if death_rows and not any(column in row for row in death_rows):
                warnings.append(f"The parser did not expose {label} kill flags for this demo.")
        player_states: list[dict[str, Any]] = []
        steamids = sorted({player.steamid for player in players if player.steamid is not None})
        state_ticks = _state_ticks(raw_events)
        if steamids and state_ticks:
            try:
                state_frame = parser.parse_ticks(
                    [
                        "is_alive",
                        "team_num",
                        "is_warmup_period",
                        "round_in_progress",
                        "total_rounds_played",
                        "is_bomb_planted",
                        "round_win_reason",
                        "team_rounds_total",
                    ],
                    players=steamids,
                    ticks=state_ticks,
                )
                player_states = _frame_records(state_frame)
            except Exception as exc:
                warnings.append(
                    f"Player-state snapshots unavailable ({type(exc).__name__}); "
                    "clutch state is incomplete."
                )
        else:
            warnings.append("Player-state snapshots are unavailable; clutch state is incomplete.")
        warnings.append(
            "Tick rate was not exposed by this parser result; derived seconds are unavailable."
        )
        if "player_death" not in event_names:
            warnings.append("The demo does not contain a player_death event stream.")
        if not any(name in event_names for name in ("round_end", "round_officially_ended")):
            warnings.append(
                "No recognized round-end event is present; round boundaries may be partial."
            )
        if not players:
            warnings.append("The parser returned no player roster records.")
        if "round_start" not in event_names or "round_end" not in event_names:
            warnings.append(
                "Canonical round_start/round_end event pair is incomplete in this demo."
            )
        return ParsedDemo(
            content_sha256=content_sha256 or sha256_file(path),
            parser_version=installed,
            schema_version=NORMALIZED_SCHEMA_VERSION,
            header={str(k): str(v) for k, v in header.items()},
            players=players,
            event_names=list(event_names),
            events=raw_events,
            player_states=player_states,
            tick_rate=None,
            warnings=warnings,
        )
    except DemoParseError:
        raise
    except Exception as exc:
        log.exception("demoparser2 failed to parse a CS2 demo")
        raise DemoParseError(f"Could not parse this CS2 demo ({type(exc).__name__}).") from exc


def parse_isolated(
    path: Path,
    *,
    content_sha256: str,
    timeout_s: float = 600,
    memory_limit_mb: int = 4096,
    cancelled: Callable[[], bool] | None = None,
) -> ParsedDemo:
    """Run parser in a child process with timeout and POSIX address-space cap."""
    validate_demo_file(path)
    with tempfile.TemporaryDirectory(prefix="autoclip-demoparse-") as temp:
        output = Path(temp) / "normalized.json"
        log_path = Path(temp) / "parser.log"
        env = _child_environment()
        kwargs: dict[str, Any] = {
            "args": [
                sys.executable,
                "-m",
                "autoclip.cs2demo.worker",
                str(path),
                str(output),
                content_sha256,
                str(memory_limit_mb),
            ],
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "env": env,
            "close_fds": True,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        with log_path.open("wb") as log_handle:
            kwargs["stderr"] = log_handle
            process = subprocess.Popen(**kwargs)
            deadline = time.monotonic() + timeout_s
            while True:
                if cancelled is not None and cancelled():
                    _terminate(process)
                    raise DemoParseCancelled("Demo parsing was cancelled.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate(process)
                    raise DemoParseError(f"Demo parsing exceeded the {timeout_s:g}-second timeout.")
                try:
                    process.communicate(timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        if process.returncode != 0:
            message = log_path.read_bytes()[-8192:].decode("utf-8", errors="replace").strip()
            if "memory" in message.lower() or "bad_alloc" in message.lower():
                raise DemoParseError("Demo parsing exceeded the available memory limit.")
            log.warning("Demo parser subprocess failed: %s", message[-1000:])
            raise DemoParseError("Demo parser failed. The demo may be truncated or unsupported.")
        if not output.is_file():
            raise DemoParseError("Demo parser exited without producing normalized match data.")
        try:
            return ParsedDemo.from_dict(json.loads(output.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DemoParseError("Demo parser produced invalid normalized data.") from exc


def _child_environment() -> dict[str, str]:
    safe = ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    return {key: os.environ[key] for key in safe if key in os.environ}


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            with _suppress_os_error():
                os.killpg(process.pid, signal.SIGKILL)
    else:  # pragma: no cover - Windows-specific
        process.kill()
    process.wait()


class _suppress_os_error:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, OSError)


def _players(frame: Any) -> list[DemoPlayer]:
    rows = frame.to_dict(orient="records")
    players: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        steamid = _optional_int(row.get("steamid"))
        name = row.get("name")
        name = str(name) if name is not None else None
        team = _optional_int(row.get("team_number"))
        player_id = f"steamid:{steamid}" if steamid is not None else f"session:{index}"
        existing = players.get(player_id)
        history = list(existing["name_history"]) if existing else []
        if name and name not in history:
            history.append(name)
        players[player_id] = {
            "session_id": f"session-{steamid if steamid is not None else index}",
            "steamid": steamid,
            "name": name or (history[-1] if history else None),
            "team_number": team,
            "name_history": tuple(history),
        }
    return [DemoPlayer(**player) for player in players.values()]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if value != value:  # NaN
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _frame_records(frame: Any) -> list[dict[str, Any]]:
    # pandas' serializer converts nullable/Arrow values and NaN to JSON nulls.
    records = json.loads(frame.to_json(orient="records", force_ascii=False))
    return [{str(k): v for k, v in record.items()} for record in records]


def _state_ticks(events: dict[str, list[dict[str, Any]]]) -> list[int]:
    ticks: set[int] = set()
    for event_name, rows in events.items():
        for row in rows:
            tick = _optional_int(row.get("tick"))
            if tick is None:
                continue
            ticks.add(tick)
            if event_name == "player_death":
                if tick > 0:
                    ticks.add(tick - 1)
                ticks.add(tick + 1)
    return sorted(ticks)
