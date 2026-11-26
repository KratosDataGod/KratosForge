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
# A new folder's limit: 260 less room for an 8.3 file name in it (CreateDirectoryW).
FOLDER_LIMIT = 248
_ENAMETOOLONG = 36


def too_long(exc: BaseException, path: str | os.PathLike[str], doing: str = "saved",
             long_paths: bool | None = None) -> str | None:
    """Words for the person when writing ``path`` failed because its path, or that of the file Windows
    was asked for (``exc.filename``, such as a temporary file beside it), is past Windows' 260
    characters on a PC where long paths are turned off (``long_paths`` False; None off Windows or when
    it isn't known). Windows then says only "The system cannot find the path specified", which sends
    the person looking for a folder that is there. A new folder Windows was asked to make is refused from
    248 characters (FOLDER_LIMIT), so one of 248 to 259 is named too, when it is surely why
    (_new_folder_past_limit). None for any other failure."""
    if long_paths is not False or not isinstance(exc, OSError):
        return None
    if getattr(exc, "winerror", None) not in _TOO_LONG_WINERRORS and exc.errno != _ENAMETOOLONG:
        return None
    named = [p for p in (path, exc.filename, exc.filename2) if isinstance(p, (str, os.PathLike))]
    longest = max(len(os.path.abspath(os.fspath(p))) for p in named)
    name = ntpath.basename(os.fspath(path)) or os.fspath(path)  # either separator, as Windows takes
    if longest < PATH_LIMIT:
        folder = _new_folder_past_limit(exc, path)
        if folder is None:
            return None
        where = ("its path" if folder == os.path.abspath(os.fspath(path))
                 else f"the folder {ntpath.basename(folder)} it goes in would have a path that")
        return (f"{name} couldn't be {doing}, because {where} is {len(folder)} characters, past the {FOLDER_LIMIT} "
                "Windows allows for a new folder on this PC while long paths are turned off. Move it to a folder with "
                "a shorter path, or ask IT to turn on long paths.")
    return _past_limit(name, longest, doing)


def _past_limit(name: str, length: int, doing: str) -> str:
    return (f"{name} couldn't be {doing}, because its path is {length} characters, past the {PATH_LIMIT} Windows "
            "allows on this PC while long paths are turned off. Move it to a folder with a shorter path, or ask IT to "
            "turn on long paths.")


def read_too_long(paths: list[str], long_paths: bool | None, doing: str = "read") -> str | None:
    """Words for the person when reading failed and one of ``paths`` (a data file, or a folder of them) is
    past Windows' 260 characters on a PC where long paths are turned off (``long_paths`` False). Reading
    then fails however the reader puts it: DuckDB's "No files found that match the pattern", Windows'
    "The system cannot find the path specified". None when no path is past the limit, or when long paths
    are on or it isn't known."""
    if long_paths is not False:
        return None
    for path in paths:
        if (length := len(os.path.abspath(path))) >= PATH_LIMIT:
            return _past_limit(ntpath.basename(path) or path, length, doing)
    return None


def _new_folder_past_limit(exc: OSError, path: str | os.PathLike[str]) -> str | None:
    """The folder Windows was asked to make (``exc.filename``) when that is what refused ``path``: a folder
    ``path`` is in, or ``path`` itself, between 248 and 259 characters long, not there yet, in a folder that
    is. Only then is the length surely why: a longer path is named as any other, and a shorter one, or one
    whose own folder is missing, is a folder that really isn't there."""
    if not isinstance(exc.filename, (str, os.PathLike)):
        return None
    folder = os.path.abspath(os.fspath(exc.filename))
    target = os.path.abspath(os.fspath(path))
    if not FOLDER_LIMIT <= len(folder) < PATH_LIMIT or not (target == folder or target.startswith(folder + os.sep)):
        return None
    if os.path.exists(folder) or not os.path.isdir(os.path.dirname(folder)):
        return None
    return folder
