"""A full disk, told apart from other failures and said plainly.

delta-rs reports a full disk as a parse or copy error ("Failed to parse parquet: ... Unable to copy
data to file: No space left on device (os error 28)") and DuckDB reports a full temp drive as running
out of memory ("failed to offload data block ... max_temp_directory_size"). Neither tells the user
what to do. The helpers here find the real reason anywhere in an exception (its errno, its Windows
error code, its text, or the exceptions it was raised from) and give a message that says the disk
is full, how much is free, and that freeing space and running again is all it takes.

A leaf module: it imports nothing from the package.
"""

from __future__ import annotations

import errno
import shutil
from pathlib import Path

# Windows: ERROR_HANDLE_DISK_FULL (39) and ERROR_DISK_FULL (112), "There is not enough space on the disk".
_WINERRORS = frozenset({39, 112})
_TEXTS = ("no space left on device", "(os error 28)", "(os error 39)", "(os error 112)",
          "not enough space on the disk", "disk is full")
# DuckDB's message when a query's temp files can't grow (it reports it as running out of memory).
_SPILL = "failed to offload data block"


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and cur not in seen and len(seen) < 20:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__
    return seen


def is_disk_full(exc: BaseException) -> bool:
    """True when ``exc``, or an exception it was raised from, is a full disk."""
    for e in _chain(exc):
        if isinstance(e, OSError) and (e.errno == errno.ENOSPC or getattr(e, "winerror", None) in _WINERRORS):
            return True
        if any(t in str(e).lower() for t in _TEXTS):
            return True
    return False


def is_spill_full(text: str) -> bool:
    """True for DuckDB's error when a query's temp files can't grow."""
    return _SPILL in text.lower()


def free_space(path: str | Path | None) -> str:
    """The free space on ``path``'s drive, as "C:\\ has 12.0 MB free" (on Windows) or "12.0 MB free",
    or "" when it can't be read."""
    if not path:
        return ""
    p = Path(path)
    for candidate in (p, *p.parents):  # the folder may not exist yet (a new table)
        try:
            free = shutil.disk_usage(candidate).free
        except OSError:
            continue
        # A Windows drive by its letter; elsewhere the mount point's name says little, so none.
        return f"{candidate.drive}\\ has {_size(free)} free" if candidate.drive else f"{_size(free)} free"
    return ""


def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"  # pragma: no cover - the loop always returns


def message(path: str | Path | None, unchanged: str) -> str:
    """The message for a full disk. ``unchanged`` says what was left as it was (for example "The table
    was not changed."); the reason the user can act on comes first."""
    where = free_space(path)
    return " ".join(part for part in (f"The disk is full{f' ({where})' if where else ''}.", unchanged,
                                      "Free some space and run it again.") if part)


def spill_message(temp_dir: str | Path | None) -> str:
    """The SQL page's message when a query's temp files can't grow: by default DuckDB lets them
    use most of the free space on the temp folder's drive, so that drive is full."""
    where = free_space(temp_dir)
    return (f"Stopped: the query ran out of room for its temp files, as the temp folder's drive is full"
            f"{f' ({where})' if where else ''}. Nothing was saved. "
            "Free some space, or narrow the query, and run it again.")


# Windows: ERROR_ACCESS_DENIED (5) and ERROR_WRITE_PROTECT (19), the second for a read-only disk.
_DENIED_WINERRORS = frozenset({5, 19})


def is_unwritable(exc: BaseException) -> bool:
    """True when ``exc``, or an exception it was raised from, is a folder Windows won't let this
    account write: access denied, or a read-only disk (a profile disk that FSLogix or a roaming
    profile attached read-only)."""
    for e in _chain(exc):
        if isinstance(e, OSError) and (e.errno in (errno.EACCES, errno.EPERM, errno.EROFS)
                                       or getattr(e, "winerror", None) in _DENIED_WINERRORS):
            return True
    return False


def _inside(name: object, folder: Path) -> bool:
    if not isinstance(name, (str, Path)) or not name:
        return False
    try:
        Path(name).resolve().relative_to(folder.resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def data_folder_message(folder: str | Path, exc: BaseException, outcome: str = "so this wasn't saved") -> str | None:
    """The message for a failure to write the app's own data folder, or None when ``exc`` is
    something else. A full disk anywhere counts; a refusal counts only when it names a file in
    ``folder``, since a refused export or OneLake call is that place's, not the profile's. ``outcome``
    says what that meant ("so this wasn't saved")."""
    folder = Path(folder)
    if any("the disk is full" in str(e).lower() for e in _chain(exc)):
        return None  # the caller already said so, in its own words
    if is_disk_full(exc):
        name = next((getattr(e, "filename", None) for e in _chain(exc) if getattr(e, "filename", None)), None)
        where = free_space(name if isinstance(name, (str, Path)) else folder)
        return (f"The disk is full{f' ({where})' if where else ''}, {outcome}. "
                "Free some space and try again. On a shared or virtual desktop, ask IT to make your profile disk bigger.")
    if is_unwritable(exc) and any(_inside(getattr(e, "filename", None), folder) for e in _chain(exc)):
        return (f"Windows won't let KratosForge write to its data folder ({folder}), {outcome}. "
                "On a shared or virtual desktop the profile disk may be attached read-only; sign out and in again, "
                "or ask IT to check it.")
    return None
