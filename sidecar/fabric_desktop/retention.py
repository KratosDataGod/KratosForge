"""Deleting local copies of lakehouse data that nobody has used for a while (FD-131b).

Reading OneLake from this PC leaves copies of the data behind: file previews (``files/``), tables
staged before a publish (``staging/``) and the metadata framework's download cache
(``onelake-cache/``). They are only caches, fetched again when needed, but they are customer data
at rest on a laptop. ``sweep`` deletes what hasn't changed for ``days`` days (policy
``local_retention_days``, default 30; 0 keeps everything).

A Delta table (a folder with ``_delta_log``) goes as a whole, judged by its newest file, so a table
is never left half deleted. Notebooks, settings, history and receipts are never touched.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

CACHE_FOLDERS = ("files", "staging", "onelake-cache")
DAY = 86400


def _newest(folder: Path) -> float:
    return max((p.stat().st_mtime for p in folder.rglob("*") if p.is_file()), default=folder.stat().st_mtime)


def _size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


# A notebook write's spill file (write_queue.Spill) is deleted once its upload ends. One left behind
# (the engine was ended mid-upload) is never needed again: it goes after a day, whatever
# local_retention_days says. A day, because one upload of a very big table can take hours.
SPILLS = Path("staging") / "_writes"
SPILL_HOURS = 24


def _stale_spills(root: Path, now: float) -> list[Path]:
    folder = root / SPILLS
    if not folder.is_dir():
        return []
    return [p for p in folder.iterdir() if p.is_file() and p.stat().st_mtime < now - SPILL_HOURS * 3600]


def sweep(root: Path, days: int, dry_run: bool = False, now: float | None = None) -> dict[str, Any]:
    """Delete (or with ``dry_run`` only list) local copies older than ``days``, and spill files left
    over from uploads. Returns what and how much."""
    now = now if now is not None else time.time()
    old: list[Path] = _stale_spills(root, now)
    if days <= 0:  # copies are kept, left-over spills still go
        total = sum(_size(p) for p in old)
        if not dry_run:
            for p in old:
                p.unlink(missing_ok=True)
        return {"days": days, "removed": [p.relative_to(root).as_posix() for p in old], "bytes": total, "dry_run": dry_run}
    cutoff = now - days * DAY
    for name in CACHE_FOLDERS:
        base = root / name
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            here = Path(dirpath)
            if "_delta_log" in dirnames:
                dirnames.clear()  # judged as one table, below
                if _newest(here) < cutoff:
                    old.append(here)
                continue
            old += [here / f for f in filenames if (here / f).stat().st_mtime < cutoff and here / f not in old]
    total = sum(_size(p) for p in old)
    if not dry_run:
        for p in old:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        for name in CACHE_FOLDERS:  # folders the sweep emptied, deepest first
            base = root / name
            if base.is_dir():
                for d in sorted((d for d in base.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
                    try:
                        d.rmdir()
                    except OSError:
                        pass  # not empty
    return {"days": days, "removed": [p.relative_to(root).as_posix() for p in old], "bytes": total, "dry_run": dry_run}
