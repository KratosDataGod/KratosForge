"""JSON log lines for the headless agent: one object per line on stdout and in a rotating file.

Every line carries the time, level, logger, message and the worker name; job and run lines add
``job_id``, ``env``, ``attempt`` and the like through ``extra={"fields": {...}}``. journald, the
Windows event collector or any log shipper (Azure Monitor agent, Fluent Bit) can read them as is.
"""

from __future__ import annotations

import datetime as dt
import faulthandler
import json
import logging
import logging.handlers
import os
import socket
import sys
from pathlib import Path
from typing import IO, Any

from .redact import walk


class JsonFormatter(logging.Formatter):
    def __init__(self, worker: str):
        super().__init__()
        self.worker, self.host = worker, socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "at": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name, "message": record.getMessage(),
            "worker": self.worker, "host": self.host}
        line |= getattr(record, "fields", None) or {}
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        # Nothing credential-shaped reaches a log file or a log shipper. Redacted value by value,
        # before JSON escapes the quotes the patterns look for.
        return json.dumps(_redacted(line), default=str)


def _redacted(value: Any) -> Any:
    return walk(value, stringify=True)  # an object becomes what default=str would write


def setup(worker: str, log_dir: Path | None, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("fabric_desktop.agent")
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False
    fmt = JsonFormatter(worker)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(log_dir / "agent.log", maxBytes=20 << 20, backupCount=10,
                                                        encoding="utf-8")
        rotating.setFormatter(fmt)
        logger.addHandler(rotating)
    return logger


def fields(**values: Any) -> dict[str, Any]:
    """``log.info("...", extra=fields(job_id=...))``"""
    return {"fields": values}


CRASH_LOG = "agent-crash.log"
START_LINE = "agent started,"
CRASH_LOG_BYTES = 1 << 20  # past this at a start, the file becomes agent-crash.prev.log
_crash_file: IO[str] | None = None  # kept open for as long as the agent runs


def report_native_crashes(log_dir: Path) -> bool:
    """A crash in native code (DuckDB, Polars, delta-rs: an access violation, say) ends the agent
    without a Python traceback or a log line. ``faulthandler`` writes one to ``agent-crash.log`` first,
    for every thread: file names, function names and line numbers only, never a value. Nothing is sent
    anywhere; the support bundle includes the file, redacted. False when no report can be kept, or
    when faulthandler is already on (``PYTHONFAULTHANDLER`` or ``-X faulthandler``): that choice stands."""
    global _crash_file
    if faulthandler.is_enabled():
        return False
    path = log_dir / CRASH_LOG
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > CRASH_LOG_BYTES:
            path.replace(path.with_name("agent-crash.prev.log"))
        crash_file = path.open("a", encoding="utf-8")
        # A trace a start line follows was a crash; one the agent ran on after was a fault native code
        # handled itself (faulthandler also sees those on Windows).
        crash_file.write(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {START_LINE} pid {os.getpid()}\n")
        crash_file.flush()
        faulthandler.enable(file=crash_file, all_threads=True)
    except (AttributeError, OSError, ValueError) as exc:  # no report then; the agent still runs
        logging.getLogger("fabric_desktop.agent").warning("native crashes won't be reported: %s", exc)
        return False
    if _crash_file is not None:
        _crash_file.close()
    _crash_file = crash_file
    return True
