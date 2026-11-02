"""Shared JSON files (settings, schedules, favorites, saved comparisons) written so a crash or a second writer never leaves half a file.

``write_json`` writes a temporary file in the same folder, flushes it to disk and swaps it in with
``os.replace``, so readers see the old file or the new one, never a mix. ``read_json`` returns the
default for a missing file as before; a file that can't be parsed is moved aside to
``<name>.corrupt-<timestamp>`` with a warning, so the next save doesn't silently overwrite what was in it.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# As retrying.local_retry waits out the Windows Delta-log race: 0.05 s doubling, about 1.5 s in all,
# long enough for a virus scanner or indexer that has the file open. Other waits on a busy local file
# (auth.SharedTokenCache) use the same.
REPLACE_ATTEMPTS = 6
REPLACE_BACKOFF = 0.05


def write_json(path: Path, data: Any, indent: int = 2) -> None:
    write_text(path, json.dumps(data, indent=indent))


def write_text(path: Path, text: str) -> None:
    """Swap ``text`` in as the whole file: readers see the old file or the new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """``os.replace``, waiting out Windows refusing it (PermissionError, WinError 5) while a reader, a
    virus scanner or an indexer has either file open: about 1.5 s, then the error. Every swap of a
    finished temporary file into place goes through here (tests/test_file_replace.py)."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))


def set_aside(path: Path, then: str = "it is being started again empty", keep: bool = False) -> Path:
    """Move an unreadable file to ``<name>.corrupt-<timestamp>`` and say so (``then``: what happens next).

    ``keep`` copies it there instead, leaving the original in place until the caller replaces it."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    if keep:
        shutil.copy2(path, dest)
    else:
        os.replace(path, dest)
    _log.warning("%s could not be read and was %s to %s; %s.", path, "copied" if keep else "moved", dest.name, then)
    return dest


def read_text(path: Path) -> str:
    """The whole file. On Windows, opening it just as a writer swaps in a new copy (write_text) fails with
    PermissionError for a moment, so that is waited out the same way the writer waits out a reader."""
    for attempt in range(REPLACE_ATTEMPTS - 1):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))
    return path.read_text(encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    """The parsed file; ``default`` when it is missing or unreadable (an unreadable one is set aside first)."""
    try:
        return json.loads(read_text(path))
    except FileNotFoundError:
        return default
    except ValueError:  # torn or not JSON (UnicodeDecodeError is a ValueError too)
        try:
            set_aside(path)
        except OSError as exc:
            _log.warning("%s could not be read or moved aside (%s); it is being treated as empty.", path, exc)
        return default
