"""Full paths for the Windows programs the engine starts (taskkill, schtasks, cmd, explorer).

Windows looks for a program named without a folder in the current folder before System32, so
``taskkill`` could start a ``taskkill.exe`` left in a notebook's or repo's folder instead. Naming
the program by its full path under the Windows folder starts only the system one.
"""

from __future__ import annotations

import ntpath
import os


def windows_folder() -> str:
    """The Windows folder (usually C:\\Windows), from SystemRoot as Windows itself sets it."""
    return os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"


def system32(name: str) -> str:
    """``C:\\Windows\\System32\\<name>``: for taskkill.exe, schtasks.exe and the other system tools."""
    return ntpath.join(windows_folder(), "System32", name)


def windows_exe(name: str) -> str:
    """``C:\\Windows\\<name>``: for explorer.exe, which lives in the Windows folder itself."""
    return ntpath.join(windows_folder(), name)
