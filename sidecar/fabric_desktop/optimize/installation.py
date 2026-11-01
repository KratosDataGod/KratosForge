"""This installation's id in the optimization lakehouse.

A random UUID made on this PC the first time Optimize needs it, saved in
``<data>/optimize/installation.json``. It is never worked out from the person, the PC or the
tenant, and it is not the usage-data install id (telemetry.py), which is opt-in and goes
elsewhere. Settings can make a new one; rows already written keep the old one.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import threading
import uuid
from pathlib import Path
from typing import Any

from .. import __version__, jsonfile


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def os_family() -> str:
    """Windows, macOS or Linux: no version or machine name."""
    return {"Darwin": "macOS"}.get(platform.system(), platform.system() or "unknown")


class Installation:
    def __init__(self, folder: Path):
        self.path = Path(folder) / "installation.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        jsonfile.write_text(self.path, json.dumps(data, indent=1))

    @property
    def id(self) -> str:
        with self._lock:
            data = self._load()
            if not _is_uuid(data.get("installation_id")):
                data = {"installation_id": str(uuid.uuid4()), "created_at": now(), "label": data.get("label", "")}
                self._save(data)
            return data["installation_id"]

    @property
    def label(self) -> str:
        return str(self._load().get("label") or "")

    def set_label(self, label: str) -> None:
        """An optional name people in the organization will see, such as "Build server". Empty by default."""
        self.id  # noqa: B018 - make sure there is one to label
        with self._lock:
            data = self._load()
            data["label"] = str(label or "").strip()[:80]
            self._save(data)

    def reset(self) -> str:
        """A new random id. What the old one wrote stays in the lakehouse under the old id."""
        with self._lock:
            data = {"installation_id": str(uuid.uuid4()), "created_at": now(), "label": self._load().get("label", "")}
            self._save(data)
            return data["installation_id"]

    def provenance(self, schema_version: int) -> dict[str, Any]:
        """The columns every row this installation writes carries (schema.PROVENANCE)."""
        return {"installation_id": self.id, "created_at": now(), "app_version": __version__,
                "schema_version": schema_version}


def _is_uuid(value: Any) -> bool:
    try:
        return str(uuid.UUID(str(value))) == value
    except ValueError:
        return False
