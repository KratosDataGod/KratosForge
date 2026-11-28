""".sql files on this PC for the SQL page (editor parity S38, Preview).

The page opens a ``.sql`` file the user picked in the Windows file dialog, and saves a tab back to it.
Only ``.sql`` files, given as a full path, are read or written; a link (a symbolic link, a junction)
is refused so a save can't land somewhere other than the file the user sees. A file keeps what it had:
its encoding (UTF-8, UTF-8 with a BOM, or UTF-16 with a BOM) and its line endings (CRLF or LF).

A save names the version it started from (``expect``). When the file changed on disk since then, the
save is refused with ``conflict`` and the page asks what to do, so another editor's change is never
written over without the user choosing it. A save swaps the whole file in at once (jsonfile.write_bytes):
a reader sees the old file or the new one, never half of it.
"""

from __future__ import annotations

import codecs
import os
import stat
from pathlib import Path
from typing import Any

from . import jsonfile

MAX_BYTES = 5 * 1024 * 1024
ENCODINGS = ("utf-8", "utf-8-sig", "utf-16")


class Conflict(ValueError):
    """The file changed on disk since the version the save started from."""


def _path(path: Any) -> Path:
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise ValueError("Give the full path of a .sql file.")
    p = Path(path.strip())
    if not p.is_absolute():
        raise ValueError("Give the full path of a .sql file.")
    if p.suffix.lower() != ".sql":
        raise ValueError("Only .sql files can be opened or saved here.")
    if _linked(p):
        raise ValueError(f"{p.name} is a link to another file. Open the file itself.")
    return p


def _linked(path: Path) -> bool:
    """A symbolic link, or on Windows any reparse point (a junction, a mount point). Missing: False."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:  # silent-ok: nothing there yet (a new file)
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0)
                                             & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _version(st: os.stat_result) -> str:
    return f"{st.st_mtime_ns}-{st.st_size}"


def info(path: str) -> dict[str, Any]:
    """Whether the file is there and its version, without reading it: the page asks this when it comes
    back into view, to say when the file changed on disk."""
    p = _path(path)
    try:
        st = p.stat()
    except FileNotFoundError:
        return {"path": str(p), "name": p.name, "exists": False}
    return {"path": str(p), "name": p.name, "exists": True, "version": _version(st), "bytes": st.st_size,
            "changed": st.st_mtime}


def read(path: str) -> dict[str, Any]:
    """The file's text, with its encoding, line endings and version for the save that follows."""
    p = _path(path)
    try:
        st = p.stat()
    except FileNotFoundError:
        raise ValueError(f"{p.name} isn't there any more.") from None
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{p.name} isn't a file.")
    if st.st_size > MAX_BYTES:
        raise ValueError(f"{p.name} is larger than {MAX_BYTES // (1024 * 1024)} MB, too large to open here.")
    data = p.read_bytes()
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8"
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        raise ValueError(f"{p.name} isn't UTF-8 or UTF-16 text. Save it as UTF-8 in another editor, "
                         "then open it again.") from None
    newline = "\r\n" if "\r\n" in text else "\n"
    return {"path": str(p), "name": p.name, "text": text.replace("\r\n", "\n"), "encoding": encoding,
            "newline": newline, "version": _version(st), "bytes": len(data)}


def write(path: str, text: str, expect: str = "", encoding: str = "utf-8", newline: str = "\r\n",
          overwrite: bool = False) -> dict[str, Any]:
    """Save ``text`` as the whole file. ``expect`` is the version the tab started from; a file that changed
    since is refused with Conflict. A new file (Save as) passes no ``expect``: one already there is only
    written over when the user said so in the dialog (``overwrite``)."""
    p = _path(path)
    if not isinstance(text, str):
        raise ValueError("text must be the query text.")
    if encoding not in ENCODINGS:
        raise ValueError(f"encoding must be one of {', '.join(ENCODINGS)}.")
    if newline not in ("\r\n", "\n"):
        raise ValueError("newline must be CRLF or LF.")
    data = text.replace("\r\n", "\n").replace("\n", newline).encode(encoding)
    if len(data) > MAX_BYTES:
        raise ValueError(f"The query is larger than {MAX_BYTES // (1024 * 1024)} MB, too large to save here.")
    if not p.parent.is_dir():
        raise ValueError(f"The folder for {p.name} isn't there any more.")
    now = info(str(p))
    if now["exists"]:
        if expect and now["version"] != expect:
            raise Conflict(f"{p.name} changed on disk since this tab opened it.")
        if not expect and not overwrite:
            raise Conflict(f"{p.name} is already there.")
    elif expect:
        raise Conflict(f"{p.name} isn't there any more.")
    jsonfile.write_bytes(p, data)
    return {**info(str(p)), "encoding": encoding, "newline": newline}
