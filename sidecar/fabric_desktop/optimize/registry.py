"""The optimization lakehouses this PC is connected to, and which one is active.

Saved in ``<data>/optimize/connections.json``. A connection is only this PC's note of where a
lakehouse is: forgetting it never touches the lakehouse or its data, and connecting again later
picks up where it left off.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .installation import now
from .. import jsonfile

FIELDS = ("workspace_id", "workspace_name", "lakehouse_id", "lakehouse_name", "schemas")


class Registry:
    def __init__(self, folder: Path):
        self.path = Path(folder) / "connections.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            data = jsonfile.read_json(self.path, {})
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("connections"), list):
            data = {"connections": [], "active": None}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        jsonfile.write_text(self.path, json.dumps(data, indent=1))

    def all(self) -> list[dict[str, Any]]:
        data = self._load()
        return [c | {"active": c["lakehouse_id"] == data.get("active")} for c in data["connections"]]

    def get(self, lakehouse_id: str) -> dict[str, Any] | None:
        return next((c for c in self.all() if c["lakehouse_id"] == lakehouse_id), None)

    def active(self) -> dict[str, Any] | None:
        return next((c for c in self.all() if c["active"]), None)

    def add(self, conn: dict[str, Any], activate: bool = True) -> dict[str, Any]:
        """Remember a lakehouse (again): its last check result is kept with it."""
        if not conn.get("workspace_id") or not conn.get("lakehouse_id"):
            raise ValueError("A connection needs a workspace and a lakehouse")
        with self._lock:
            data = self._load()
            old = next((c for c in data["connections"] if c["lakehouse_id"] == conn["lakehouse_id"]), {})
            entry = old | {k: conn[k] for k in conn if k in FIELDS or k in ("schema_version", "access", "checked_at")}
            entry.setdefault("added_at", now())
            data["connections"] = [c for c in data["connections"] if c["lakehouse_id"] != conn["lakehouse_id"]] + [entry]
            if activate or not data.get("active"):
                data["active"] = conn["lakehouse_id"]
            self._save(data)
        return self.get(conn["lakehouse_id"])  # type: ignore[return-value]

    def activate(self, lakehouse_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            if not any(c["lakehouse_id"] == lakehouse_id for c in data["connections"]):
                raise LookupError("This PC isn't connected to that lakehouse. Connect to it first.")
            data["active"] = lakehouse_id
            self._save(data)
        return self.get(lakehouse_id)  # type: ignore[return-value]

    def forget(self, lakehouse_id: str) -> None:
        """Disconnect this PC. The lakehouse and everything in it stay as they are."""
        with self._lock:
            data = self._load()
            data["connections"] = [c for c in data["connections"] if c["lakehouse_id"] != lakehouse_id]
            if data.get("active") == lakehouse_id:
                data["active"] = data["connections"][-1]["lakehouse_id"] if data["connections"] else None
            self._save(data)
