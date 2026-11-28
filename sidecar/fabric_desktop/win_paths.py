"""Telling a network share from a folder on this PC in a Windows path, whatever form the path is in.

A Windows path can name the same folder several ways: ``C:\\Users\\ann\\proj``, the long-path form
``\\\\?\\C:\\Users\\ann\\proj`` (which a path past 260 characters needs), a share as
``\\\\server\\share\\proj`` or ``\\\\?\\UNC\\server\\share\\proj``, and a mapped drive ``Z:\\proj`` that is
that share. Checking for a leading ``\\\\`` alone takes the long-path form of a local folder for a share;
``is_share`` doesn't. And ``Path.resolve`` on Windows turns a mapped drive into the share's
``\\\\server\\share`` path, so a project made on ``Z:`` would be kept, and shown, under a path the person
never typed; ``keep_drive_letter`` keeps the drive letter they used.

Pure string logic (``ntpath``), so it behaves the same on every OS and is tested off Windows. A leaf
module: it imports nothing from the package.
"""

from __future__ import annotations

import ntpath
import os


def plain(text: str) -> str:
    """``text`` without the long-path or device prefix: ``\\\\?\\C:\\x`` is ``C:\\x``, and
    ``\\\\?\\UNC\\server\\share`` is ``\\\\server\\share``. Any other path comes back as it was."""
    for prefix in ("\\\\?\\", "\\\\.\\", "//?/", "//./"):
        if text.startswith(prefix):
            rest = text[len(prefix):]
            if rest[:4].upper() in ("UNC\\", "UNC/"):
                return "\\\\" + rest[4:]
            return rest
    return text


def is_share(text: str) -> bool:
    """Whether ``text`` names a folder on a network share by its ``\\\\server\\share`` path, in any form.
    A drive letter, mapped or not, is not: whether it is mapped is Windows' to say (readiness.py asks)."""
    return plain(text).startswith(("\\\\", "//"))


def keep_drive_letter(given: str, resolved: str) -> str:
    """The folder to keep for a project the person gave as ``given``, once resolved to ``resolved``: the
    resolved path, unless resolving turned a drive letter into a share path (a mapped drive), in which
    case the drive-letter form of ``given`` (made absolute, ``..`` removed), as they typed it."""
    drive = ntpath.splitdrive(plain(given))[0]
    if len(drive) == 2 and drive[1] == ":" and is_share(resolved):
        return ntpath.abspath(plain(given)) if ntpath.isabs(plain(given)) else resolved
    return resolved


# Windows' limit on a path, unless long paths are turned on for the PC (LongPathsEnabled).
PATH_LIMIT = 260
# ERROR_PATH_NOT_FOUND and ERROR_FILENAME_EXCED_RANGE: what Windows answers for a path past the limit.
_TOO_LONG_WINERRORS = frozenset({3, 206})
_ENAMETOOLONG = 36


def too_long(exc: BaseException, path: str | os.PathLike[str], doing: str = "saved",
             long_paths: bool | None = None) -> str | None:
    """Words for the person when writing ``path`` failed because its path, or that of the file Windows
    was asked for (``exc.filename``, such as a temporary file beside it), is past Windows' 260
    characters on a PC where long paths are turned off (``long_paths`` False; None off Windows or when
    it isn't known). Windows then says only "The system cannot find the path specified", which sends
    the person looking for a folder that is there. None for any other failure."""
    if long_paths is not False or not isinstance(exc, OSError):
        return None
    if getattr(exc, "winerror", None) not in _TOO_LONG_WINERRORS and exc.errno != _ENAMETOOLONG:
        return None
    named = [p for p in (path, exc.filename, exc.filename2) if isinstance(p, (str, os.PathLike))]
    longest = max(len(os.path.abspath(os.fspath(p))) for p in named)
    if longest < PATH_LIMIT:
        return None
    name = ntpath.basename(os.fspath(path)) or os.fspath(path)  # either separator, as Windows takes
    return (f"{name} couldn't be {doing}, because its path is {longest} characters, past the {PATH_LIMIT} Windows "
            "allows on this PC while long paths are turned off. Move it to a folder with a shorter path, or ask IT to "
            "turn on long paths.")
