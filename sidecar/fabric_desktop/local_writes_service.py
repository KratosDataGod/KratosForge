"""Settings > Advanced > OneLake writes: where notebook and job writes keep their files on this PC,
how much is there, a way to open the folder (to look) and Delete left-overs (to clear up after a
failed run).

What is there: ``staging/`` holds each written table's schema (a few KB per table), the spill files
of writes still uploading (``staging/_writes``) and a job's table while it publishes. The TPC-H data
notebook generates into ``tpch_*`` folders in the system temp folder, deleted when its write cell
ends; one is only left when the engine was ended mid-run. Spill files left over go after a day
(retention.py); the rest stays until the retention sweep or the person deletes it.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .notebook_resources_service import _reveal
from .retention import SPILLS

METHODS = {"local_writes", "local_writes_reveal", "local_writes_clear"}
TPCH_PREFIX = "tpch_"
_log = logging.getLogger(__name__)


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


def _remove(path: Path) -> int | None:
    """Delete a file or folder; its size, or None when something in it is in use (Windows keeps an
    open file: a write still uploading, a DuckDB file a run has open) and it was left."""
    size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else path.stat().st_size
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return 0
    except OSError as exc:
        _log.info("Left %s, it's in use: %s", path.name, exc)
        return None
    return size


class LocalWritesMixin:
    root: Path
    sessions: Any

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

    def local_writes_clear(self) -> dict[str, Any]:
        """Settings' Delete left-overs: everything in the staging folder and the TPC-H temp folders.
        Refused while a notebook cell runs (its writes may be uploading from here); a file another
        process still has open (a scheduled run's upload) is left and counted as in use."""
        if self.sessions.any_running():
            raise RuntimeError("A notebook is running, and its writes may still be uploading from this folder. "
                               "Delete left-overs once it has finished.")
        staging = self.root / "staging"
        targets = sorted(staging.iterdir()) if staging.is_dir() else []
        if (staging / "_writes") in targets:  # its files one by one, so a spill in use doesn't keep the rest
            targets.remove(staging / "_writes")
            targets += sorted((staging / "_writes").iterdir())
        targets += _tpch_folders()
        removed, freed, in_use = 0, 0, []
        for path in targets:
            size = _remove(path)
            if size is None:
                in_use.append(str(path))
            else:
                removed += 1
                freed += size
        return {"removed": removed, "bytes": freed, "in_use": in_use}
