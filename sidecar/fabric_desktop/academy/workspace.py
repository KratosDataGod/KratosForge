"""Academy exercise workspaces (ACD-004): reading what a learner made, as untrusted input.

An exercise workspace is ``<data>/academy/<pack>/<exercise>/``. The learner, or code the learner
ran, writes there, so everything a validator reads from it is treated as untrusted input to the
engine: every path stays inside the workspace, no part of it may be a link (a symbolic link, a
junction or another Windows name-surrogate reparse point), and files are size-capped before they
are read. Validators never import, run or ``eval`` anything from a workspace.

Grading material (reference solutions, hidden checks) lives in the pack's ``grading/`` folder in
the install and is never copied here.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb

from ..duckdb_rows import one_row

MAX_FILE_BYTES = 256 * 1024 * 1024   # one data file a validator reads
MAX_JSON_BYTES = 4 * 1024 * 1024     # a settings document, pipeline or Delta log entry
MAX_FILES = 20_000                   # files a validator lists under one folder
MAX_ROWS = 5_000_000                 # rows in one table a validator reads
MEMORY_LIMIT = "1GB"
# Windows reserved device names, with or without an extension or trailing dots and spaces (#1736).
_DEVICE = re.compile(r"(?i)(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]|CONIN\$|CONOUT\$)(\.[^/]*)?[. ]*\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

# Windows reparse tags with the name-surrogate bit point somewhere else: symbolic links, junctions,
# WSL links. Others (OneDrive placeholders) are files in place. Same rule as notebook_files_service.
_NAME_SURROGATE = 0x20000000
_WINDOWS = os.name == "nt"


class Refused(ValueError):
    """A path or file a validator won't read; the message says why, in words for the learner."""


def is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_reparse_tag", 0) & _NAME_SURROGATE)


def links(path: Path, st: os.stat_result) -> int:
    """How many names the file has. On Windows ``lstat`` and ``scandir`` don't fill in the link
    count, so it's read from an open handle (GetFileInformationByHandle, through ``fstat``); the
    handle must name the same file the ``lstat`` saw, or the file counts as linked."""
    if not _WINDOWS:
        return st.st_nlink
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise Refused(f"{path.name} can't be read: {exc.strerror}") from exc
    try:
        opened = os.fstat(fd)
    finally:
        os.close(fd)
    if (opened.st_ino, opened.st_dev) != (st.st_ino, st.st_dev) and st.st_ino:
        return 2   # swapped between the two looks: treat it as a link to somewhere else
    return opened.st_nlink


def is_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def workspace_dir(root: Path, pack: str, exercise: str) -> Path:
    """``<root>/academy/<pack>/<exercise>``; refuses anything that isn't a plain id."""
    if not is_id(pack) or not is_id(exercise):
        raise Refused("Not an Academy exercise.")
    base = (Path(root) / "academy").resolve()
    folder = (base / pack / exercise).resolve()
    if folder.parent.parent != base:
        raise Refused("Not an Academy exercise.")
    return folder


