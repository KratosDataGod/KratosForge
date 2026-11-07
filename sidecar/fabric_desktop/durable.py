"""Flush a local Delta commit to disk, so a power cut right after it can't take the commit's data
with it.

delta-rs writes each data file and then the commit file through the OS cache and syncs neither, so
for up to the OS's write-back delay (about 30 s on Linux) a power cut can leave the published
commit naming a data file the disk never got, or an empty commit file. Syncing right after the
commit cuts that window to the moment between delta-rs publishing the commit and this running. It
can't order the data before the commit (delta-rs has published it by then); only delta-rs syncing
before it publishes could. A killed app loses nothing either way: the OS still writes what it holds.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from urllib.parse import unquote

_log = logging.getLogger(__name__)


def _sync(path: Path, folder: bool = False) -> None:
    if folder and os.name == "nt":
        return  # NTFS journals folder entries itself, and Windows can't open a folder to flush it
    # FlushFileBuffers (os.fsync on Windows) needs a handle open for writing.
    flags = (os.O_RDONLY if folder or os.name != "nt" else os.O_RDWR) | getattr(os, "O_BINARY", 0)
    # A pipe or device under a data file's name would block the open, and the table's lock with it.
    fd = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
    try:
        mode = os.fstat(fd).st_mode
        if stat.S_ISREG(mode) or (folder and stat.S_ISDIR(mode)):
            os.fsync(fd)
    finally:
        os.close(fd)


def commit_files(uri: str, version: int) -> tuple[Path, list[Path]]:
    """The commit file of ``version`` and the files under the table folder that it adds (data and
    change-data files); paths that aren't inside the table folder are left out."""
    root = Path(uri)
    log = root / "_delta_log" / f"{version:020d}.json"
    added = []
    for line in log.read_text(encoding="utf-8").splitlines():
        action = json.loads(line) if line.strip() else {}
        for kind in ("add", "cdc"):
            rel = (action.get(kind) or {}).get("path")
            if not rel or "://" in rel or os.path.isabs(unquote(rel)):
                continue
            path = root / unquote(rel)
            if root.resolve() in path.resolve().parents:
                added.append(path)
    return log, added


def sync_commits(uri: str, first: int, last: int) -> bool:
    """Flush commits ``first`` to ``last`` of the local table at ``uri``: each one's added files, their
    folders, its commit file and _delta_log. Never raises: the commits stand whatever happens here,
    and a write retried for it would write its rows twice. False (and a warning) when one couldn't
    be flushed."""
    try:
        files: list[Path] = []
        logs: list[Path] = []
        for v in range(first, last + 1):
            log, added = commit_files(uri, v)
            logs.append(log)
            files += added
        for p in files:
            _sync(p)
        for folder in sorted({p.parent for p in files} | {Path(uri)}):
            _sync(folder, folder=True)
        for log in logs:
            _sync(log)
        _sync(Path(uri) / "_delta_log", folder=True)
        return True
    except Exception as exc:  # noqa: BLE001  the commit stands; only its flush to disk is uncertain
        _log.warning("Couldn't flush commits %s-%s of %s to disk: %s", first, last, uri, exc)
        return False
