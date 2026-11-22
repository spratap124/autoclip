"""Isolated parser entry point. Invoked only by adapter.parse_isolated()."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .adapter import parse_demo


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    demo_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    content_sha256 = sys.argv[3]
    memory_limit_mb = int(sys.argv[4])
    _apply_memory_limit(memory_limit_mb)
    parsed = parse_demo(demo_path, content_sha256)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="normalized-", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(parsed.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(output_path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return 0


def _apply_memory_limit(memory_limit_mb: int) -> None:
    if memory_limit_mb <= 0 or os.name != "posix":
        return
    try:
        import resource

        limit = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, OSError, ValueError):
        # Resource limits are not consistently supported on all platforms.
        pass


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
