"""Errors an Academy screen shows as they are.

A ``LabError`` carries a sentence written for the learner: the engine's own reason (DuckDB's message for a
SQL answer that didn't run) or a plain statement of what the app couldn't do. The app's error text shows it
word for word (``friendlyError`` in app/ui/activity.js), never as a bug report or a Fabric message.
"""

from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# A pack file KratosForge installed that can't be read on this PC: damaged or removed after install. The
# real reason and path go to the log, not to the learner (CI's problems() catches these in the repo).
DAMAGED = "{what} files are damaged on this PC. Reinstall or update KratosForge to repair them."


class LabError(ValueError):
    """A message for the learner, shown as it is."""


def damaged(path: Path | str, exc: BaseException, what: str = "This exercise's") -> LabError:
    """The learner's error for an unreadable pack file, with the real reason logged."""
    _log.warning("An Academy pack file couldn't be read (%s): %s: %s", path, type(exc).__name__, exc)
    return LabError(DAMAGED.format(what=what))
