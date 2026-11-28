"""SQLite store files the app keeps on its own disk: one way to open them, and what to do with one
SQLite can't read (EA-028).

``connect`` is a connection for one read or change, closed after it: one kept per thread was never
closed when its thread ended, and on Windows an open handle keeps the file locked. ``transaction`` is
the same for a group of changes that land together or not at all. Every store opens its file through
one of the two (tests/test_sqlite_durability.py fails on a ``sqlite3.connect`` anywhere else).

Both set ``synchronous=full``: a commit returns only once its row is on the disk, so a job that says
succeeded, an approval, a key or a notice marked sent is still there after a power cut. SQLite's own
default depends on how it was built (WAL files may default to ``normal``, which can lose the last
commits), so the stores don't rely on it. docs/quality/sqlite-stores.md lists each store and what it holds.

``open_store`` runs a store's first open (its schema, its pragmas) after ``check`` has read the whole
file. When the file is damaged or isn't a database it is moved aside to ``<name>.corrupt-<time>``,
with its -wal and -shm, and the store starts empty, or, for a store whose rows the person can't get
back by starting again, the open is refused with a message that says what to do. Each store says
which in a comment where it opens. A busy or locked file (OperationalError) is never moved.

``busy_message`` is what the app says when a call gives up waiting for a SQLite file another
connection holds: SQLite's own "database is locked" says nothing about why or what to do.
"""

from __future__ import annotations

import errno
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from . import jsonfile


def _open(path: Path, timeout: float, autocommit: bool) -> sqlite3.Connection:
    db = (sqlite3.connect(path, timeout=timeout, isolation_level=None) if autocommit
          else sqlite3.connect(path, timeout=timeout))
    try:
        db.execute("pragma synchronous=full")  # outside any transaction, which is where SQLite applies it
    except BaseException:
        db.close()
        raise
    return db


@contextmanager
def connect(path: Path, timeout: float = 30) -> Iterator[sqlite3.Connection]:
    """An autocommit connection with rows by name, closed after the block."""
    db = _open(path, timeout, autocommit=True)
    try:
        db.row_factory = sqlite3.Row
        yield db
    finally:
        db.close()


@contextmanager
def transaction(path: Path, timeout: float = 30, *, rows: bool = False) -> Iterator[sqlite3.Connection]:
    """A connection whose changes commit when the block ends and roll back when it raises; closed
    either way. ``with sqlite3.connect(...)`` alone commits or rolls back but leaves the connection
    open. ``rows``: rows by name (sqlite3.Row) instead of tuples."""
    db = _open(path, timeout, autocommit=False)
    try:
        if rows:
            db.row_factory = sqlite3.Row
        with db:
            yield db
    finally:
        db.close()


def busy_message(exc: BaseException) -> str | None:
    """The message for a call that gave up waiting on a SQLite file another connection held (SQLITE_BUSY,
    worded "database is locked"), or None when ``exc`` is something else."""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, sqlite3.OperationalError) and str(e).strip().lower() == "database is locked":
            return ("A database file stayed busy with another change for too long, so this didn't finish. "
                    "Try again in a moment. If it keeps happening, a scheduled run or the agent may "
                    "be in the middle of a long change, or antivirus or backup software may be holding the file.")
        e = e.__cause__ or e.__context__
    return None


def check(db: sqlite3.Connection) -> None:
    """Read every page now: a damaged file can open and fail only later, on a page a query reaches."""
    result = db.execute("pragma quick_check").fetchone()[0]
    if result != "ok":
        raise sqlite3.DatabaseError(f"quick_check: {result}")


def set_aside(path: Path, then: str) -> Path:
    """Move ``path`` and its -wal and -shm to ``<name>.corrupt-<time>`` and log ``then`` (what happens next)."""
    from .jsonfile import replace
    from .jsonfile import set_aside as move
    dest = move(path, then)
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            replace(side, dest.with_name(dest.name + suffix))
    return dest


def open_store(path: Path, first_open: Callable[[], None], *, then: str | None = None,
               refuse: str | None = None) -> Path | None:
    """Run ``first_open``. When SQLite can't read the file: with ``then``, move it aside, log ``then``
    and open again (empty), returning where it went; with ``refuse``, raise ValueError(refuse).
    Returns None when the file opened as it was."""
    if (then is None) == (refuse is None):
        raise TypeError("give open_store one of then= or refuse=")
    with too_long_named(path):  # its folder past Windows' 260 without long paths: named
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with too_long_named(path):
            first_open()
        return None
    except sqlite3.DatabaseError as exc:
        if isinstance(exc, sqlite3.OperationalError) or not path.exists():
            raise  # busy or locked, or no disk: not a damaged file, so nothing is moved
        if refuse is not None:
            raise ValueError(refuse.format(path=path, reason=exc)) from None
        from .redact import describe
        dest = set_aside(path, f"{then} ({describe(exc)})")
    with too_long_named(path):
        first_open()
    return dest


@contextmanager
def too_long_named(path: Path, doing: str = "opened") -> Iterator[None]:
    """A SQLite file whose path is past Windows' 260 characters on a PC where long paths are turned
    off: SQLite says only "unable to open database file", and Windows "cannot find the path". Either
    is raised as jsonfile.path_too_long's error, naming the file and the setting, with the original
    as its cause. Any other failure, or a path within the limit, is raised as it is."""
    try:
        yield
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc):
            raise
        # SQLite gives no OS error: ask as Windows would have answered (WinError 3, the path not found).
        probe = FileNotFoundError(errno.ENOENT, str(exc), os.fspath(path))
        probe.winerror = 3  # type: ignore[attr-defined]  # Windows' errors carry it; this one stands in
        refused = jsonfile.path_too_long(probe, path, doing)
        if refused is None:
            raise
        raise refused from exc
    except OSError as exc:
        refused = jsonfile.path_too_long(exc, path, doing)
        if refused is None:
            raise
        raise refused from exc
