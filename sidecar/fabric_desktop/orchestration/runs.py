"""Where local pipeline run records live (ORCH-003): one JSON file per run under the app's data
folder, written atomically (jsonfile.py), so a record survives the app stopping mid-run. On Windows a
reader (the status poll, a virus scanner) can hold the file open just as a save swaps the new copy in;
jsonfile waits that out on both sides instead of failing the run with WinError 5.

When the store opens, a run still marked running or pending is from an app that stopped while it
was going. It is marked ``interrupted`` and never resumed: activities that were running may have
done part of their work, so running them again needs a person to decide.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import jsonfile

KEEP = 200
_ID_CHARS = set("0123456789abcdefABCDEF-")


class RunStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._lock = threading.Lock()
        self._recovered = False

    def _path(self, run_id: str) -> Path:
        if not run_id or len(run_id) > 64 or not set(run_id) <= _ID_CHARS:
            raise ValueError("not a run id")
        return self.folder / f"{run_id}.json"

    def save(self, record: dict[str, Any]) -> None:
        path = self._path(record["run_id"])
        with self._lock:
            jsonfile.write_text(path, json.dumps(record, default=str))

    def get(self, run_id: str) -> dict[str, Any] | None:
        self.recover()
        try:
            return json.loads(jsonfile.read_text(self._path(run_id)))
        except (OSError, ValueError):
            return None

    def _files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(self.folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        self.recover()
        out = []
        for path in self._files()[:max(1, min(limit, KEEP))]:
            try:
                out.append(json.loads(jsonfile.read_text(path)))
            except (OSError, ValueError):
                continue
        return out

    def trim(self) -> None:
        for path in self._files()[KEEP:]:
            try:
                path.unlink()
            except OSError:
                pass

    def recover(self, live: set[str] | None = None) -> int:
        """Mark runs left going by an app that stopped as interrupted (once per store). ``live`` are
        runs this process is running now."""
        if self._recovered:
            return 0
        self._recovered = True
        count = 0
        for path in self._files():
            try:
                record = json.loads(jsonfile.read_text(path))
            except (OSError, ValueError):
                continue
            if record.get("state") in ("running", "pending") and record.get("run_id") not in (live or set()):
                record["state"], record["stop_reason"] = "interrupted", "app_stopped"
                record["ended"] = record.get("ended") or datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                for act in record.get("activities", []):
                    if act.get("state") == "running":
                        act["state"], act["status"] = "interrupted", "Interrupted"
                record.setdefault("notes", []).append(
                    "KratosForge stopped while this run was going. It wasn't resumed: activities "
                    "that were running may have finished part of their work.")
                self.save(record)
                count += 1
        return count
