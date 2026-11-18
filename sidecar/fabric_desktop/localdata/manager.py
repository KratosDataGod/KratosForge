"""The open local engines, and queries the app runs in the background and polls.

The app's engine answers one request at a time, so a long query is started (``start``), polled
(``status``) and cancelled (``cancel``) instead of blocking every other call while it runs.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .discovery import Source
from .engine import OWN_KEY, LocalEngine, QueryCancelled
from .projects import Project, ProjectStore, Settings

_KEEP_FINISHED = 600  # seconds a finished background query's result waits to be collected


class LocalData:
    def __init__(self, root: Path):
        self.store = ProjectStore(root)
        self._engines: dict[str, LocalEngine] = {}
        self._lock = threading.Lock()
        self._queries: dict[str, dict[str, Any]] = {}
        self._queries_lock = threading.Lock()  # a query's thread finishes its record while status() reads it

    # Projects

    def projects(self) -> list[dict[str, Any]]:
        return [p.summary() | {"open": p.id in self._engines and self._engines[p.id].is_open}
                for p in self.store.list()]

    def create(self, name: str, storage: str = "persistent", folder: str = "",
               settings: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.store.create(name, storage, folder, settings).summary()

    def engine(self, pid: str) -> LocalEngine:
        with self._lock:
            engine = self._engines.get(pid)
            if engine is None:
                engine = self._engines[pid] = LocalEngine(self.store.load(pid), self.store)
            return engine

    def project(self, pid: str) -> Project:
        return self.engine(pid).project

    def set_settings(self, pid: str, settings: dict[str, Any]) -> dict[str, Any]:
        engine = self.engine(pid)
        merged = asdict(engine.project.settings) | dict(settings)
        engine.project.settings = Settings(**merged)
        engine.apply_settings()
        self.store.save(engine.project)
        return engine.project.summary()

    def close(self, pid: str) -> None:
        with self._lock:
            engine = self._engines.pop(pid, None)
        if engine is not None:
            engine.close()

    def close_all(self) -> None:
        for pid in list(self._engines):
            self.close(pid)

    def forget(self, pid: str) -> None:
        self.close(pid)
        self.store.forget(pid)

    # Folders

    def add_source(self, pid: str, path: str, **options: Any) -> dict[str, Any]:
        engine = self.engine(pid)
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise ValueError(f"{path} isn't a folder on this PC.")
        resolved = str(folder.resolve())
        if engine.project.source(resolved):
            raise ValueError(f"{resolved} is already a folder of {engine.project.name}.")
        home = Path(engine.project.folder).resolve()
        if folder.resolve() == home or folder.resolve() in home.parents or home in folder.resolve().parents:
            raise ValueError("That folder holds the project's own database and Delta tables. Pick the folder with "
                             "your data files.")
        source = Source(path=resolved, **{k: v for k, v in options.items() if v is not None})
        engine.project.sources.append(source)
        try:
            result = engine.refresh(resolved)
        except Exception:
            engine.project.sources.remove(source)
            raise
        return result | {"issues": engine.project.refreshed[resolved]["issues"]}

    def catalog(self, pid: str) -> dict[str, Any]:
        engine = self.engine(pid)
        return {"project": engine.project.summary(), "tables": engine.tables(),
                "sources": [asdict(s) | engine.project.refreshed.get(s.path, {}) for s in engine.project.sources],
                "tables_folder": {"path": str(engine.tables_dir)} | engine.project.refreshed.get(OWN_KEY, {})}

    # Background queries

    def start(self, pid: str, sql: str, **options: Any) -> dict[str, Any]:
        engine = self.engine(pid)
        query_id = uuid.uuid4().hex
        record = {"query_id": query_id, "project": pid, "state": "running", "started_at": time.time(),
                  "_clock": time.monotonic()}  # elapsed comes from this, unmoved by a change to the PC's clock
        with self._queries_lock:
            self._queries[query_id] = record

        def run() -> None:
            # The outcome is built apart and put in the record in one step, so status() never sees a
            # finished state without its result or error (and collects it), or a record changing size.
            try:
                end: dict[str, Any] = {"result": engine.execute(sql, query_id=query_id, **options), "state": "done"}
            except Exception as exc:  # noqa: BLE001 - every failure goes back to the page
                end = {"state": "cancelled" if isinstance(exc, QueryCancelled) else "error", "error": str(exc)}
            end["finished_at"] = time.time()
            with self._queries_lock:
                record.update(end)
        threading.Thread(target=run, name=f"local query {query_id[:8]}", daemon=True).start()
        self._sweep()
        return {"query_id": query_id, "state": "running"}

    def status(self, query_id: str) -> dict[str, Any]:
        with self._queries_lock:
            record = self._queries.get(query_id)
            if record is None:
                raise KeyError("That query isn't running and has no result waiting (results are kept for 10 minutes).")
            snapshot = dict(record)
            if snapshot["state"] != "running":
                self._queries.pop(query_id, None)  # collected
        return {k: v for k, v in snapshot.items() if k not in ("started_at", "_clock")} | \
            {"elapsed": round(max(0.0, time.monotonic() - snapshot["_clock"]), 1)}

    def cancel(self, query_id: str) -> dict[str, Any]:
        with self._queries_lock:
            record = self._queries.get(query_id)
            running = record is not None and record["state"] == "running"
        if record is None or not running:
            return {"cancelled": False}
        # The query's thread may not have reached the engine yet: it is then stopped as it starts.
        return {"cancelled": self.engine(record["project"]).cancel(query_id, not_started=True)}

    def _sweep(self) -> None:
        now = time.time()
        with self._queries_lock:
            for qid, record in list(self._queries.items()):
                if record.get("finished_at") and now - record["finished_at"] > _KEEP_FINISHED:
                    self._queries.pop(qid, None)
