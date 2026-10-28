"""Schedules in one place: runs on this PC (Task Scheduler) and schedules in Fabric's job scheduler."""

from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import notebook_cells
from .fabric_api import FabricClient
from .fabric_run import TERMINAL, describe_schedule, schedule_configuration
from .fabric_step import is_step
from .local_schedules import LocalSchedules, check_target, target_label
from .orchestration import definition as pipeline_definition
from .orchestration.definition import DefinitionError
from .redact import describe
from .time_zones import time_zones
from .workloads.notify import Outbox, send, single

_log = logging.getLogger(__name__)
_PIPELINE_POLL = 0.25  # seconds between looks at a scheduled pipeline run

SCHEDULE_ALERTS = "schedule_alerts.sqlite3"
ALERTS_LOCK = "_alerts"  # a schedule_locks name no schedule id takes


class TeamsUnreachable(Exception):
    """A waiting alert's post failed; the message is the original error without the webhook."""

# Fabric item types that have schedules, and the job type each one runs.
SCHEDULED_TYPES = {"Notebook": "RunNotebook", "DataPipeline": "Pipeline", "SparkJobDefinition": "sparkjob"}
TYPE_LABELS = {"Notebook": "Notebook", "DataPipeline": "Pipeline", "SparkJobDefinition": "Spark job"}
STALE_LOCK_SECONDS = 12 * 3600


