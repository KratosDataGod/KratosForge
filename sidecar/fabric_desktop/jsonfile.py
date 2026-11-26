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
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import file_locks, win_paths

_log = logging.getLogger(__name__)

# As retrying.local_retry waits out the Windows Delta-log race: 0.05 s doubling, about 1.5 s in all,
# long enough for a virus scanner or indexer that has the file open. Other waits on a busy local file
# (auth.SharedTokenCache) use the same.
REPLACE_ATTEMPTS = 6
REPLACE_BACKOFF = 0.05
# Set-aside copies kept per file: the newest ones, so a file damaged again and again can't fill the disk.
KEEP_SET_ASIDE = 5


def damaged(message: str) -> ValueError:
    """The ValueError for a file on disk that can't be read as what it should be (cut off, not JSON,
    the wrong shape). It is a plain ValueError, so the app shows and logs it as before; it is marked
    ``damaged_file`` so the local API and MCP tools answer ``failed`` for it (the request was right,
    the file is what's wrong), where any other ValueError is ``invalid_input``."""
    exc = ValueError(message)
    exc.damaged_file = True  # type: ignore[attr-defined]
    return exc


def write_json(path: Path, data: Any, indent: int | None = 2) -> None:
    write_text(path, json.dumps(data, indent=indent))


