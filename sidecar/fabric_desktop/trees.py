"""Deleting a folder and everything in it the way Windows lets it be deleted.

``shutil.rmtree`` stops at the first file Windows refuses, which on a person's PC is common:

* a read-only file (copied from a read-only share or a zip with ``shutil.copy2``, a Git object, a file
  a person marked read-only) can't be deleted until the attribute is cleared;
* a file a virus scanner, the search indexer or OneDrive has just opened to look at a new write is
  refused with a sharing violation (PermissionError, WinError 32 or 5) for a moment;
* a folder whose files were deleted while one of those still had them open is "not empty"
  (WinError 145) until the last handle closes, because Windows only marks such files for deletion.

``remove`` clears the read-only attribute and waits those out, for about as long as jsonfile.replace
waits out a busy file (about 1.5 s), then raises the error as rmtree would. A link or junction inside
the folder is removed as a link, never followed and never chmod-ed, so nothing outside the folder is
touched. Every ``shutil.rmtree`` that should delete or fail goes through here (tests/test_trees.py).
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import time
from typing import Any, Callable

from .jsonfile import REPLACE_ATTEMPTS, REPLACE_BACKOFF

# Windows' "The directory is not empty": a deleted file inside is still open somewhere.
_ERROR_DIR_NOT_EMPTY = 145
# The calls rmtree reports a failed delete with (os.unlink, os.remove, os.rmdir); a failed listing or
# stat (os.scandir, os.lstat, os.open) is raised as it is.
_DELETES = frozenset({"unlink", "remove", "rmdir"})


def remove(path: str | os.PathLike[str]) -> None:
    """Delete ``path``, a folder, and everything in it; raises OSError as shutil.rmtree does when
    something is still in the way after waiting."""
    shutil.rmtree(path, onexc=_clear_and_retry)


def _is_link(path: str) -> bool:
    return os.path.islink(path) or os.path.isjunction(path)


def _busy(exc: BaseException) -> bool:
    """Whether ``exc`` is Windows refusing for now: a read-only file, a file another program has open,
    or a folder whose deleted files are still open."""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, "winerror", None) == _ERROR_DIR_NOT_EMPTY or exc.errno == errno.ENOTEMPTY
    return False


def _clear_and_retry(func: Callable[..., Any], path: str, exc: BaseException) -> None:
    """rmtree's onexc: try ``func`` (os.unlink or os.rmdir) on ``path`` again, read-only cleared,
    waiting between tries; anything else, or the same refusal after about 1.5 s, is raised."""
    if getattr(func, "__name__", "") not in _DELETES or not _busy(exc):
        raise exc
    if not _is_link(path):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if os.path.isdir(path) else 0))
        except FileNotFoundError:
            return  # gone in the meantime: that's what was wanted
        except OSError:
            pass  # e.g. still open elsewhere; the retries below wait for that
    last: BaseException = exc
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            func(path)
            return
        except FileNotFoundError:
            return
        except OSError as again:
            if not _busy(again):
                raise
            last = again
        if attempt < REPLACE_ATTEMPTS - 1:
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))
    raise last


__all__ = ["remove"]
