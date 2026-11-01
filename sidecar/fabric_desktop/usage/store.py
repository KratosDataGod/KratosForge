"""Imported usage files, kept on this PC in ``<data>/usage_imports/<id>.json``.

Each import keeps its rows (measured measurements), what file they came from (name and SHA-256),
when, and the choices made for reading it. Nothing is sent anywhere. Removing an import deletes
only its local copy; the person's exported file is never touched.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any
from .. import jsonfile

FOLDER = "usage_imports"
SUMMARY = ("id", "provider", "provider_label", "layout", "layout_label", "file_name", "sha256", "imported_at", "options",
           "period", "workspaces", "capacities", "rows", "total", "cost", "skipped", "notes")


class Store:
    def __init__(self, root: Path):
        self.folder = Path(root) / FOLDER

    def _path(self, import_id: str) -> Path:
        if not re.fullmatch(r"u-[0-9a-f]{16}", import_id or ""):
            raise ValueError(f"{import_id!r} isn't an import id")
        return self.folder / f"{import_id}.json"

    def _load(self, path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def all(self) -> list[dict[str, Any]]:
        """Every import with its rows, newest first."""
        docs = [d for p in sorted(self.folder.glob("u-*.json")) if (d := self._load(p))]
        return sorted(docs, key=lambda d: d.get("imported_at") or "", reverse=True)

    def summaries(self) -> list[dict[str, Any]]:
        return [{k: (len(d["rows"]) if k == "rows" else d.get(k)) for k in SUMMARY} for d in self.all()]

    def keys(self) -> set[str]:
        return {r["key"] for d in self.all() for r in d.get("rows", [])}

    def slots(self) -> set[str]:
        return {r["slot"] for d in self.all() for r in d.get("rows", []) if r.get("slot")}

    def save(self, preview: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
        doc = {"id": "u-" + preview["sha256"][:16], "provider": preview["provider"]["id"],
               "provider_label": preview["provider"]["label"], "layout": preview["layout"]["id"],
               "layout_label": preview["layout"]["label"], "file_name": preview["file_name"],
               "sha256": preview["sha256"], "imported_at": now.astimezone(dt.timezone.utc).isoformat(timespec="microseconds"),
               "options": preview["options"], "period": preview["period"], "workspaces": preview["workspaces"],
               "capacities": preview.get("capacities", []), "total": preview["total"], "cost": preview.get("cost", []), "skipped": preview["skipped"], "notes": preview["notes"], "rows": preview["rows"]}
        path = self._path(doc["id"])
        if path.exists():
            raise ValueError("This file is already imported.")
        jsonfile.write_text(path, json.dumps(doc, default=str))
        return {k: (len(doc["rows"]) if k == "rows" else doc.get(k)) for k in SUMMARY}

    def delete(self, import_id: str) -> bool:
        path = self._path(import_id)
        if not path.exists():
            return False
        path.unlink()
        return True