def _without_webhook(text: str, url: Any) -> str:
    """``text`` with the webhook's path and query replaced: a legacy Office 365 connector URL carries its
    secret in the path, which requests puts in connection errors and redact.secrets doesn't know."""
    if not isinstance(url, str) or not url:
        return text
    parts = urlsplit(url)
    for piece in sorted({url, parts.path + ("?" + parts.query if parts.query else ""), parts.path, parts.query},
                        key=len, reverse=True):
        if len(piece) > 1:
            text = text.replace(piece, "[webhook]")
    return text


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class ScheduleMixin:
    _fabric_schedules: dict[str, Any] | None = None
    _fabric_schedules_lock = threading.Lock()

    @property
    def pc_schedules(self) -> LocalSchedules:
        return LocalSchedules(self.root)  # type: ignore[attr-defined]

    # ---- schedules on this PC

    def add_pc_schedule(self, target: dict[str, Any], schedule: dict[str, Any]) -> dict[str, Any]:
        if target.get("kind") == "notebook":
            self.notebooks.load(target.get("name", ""))  # type: ignore[attr-defined]  # it exists
        elif target.get("kind") == "pipeline":
            target = self._pipeline_schedule_target(target)
        elif target.get("kind") == "meta":
            project = self._project()  # type: ignore[attr-defined]
            unknown = [n for n in target.get("entities") or [] if n not in project.entities]
            if unknown:
                raise ValueError(f"Not in the project: {', '.join(unknown)}")
        item = self.pc_schedules.add(target, schedule)
        return {"schedule": item, "windows": self.pc_schedules.scheduler.available}

    def _pipeline_schedule_target(self, target: dict[str, Any]) -> dict[str, Any]:
        """A pipeline schedule's target, once the pipeline is known to run here with these settings:
        a scheduled run has no one to give a missing mock. The folder is read again at every run, so
        a change pulled from Git is what the next run runs."""
        target = check_target(target)
        secure = set()
        try:
            imp = pipeline_definition.load(Path(target["path"]))
            secure = {n for n, d in imp.pipeline.parameters.items()
                      if isinstance(d, dict) and str(d.get("type", "")).lower() == "securestring"}
        except (DefinitionError, OSError) as exc:
            raise ValueError(f"Could not read the pipeline: {exc}") from exc
        kept = sorted(secure & set(target["parameters"]))
        if kept:
            raise ValueError(f"{', '.join(kept)} {'is a SecureString' if len(kept) == 1 else 'are SecureStrings'}; "
                             "a schedule doesn't keep secret values. Leave it out to use the pipeline's default.")
        check = self.pipeline_run_check(path=target["path"], parameters=target["parameters"],  # type: ignore[attr-defined]
                                        mocks=target["mocks"], notebooks=target["notebooks"] or None,
                                        read_lakehouses=target["read_lakehouses"],
                                        workspace_id=target["workspace_id"] or None,
                                        sql_projects=target["sql_projects"] or None)
        if not check["ok"]:
            raise ValueError("The pipeline can't run on this PC with these settings: "
                             + " ".join(p["message"] for p in check["problems"]))
        return target | {"name": check["pipeline"]}

    def pc_schedules_for(self, kind: str, name: str = "", env: str = "", path: str = "") -> list[dict[str, Any]]:
        """This PC's schedules for one notebook (kind "notebook"), one environment (kind "meta"), one
        pipeline folder (kind "pipeline"), or all."""
        if kind == "all":
            return self.pc_schedules.listing()
        key, value = {"notebook": ("name", name), "pipeline": ("path", path)}.get(kind, ("env", env))
        return [s for s in self.pc_schedules.listing() if s["target"]["kind"] == kind and s["target"].get(key) == value]

    def delete_pc_schedule(self, schedule_id: str) -> dict[str, Any]:
        self.pc_schedules.delete(schedule_id)
        return {"deleted": schedule_id}

    def set_pc_schedule_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        return self.pc_schedules.set_enabled(schedule_id, enabled)

    def update_pc_schedule(self, schedule_id: str, schedule: dict[str, Any]) -> dict[str, Any]:
        """Change when one of this PC's schedules runs (PB-048)."""
        return self.pc_schedules.update(schedule_id, schedule)

    def time_zones(self) -> dict[str, Any]:
        """Every Windows time zone and this PC's, for the Fabric schedule form (PB-048)."""
        return time_zones()

    def run_pc_schedule_now(self, schedule_id: str) -> dict[str, Any]:
        """Start the run in its own process, as Task Scheduler would, so the app stays free."""
        self.pc_schedules.get(schedule_id)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([sys.executable, "-m", "fabric_desktop.cli", "run-schedule", schedule_id, "--manual"],
                         cwd=str(self.root), creationflags=flags, env=os.environ.copy(),  # type: ignore[attr-defined]
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"started": schedule_id}

    def pc_schedule_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.pc_schedules.runs(limit)

    def notebook_runs(self, name: str, limit: int = 50, refresh: bool = False) -> list[dict[str, Any]]:
        """One notebook's recent runs, newest first: this PC's scheduled runs and the Fabric runs this app
        started for it. Reads the two run logs; ``refresh`` asks Fabric for unfinished jobs' status, as
        Monitor > Runs does (fabric_runs), and never starts a job."""
        listing = self.pc_schedules.listing()
        ours = {s["id"] for s in listing if s["target"]["kind"] == "notebook" and s["target"].get("name") == name}
        meta = {s["id"] for s in listing if s["target"]["kind"] != "notebook"}
        rows: list[dict[str, Any]] = []
        for r in self.pc_schedules.runs(500):
            # A removed schedule's runs still count: they carry the notebook's name as their label.
            if r.get("schedule_id") in ours or (r.get("schedule_id") not in meta and r.get("label") == name):
                rows.append({"source": "pc", "started": r.get("started"), "ended": r.get("ended"),
                             "seconds": r.get("seconds"), "status": r.get("status", ""),
                             "trigger": "manual" if r.get("trigger") == "manual" else "schedule",
                             "message": r.get("message", ""), "alert_error": r.get("alert_error", ""),
                             "cells": r.get("cells"), "schedule_id": r.get("schedule_id")})
        mine = self._notebook_fabric_runs(name, self.fabric_runs(500, refresh=False))  # type: ignore[attr-defined]
        if refresh:  # ask Fabric about this notebook's unfinished jobs only, never every open run
            going = [r["job_id"] for r in mine if r.get("status") not in TERMINAL and r.get("job_id")]
            if going:
                mine = self._notebook_fabric_runs(name, self.fabric_runs(500, refresh=True, job_ids=going))  # type: ignore[attr-defined]
        from .cloud_service import FABRIC_APP
        for r in mine:
            seconds = None
            if r.get("finished_at") and r.get("started_at"):
                seconds = round((dt.datetime.fromisoformat(r["finished_at"].replace("Z", "+00:00"))
                                 - dt.datetime.fromisoformat(r["started_at"])).total_seconds(), 1)
            url = r.get("url") or (f"{FABRIC_APP}/groups/{r['workspace_id']}/synapsenotebooks/{r['item_id']}"
                                   "?experience=fabric-developer" if r.get("item_id") else "")
            trigger = ("prove" if r.get("proof") else "side_by_side" if r.get("side_by_side")
                       else "run_now" if r["kind"] == "Scheduled item run" else "fabric")
            rows.append({"source": "fabric", "started": r.get("started_at"), "ended": r.get("finished_at"),
                         "seconds": seconds, "status": r.get("status", ""), "trigger": trigger,
                         "message": r.get("message", ""), "workspace_name": r.get("workspace_name", ""),
                         "url": url, "job_id": r.get("job_id")})
        rows.sort(key=lambda r: str(r["started"] or ""), reverse=True)
        return rows[:limit]

    def _notebook_fabric_runs(self, name: str, fabric: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The Fabric runs that are this notebook's: by its Fabric item when the app knows it (the item it
        was published to), so a renamed notebook or a same-named one in another workspace doesn't pick up
        the wrong runs; else by name, in its publish workspace when it has one."""
        kinds = ("Notebook run", "Scheduled item run")
        link = self.workload_store.links().get(name)  # type: ignore[attr-defined]
        if link:
            return [r for r in fabric if r.get("kind") in kinds and r.get("item_id") == link["item_id"]]
        try:
            nb = self.notebooks.load(name)  # type: ignore[attr-defined]
            ws = self._publish_workspace(name, nb.get("lakehouse"), nb.get("workspace"))["workspace_id"]  # type: ignore[attr-defined]
        except (OSError, ValueError):  # not on this PC, or no workspace picked yet: by name alone
            ws = None
        mine = [r for r in fabric if r.get("name") == name and r.get("kind") == "Notebook run"
                and (ws is None or r.get("workspace_id") == ws)]
        items = {r.get("item_id") for r in mine}
        # Run now on a Fabric schedule (Monitor > Fabric > Schedules) logs no item type: count it when the item is
        # one this notebook ran as, so a pipeline with the same name isn't listed.
        return mine + [r for r in fabric if r.get("kind") == "Scheduled item run" and r.get("name") == name
                       and r.get("item_id") in items]

    def sync_pc_schedules(self) -> dict[str, Any]:
        """Called when the app starts: point every task at this engine."""
        try:
            return {"updated": self.pc_schedules.sync()}
        except Exception as exc:  # Task Scheduler refused: the list still shows them
            return {"updated": 0, "error": f"{type(exc).__name__}: {exc}"}

    def _lock(self, schedule_id: str) -> Path | None:
        folder = self.root / "schedule_locks"  # type: ignore[attr-defined]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{schedule_id}.lock"
        if path.exists() and time.time() - path.stat().st_mtime > STALE_LOCK_SECONDS:
            path.unlink(missing_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return path

    def run_schedule(self, schedule_id: str, trigger: str = "schedule") -> dict[str, Any]:
        """One scheduled run on this PC (Task Scheduler calls this through the CLI)."""
        schedules = self.pc_schedules
        s = schedules.get(schedule_id)
        lock = self._lock(schedule_id)
        row: dict[str, Any] = {"schedule_id": schedule_id, "label": target_label(s["target"]), "trigger": trigger,
                               "started": _now(), "pid": os.getpid()}
        if lock is None:
            row |= {"ended": _now(), "seconds": 0, "status": "skipped",
                    "message": "The previous run of this schedule was still going"}
            schedules.log_run(row)
            return row
        t0 = time.monotonic()
        try:
            if s["target"]["kind"] == "notebook":
                row |= self._run_notebook_here(s["target"]["name"])
            elif s["target"]["kind"] == "pipeline":
                row |= self._run_pipeline_here(s["target"])
            else:
                row |= self._run_meta_here(s["target"]["env"], s["target"].get("entities") or None)
        except Exception as exc:
            row |= {"status": "failed", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            lock.unlink(missing_ok=True)
        row |= {"ended": _now(), "seconds": round(time.monotonic() - t0, 1)}
        problem = self._send_schedule_alerts(row)
        if problem:  # recorded with the run, so Monitor shows the alert didn't go out
            row["alert_error"] = problem
        schedules.log_run(row)
        return row

    def _run_notebook_here(self, name: str) -> dict[str, Any]:
        nb = self.notebooks.load(name)  # type: ignore[attr-defined]
        self._attach(nb["lakehouse"])  # type: ignore[attr-defined]
        self.kernel.resources_dir = self._resources_dir(name)  # type: ignore[attr-defined]  # builtin/ paths
        self.kernel.onelake_writes = self._onelake_writes(name)  # type: ignore[attr-defined]
        # As Run all: code cells with something in them, and never a frozen one.
        frozen = sum(1 for c in nb["cells"] if notebook_cells.frozen(c) and c.get("type") != "markdown"
                     and (c.get("source") or "").strip())
        code = [c for c in nb["cells"] if notebook_cells.runs(c)]
        skipped = f", {frozen} frozen cell{'s' if frozen != 1 else ''} skipped" if frozen else ""
        if any(is_step(c) for c in code):  # it runs on Fabric Spark only when started from the notebook
            code = [c for c in code if not is_step(c)]
            skipped += ", the Fabric step skipped"
        for i, cell in enumerate(code, 1):
            res = self.kernel.run(cell["source"])  # type: ignore[attr-defined]
            if res["status"] not in ("ok",):
                err = next((o for o in res["outputs"] if o.get("type") == "error"), {})
                why = f"{err.get('ename')}: {err.get('evalue')}" if err else res["status"]
                return {"status": "failed", "message": f"Cell {i} of {len(code)} failed{skipped}: {why}", "cells": i}
        return {"status": "ok", "message": f"{len(code)} cell{'s' if len(code) != 1 else ''} ran{skipped}", "cells": len(code)}

    def _run_pipeline_here(self, target: dict[str, Any]) -> dict[str, Any]:
        """A local pipeline run with the schedule's settings, followed to its end. The run is kept with
        the other local runs, so Monitor > Runs and the Pipelines page show it; ``run_id`` links to it."""
        started = self.pipeline_run_start(path=target["path"], parameters=target.get("parameters"),  # type: ignore[attr-defined]
                                          mocks=target.get("mocks"), notebooks=target.get("notebooks") or None,
                                          read_lakehouses=bool(target.get("read_lakehouses")),
                                          workspace_id=target.get("workspace_id") or None,
                                          sql_projects=target.get("sql_projects") or None)
        if not started["ok"]:
            return {"status": "failed", "message": "The pipeline can't run on this PC now: "
                    + " ".join(p["message"] for p in started["problems"])}
        rid = started["run_id"]
        record = self.pipeline_run_status(rid, values=False)  # type: ignore[attr-defined]
        while record["state"] in ("pending", "running"):
            time.sleep(_PIPELINE_POLL)
            record = self.pipeline_run_status(rid, values=False)  # type: ignore[attr-defined]
        record = self.pipeline_run_status(rid)  # type: ignore[attr-defined]  # with error messages
        acts = record.get("activities") or []
        mocked = sum(1 for a in acts if a.get("executed_by") == "mock")
        what = f"{len(acts)} activit{'y' if len(acts) == 1 else 'ies'}" + (f", {mocked} mocked" if mocked else "")
        if record["state"] == "succeeded":
            return {"status": "ok", "message": f"{record['pipeline']} succeeded: {what}", "run_id": rid}
        failed = next((a for a in acts if a.get("state") == "failed"), None)
        why = f" {failed['name']}: {(failed.get('error') or {}).get('message') or 'failed'}" if failed else ""
        return {"status": "failed", "message": f"{record['pipeline']} {record['state']}: {what}.{why}", "run_id": rid}

    def _run_meta_here(self, env: str, names: list[str] | None) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        results = self._run_here(project, env, names)  # type: ignore[attr-defined]
        failed = [r for r in results if r["status"] not in ("ok", "skipped")]
        rows = sum(r.get("rows_written") or 0 for r in results)
        if failed:
            first = failed[0]
            return {"status": "failed", "tables": len(results), "rows": rows,
                    "message": f"{len(failed)} of {len(results)} tables didn't finish. {first['entity']}: {first['message']}"}
        return {"status": "ok", "tables": len(results), "rows": rows,
                "message": f"{len(results)} tables, {rows:,} rows written"}

    def _send_schedule_alerts(self, row: dict[str, Any]) -> str:
        """After every run: post the alerts Teams didn't take before, then this run's if it failed.
        Returns why this run's alert didn't go out, or "" (sent, queued after a success, or no webhook)."""
        from .dataops_service import post_teams
        failed = row["status"] == "failed"
        url = None
        try:
            url = self._settings().get("teams_webhook")  # type: ignore[attr-defined]
            if not url:
                return ""
            self._send_waiting_schedule_alerts(url, post_teams)  # older alerts first
            if not failed:
                return ""
            title = f"Scheduled run failed on this PC: {row['label']}"
            lines = [row.get("message") or "See Monitor > KratosForge > Schedules"]
            try:
                post_teams(url, title, lines)
                return ""
            except Exception as exc:  # kept, and sent after the next run here
                why = _without_webhook(describe(exc), url).rstrip(".")
                self._schedule_alerts().add(title, lines, why)
                problem = (f"Couldn't post the Teams alert: {why}. It waits on this PC and is sent, marked Delayed, "
                           "after the next scheduled run here, for up to three days.")
        except Exception as exc:  # the run is still logged, with the webhook kept out of what it says
            if not failed:
                _log.warning("Couldn't send waiting schedule alerts: %s", _without_webhook(describe(exc), url))
                return ""
            problem = f"Couldn't post the Teams alert: {_without_webhook(describe(exc), url)}"
        _log.warning("%s (schedule %s)", problem, row.get("schedule_id"))
        return problem

    def _schedule_alerts(self) -> Outbox:
        return Outbox(self.root / SCHEDULE_ALERTS)  # type: ignore[attr-defined]

    def _send_waiting_schedule_alerts(self, url: str, post: Any) -> None:
        """Failed-run alerts Teams didn't take (no network, Teams down) wait in schedule_alerts.sqlite3
        and go, marked Delayed, after the next run on this PC; one older than three days is dropped.
        Each run is its own process, so one sends them at a time and the others leave them."""
        if not (self.root / SCHEDULE_ALERTS).exists():  # type: ignore[attr-defined]
            return
        lock = self._lock(ALERTS_LOCK)
        if lock is None:
            return
        def scrubbed(to: str, title: str, lines: list[str]) -> None:
            # A legacy connector URL carries its secret in the path, which requests puts in its
            # errors; what the outbox keeps of a failed post must not have it.
            try:
                post(to, title, lines)
            except Exception as exc:
                raise TeamsUnreachable(_without_webhook(describe(exc), to)) from None
        try:
            result = send([], single(url), self._schedule_alerts(), "this PC's schedules", scrubbed)
        finally:
            lock.unlink(missing_ok=True)
        if result.get("error"):
            _log.warning("Couldn't post waiting schedule alerts; they go after the next run: %s",
                         _without_webhook(result["error"], url))

    # ---- every schedule, in one list

    def _schedule_workspaces(self, all_workspaces: bool, token: str) -> list[dict[str, str]]:
        """Workspaces whose Fabric schedules are listed: every one, or the ones this app works in."""
        listed = FabricClient(lambda: token).list_workspaces()
        if all_workspaces:
            return [{"id": w["id"], "name": w["displayName"]} for w in listed]
        names = {w["id"]: w["displayName"] for w in listed}
        wanted: set[str] = set(self.workspace_marks().get("pins", []))  # type: ignore[attr-defined]
        try:
            project = self._project()  # type: ignore[attr-defined]
            wanted |= {c.get("workspace_id") for c in project.environments.values() if c.get("workspace_id")}
        except Exception as exc:
            _log.info("No metadata project; its workspaces aren't added: %s", describe(exc))
        for n in self.notebooks.list():  # type: ignore[attr-defined]
            try:
                nb = self.notebooks.load(n["name"])  # type: ignore[attr-defined]
            except Exception as exc:
                _log.warning("Couldn't load notebook %r to find its workspace: %s", n["name"], describe(exc))
                continue
            ws = (nb.get("workspace") or {}).get("workspace_id") or (nb.get("lakehouse") or {}).get("workspace_id")
            if ws:
                wanted.add(ws)
        return [{"id": w, "name": names[w]} for w in wanted if w in names]

    def all_schedules(self, refresh: bool = False, all_workspaces: bool = False) -> dict[str, Any]:
        """This PC's schedules now; Fabric's schedules from a background scan ({"pending": True} meanwhile)."""
        pc = self.pc_schedules.listing()
        with self._fabric_schedules_lock:
            cached = self._fabric_schedules
            scope = cached.get("all_workspaces") if cached else None
            if cached is None or refresh or (scope != all_workspaces and not cached.get("pending")):
                try:
                    token = self.auth.fabric_token()  # type: ignore[attr-defined]  # here: MSAL isn't thread-safe
                except Exception as exc:
                    self._fabric_schedules = {"pending": False, "items": [], "all_workspaces": all_workspaces,
                                              "error": f"{type(exc).__name__}: {exc}"}
                else:
                    self._fabric_schedules = {"pending": True, "items": [], "all_workspaces": all_workspaces}
                    threading.Thread(target=self._scan_fabric_schedules, args=(token, all_workspaces), daemon=True).start()
            fabric = dict(self._fabric_schedules or {})
        return {"pc": pc, "windows": self.pc_schedules.scheduler.available, "fabric": fabric,
                "runs": self.pc_schedules.runs(200), "pc_set_aside": self.pc_schedules.set_aside()}

    def _scan_fabric_schedules(self, token: str, all_workspaces: bool) -> None:
        client = FabricClient(lambda: token)
        try:
            workspaces = self._schedule_workspaces(all_workspaces, token)
        except Exception as exc:
            with self._fabric_schedules_lock:
                self._fabric_schedules = {"pending": False, "items": [], "all_workspaces": all_workspaces,
                                          "error": f"{type(exc).__name__}: {exc}"}
            return

        missed: list[str] = []  # one log line per scan, not one per item (a large tenant could flood the log)

        def items(ws: dict[str, str]) -> list[dict[str, Any]]:
            try:
                return [{"workspace_id": ws["id"], "workspace_name": ws["name"], **it}
                        for it in client.list_items(ws["id"]) if it.get("type") in SCHEDULED_TYPES]
            except Exception as exc:
                missed.append(f"items in workspace {ws['id']}: {describe(exc)}")
                return []

        def schedules(it: dict[str, Any]) -> list[dict[str, Any]]:
            job = SCHEDULED_TYPES[it["type"]]
            try:
                found = client.list_schedules(it["workspace_id"], it["id"], job)
            except Exception as exc:
                missed.append(f"schedules of item {it['id']}: {describe(exc)}")
                return []
            if not found:
                return []
            last = None
            try:
                runs = client.list_job_instances(it["workspace_id"], it["id"])
                last = runs[0] if runs else None
            except Exception as exc:
                missed.append(f"last run of item {it['id']}: {describe(exc)}")
            return [{"id": s["id"], "enabled": s.get("enabled", False), "text": describe_schedule(s),
                     "owner": s.get("owner") if isinstance(s.get("owner"), dict) else None,  # Fabric runs it as its owner
                     "configuration": s.get("configuration") or {}, "job_type": job,
                     "workspace_id": it["workspace_id"], "workspace_name": it["workspace_name"],
                     "item_id": it["id"], "name": it.get("displayName"), "type": TYPE_LABELS[it["type"]], "item_type": it["type"],
                     "last": {"status": last.get("status"), "started": last.get("startTimeUtc"),
                              "ended": last.get("endTimeUtc"),
                              "message": (last.get("failureReason") or {}).get("message", "")} if last else None}
                    for s in found]

        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [it for chunk in pool.map(items, workspaces) for it in chunk]
            rows = [r for chunk in pool.map(schedules, found) for r in chunk]
        if missed:
            _log.warning("Fabric schedules: %d reads failed; the first was %s", len(missed), missed[0])
        rows.sort(key=lambda r: (r["workspace_name"].lower(), str(r["name"]).lower()))
        with self._fabric_schedules_lock:
            self._fabric_schedules = {"pending": False, "items": rows, "all_workspaces": all_workspaces,
                                      "workspaces": len(workspaces), "scanned": _now()}

    def set_fabric_schedule_enabled(self, workspace_id: str, item_id: str, job_type: str, schedule_id: str,
                                    enabled: bool, configuration: dict[str, Any]) -> dict[str, Any]:
        out = self.fabric_jobs.update_schedule(workspace_id, item_id, job_type, schedule_id,  # type: ignore[attr-defined]
                                               enabled, configuration)
        self._forget_fabric_schedule(schedule_id, enabled=enabled)
        return out

    def update_fabric_schedule(self, workspace_id: str, item_id: str, job_type: str, schedule_id: str,
                               schedule: dict[str, Any], enabled: bool) -> dict[str, Any]:
        """Change when a Fabric schedule runs, in place (Update Item Schedule): it keeps its id and whether
        it is on (PB-048)."""
        config = schedule_configuration(schedule.get("kind", "daily"), schedule.get("times"),
                                        schedule.get("every_minutes"), schedule.get("weekdays"),
                                        schedule.get("time_zone") or "UTC")
        out = self.fabric_jobs.update_schedule(workspace_id, item_id, job_type, schedule_id,  # type: ignore[attr-defined]
                                               enabled, config)
        with self._fabric_schedules_lock:
            for r in (self._fabric_schedules or {}).get("items", []):
                if r["id"] == schedule_id:
                    r["configuration"] = config
                    r["text"] = describe_schedule({"configuration": config})
        return out

    def delete_fabric_schedule(self, workspace_id: str, item_id: str, job_type: str, schedule_id: str) -> dict[str, Any]:
        self.fabric_jobs.delete_schedule(workspace_id, item_id, job_type, schedule_id)  # type: ignore[attr-defined]
        self._forget_fabric_schedule(schedule_id)
        return {"deleted": schedule_id}

    def _forget_fabric_schedule(self, schedule_id: str, enabled: bool | None = None) -> None:
        with self._fabric_schedules_lock:
            cached = self._fabric_schedules
            if not cached:
                return
            if enabled is None:
                cached["items"] = [r for r in cached["items"] if r["id"] != schedule_id]
            else:
                for r in cached["items"]:
                    if r["id"] == schedule_id:
                        r["enabled"] = enabled

    def run_fabric_item_now(self, workspace_id: str, item_id: str, job_type: str, name: str,
                            workspace_name: str = "", item_type: str = "") -> dict[str, Any]:
        jobs = self.fabric_jobs  # type: ignore[attr-defined]
        if item_type in jobs.EXECUTE_PATHS:  # a copy job or a dataflow: Data Factory, not Spark
            job_id, engine = jobs.run_item_execute(workspace_id, item_id, item_type), "data_factory"
        else:
            job_id, engine = jobs.run_item_job(workspace_id, item_id, job_type), "spark"
        return self._log_fabric_run({"kind": "Scheduled item run", "name": name, "workspace_id": workspace_id,  # type: ignore[attr-defined]
                                     "workspace_name": workspace_name, "item_id": item_id, "job_id": job_id,
                                     "engine": engine})

