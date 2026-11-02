"""SQLite store files the app keeps on its own disk: one way to open them, and what to do with one
SQLite can't read (EA-028).

``connect`` is a connection for one read or change, closed after it: one kept per thread was never
closed when its thread ended, and on Windows an open handle keeps the file locked.

``open_store`` runs a store's first open (its schema, its pragmas) after ``check`` has read the whole
file. When the file is damaged or isn't a database it is moved aside to ``<name>.corrupt-<time>``,
with its -wal and -shm, and the store starts empty, or, for a store whose rows the person can't get
back by starting again, the open is refused with a message that says what to do. Each store says
which in a comment where it opens. A busy or locked file (OperationalError) is never moved.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


@contextmanager
def connect(path: Path, timeout: float = 30) -> Iterator[sqlite3.Connection]:
    """An autocommit connection with rows by name, closed after the block."""
    db = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        yield db
    finally:
        db.close()


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
    try:
        first_open()
        return None
    except sqlite3.DatabaseError as exc:
        if isinstance(exc, sqlite3.OperationalError) or not path.exists():
            raise  # busy or locked, or no disk: not a damaged file, so nothing is moved
        if refuse is not None:
            raise ValueError(refuse.format(path=path, reason=exc)) from None
        from .redact import describe
        dest = set_aside(path, f"{then} ({describe(exc)})")
    first_open()
    return dest
