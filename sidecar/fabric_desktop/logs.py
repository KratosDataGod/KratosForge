"""JSON log lines for the headless agent: one object per line on stdout and in a rotating file.

Every line carries the time, level, logger, message and the worker name; job and run lines add
``job_id``, ``env``, ``attempt`` and the like through ``extra={"fields": {...}}``. journald, the
Windows event collector or any log shipper (Azure Monitor agent, Fluent Bit) can read them as is.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import logging.handlers
import socket
import sys
from pathlib import Path
from typing import Any

from .redact import REMOVED, secret_key, secrets


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
    if isinstance(value, str):
        return secrets(value)
    if isinstance(value, dict):
        return {k: REMOVED if secret_key(k) and isinstance(v, str) else _redacted(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return secrets(str(value))  # what default=str would write


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
