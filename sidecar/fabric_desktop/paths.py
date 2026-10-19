"""Where Fabric Desktop keeps files on this PC. No imports from the rest of the package."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def desktop_data_base() -> Path:
    """Parent of the FabricDesktop folder when LOCALAPPDATA is unset."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".local" / "share"


def data_dir() -> Path:
    return desktop_data_base() / "FabricDesktop"
