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
