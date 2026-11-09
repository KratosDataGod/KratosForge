"""Run logs on this PC that only ever grow by one line at a time: ``history.jsonl`` (runs and notebook
table writes) and ``schedule_runs.jsonl`` (scheduled runs).

``append`` adds one JSON line under a lock the app, scheduled runs and notebook processes share, and
once the file passes ``trim_bytes`` keeps only its newest ``keep_rows`` lines or ``keep_bytes`` (half
of ``trim_bytes`` unless given), whichever is smaller. Keeping half the trigger size means rows with
long text can't leave the file over the cap and rewritten on every append. Years of runs can't grow
the file, or every read of it, without bound.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .jsonfile import write_text

TRIM_BYTES = 8 * 1024 * 1024
KEEP_ROWS = 10_000


def append(path: Path, row: dict[str, Any], trim_bytes: int = TRIM_BYTES, keep_rows: int = KEEP_ROWS,
           keep_bytes: int | None = None) -> None:
    from .leases import _file_lock  # several processes append at once: trimming waits its turn

    path = Path(path)
    keep_bytes = trim_bytes // 2 if keep_bytes is None else keep_bytes
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path.with_name(path.name + ".lock")):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        if path.stat().st_size > trim_bytes:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            kept: list[str] = []
            size = 0
            for line in reversed(lines[-keep_rows:]):
                size += len(line.encode("utf-8"))
                if size > keep_bytes and kept:
                    break
                kept.append(line)
            write_text(path, "".join(reversed(kept)))
