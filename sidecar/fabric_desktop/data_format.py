"""Which data format a data folder is in, so an older build doesn't open what a newer one changed.

Most changes to the stores in the data folder are ones older builds read without harm: a new table,
a new column, a new optional key (tests/test_store_upgrades.py opens each store from the last Stable
builds with this code). DATA_FORMAT goes up only for a change an older build would misread or
overwrite. A build that finds its folder marked with a higher format refuses to start there and says
what to do, rather than damage what the newer build saved. Otherwise it raises the mark to its own.

The mark is ``data-format.json`` in the folder: the app's data folder (``%LOCALAPPDATA%\\FabricDesktop``)
and the headless agent's home. Builds from before the mark never read it.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import __version__

DATA_FORMAT = 1
MARK = "data-format.json"


class NewerData(RuntimeError):
    """The folder was last used by a build with a newer data format."""


def marked(root: Path) -> int | None:
    """The format the folder is marked with, or None when it has no readable mark."""
    try:
        data = json.loads((Path(root) / MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    seen = data.get("format") if isinstance(data, dict) else None
    return seen if isinstance(seen, int) and not isinstance(seen, bool) and seen > 0 else None


def check(root: Path) -> None:
    """Refuse a folder a newer build marked (NewerData); otherwise mark it with this build's format."""
    root = Path(root)
    seen = marked(root)
    if seen is not None and seen > DATA_FORMAT:
        raise NewerData(
            f"The data in {root} was saved by a newer KratosForge (data format {seen}; this build, "
            f"{__version__}, reads up to {DATA_FORMAT}). This older build won't open it, so nothing the newer "
            "one saved is damaged. Run the newer build again, or restore a backup taken before that upgrade "
            "(docs/install.md, Going back to an older build).")
    if seen is None or seen < DATA_FORMAT:
        from . import jsonfile
        jsonfile.write_text(root / MARK, json.dumps({"format": DATA_FORMAT, "written_by": __version__}) + "\n")
