"""Why ``agent run`` stopped before or as it started, kept on this machine.

Task Scheduler restarts a stopped agent every minute and keeps only "the task ended with code 2";
the reason went to a console nobody sees. Each such stop now adds a few lines to
``<home>/logs/agent-start.log``: when (UTC), the version, which stage (settings, data, start,
crash), the exit code and the reason, made log-safe (``redact.describe``). Where the settings can't
be read, ``<home>`` is the default one (``agent_config.default_home``), which the installer lets the
agent's account write. The file stays under ``MAX_BYTES`` by dropping its oldest half, and nothing
is sent anywhere. A crash keeps its stack as file, line and function names only.
"""

from __future__ import annotations

import datetime as dt
import traceback
from pathlib import Path

from . import __version__
from .redact import describe, one_line

START_LOG = "agent-start.log"
MAX_BYTES = 256 << 10
STAGES = ("settings", "data", "start", "crash")


def record(home: Path | None, stage: str, reason: BaseException | str, code: int) -> Path | None:
    """Add one entry; returns the file, or None when it couldn't be written."""
    from .agent_config import default_home
    try:
        path = Path(home or default_home()) / "logs" / START_LOG
        text = describe(reason) if isinstance(reason, BaseException) else one_line(reason, 2000)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        lines = [f"{now} agent run stopped: stage={stage} exit={code} version={__version__}", f"  {text}"]
        if stage == "crash" and isinstance(reason, BaseException):
            lines += [f"  at {f.filename}:{f.lineno} in {f.name}" for f in traceback.extract_tb(reason.__traceback__)[-12:]]
        entry = ("\n".join(lines) + "\n").encode("utf-8", "replace")
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_bytes() if path.exists() else b""
        if len(old) + len(entry) > MAX_BYTES:
            old = old[len(old) // 2:]
            old = old[old.find(b"\n") + 1:]  # from a whole line
            path.write_bytes(old + entry)
        else:
            with open(path, "ab") as f:
                f.write(entry)
        return path
    except OSError:  # a home it can't write: the exit code and the printed reason still say it stopped
        return None
