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
import os
import shutil
import sys
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


MARKER = ".kratosforge"  # in a TPC-H temp folder (benchmark._GENERATE): the pid of the process using it


def _tpch_folders() -> list[Path]:
    """The TPC-H data notebook's temp folders: named tpch_* and holding its marker, so another
    tool's tpch_ folder is never listed or deleted."""
    temp = Path(tempfile.gettempdir())
    try:
        return sorted(p for p in temp.iterdir() if p.is_dir() and p.name.startswith(TPCH_PREFIX) and (p / MARKER).is_file())
    except OSError:  # no temp folder to list
        return []


def _owner(path: Path) -> int | None:
    """The pid of the process a spill file (write-<pid>-*.arrow) or TPC-H folder belongs to; None
    for one from a build before it was named."""
    try:
        if path.is_dir():
            return int((path / MARKER).read_text().strip())
        parts = path.name.split("-")
        return int(parts[1]) if len(parts) > 2 else None
    except (OSError, ValueError):
        return None


def pid_running(pid: int) -> bool:
    """Whether a process with this id is running (a scheduled run or another engine still writing)."""
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            return not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
        Refused while a notebook cell runs (its writes may be uploading from here). A spill file or
        TPC-H folder of another process still running is left and counted as in use, and so is a
        file Windows won't delete because something has it open."""
        if self.sessions.any_running():
            raise RuntimeError("A notebook is running, and its writes may still be uploading from this folder. "
                               "Delete left-overs once it has finished.")
        idle = self._idle_kernel_pids()
        staging = self.root / "staging"
        targets = sorted(staging.iterdir()) if staging.is_dir() else []
        if (staging / "_writes") in targets:  # its files one by one, so a spill in use doesn't keep the rest
            targets.remove(staging / "_writes")
            targets += sorted((staging / "_writes").iterdir())
        targets += _tpch_folders()
        removed, freed, in_use = 0, 0, []
        for path in targets:
            # A spill or TPC-H folder of a process still running (a scheduled run, which runs in a
            # process of its own, or a kernel mid-cell) may still be needed. This engine's own idle
            # kernels hold none: a cell's writes are uploaded or deleted before it ends.
            owner = _owner(path)
            if owner is not None and owner != os.getpid() and pid_running(owner) and owner not in idle:
                in_use.append(str(path))
                continue
            size = _remove(path)
            if size is None:
                in_use.append(str(path))
            else:
                removed += 1
                freed += size
        return {"removed": removed, "bytes": freed, "in_use": in_use}

    def _idle_kernel_pids(self) -> set[int]:
        """This engine's notebook processes: none is running a cell (checked above), so what they
        left can go."""
        pids = set()
        for kernel in self.sessions.kernels():
            pid = getattr(getattr(kernel, "_proc", None), "pid", None)
            if pid:
                pids.add(pid)
        return pids
