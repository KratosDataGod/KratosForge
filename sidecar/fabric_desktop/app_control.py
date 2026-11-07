"""Find the files in a folder that Windows' application control won't let Python load.

The engine's own startup check does the same for the engine's packages (``updater.blocked_files``);
this covers what a person installs later, into a notebook environment. Under App Control for
Business (WDAC) or Smart App Control, an unsigned ``.pyd`` or ``.dll`` is refused when it's
loaded, so a package can install cleanly and still fail at ``import``.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

DONT_RESOLVE_DLL_REFERENCES = 0x1
# ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION (App Control for Business / Smart App Control) and
# ERROR_INVALID_IMAGE_HASH (an unsigned or tampered image under a signing policy).
BLOCKED_CODES = {4551, 577}
MAX_FILES = 5000  # a diagnosis only: an environment with more binaries than this is checked in part


def _load_blocked(path: str) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows-only
    kernel32.LoadLibraryExW.restype = ctypes.c_void_p
    kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
    # Mapped as data-and-code without running DllMain or loading its dependencies: enough for
    # the policy to decide, and nothing in the file runs.
    handle = kernel32.LoadLibraryExW(path, None, DONT_RESOLVE_DLL_REFERENCES)
    if handle:
        kernel32.FreeLibrary(handle)
        return False
    return ctypes.get_last_error() in BLOCKED_CODES  # type: ignore[attr-defined]  # Windows-only


def blocked_packages(folder: Path, probe: Callable[[str], bool] | None = None) -> dict[str, list[str]]:
    """The top-level packages in ``folder`` with a ``.pyd`` or ``.dll`` Windows refuses to load.

    Empty when nothing is blocked, off Windows, or if the check itself fails.
    """
    if sys.platform != "win32":
        return {}
    probe = probe or _load_blocked
    found: dict[str, list[str]] = {}
    try:
        seen = 0
        for file in sorted(folder.rglob("*")):
            if file.suffix.lower() not in (".pyd", ".dll") or not file.is_file():
                continue
            seen += 1
            if seen > MAX_FILES:
                break
            if probe(str(file)):
                rel = file.relative_to(folder)
                found.setdefault(rel.parts[0] if len(rel.parts) > 1 else rel.stem.split(".")[0], []).append(str(rel))
    except OSError:  # silent-ok: a diagnosis only, never a reason to fail the install
        return found
    return found


def blocked_line(blocked: dict[str, list[str]]) -> str | None:
    """One line for an install log that names the blocked packages and what to do."""
    if not blocked:
        return None
    names = ", ".join(sorted(blocked))
    return (f"Installed, but your organization's application control (App Control for Business or Smart App "
            f"Control) blocks files in: {names}. Importing them will fail. Ask IT to allow these files by hash, "
            "or to add the packages to your organization's package folder; the enterprise install guide lists "
            "the rules (Application control).")