def _relative_parts(rel: str) -> list[str]:
    if not isinstance(rel, str) or not rel or "\x00" in rel or len(rel) > 400:
        raise Refused("Not a file name in the exercise.")
    text = rel.replace("\\", "/")
    if text.startswith("/") or re.match(r"[A-Za-z]:", text) or "://" in text:
        raise Refused(f"{rel} isn't inside the exercise folder.")
    parts = [p for p in PurePosixPath(text).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise Refused(f"{rel} isn't inside the exercise folder.")
    if any(_DEVICE.match(p) for p in parts):
        raise Refused(f"{rel} is a Windows device name, not a file.")
    return parts


def inside(workspace: Path, rel: str, must_exist: bool = True) -> Path:
    """The workspace path for ``rel``. Refused if it leaves the workspace or any part of it, the
    workspace itself included, is a link or junction."""
    here = Path(workspace)
    try:
        if is_link(os.lstat(here)) or not here.is_dir():
            raise Refused("The exercise folder isn't a plain folder.")
    except OSError as exc:
        raise Refused("The exercise folder isn't there; start the exercise again.") from exc
    for part in ([] if rel == "." else _relative_parts(rel)):   # "." is the workspace itself
        here = here / part
        try:
            st = os.lstat(here)
        except FileNotFoundError:
            if must_exist:
                raise Refused(f"{rel} isn't there.") from None
            return here
        except OSError as exc:
            raise Refused(f"{rel} can't be read: {exc.strerror}") from exc
        if is_link(st):
            raise Refused(f"{rel} is a link to somewhere else; the check reads only files in the exercise folder.")
        if stat.S_ISREG(st.st_mode) and links(here, st) > 1:
            raise Refused(f"{rel} is a hard link to a file that may be outside the exercise folder; "
                          "save a copy of the file instead.")
    return here


def read_bytes(workspace: Path, rel: str, limit: int = MAX_FILE_BYTES) -> bytes:
    path = inside(workspace, rel)
    if not path.is_file():
        raise Refused(f"{rel} isn't a file.")
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise Refused(f"{rel} is bigger than {limit // (1024 * 1024)} MB; an exercise answer is smaller.")
    return data


def read_json(workspace: Path, rel: str) -> Any:
    raw = read_bytes(workspace, rel, MAX_JSON_BYTES)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refused(f"{rel} isn't valid JSON (line {getattr(exc, 'lineno', '?')}).") from exc
    except RecursionError as exc:
        raise Refused(f"{rel} is nested too deeply to be a settings document.") from exc


def files_under(workspace: Path, rel: str) -> list[str]:
    """Files under ``rel`` as workspace-relative names, never following a link; refused past MAX_FILES."""
    top = inside(workspace, rel)
    if not top.is_dir():
        raise Refused(f"{rel} isn't a folder.")
    out: list[str] = []
    stack = [top]
    while stack:
        folder = stack.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                st = entry.stat(follow_symlinks=False)
                if is_link(st):
                    raise Refused(f"{Path(entry.path).relative_to(workspace).as_posix()} is a link; "
                                  "the check reads only files in the exercise folder.")
                if stat.S_ISDIR(st.st_mode):
                    stack.append(Path(entry.path))
                elif stat.S_ISREG(st.st_mode):
                    if links(Path(entry.path), st) > 1:
                        raise Refused(f"{Path(entry.path).relative_to(workspace).as_posix()} is a hard link; "
                                      "save a copy of the file instead.")
                    out.append(Path(entry.path).relative_to(workspace).as_posix())
                    if len(out) > MAX_FILES:
                        raise Refused(f"{rel} has more than {MAX_FILES:,} files; an exercise answer has fewer.")
    return sorted(out)


def connect(workspace: Path) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB for validators: it can read files in the workspace only, has no
    network, a memory limit, and settings nobody can change afterwards."""
    con = duckdb.connect()
    con.execute("SET allowed_directories = ?", [[str(Path(workspace).resolve())]])
    con.execute("SET enable_external_access = false")
    con.execute(f"SET memory_limit = '{MEMORY_LIMIT}'")
    con.execute("SET lock_configuration = true")
    return con


_READERS = {"parquet": "read_parquet", "csv": "read_csv", "json": "read_json"}


def load_table(con: duckdb.DuckDBPyConnection, workspace: Path, rel: str, name: str, fmt: str = "parquet",
         max_rows: int = MAX_ROWS) -> int:
    """Load a learner's file (or folder of parquet files) as temp table ``name`` on a ``connect``
    connection: read by DuckDB under its memory limit and at most ``max_rows + 1`` rows, never
    read whole into Python first. Returns the row count; refused past ``max_rows``."""
    if fmt not in _READERS or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
        raise Refused("Not a table the check can read.")
    path = inside(workspace, rel)
    if path.is_dir():
        files = [f for f in files_under(workspace, rel) if f.endswith(f".{fmt}")]
        if not files:
            raise Refused(f"{rel} has no .{fmt} files.")
        target: Any = [str(Path(workspace) / f) for f in files]
    else:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise Refused(f"{rel} isn't a file.")
        target = str(path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise Refused(f"{rel} is bigger than {MAX_FILE_BYTES // (1024 * 1024)} MB; an exercise answer is smaller.")
    # the SQL text is fixed; the path list is a bound value, so a file name can't change the SQL
    con.execute(f'CREATE OR REPLACE TEMP TABLE "{name}" AS SELECT * FROM {_READERS[fmt]}(?) LIMIT ?',
                [target, max_rows + 1])
    rows = int(one_row(con.execute(f'SELECT count(*) FROM "{name}"'))[0])
    if rows > max_rows:
        con.execute(f'DROP TABLE "{name}"')
        raise Refused(f"{rel} has more than {max_rows:,} rows; an exercise answer has fewer.")
    return rows
