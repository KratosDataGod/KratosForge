"""Find the files in a folder that Windows' application control won't let Python load.

The engine's own startup check does the same for the engine's packages (``updater.blocked_files``);
this covers what a person installs later, into a notebook environment. Under App Control for
Business (WDAC) or Smart App Control, an unsigned ``.pyd`` or ``.dll`` is refused when it's
loaded, so a package can install cleanly and still fail at ``import``.
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

DONT_RESOLVE_DLL_REFERENCES = 0x1
# ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION (App Control for Business / Smart App Control) and
# ERROR_INVALID_IMAGE_HASH (an unsigned or tampered image under a signing policy).
BLOCKED_CODES = {4551, 577}
# ERROR_ACCESS_DISABLED_BY_POLICY: AppLocker (or Software Restriction Policies) refused to start a
# program. Together with the two above, what Windows answers when application control blocks a file.
APPLOCKER_CODE = 1260
POLICY_CODES = {APPLOCKER_CODE} | BLOCKED_CODES
# The same, as Windows words them when Python passes on only the text (an import's "DLL load failed").
_POLICY_TEXT = ("blocked by group policy", "application control policy has blocked",
                "windows cannot verify the digital signature for this file")
_IMPORTING = re.compile(r"DLL load failed while importing ([A-Za-z0-9_.]+)")
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
    seen = 0
    try:
        # os.walk without following links, so a junction loop can't hold the install, and the cap
        # applies while walking rather than after listing the whole tree.
        for root, dirs, files in os.walk(folder, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                if Path(name).suffix.lower() not in (".pyd", ".dll"):
                    continue
                seen += 1
                if seen > MAX_FILES:
                    return found
                file = Path(root) / name
                if probe(str(file)):
                    rel = file.relative_to(folder)
                    found.setdefault(rel.parts[0] if len(rel.parts) > 1 else name.split(".")[0], []).append(str(rel))
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


def explain(exc: BaseException) -> str | None:
    """One line for an error caused by Windows' application control (AppLocker, or App Control for
    Business, also called WDAC): a program the engine starts (a notebook's Python process, a SQL
    worker, the local API) or a package's file that a cell imports was refused. None for anything
    else, and for a message that already says so."""
    cur: BaseException | None = exc
    seen: list[BaseException] = []
    while cur is not None and cur not in seen and len(seen) < 20:
        seen.append(cur)
        code = getattr(cur, "winerror", None)
        text = str(cur)
        if "application control (" in text.lower():
            return None  # already said
        if code in POLICY_CODES or any(p in text.lower() for p in _POLICY_TEXT):
            applocker = code == APPLOCKER_CODE or "group policy" in text.lower()
            by = "AppLocker" if applocker else "App Control for Business, also called WDAC"
            found = _IMPORTING.search(text)
            what = f"a file of the {found.group(1).lstrip('_')} package" if found else "a program KratosForge starts"
            if code == 577 or "verify the digital signature" in text.lower():
                # Also a file that changed after it was signed: a fresh copy comes first, so a hash rule
                # never allows a changed file. A restart alone doesn't replace the engine's files.
                fresh = ("close KratosForge, delete the engine folder (FabricDesktop\\engine in your AppData\\Local "
                         "folder) and start KratosForge again to download a fresh engine" if _own_engine() else
                         "ask IT to copy this build's engine into your organization's engine folder again")
                return (f"Windows couldn't verify the signature on {what}, so this can't run. The file may have "
                        f"been changed: {fresh}, or reinstall the package if it's in an environment. If it's "
                        "still blocked, your "
                        "organization's application control (App Control for Business, also called WDAC) is "
                        "refusing it: ask IT to allow KratosForge's engine files by hash or publisher; the "
                        "enterprise install guide lists the rules (Application control).")
            return (f"Your organization's application control ({by}) blocked {what}, so this can't run. "
                    "Ask IT to allow KratosForge's engine files by hash or publisher; the enterprise install "
                    "guide lists the rules (Application control).")
        cur = cur.__cause__ or cur.__context__
    return None


def _own_engine(prefix: str | None = None) -> bool:
    """Whether this engine is one the person can delete for a fresh copy, rather than one IT keeps in
    the folder the engine_folder policy names, which only IT can replace."""
    from . import app_settings  # here, not at the top: app_settings is heavier and this runs on errors only

    folder = app_settings.read_policy()[0].get("engine_folder")
    if not isinstance(folder, str) or not folder.strip():
        return True
    try:
        Path(prefix or sys.prefix).resolve().relative_to(Path(folder).resolve())
    except ValueError:
        return True
    return False