def write_text(path: Path, text: str, newline: str | None = None) -> None:
    """Swap ``text`` in as the whole file: readers see the old file or the new one, never half of it.
    ``newline`` is open()'s: None writes this OS's line endings, "\n" keeps them as LF. A path past
    Windows' limit on a PC without long paths is refused by name (path_too_long)."""
    with long_path_named(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = _new_temp(path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def write_bytes(path: Path, data: bytes) -> None:
    """``write_text`` for bytes (a profile picture): the old file or the new one, never half of it."""
    with long_path_named(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = _new_temp(path)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def path_too_long(exc: BaseException, path: str | os.PathLike[str], doing: str = "saved") -> OSError | None:
    """The error to raise in place of ``exc`` when writing ``path`` failed because a path is past
    Windows' 260 characters on a PC where long paths are turned off: its text names the file, the
    length and the setting (win_paths.too_long), and it keeps errno, winerror and the path. None for
    any other failure, and off Windows."""
    if not isinstance(exc, OSError):
        return None
    from .support_bundle import long_paths_enabled  # here: support_bundle imports much of the package
    return file_locks.plain_error(exc, path, win_paths.too_long(exc, path, doing, long_paths_enabled()))


@contextmanager
def long_path_named(path: str | os.PathLike[str], doing: str = "saved") -> Iterator[None]:
    """Raise ``path_too_long``'s error in place of an OSError from the block that it explains."""
    try:
        yield
    except OSError as exc:
        refused = path_too_long(exc, path, doing)
        if refused is None:
            raise
        raise refused from exc


def _new_temp(path: Path) -> tuple[int, str]:
    """A new temporary file beside ``path``; a folder Windows won't let the app write in (Controlled
    folder access, a folder the account can't change) is refused with a message naming it."""
    try:
        return tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    except OSError as exc:
        refused = file_locks.folder_error(exc, path)
        if refused is None:
            raise
        raise refused from exc


def replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """``os.replace``, waiting out Windows refusing it (PermissionError, WinError 5) while a reader, a
    virus scanner or an indexer has either file open: about 1.5 s, then the error. Every swap of a
    finished temporary file into place goes through here (tests/test_file_replace.py). When a program
    keeps the file open past that, the error's text names the file and, where Windows says, the
    program (file_locks.explain)."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if not isinstance(exc, PermissionError) or attempt == REPLACE_ATTEMPTS - 1:
                # Still refused (a program keeps the file open, or it is read-only), or Windows Security
                # stopped it: say so, naming the file and, where Windows can tell, the program.
                held = file_locks.locked_error(exc, dst)
                if held is None:
                    raise
                raise held from exc
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))


def unlink(path: str | os.PathLike[str]) -> None:
    """Remove ``path`` if it is there, waiting out Windows refusing it (PermissionError) while a virus
    scanner or the indexer has it open, as ``replace`` does; still refused after that, the error."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            Path(path).unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))


def set_aside(path: Path, then: str = "it is being started again empty", keep: bool = False) -> Path:
    """Move an unreadable file to ``<name>.corrupt-<timestamp>`` and say so (``then``: what happens next).

    ``keep`` copies it there instead, leaving the original in place until the caller replaces it."""
    dest = path.with_name(f"{path.name}.corrupt-{_next_stamp(path):%Y%m%dT%H%M%S%fZ}")
    if keep:
        shutil.copy2(path, dest)
    else:
        os.replace(path, dest)
    _log.warning("%s could not be read and was %s to %s; %s.", path, "copied" if keep else "moved", dest.name, then)
    _prune_set_aside(path, dest)
    return dest


def _next_stamp(path: Path) -> dt.datetime:
    """The time for a new set-aside copy of ``path``: now, or just after its newest copy when that is
    already at or past now. Windows' clock can give two set-asides the same time, and a copy with the
    same name would replace the one kept before it; a copy stamped before the newest would be the first
    one pruned. Stamps that aren't a date, or are more than a day ahead (renamed by hand, not ours),
    are left out of the ordering."""
    now = dt.datetime.now(dt.timezone.utc)
    own = re.compile(re.escape(path.name) + r"\.corrupt-(\d{8}T\d{12})Z")
    newest: dt.datetime | None = None
    for p in path.parent.glob(f"{path.name}.corrupt-*"):
        m = own.fullmatch(p.name)
        if m is None:
            continue
        try:
            when = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S%f").replace(tzinfo=dt.timezone.utc)
        except ValueError:  # digits that aren't a date
            _log.info("Ignoring %s when naming the next set-aside copy: its stamp isn't a date.", p.name)
            continue
        if when - now > _SET_ASIDE_AHEAD:
            _log.info("Ignoring %s when naming the next set-aside copy: its stamp is in the future.", p.name)
            continue
        if newest is None or when > newest:
            newest = when
    if newest is not None and newest >= now:
        return newest + dt.timedelta(microseconds=1)
    return now


_SET_ASIDE_AHEAD = dt.timedelta(days=1)  # a clock stepped back by more than this restarts the ordering


def _prune_set_aside(path: Path, dest: Path) -> None:
    """Remove all but the KEEP_SET_ASIDE newest ``<name>.corrupt-<timestamp>`` copies of ``path``, each
    with its -wal and -shm. ``dest``, the copy just made, is always kept, even if the clock stepped back
    and its stamp sorts first; a copy renamed by hand doesn't match the pattern and is never removed."""
    own = re.compile(re.escape(path.name) + r"\.corrupt-\d{8}T\d{12}Z")
    copies = sorted(p for p in path.parent.glob(f"{path.name}.corrupt-*") if own.fullmatch(p.name) and p != dest)
    for old in copies[:-(KEEP_SET_ASIDE - 1) or None]:
        for victim in (old, old.with_name(old.name + "-wal"), old.with_name(old.name + "-shm")):
            try:
                victim.unlink(missing_ok=True)
            except OSError as exc:  # in use or read-only: it stays, and the next set-aside tries again
                _log.info("Couldn't remove the old set-aside copy %s: %s", victim, exc)


def read_text(path: Path) -> str:
    """The whole file. On Windows, opening it just as a writer swaps in a new copy (write_text) fails with
    PermissionError for a moment, so that is waited out the same way the writer waits out a reader."""
    for attempt in range(REPLACE_ATTEMPTS - 1):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))
    return path.read_text(encoding="utf-8")


def read_bytes(path: str | os.PathLike[str], limit: int | None = None) -> bytes:
    """The file's bytes (only its first ``limit`` with one), to copy or upload. On Windows a virus scanner,
    the indexer or OneDrive can hold a file just written so that opening it fails for a moment (a sharing
    violation): that is waited out as ``read_text`` does. Still refused after that, the error names the
    file and, where Windows says, the program (file_locks)."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            with open(path, "rb") as f:
                return f.read() if limit is None else f.read(limit)
        except PermissionError as exc:
            if attempt == REPLACE_ATTEMPTS - 1:
                held = file_locks.locked_error(exc, path, "read")
                if held is None:
                    raise
                raise held from exc
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))
    raise AssertionError("unreachable")


def read_json(path: Path, default: Any) -> Any:
    """The parsed file; ``default`` when it is missing or unreadable (an unreadable one is set aside first)."""
    try:
        return json.loads(read_text(path))
    except FileNotFoundError:
        return default
    except (ValueError, RecursionError):  # torn or not JSON (UnicodeDecodeError is a ValueError too), or nested too deep
        try:
            set_aside(path)
        except OSError as exc:
            _log.warning("%s could not be read or moved aside (%s); it is being treated as empty.", path, exc)
        return default


def read_object(path: Path) -> dict[str, Any]:
    """``read_json`` for a file that holds one JSON object (a settings file): {} when it is missing or
    unreadable, and also when it parsed as something else (a list, a number), which is set aside first
    like an unreadable one, so the next save doesn't silently replace what was in it."""
    data = read_json(path, {})
    if isinstance(data, dict):
        return data
    try:
        set_aside(path)
    except OSError as exc:
        _log.warning("%s isn't one JSON object and could not be moved aside (%s); it is being treated as empty.",
                     path, exc)
    return {}
