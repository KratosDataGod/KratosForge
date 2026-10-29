"""Run several notebooks at once from Home (Recent notebooks > Run). The ones that run on this PC go
to one background process, which runs each like Run all (its saved version, frozen cells skipped) in
a process of its own, a few at a time, so the app stays free. Each run is logged with this PC's other
background runs, so the notebook's Runs panel and Monitor list it. Ones that run in Fabric are
published and started there one by one (notebook_batch_fabric), after the app's confirm and risk check.

Also the details Home shows for each recent notebook (notebook_details)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import jsonfile
from .notebook import check_notebook_name
from .redact import describe

_log = logging.getLogger(__name__)

BATCHES = "notebook_batches"
MAX_NOTEBOOKS = 50
MAX_PARALLEL = 4
HEARTBEAT_SECONDS = 10
LOST_SECONDS = 90  # no heartbeat for this long: the background process is gone
_BATCH_ID = re.compile(r"^[0-9a-f]{12}$")
DONE = ("ok", "failed", "skipped")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class NotebookBatchMixin:
    root: Path

    # ---- details for Home

    def notebook_details(self, names: list[str]) -> list[dict[str, Any]]:
        """For each notebook: the workspace it publishes to (its own pick, else its lakehouse's), its
        lakehouse, where Run all runs it ("local", "fabric" or "default") and its last background run
        (schedules, Run from Home, Fabric). Reads files on this PC only. A notebook that can't be read
        comes back with ``error``."""
        out: list[dict[str, Any]] = []
        for name in list(names or [])[:MAX_NOTEBOOKS]:
            try:
                nb = self.notebooks.load(name)  # type: ignore[attr-defined]
            except (OSError, ValueError) as exc:
                out.append({"name": name, "error": describe(exc)})
                continue
            lh = nb.get("lakehouse") or {}
            ws = nb.get("workspace") or {}
            if not ws.get("workspace_id") and lh.get("workspace_id"):
                ws = {"workspace_id": lh["workspace_id"], "workspace_name": lh.get("workspace_name", "")}
            try:
                runs = self.notebook_runs(name, limit=1)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - a run log that can't be read leaves Last run empty
                _log.warning("Couldn't read the runs of %r: %s", name, describe(exc))
                runs = []
            last = runs[0] if runs else None
            out.append({"name": name, "workspace_id": ws.get("workspace_id") or "",
                        "workspace_name": ws.get("workspace_name") or "", "lakehouse": lh.get("name") or "",
                        "compute": nb.get("compute") or "default",
                        "last_run": {k: last.get(k) for k in ("source", "status", "started", "ended", "seconds", "message", "trigger")}
                        if last else None})
        return out

    # ---- runs on this PC

    def _batch_dir(self) -> Path:
        return self.root / BATCHES

    def _batch_file(self, id: str) -> Path:  # noqa: A002
        if not _BATCH_ID.match(str(id or "")):
            raise ValueError("That isn't a run of several notebooks")
        return self._batch_dir() / f"{id}.json"

    def notebook_batch_start(self, names: list[str], parallel: int = 2) -> dict[str, Any]:
        """Run these notebooks on this PC in the background, at most ``parallel`` at a time. Returns
        the batch to follow with notebook_batch."""
        names = [str(n) for n in dict.fromkeys(names or [])]
        if not names:
            raise ValueError("Pick at least one notebook to run")
        if len(names) > MAX_NOTEBOOKS:
            raise ValueError(f"Up to {MAX_NOTEBOOKS} notebooks can run at once")
        for n in names:
            check_notebook_name(n)
            if not self.notebooks.path(n).is_file():  # type: ignore[attr-defined]
                raise ValueError(f"There's no notebook named {n!r}.")
        parallel = max(1, min(MAX_PARALLEL, int(parallel or 1)))
        self._forget_old_batches()
        batch_id = uuid.uuid4().hex[:12]
        batch = {"id": batch_id, "started": _now(), "parallel": parallel, "state": "starting", "beat": time.time(),
                 "items": [{"name": n, "status": "waiting"} for n in names]}
        jsonfile.write_json(self._batch_file(batch_id), batch)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, "-m", "fabric_desktop.cli", "run-notebooks", batch_id],
                         cwd=str(self.root), creationflags=flags, env=os.environ.copy(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return batch

    def notebook_batch(self, id: str) -> dict[str, Any]:  # noqa: A002
        """Where the run of several notebooks is: each notebook waiting, running (with its cell),
        ok, failed or skipped. A batch whose background process stopped answering says so."""
        path = self._batch_file(id)
        batch = jsonfile.read_json(path, None)
        if not isinstance(batch, dict):
            raise LookupError("That run of several notebooks isn't on this PC")
        for index, item in enumerate(batch["items"]):
            if item["status"] != "running":
                continue
            prog = jsonfile.read_json(self._batch_dir() / f"{id}.{index}.progress", None)
            if isinstance(prog, dict):
                item |= {"cell": prog.get("cell"), "cells": prog.get("cells")}
        if batch["state"] != "done" and time.time() - float(batch.get("beat") or 0) > LOST_SECONDS:
            batch["state"] = "lost"
            for item in batch["items"]:
                if item["status"] not in DONE:
                    item |= {"status": "failed", "message": "The background run stopped without finishing"}
        batch["stopping"] = (self._batch_dir() / f"{id}.stop").exists()
        return batch

    def notebook_batch_stop(self, id: str) -> dict[str, Any]:  # noqa: A002
        """Don't start the notebooks still waiting. Ones already running finish."""
        self._batch_file(id)
        (self._batch_dir() / f"{id}.stop").write_text(_now(), encoding="utf-8")
        return self.notebook_batch(id)

    def run_notebook_batch(self, id: str) -> dict[str, Any]:  # noqa: A002
        """The background process (cli run-notebooks): runs each waiting notebook in its own process."""
        path = self._batch_file(id)
        batch = jsonfile.read_json(path, None)
        if not isinstance(batch, dict):
            raise LookupError("That run of several notebooks isn't on this PC")
        lock = threading.Lock()
        stop = self._batch_dir() / f"{id}.stop"

        def save(**change: Any) -> None:
            with lock:
                batch.update(change, beat=time.time())
                jsonfile.write_json(path, batch)

        def one(index: int) -> None:
            item = batch["items"][index]
            if stop.exists():
                with lock:
                    item |= {"status": "skipped", "message": "Not started: you stopped the rest"}
                save()
                return
            with lock:
                item |= {"status": "running", "started": _now()}
            save()
            t0 = time.monotonic()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                done = subprocess.run([sys.executable, "-m", "fabric_desktop.cli", "run-notebook", id, str(index)],
                                      cwd=str(self.root), creationflags=flags, env=os.environ.copy(),
                                      stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
                lines = [x for x in (done.stdout or "").splitlines() if x.strip().startswith("{")]
                row = json.loads(lines[-1]) if lines else {
                    "status": "failed", "message": f"The run stopped without a result (exit code {done.returncode})"}
            except Exception as exc:  # noqa: BLE001 - recorded on the item; the others still run
                row = {"status": "failed", "message": describe(exc)}
            with lock:
                item |= {"status": row.get("status") or "failed", "message": row.get("message") or "",
                         "ended": _now(), "seconds": round(time.monotonic() - t0, 1)}
            save()

        def beat() -> None:
            while batch["state"] == "running":
                time.sleep(HEARTBEAT_SECONDS)
                if batch["state"] == "running":
                    save()

        save(state="running")
        threading.Thread(target=beat, daemon=True).start()
        waiting = [i for i, item in enumerate(batch["items"]) if item["status"] == "waiting"]
        with ThreadPoolExecutor(max_workers=int(batch.get("parallel") or 1)) as pool:
            list(pool.map(one, waiting))
        save(state="done", ended=_now())
        for p in self._batch_dir().glob(f"{id}.*.progress"):
            p.unlink(missing_ok=True)
        stop.unlink(missing_ok=True)
        return batch

    def run_batch_notebook(self, id: str, index: int) -> dict[str, Any]:  # noqa: A002
        """One notebook of the batch, in this process (cli run-notebook): run as Run all, then logged
        with this PC's background runs as "Run from Home"."""
        batch = jsonfile.read_json(self._batch_file(id), None)
        if not isinstance(batch, dict) or not 0 <= int(index) < len(batch["items"]):
            raise LookupError("That notebook isn't part of this run")
        name = batch["items"][int(index)]["name"]
        progress = self._batch_dir() / f"{id}.{int(index)}.progress"
        row: dict[str, Any] = {"schedule_id": "", "label": name, "trigger": "home", "started": _now(), "pid": os.getpid()}
        t0 = time.monotonic()
        try:
            row |= self._run_notebook_here(name, lambda cell, cells: jsonfile.write_text(  # type: ignore[attr-defined]
                progress, json.dumps({"cell": cell, "cells": cells})))
        except Exception as exc:  # noqa: BLE001 - the run's outcome is the result, not a crash
            row |= {"status": "failed", "message": describe(exc)}
        row |= {"ended": _now(), "seconds": round(time.monotonic() - t0, 1)}
        self.pc_schedules.log_run(row)  # type: ignore[attr-defined]
        return row

    def _forget_old_batches(self, days: int = 7) -> None:
        """Batches (and their stop and progress files) older than ``days`` go."""
        cutoff = time.time() - days * 86400
        if not self._batch_dir().is_dir():
            return
        for p in self._batch_dir().iterdir():
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                continue

    # ---- runs in Fabric

    def notebook_batch_fabric(self, name: str, replace_fabric_changes: bool = False) -> dict[str, Any]:
        """Publish the saved notebook to its Fabric item and run it there, as the notebook's own Run
        in Fabric does. The app asks first and runs the risk check."""
        nb = self.notebooks.load(name)  # type: ignore[attr-defined]
        return self.run_notebook_in_fabric(name, nb["cells"], nb.get("lakehouse"), nb.get("compute"),  # type: ignore[attr-defined]
                                           nb.get("workspace"), replace_fabric_changes=replace_fabric_changes)
