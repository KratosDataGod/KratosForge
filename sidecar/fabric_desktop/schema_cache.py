"""Table and column names last read from each lakehouse, so notebook SQL completion works offline (PB-027).

One small file per lakehouse in ``onelake-cache/sql-schemas``: the table list from ``list_tables`` and the
columns of each table ``table_columns`` has read. Names only, never data. The files live in the download
cache, so ``local_retention_days`` removes one that hasn't been refreshed for that long (retention.py).
A cache that can't be written never fails the call that read the names; a missing or broken one reads as empty.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any

from . import jsonfile

_log = logging.getLogger(__name__)
_lock = threading.Lock()  # two reads finishing together must not drop each other's columns

FOLDER = Path("onelake-cache") / "sql-schemas"


def _path(root: Path, lakehouse_id: str) -> Path | None:
    safe = re.sub(r"[^A-Za-z0-9-]", "", str(lakehouse_id or ""))[:64]
    return root / FOLDER / f"{safe}.json" if safe else None


def load(root: Path, lakehouse_id: str) -> dict[str, Any]:
    """{"tables": {schema: [name, ...]}, "columns": {table: [{name, type}, ...]}}; empty when nothing is cached."""
    path = _path(root, lakehouse_id)
    data = jsonfile.read_json(path, {}) if path else {}
    tables = data.get("tables") if isinstance(data, dict) else None
    columns = data.get("columns") if isinstance(data, dict) else None
    return {"tables": tables if isinstance(tables, dict) else {}, "columns": columns if isinstance(columns, dict) else {}}


def _update(root: Path, lakehouse_id: str, change) -> None:
    path = _path(root, lakehouse_id)
    if not path:
        return
    with _lock:
        try:
            data = load(root, lakehouse_id)
            change(data)
            jsonfile.write_json(path, data, indent=None)
        except OSError as exc:  # a full or read-only disk: completion just has nothing cached next time
            _log.warning("Couldn't save the table names for completion: %s", exc)


def record_tables(root: Path, lakehouse_id: str, by_schema: dict[str, list[str]]) -> None:
    _update(root, lakehouse_id, lambda data: data.update(tables=by_schema))


def record_columns(root: Path, lakehouse_id: str, table: str, columns: list[dict[str, str]]) -> None:
    _update(root, lakehouse_id, lambda data: data["columns"].update({table: columns}))
