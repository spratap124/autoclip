"""Versioned on-disk cache for normalized CS2 demo data."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from .. import paths
from .adapter import (
    PARSER_VERSION,
    DemoParseError,
    parse_isolated,
    sha256_file,
    validate_demo_file,
)
from .models import NORMALIZED_SCHEMA_VERSION, ParsedDemo


def cache_path(content_sha256: str, *, root: Path | None = None) -> Path:
    base = root or paths.root() / "cache" / "cs2demo"
    return (
        base / content_sha256 / f"parser-{PARSER_VERSION}-schema-{NORMALIZED_SCHEMA_VERSION}.json"
    )


def load_cached(path: Path, *, content_sha256: str) -> ParsedDemo | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        parsed = ParsedDemo.from_dict(value)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (
        parsed.content_sha256 != content_sha256
        or parsed.parser_version != PARSER_VERSION
        or parsed.schema_version != NORMALIZED_SCHEMA_VERSION
    ):
        return None
    return parsed


def parse_cached(
    demo_path: Path,
    *,
    cache_root: Path | None = None,
    timeout_s: float = 600,
    memory_limit_mb: int = 4096,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[ParsedDemo, bool]:
    validate_demo_file(demo_path)
    content_sha256 = sha256_file(demo_path)
    destination = cache_path(content_sha256, root=cache_root)
    cached = load_cached(destination, content_sha256=content_sha256)
    if cached is not None:
        return cached, True
    parsed = parse_isolated(
        demo_path,
        content_sha256=content_sha256,
        timeout_s=timeout_s,
        memory_limit_mb=memory_limit_mb,
        cancelled=cancelled,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="parsed-demo-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(parsed.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(destination)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return parsed, False


def require_cached(content_sha256: str, *, cache_root: Path | None = None) -> ParsedDemo:
    cached = load_cached(cache_path(content_sha256, root=cache_root), content_sha256=content_sha256)
    if cached is None:
        raise DemoParseError("No compatible parsed demo cache exists.")
    return cached
