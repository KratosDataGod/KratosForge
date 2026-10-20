"""Shared JSON files (settings, schedules) written so a crash or a second writer never leaves half a file.

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
import tempfile
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# As retrying.local_retry waits out the Windows Delta-log race: 0.05 s doubling, about 1.5 s in all,
# long enough for a virus scanner or indexer that has the file open.
_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF = 0.05


def write_json(path: Path, data: Any, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=indent))
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: a reader has the file open right now
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF * (2 ** attempt))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_aside(path: Path) -> Path:
    """Move an unreadable file to ``<name>.corrupt-<timestamp>`` and say so. Returns where it went."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    os.replace(path, dest)
    _log.warning("%s could not be read and was moved to %s; it is being started again empty.", path, dest.name)
    return dest


def read_json(path: Path, default: Any) -> Any:
    """The parsed file; ``default`` when it is missing or unreadable (an unreadable one is set aside first)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError:  # torn or not JSON (UnicodeDecodeError is a ValueError too)
        try:
            set_aside(path)
        except OSError as exc:
            _log.warning("%s could not be read or moved aside (%s); it is being treated as empty.", path, exc)
        return default
