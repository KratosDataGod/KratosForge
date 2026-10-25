"""Settings > Advanced > OneLake writes: where notebook and job writes keep their files on this PC,
how much is there, and a way to open the folder (to look, or to clear up after a failed run).

What is there: ``staging/`` holds each written table's schema (a few KB per table), the spill files
of writes still uploading (``staging/_writes``) and a job's table while it publishes. The TPC-H data
notebook generates into ``tpch_*`` folders in the system temp folder, deleted when its write cell
ends; one is only left when the engine was ended mid-run. Spill files left over go after a day
(retention.py); the rest stays until the retention sweep or the person deletes it.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from .notebook_resources_service import _reveal
from .retention import SPILLS

METHODS = {"local_writes", "local_writes_reveal"}
TPCH_PREFIX = "tpch_"


def _usage(folder: Path, now: float, stale_after: float | None = None) -> dict[str, Any]:
    files = [p for p in folder.rglob("*") if p.is_file()] if folder.is_dir() else []
    out: dict[str, Any] = {"files": 0, "bytes": 0, "newest": None}
    for p in files:
        try:
            st = p.stat()
        except OSError:  # deleted while we looked (an upload finished)
            continue
        out["files"] += 1
        out["bytes"] += st.st_size
        out["newest"] = max(out["newest"] or 0, st.st_mtime)
    if out["newest"] is not None:
        out["newest"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(out["newest"]))
    return out


def _tpch_folders() -> list[Path]:
    temp = Path(tempfile.gettempdir())
    try:
        return sorted(p for p in temp.iterdir() if p.is_dir() and p.name.startswith(TPCH_PREFIX))
    except OSError:  # no temp folder to list
        return []


class LocalWritesMixin:
    root: Path

    def local_writes(self) -> dict[str, Any]:
        """The local write folders and what they hold."""
        now = time.time()
        staging = self.root / "staging"
        return {"folder": str(staging), **_usage(staging, now),
                "uploading": _usage(self.root / SPILLS, now) | {"folder": str(self.root / SPILLS)},
                "tpch": [{"folder": str(p), **_usage(p, now)} for p in _tpch_folders()]}

    def local_writes_reveal(self, folder: str | None = None) -> dict[str, Any]:
        """Open a local write folder in File Explorer: the staging folder, or one listed by local_writes
        (nothing else, so this can't open any path it's handed)."""
        staging = self.root / "staging"
        allowed = {str(staging): staging, str(self.root / SPILLS): self.root / SPILLS,
                   **{str(p): p for p in _tpch_folders()}}
        target = allowed.get(folder or str(staging))
        if target is None:
            raise ValueError("That folder isn't one of KratosForge's local write folders, or it's gone.")
        target.mkdir(parents=True, exist_ok=True)
        _reveal(target, select=False)
        return {"shown": str(target)}
