"""Deleting local copies of lakehouse data that nobody has used for a while (FD-131b).

Reading OneLake from this PC leaves copies of the data behind: files notebooks read (``files/``), tables
staged before a publish (``staging/``) and the metadata framework's download cache
(``onelake-cache/``). They are only caches, fetched again when needed, but they are customer data
at rest on a laptop. The app's copy of a replay bundle someone opened (``replays/``) goes the same
way: a bundle can hold a sample of the tables its notebook read, and the original stays wherever it
was opened from (Downloads, usually). Replaying a bundle touches its copy, so one in use stays. ``sweep`` deletes what hasn't changed for ``days`` days (policy
``local_retention_days``, default 30; 0 keeps everything).

A Delta table (a folder with ``_delta_log``) goes as a whole, judged by its newest file, so a table
is never left half deleted: a folder is first renamed aside (``.<name>.deleting-<pid>``) and only then
deleted. On Windows that rename fails while a file inside is open, as when a notebook is reading the
table during a sweep, so the folder stays whole and is tried again at the next sweep. A renamed folder
a sweep didn't finish goes at the next one. Notebooks, settings, history and receipts are never touched.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

CACHE_FOLDERS = ("files", "staging", "onelake-cache", "replays")
DAY = 86400


def _linked(path: Path) -> bool:
    """A symbolic link or (Windows) junction: the sweep never goes into one, whatever it points to.
    os.walk without followlinks still enters a junction on Windows."""
    return path.is_symlink() or path.is_junction()


def _newest(folder: Path) -> float:
    return max((p.stat().st_mtime for p in folder.rglob("*") if p.is_file()), default=folder.stat().st_mtime)


FRESH_FOLDER_HOURS = 1  # an empty folder younger than this stays: a download may be about to fill it
TRASH = ".deleting-"  # a folder renamed aside for deleting: ".<name>.deleting-<pid>"


def _is_trash(path: Path) -> bool:
    return path.name.startswith(".") and TRASH in path.name


def _remove(path: Path) -> bool:
    """Delete a file, or a folder as a whole; False (and logged) when it is in use and was kept."""
    try:
        if not path.is_dir():
            path.unlink(missing_ok=True)
            return True
        aside = path if _is_trash(path) else path.with_name(f".{path.name}{TRASH}{os.getpid()}")
        if aside != path:
            path.rename(aside)  # fails on Windows while a file inside is open: nothing is deleted then
    except OSError as exc:  # in use (a table being read, a spill still uploading): tried again next sweep
        _log.info("Couldn't remove %s now: %s", path.name, exc)
        return False
    shutil.rmtree(aside, ignore_errors=True)
    return True


def _size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


# A notebook write's spill file (write_queue.Spill) is deleted once its upload ends. One left behind
# (the engine was ended mid-upload) is never needed again: it goes after a day, whatever
# local_retention_days says. A day, because one upload of a very big table can take hours.
SPILLS = Path("staging") / "_writes"
SPILL_HOURS = 24


def _stale_spills(root: Path, now: float) -> list[Path]:
    folder = root / SPILLS
    if not folder.is_dir() or _linked(root / "staging") or _linked(folder):
        return []
    return [p for p in folder.iterdir() if p.is_file() and p.stat().st_mtime < now - SPILL_HOURS * 3600]


# A file preview downloads into a folder of its own under preview-files/ and removes it once read
# (preview_service.py). One left behind (the engine stopped mid-preview) goes at the next start once
# it is an hour old, whatever local_retention_days says: an hour, so a preview another engine
# process is reading right now is never taken from it.
PREVIEWS = "preview-files"
PREVIEW_HOURS = 1


def _stale_previews(root: Path, now: float) -> list[Path]:
    folder = root / PREVIEWS
    if not folder.is_dir() or _linked(folder):
        return []
    return [p for p in folder.iterdir() if p.is_dir() and p.stat().st_mtime < now - PREVIEW_HOURS * 3600]


def sweep(root: Path, days: int, dry_run: bool = False, now: float | None = None) -> dict[str, Any]:
    """Delete (or with ``dry_run`` only list) local copies older than ``days``, and spill files left
    over from uploads and previews. Returns what and how much."""
    now = now if now is not None else time.time()
    old: list[Path] = _stale_spills(root, now) + _stale_previews(root, now)
    if days <= 0:  # copies are kept, left-over spills and previews still go
        return _result(root, old, days, dry_run)
    cutoff = now - days * DAY
    for name in CACHE_FOLDERS:
        base = root / name
        if not base.is_dir() or _linked(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            here = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not _linked(here / d)]  # never into a link or junction
            old += [here / d for d in dirnames if _is_trash(here / d)]  # a deletion a sweep didn't finish
            dirnames[:] = [d for d in dirnames if not _is_trash(here / d)]
            if "_delta_log" in dirnames:
                dirnames.clear()  # judged as one table, below
                if _newest(here) < cutoff:
                    old.append(here)
                continue
            old += [here / f for f in filenames if (here / f).stat().st_mtime < cutoff and here / f not in old]
    # Folders the sweep may leave empty: those it deleted something from, and others older than an hour
    # before it started. A newer empty one, or one made during the sweep, may be a download's that is
    # about to write its file.
    settled = [] if dry_run else [d for name in CACHE_FOLDERS if (root / name).is_dir()
                                  for d in (root / name).rglob("*")
                                  if d.is_dir() and d.stat().st_mtime < now - FRESH_FOLDER_HOURS * 3600]
    result = _result(root, old, days, dry_run)
    if not dry_run:
        bases = [root / name for name in CACHE_FOLDERS]
        emptied = {parent for p in result["removed"] for parent in (root / p).parents
                   if parent not in bases and any(parent.is_relative_to(base) for base in bases)}
        for d in sorted({*settled, *emptied}, key=lambda d: len(d.parts), reverse=True):  # deepest first
            try:
                d.rmdir()
            except OSError:
                pass  # not empty, or already gone
    return result


def _result(root: Path, old: list[Path], days: int, dry_run: bool) -> dict[str, Any]:
    """Delete ``old`` (unless ``dry_run``); what was removed, and how many bytes. Kept items aren't listed."""
    sizes = {p: _size(p) for p in old}
    removed = old if dry_run else [p for p in old if _remove(p)]
    return {"days": days, "removed": [p.relative_to(root).as_posix() for p in removed],
            "bytes": sum(sizes[p] for p in removed), "dry_run": dry_run}
