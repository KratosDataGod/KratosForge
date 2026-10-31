"""Side by side runs: the same notebook on this PC, then in Fabric, compared. See side_by_side.py."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from deltalake import DeltaTable

from . import depgraph, jsonfile, notebook_cells, receipts
from . import side_by_side as sbs
from .fabric_run import TERMINAL, compute_check, uses_local_module, uses_spark
from .fabric_step import STEP_REFUSED, is_step
from .onelake import DFS_HOST, FileStore, storage_options

_log = logging.getLogger(__name__)
_SAVE_LOCK = threading.Lock()  # side_by_side.jsonl is read, changed and written back whole

NOTEBOOK_JOB = "RunNotebook"
POLL_SECONDS = 5
MAX_FABRIC_SECONDS = 6 * 3600


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(at: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


class OneLakeTables(sbs.Tables):
    """The attached lakehouse's tables, read from OneLake storage (so tables Fabric hasn't
    registered yet are seen too)."""

    def __init__(self, workspace_id: str, lakehouse_id: str, schemas: bool, token: Callable[[], str]):
        self.workspace_id, self.lakehouse_id, self.schemas, self.token = workspace_id, lakehouse_id, schemas, token
        self.files = FileStore(token)

    def _uri(self, table: str) -> str:
        schema, _, name = table.rpartition(".")
        folder = f"{schema}/{name}" if self.schemas else name
        return f"abfss://{self.workspace_id}@{DFS_HOST}/{self.lakehouse_id}/Tables/{folder}"

    def _names(self) -> list[str]:
        top = [e for e in self.files.list(self.workspace_id, self.lakehouse_id, "Tables") if e["is_dir"]]
        if not self.schemas:
            return [f"dbo.{e['name']}" for e in top]
        names = []
        for schema in top:
            names += [f"{schema['name']}.{e['name']}" for e in self.files.list(self.workspace_id, self.lakehouse_id,
                                                                               schema["path"]) if e["is_dir"]]
        return names

    def _open(self, table: str, version: int | None = None) -> DeltaTable:
        return DeltaTable(self._uri(table), version=version, storage_options=storage_options(self.token()))

    def versions(self) -> dict[str, int]:
        def one(name: str) -> tuple[str, int | None]:
            try:
                return name, self._open(name).version()
            except Exception:  # noqa: BLE001 - a folder that isn't a Delta table
                return name, None
        with ThreadPoolExecutor(max_workers=8) as pool:
            return {n: v for n, v in pool.map(one, self._names()) if v is not None}

    def history(self, table: str) -> list[dict[str, Any]]:
        return self._open(table).history(limit=200)

    def dataset(self, table: str, version: int) -> Any:
        return self._open(table, version).to_pyarrow_dataset()


class SideBySideMixin:
    root: Path

    @property
    def side_by_side_file(self) -> Path:
        return self.root / "side_by_side.jsonl"

    def _sbs_state(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_sbs_runs"):
            self._sbs_runs: dict[str, dict[str, Any]] = {}
        return self._sbs_runs

    def _sbs_saved(self) -> list[dict[str, Any]]:
        try:
            lines = jsonfile.read_text(self.side_by_side_file).splitlines()
        except FileNotFoundError:
            return []
        except ValueError:  # not UTF-8: keep the file, start again
            jsonfile.set_aside(self.side_by_side_file)
            return []
        rows, torn = [], False
        for line in filter(str.strip, lines):
            try:
                rows.append(json.loads(line))
            except ValueError:
                torn = True
        if torn:
            self._sbs_repair(rows)
        return rows

    def _sbs_repair(self, rows: list[dict[str, Any]]) -> None:
        """Copy the torn file aside, then swap in the rows that still read.

        If that fails, the original stays in place and the next save rewrites it. Until the file
        changes, reads don't try again, so Monitor's polls don't pile up copies or wait on retries."""
        path = self.side_by_side_file
        try:
            stat = path.stat()
        except OSError:
            return
        seen = (stat.st_mtime_ns, stat.st_size)
        if getattr(self, "_sbs_failed_repair", None) == seen:
            return
        try:
            jsonfile.set_aside(path, keep=True, then=f"the {len(rows)} comparisons that could be read were kept")
            jsonfile.write_text(path, "".join(json.dumps(r, default=str) + "\n" for r in rows))
        except OSError as exc:
            self._sbs_failed_repair = seen
            _log.warning("%s could not be repaired (%s); showing the comparisons that could be read.", path, exc)

    def _sbs_save(self, record: dict[str, Any]) -> None:
        """Keep the finished comparison (without cell outputs, which can be large)."""
        keep = {k: v for k, v in record.items() if not k.startswith("_")}
        keep["local"] = {k: v for k, v in record["local"].items() if k != "cells"}
        with _SAVE_LOCK:  # two comparisons finishing together would each drop the other's row
            rows = [r for r in self._sbs_saved() if r.get("id") != record["id"]] + [keep]
            jsonfile.write_text(self.side_by_side_file, "".join(json.dumps(r, default=str) + "\n" for r in rows[-200:]))

    # ---- what the app calls

    def side_by_side_writes(self, cells: list[dict[str, Any]]) -> dict[str, Any]:
        """The tables a side-by-side run writes for real, twice (once per side), read from the code.
        ``unknown`` is True when a cell does something reading can't follow (exec, %run, import *)."""
        return _writes(cells)

    def _sbs_cores_choice(self, name: str) -> Any:
        try:
            choice = self.notebooks.extra(name, "sbs_cores")  # type: ignore[attr-defined]
        except (OSError, ValueError):  # not saved yet
            choice = None
        if choice in sbs.CORE_CHOICES or (isinstance(choice, int) and not isinstance(choice, bool) and choice >= 1):
            return choice
        return sbs.DEFAULT_CORES

    def side_by_side_cores(self, name: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
        """The notebook's "Cores on this PC" choice for side by side runs: "all", "match" (the
        vCores of its Fabric session) or a number, with what each comes to on this PC."""
        engine = _engine(cells)
        vcores = sbs.fabric_vcores(cells, engine)
        choice = self._sbs_cores_choice(name)
        pc = os.cpu_count()
        return {"choice": choice, "pc_cores": pc, "engine": engine, "fabric_vcores": vcores,
                "cores": sbs.cores_for(choice, vcores, pc) or pc}

    def side_by_side_cores_set(self, name: str, cells: list[dict[str, Any]], choice: Any) -> dict[str, Any]:
        """Keep the notebook's choice: "all", "match", or a number of cores from 1 to this PC's."""
        pc = os.cpu_count() or 1
        if isinstance(choice, bool) or not (choice in sbs.CORE_CHOICES or (isinstance(choice, int) and 1 <= choice <= pc)):
            raise ValueError(f"Cores must be all, match, or a number from 1 to {pc}")
        self.notebooks.set_extra(name, "sbs_cores", choice)  # type: ignore[attr-defined]
        return self.side_by_side_cores(name, cells)

    def side_by_side_start(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                           compute: str | None = None, workspace: dict[str, str] | None = None,
                           confirm_writes: list[str] | None = None, confirm_unknown: bool = False,
                           location_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Run the notebook on this PC, then publish and run it in Fabric, then compare. Returns at once;
        side_by_side_status reports progress. Takes what the notebook screen sends (publishPayload):
        ``location_ref`` is the repository location's lakehouse, used when the notebook has none and
        never saved into it.

        Both runs write the attached lakehouse's real tables (Prove is the sandboxed way). So a notebook
        that writes tables runs only when the caller confirms exactly the tables side_by_side_writes
        lists, and confirm_unknown when that says reading can't tell."""
        saved_ref, lakehouse_ref = lakehouse_ref, lakehouse_ref or location_ref
        runs = self._sbs_state()
        if any(r["state"] in ("local", "fabric", "checking") for r in runs.values()):
            raise RuntimeError("A side-by-side run is already going. Wait for it, or cancel it.")
        self._replay_refuse(name, "running it side by side")  # type: ignore[attr-defined]
        if self._replay_live():  # type: ignore[attr-defined]
            raise RuntimeError("A replay is using the kernel. End it first.")
        if not lakehouse_ref:
            raise ValueError("Attach a lakehouse first. Both runs read and write it, so their results can be compared.")
        step = next((n for n, c in enumerate(cells, 1) if is_step(c)), None)
        if step:
            raise ValueError(f"This notebook can't run side by side: Cell {step} {STEP_REFUSED}")
        check = compute_check(cells, lakehouse_ref, **self._checks(name))  # type: ignore[attr-defined]
        for where, label in (("local", "on this PC"), ("fabric", "in Fabric")):
            if check[where]["reasons"]:  # Unknown doesn't stop it: comparing the runs is how to find out
                reason = check[where]["reasons"][0]
                cell = f"Cell {reason['cell']} " if reason.get("cell") else "It "
                raise ValueError(f"This notebook can't run {label}: {cell}{reason['text']}.")
        writes = _writes(cells)
        if sorted(confirm_writes or []) != writes["tables"] or (writes["unknown"] and not confirm_unknown):
            raise PermissionError("Side by side writes this notebook's tables for real, on this PC and then in Fabric. "
                                  "Confirm the tables it writes first, or use Prove, which keeps both runs' writes in a sandbox.")
        # Both runs use the Parameters bar's values: Fabric as the run's parameters, this PC as the
        # lines runCell adds after the parameter cell. A value the bar shows in red stops it here.
        parameters = self._bar_parameters(name, cells)  # type: ignore[attr-defined]
        saved = self.notebooks.save(name, cells, saved_ref, compute, workspace)  # type: ignore[attr-defined]
        # The code revision a receipt would name, so a comparison counts only for the code it ran.
        revision = receipts.code_revision([dict(c) | {"cell_id": cid} for c, cid in zip(cells, saved["cell_ids"], strict=True)])
        ws = self._publish_workspace(name, lakehouse_ref, workspace)  # type: ignore[attr-defined]
        # Sign in now, while the app is in front, rather than from the background.
        self.auth.storage_token()  # type: ignore[attr-defined]
        self.auth.fabric_token()  # type: ignore[attr-defined]
        code = [i for i, c in enumerate(cells) if notebook_cells.runs(c)]
        engine = _engine(cells)
        vcores = sbs.fabric_vcores(cells, engine)
        cores = sbs.cores_for(self._sbs_cores_choice(name), vcores, os.cpu_count())
        record: dict[str, Any] = {
            "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "name": name, "state": "local",
            "started_at": _now(), "finished_at": None, "message": "",
            # Workspace ids, so a tool limited to some workspaces can be shown the run (HD-014).
            "lakehouse": {"name": lakehouse_ref.get("name", ""), "workspace_name": lakehouse_ref.get("workspace_name", ""),
                          "workspace_id": lakehouse_ref.get("workspace_id", ""),
                          "lakehouse_id": lakehouse_ref.get("lakehouse_id", "")},
            "workspace_id": ws["workspace_id"], "workspace_name": ws["workspace_name"], "machine": sbs.machine(cores),
            "code_revision": revision["revision"],
            "local": {"status": "pending", "seconds": None, "cell": None, "cells_total": len(code), "cells": {}},
            "fabric": {"status": "pending", "engine": engine, "vcores": vcores},
            "tables": [], "verdict": None, "speed": {}, "_cancel": False,
            "_cores": cores, "_parameters": parameters, "_saved_ref": saved_ref, "_suffixes": self._bar_suffixes(name, cells)}  # type: ignore[attr-defined]
        runs[record["id"]] = record
        threading.Thread(target=self._sbs_run, args=(record, cells, code, lakehouse_ref, compute, ws), daemon=True,
                         name=f"side-by-side-{record['id']}").start()
        return self.side_by_side_status(record["id"])

    def side_by_side_status(self, id: str) -> dict[str, Any]:
        record = self._sbs_state().get(id) or next((r for r in self._sbs_saved() if r.get("id") == id), None)
        if record is None:
            raise LookupError(f"No side-by-side run {id}")
        return _copy(record)

    def side_by_side_list(self, name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Comparisons, newest first (running ones included); ``name`` keeps one notebook's."""
        saved = {r["id"]: r for r in self._sbs_saved()}
        for r in self._sbs_state().values():
            live = _copy(r)
            live["local"].pop("cells", None)
            saved[r["id"]] = live
        rows = [r for r in saved.values() if not name or r.get("name") == name]
        return sorted(rows, key=lambda r: r.get("started_at") or "", reverse=True)[:limit]

    def side_by_side_cancel(self, id: str) -> dict[str, Any]:
        record = self._sbs_state().get(id)
        if record is None or record["state"] not in ("local", "fabric", "checking"):
            return {"cancelled": False}
        record["_cancel"] = True
        if record["state"] == "local" and record.get("_kernel") is not None:
            # The run's own process (a core cap): nobody's variables to keep, and an interrupt can
            # miss a cell stuck in native code, so end it and everything it started.
            record["_kernel"].stop()
        elif record["state"] == "local":
            self.sessions.kernel(record["name"]).interrupt()  # type: ignore[attr-defined]
        elif record["fabric"].get("job_id"):
            f = record["fabric"]
            try:
                self.fabric_jobs.cancel_job(f["workspace_id"], f["item_id"], f["job_id"])  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                record["message"] = f"Couldn't cancel the Fabric run: {exc}"
        return {"cancelled": True}

    def benchmark_notebooks(self, lakehouse_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Add the TPC-H benchmark notebooks (benchmark.py). Ones already here are left as they are."""
        from . import benchmark
        added = []
        for name, cells in benchmark.notebooks().items():
            if not self.notebooks.path(name).exists():  # type: ignore[attr-defined]
                self.notebooks.save(name, cells, lakehouse_ref)  # type: ignore[attr-defined]
                added.append(name)
        return {"notebooks": [benchmark.DATA_NOTEBOOK, benchmark.BENCHMARK_NOTEBOOK], "added": added}

    # ---- the run, on its own thread

    def _sbs_tables(self, lakehouse_ref: dict[str, str]) -> sbs.Tables:
        schemas = self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])  # type: ignore[attr-defined]
        return OneLakeTables(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"], schemas,
                             self.auth.storage_token)  # type: ignore[attr-defined]

    def _sbs_run(self, record: dict[str, Any], cells: list[dict[str, Any]], code: list[int],
                 lakehouse_ref: dict[str, str], compute: str | None, ws: dict[str, str]) -> None:
        try:
            tables = self._sbs_tables(lakehouse_ref)
            before = tables.versions()
            self._sbs_local(record, cells, code, lakehouse_ref)
            after_local = tables.versions()
            if record["_cancel"]:
                raise _Cancelled()
            record["state"] = "fabric"
            self._sbs_fabric(record, cells, lakehouse_ref, compute, ws)
            if record["_cancel"]:
                raise _Cancelled()
            record["state"] = "checking"
            after_fabric = tables.versions()
            record["tables"] = sbs.compare(tables, before, after_local, after_fabric)
            record["verdict"] = sbs.verdict(record["tables"], record["local"]["status"], record["fabric"]["status"],
                                            record["local"].get("failed_cell"))
            f = record["fabric"]
            if record["local"]["status"] == "ok" and f["status"] == "ok":
                # A run that stopped early isn't faster, it's shorter.
                record["speed"] = sbs.speed(record["local"]["seconds"], f.get("total_seconds"), f.get("run_seconds"),
                                            record["local"].get("processing_seconds"))
                record["speed"]["cores"] = sbs.cores_line(record["machine"], f.get("vcores"))
            record["state"] = "done"
        except _Cancelled:
            record["state"], record["message"] = "cancelled", "Cancelled."
        except Exception as exc:  # noqa: BLE001 - reported on the panel
            record["state"], record["message"] = "failed", f"{type(exc).__name__}: {exc}"
        record["finished_at"] = _now()
        self._sbs_save(record)

    def _sbs_local(self, record: dict[str, Any], cells: list[dict[str, Any]], code: list[int],
                   lakehouse_ref: dict[str, str]) -> None:
        """Every code cell in a fresh kernel, as Run all does, timed from the first cell to the last."""
        local = record["local"]
        local["status"] = "running"
        self._attach(lakehouse_ref)  # type: ignore[attr-defined]
        if record.get("_cores"):
            # Held to fewer cores: a process of its own, so the cap (cpu_cap.py) binds the run and
            # nothing else, and the notebook's own session keeps its variables.
            kernel = self._new_kernel(process=True)  # type: ignore[attr-defined]
            self._prepare_kernel(record["name"], kernel)  # type: ignore[attr-defined]
            kernel.cores = record["_cores"]
            record["_kernel"] = kernel
            try:
                self._sbs_cells(record, kernel, cells, code)
            finally:
                kernel.close()  # its process, and the output it spilled
            return
        kernel = self.sessions.kernel(record["name"])  # type: ignore[attr-defined]
        kernel.restart()  # a clean start, as Fabric has
        self._sbs_cells(record, kernel, cells, code)

    def _sbs_cells(self, record: dict[str, Any], kernel: Any, cells: list[dict[str, Any]], code: list[int]) -> None:
        local = record["local"]
        kernel.notebook_name = record["name"]
        kernel.resources_dir = self._resources_dir(record["name"])  # type: ignore[attr-defined]
        kernel.onelake_writes = self._onelake_writes(record["name"])  # type: ignore[attr-defined]
        kernel.widget_values = dict((self._saved_inputs(record["name"]) or {}).get("widgets") or {})  # type: ignore[attr-defined]
        suffixes = record.get("_suffixes") or []
        started = time.monotonic()
        status = "ok"
        for n, i in enumerate(code, 1):
            if record["_cancel"]:
                status = "cancelled"
                break
            local["cell"] = n
            extra = suffixes[i] if i < len(suffixes) else ""
            kernel.start(cells[i]["source"] + (f"\n{extra}" if extra else ""))
            snap = kernel.poll(-1, 0.0)
            outputs = snap.get("outputs") or []
            while snap["status"] == "running":
                local["cells"][str(i)] = {"status": "running", "seconds": snap["seconds"], "outputs": outputs}
                snap = kernel.poll(snap["version"], 1.0)
                if snap.get("outputs") is not None:
                    outputs = snap["outputs"]
            snap = kernel.poll(-1, 0.0)  # the finished cell, with all its output
            local["cells"][str(i)] = {"status": snap["status"], "seconds": snap["seconds"],
                                      "outputs": snap.get("outputs") or outputs, "execution_count": snap.get("execution_count")}
            if snap.get("onelake"):
                local["cells"][str(i)]["onelake"] = snap["onelake"]
            if snap["status"] != "ok":
                status = "cancelled" if record["_cancel"] else "failed"
                local["failed_cell"] = i + 1
                err = next((o for o in reversed(local["cells"][str(i)]["outputs"]) if o.get("type") == "error"), None)
                if err:
                    local["message"] = f"{err.get('ename', 'Error')}: {err.get('evalue', '')}".strip()[:1000]
                break
        local["seconds"] = round(time.monotonic() - started, 2)
        local.update(_split(local["seconds"], [c.get("onelake") or {} for c in local["cells"].values()]))
        local["status"] = status

    def _sbs_fabric(self, record: dict[str, Any], cells: list[dict[str, Any]], lakehouse_ref: dict[str, str],
                    compute: str | None, ws: dict[str, str]) -> None:
        """Publish the notebook with the timing cells, run it on the workspace's capacity, wait for it."""
        f = record["fabric"]
        f["status"] = "publishing"
        first, last = sbs.timing_cells(record["id"])
        started = time.monotonic()
        item = self._publish(record["name"], cells, record.get("_saved_ref"), compute, ws,  # type: ignore[attr-defined]
                             fabric_cells=[first] + cells + [last], location_ref=lakehouse_ref)
        parameters = record.get("_parameters")
        job_id = self.fabric_jobs.run_item_job(item["workspace_id"], item["id"], NOTEBOOK_JOB,  # type: ignore[attr-defined]
                                               {"parameters": parameters} if parameters else None)
        f.update(status="running", job_id=job_id, item_id=item["id"], workspace_id=item["workspace_id"],
                 url=item["url"], submitted_at=_now())
        self._log_fabric_run({"kind": "Notebook run", "name": record["name"], "workspace_id": item["workspace_id"],  # type: ignore[attr-defined]
                              "workspace_name": item["workspace_name"], "engine": f["engine"], "item_id": item["id"],
                              "job_id": job_id, "url": item["url"], "side_by_side": record["id"]})
        job: dict[str, Any] = {}
        while time.monotonic() - started < MAX_FABRIC_SECONDS:
            time.sleep(POLL_SECONDS)
            try:
                job = self.fabric.job_instance(item["workspace_id"], item["id"], job_id)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - a dropped connection: ask again
                f["message"] = f"Status not available: {exc}"
                continue
            f["job_status"] = job.get("status")
            f["elapsed"] = round(time.monotonic() - started)
            if job.get("status") in TERMINAL:
                break
        f["total_seconds"] = round(time.monotonic() - started, 2)
        a, b = _parse(job.get("startTimeUtc")), _parse(job.get("endTimeUtc"))
        f["job_seconds"] = round((b - a).total_seconds(), 2) if a and b else None
        f["status"] = {"Completed": "ok", "Cancelled": "cancelled"}.get(job.get("status", ""), "failed")
        reason = job.get("failureReason") or {}
        f["message"] = reason.get("message", "") if isinstance(reason, dict) else str(reason)
        files = FileStore(self.auth.storage_token)  # type: ignore[attr-defined]
        timing_path = f"{sbs.TIMING_DIR}/{record['id']}.json"
        try:
            f["run_seconds"] = sbs.parse_timing(files.read_bytes(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"], timing_path))
            files.delete(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"], timing_path)
        except Exception:  # noqa: BLE001 - only the start-up split is lost
            f["run_seconds"] = None
        if f.get("job_seconds") and f.get("run_seconds"):
            f["start_seconds"] = round(max(f["job_seconds"] - f["run_seconds"], 0), 2)
        cfg = self._cost_settings()  # type: ignore[attr-defined]
        cu = float(cfg["python_cu"] if f["engine"] == "python" else cfg["spark_cu"])
        if f.get("job_seconds"):
            f["cu_seconds"] = round(f["job_seconds"] * cu, 1)
            f["cost"] = round(f["cu_seconds"] / 3600 * float(cfg["price_per_cu_hour"]), 4)
            f["currency"] = cfg["currency"]


def _split(seconds: float, cells: list[dict[str, Any]]) -> dict[str, Any]:
    """This PC's time as processing and waiting on OneLake writes, with what the writes uploaded.

    Cells run one after another, so their waits add up; so do their upload times (each cell's own
    writes counted once however many ran side by side)."""
    wait = min(sum(c.get("wait_seconds", 0) for c in cells), seconds)
    upload = sum(c.get("upload_seconds", 0) for c in cells)
    total = {k: sum(c.get(k, 0) for c in cells) for k in ("tables", "rows", "bytes", "skipped")}
    return {"processing_seconds": round(seconds - wait, 2), "upload_wait_seconds": round(wait, 2),
            "upload": {"seconds": round(upload, 2), **total,
                       "mb_per_second": round(total["bytes"] / 1e6 / upload, 2) if upload > 0 and total["bytes"] else None}}


def _writes(cells: list[dict[str, Any]]) -> dict[str, Any]:
    tables: set[str] = set()
    unknown: list[str] = []
    for n, c in enumerate(cells, 1):
        if c.get("type") == "markdown" or not (c.get("source") or "").strip() or (c.get("meta") or {}).get("frozen"):
            continue
        a = depgraph.analyze(c["source"])
        tables |= {w["name"] for w in a["writes"] if w["kind"] == "table"}
        unknown += [f"Cell {n}: {d['text']}" for d in a["dynamic"]]
    return {"tables": sorted(tables), "unknown": bool(unknown), "reasons": unknown}


class _Cancelled(Exception):
    pass


def _engine(cells: list[dict[str, Any]]) -> str:
    """The kind of Fabric notebook a side by side run publishes."""
    return "python" if uses_local_module(cells) and not uses_spark(cells) else "spark"


def _copy(record: dict[str, Any]) -> dict[str, Any]:
    """A snapshot of a record the run's thread may be changing right now."""
    for _ in range(20):
        try:
            return json.loads(json.dumps({k: v for k, v in record.items() if not k.startswith("_")}, default=str))
        except RuntimeError:  # changed size while being copied: try again
            time.sleep(0.01)
    raise RuntimeError("The side-by-side run is busy; try again")
