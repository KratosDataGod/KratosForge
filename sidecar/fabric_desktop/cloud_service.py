"""UI actions that use Fabric capacity on purpose, mixed into ``Service``.

Development runs on this PC; these hand finished work to Fabric: run a published notebook
there now or on a schedule, keep tables V-Ordered and vacuumed with Fabric's table
maintenance job, and put a Direct Lake semantic model over the last layer. Profiling is
here too: it runs locally and turns what it finds into cleaning rules and checks.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import polars as pl
from deltalake.exceptions import TableNotFoundError

from . import fabric_step, jsonfile, maintenance, notebook_files, profiling, retrying, semantic
from .auth import FABRIC_SCOPES, STORAGE_SCOPES
from .errors import NotSetUp
from .fabric_api import FabricClient, FabricError
from .fabric_compute import BUNDLE_DIR, RESULTS_DIR, RUNNER, bundle_files, bundle_hash, run_parameters, runner_ipynb
from .fabric_run import TERMINAL, describe_schedule, fabric_ipynb, schedule_configuration, uses_local_module, uses_spark
from .meta.engine import Engine, RunResult, apply_clean
from .meta.project import ENVIRONMENTS, GateError, validate
from .meta import keys as keys_mod
from .meta_service import NoMetadataProject
from .notebook import leaf_name
from .meta.store import META_LAKEHOUSE, META_SCHEMAS, LocalStore, OneLakeStore
from .onelake import FileStore, TableRef, storage_options
from .redact import describe, one_line

_log = logging.getLogger(__name__)
# How long Monitor waits for Fabric to say how its open runs are doing before showing the rest as last known.
RUNS_REFRESH_SECONDS = 30


class FabricChanged(RuntimeError):
    """The notebook's Fabric item changed since this app last published it."""

NOTEBOOK_JOB = "RunNotebook"
FABRIC_APP = "https://app.fabric.microsoft.com"
# A OneLake lakehouse is addressed by GUIDs; anything else is one Fabric can't reach.
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class CloudMixin:
    root: Path

    # ---- the log of jobs started from this app in Fabric (notebook runs, table maintenance)

    @property
    def fabric_runs_file(self) -> Path:
        return self.root / "fabric_runs.jsonl"

    def _read_fabric_runs(self) -> list[dict[str, Any]]:
        try:
            lines = self.fabric_runs_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def _write_fabric_runs(self, runs: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        jsonfile.write_text(self.fabric_runs_file, "".join(json.dumps(r) + "\n" for r in runs[-500:]))

    def _log_fabric_run(self, record: dict[str, Any]) -> dict[str, Any]:
        record = {"status": "NotStarted", "started_at": _now(), "finished_at": None, "message": ""} | record
        self._write_fabric_runs(self._read_fabric_runs() + [record])
        return record

    def fabric_runs(self, limit: int = 50, refresh: bool = True, job_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Jobs this app started in Fabric, newest first; unfinished ones are asked for their status
        (only those in ``job_ids`` when it is given)."""
        runs = self._read_fabric_runs()
        if refresh:
            open_runs = [r for r in runs[-limit:] if r.get("status") not in TERMINAL
                         and (job_ids is None or r.get("job_id") in job_ids)]
            fabric = self._quiet_fabric() if open_runs else None
            if fabric is not None:
                unchecked = self._refresh_fabric_runs(fabric, open_runs)
                self._write_fabric_runs(runs)
                if unchecked:
                    # Said only in this answer, not saved: the next refresh may well reach them.
                    note = "Status not checked this time: Fabric was slow or unreachable, so this is the last known status."
                    return [dict(r, stale=True, message=" ".join(x for x in (r.get("message"), note) if x))
                            if id(r) in unchecked else r for r in runs[-limit:][::-1]]
        return runs[-limit:][::-1]

    def _refresh_fabric_runs(self, fabric: FabricClient, open_runs: list[dict[str, Any]]) -> set[int]:
        """Ask Fabric how each open run is doing, newest first, and return the ones not asked (by id()).
        Monitor opens with this, and every other page's calls wait behind it, so no new request starts
        after RUNS_REFRESH_SECONDS, nor after two in a row found Fabric unreachable (a run Fabric no
        longer knows doesn't count). One request can itself take a few minutes (5 tries of up to 60 s)."""
        deadline, unreachable, unchecked = time.monotonic() + RUNS_REFRESH_SECONDS, 0, set()
        for r in reversed(open_runs):
            if time.monotonic() > deadline or unreachable >= 2:
                unchecked.add(id(r))
                continue
            try:
                job = fabric.job_instance(r["workspace_id"], r["item_id"], r["job_id"])
            except Exception as exc:  # offline or gone: keep the last known status
                r["message"] = r.get("message") or f"Status not available: {one_line(exc)}"
                status = retrying.status_of(exc)
                unreachable = unreachable + 1 if status is None or status in retrying.TRANSIENT_STATUS else 0
                continue
            unreachable = 0
            r["status"] = job.get("status", r["status"])
            r["finished_at"] = job.get("endTimeUtc") or r.get("finished_at")
            reason = job.get("failureReason") or {}
            r["message"] = one_line((reason.get("message") or "") if isinstance(reason, dict) else reason, 1000)
        return unchecked

    def _quiet_fabric(self) -> FabricClient | None:
        """A Fabric client only if the user is already signed in: Monitor must never pop a sign-in."""
        try:
            token = self.auth.silent_token(FABRIC_SCOPES)  # type: ignore[attr-defined]
        except Exception as exc:  # no client ID configured
            _log.info("No silent Fabric token for Monitor: %s", describe(exc))
            return None
        return FabricClient(lambda: token) if token else None

    def meta_runs_all(self, limit: int = 200) -> dict[str, Any]:
        """Metadata runs from every environment, newest first, for Monitor and Home.

        Environments in OneLake are read only when the user is already signed in, so this never
        pops a sign-in; ``unavailable`` says why an environment was left out. A project that can't be
        read (a broken project.toml, say) is not "no runs": ``project_error`` says why."""
        try:
            project = self._project()  # type: ignore[attr-defined]
        except NoMetadataProject:  # no metadata project yet
            return {"runs": [], "unavailable": {}}
        except Exception as exc:
            _log.warning("Couldn't read the metadata project for its runs: %s", describe(exc))
            return {"runs": [], "unavailable": {}, "project_error": describe(exc)}
        signed_in = None
        runs: list[dict[str, Any]] = []
        unavailable: dict[str, str] = {}
        for env in ENVIRONMENTS:
            cfg = self._env_cfg(project, env)  # type: ignore[attr-defined]
            local = cfg.get("target", "onelake" if env != "dev" else "local") == "local"
            if not local and not cfg.get("workspace_id"):
                continue  # not set up yet
            if not local:
                if signed_in is None:
                    try:
                        signed_in = bool(self.auth.silent_token(STORAGE_SCOPES)) and self._quiet_fabric() is not None  # type: ignore[attr-defined]
                    except Exception:
                        signed_in = False
                if not signed_in:
                    unavailable[env] = "Sign in to see its runs"
                    continue
            try:
                rows = self.meta_runs(env, limit)  # type: ignore[attr-defined]
            except Exception as exc:  # offline, or a lakehouse is gone
                unavailable[env] = str(exc)
                continue
            for r in rows:
                r["env"] = env
                r["compute"] = "fabric" if str(r.get("batch_id") or "").startswith("fabric-") else "local"
            runs += rows
        runs.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        return {"runs": runs[:limit], "unavailable": unavailable}

    def cancel_fabric_run(self, workspace_id: str, item_id: str, job_id: str) -> dict[str, Any]:
        """Ask Fabric to stop a job. Fabric stops it in its own time, so a run this app logged is
        marked ``cancel_requested`` until fabric_runs reads Cancelled (or how it really ended)."""
        logged = next((r for r in self._read_fabric_runs() if r.get("job_id") == job_id), None)
        if logged and (logged.get("workspace_id"), logged.get("item_id")) != (workspace_id, item_id):
            raise ValueError("That job belongs to another item in the app's run log, so it wasn't cancelled.")
        if logged and logged.get("status") in TERMINAL:
            raise ValueError(f"{logged.get('name') or 'This run'} already ended in Fabric ({logged['status']}), "
                             "so there's nothing to cancel.")
        self.fabric_jobs.cancel_job(workspace_id, item_id, job_id)  # type: ignore[attr-defined]
        if logged:
            # Read again just before writing, so a status poll since the read above isn't undone. A poll
            # that writes in between can still drop the mark: the run then reads Running, not
            # Cancelling, until Fabric reports Cancelled, which is harmless.
            runs = self._read_fabric_runs()
            for r in runs:
                if r.get("job_id") == job_id and r.get("status") not in TERMINAL:
                    r["cancel_requested"] = _now()
            self._write_fabric_runs(runs)
        return {"cancelled": job_id}

    # ---- notebooks in Fabric

    def _publish_workspace(self, name: str, lakehouse_ref: dict[str, str] | None,
                           workspace: dict[str, str] | None = None) -> dict[str, str]:
        """Where the notebook is published: the workspace picked for it (passed now or saved with it),
        else the attached lakehouse's workspace."""
        if not (workspace and workspace.get("workspace_id")):
            path = self.notebooks.path(name)  # type: ignore[attr-defined]
            workspace = self.notebooks.load(name).get("workspace") if path.exists() else None  # type: ignore[attr-defined]
        if workspace and workspace.get("workspace_id"):
            return {"workspace_id": workspace["workspace_id"], "workspace_name": workspace.get("workspace_name", "")}
        if lakehouse_ref and lakehouse_ref.get("workspace_id"):
            return {"workspace_id": lakehouse_ref["workspace_id"], "workspace_name": lakehouse_ref.get("workspace_name", "")}
        raise ValueError(f"Pick a workspace to publish {name} to.")

    def _publish(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None,
                 compute: str | None = None, workspace: dict[str, str] | None = None,
                 fabric_cells: list[dict[str, Any]] | None = None,
                 location_ref: dict[str, str] | None = None, replace_fabric_changes: bool = False) -> dict[str, Any]:
        """``fabric_cells``, when given, is what goes to Fabric instead of ``cells`` (which are saved here).
        ``location_ref`` is the lakehouse from the notebook repository's locations for the current
        environment: Fabric gets it when the notebook has none of its own, but it isn't saved into
        the notebook, so the notebook keeps following the environment.

        When the Fabric item changed since this app last published it (someone edited it in Fabric),
        nothing is sent and FabricChanged says so, unless ``replace_fabric_changes``."""
        self._replay_refuse(name, "publishing it to Fabric")  # type: ignore[attr-defined]
        saved_ref, lakehouse_ref = lakehouse_ref, lakehouse_ref or location_ref
        ws = self._publish_workspace(name, lakehouse_ref, workspace)
        self.notebooks.save(name, cells, saved_ref, compute, ws)  # type: ignore[attr-defined]
        schemas = (self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])  # type: ignore[attr-defined]
                   if lakehouse_ref else True)
        doc = fabric_ipynb(fabric_cells or cells, lakehouse_ref, schemas)
        item_name = leaf_name(name)  # a notebook in a folder (notebook_folders) is named without it in Fabric
        self._fabric_name_free(name, ws)
        target, note = self._published_item(name, ws["workspace_id"])
        if not replace_fabric_changes:
            change = self._fabric_change(name, ws["workspace_id"], doc, target=(target,))
            if change and change["changed"]:
                raise FabricChanged(f"{name} changed in Fabric after it was last published from here, so it wasn't "
                                    "published. Publish it from the notebook to see the changes and choose.")
        if target is not None:  # the item it published before (by id), or the one with its name
            item = self.fabric.update_notebook(ws["workspace_id"], target, self._fabric_item_name(name, target),  # type: ignore[attr-defined]
                                               json.dumps(doc).encode())
        else:
            item = self.fabric.save_notebook(ws["workspace_id"], item_name, json.dumps(doc).encode())  # type: ignore[attr-defined]
        if not item.get("id"):  # a create can come back without the item; look it up
            item = next((n for n in self.fabric.list_notebooks(ws["workspace_id"])  # type: ignore[attr-defined]
                         if n["displayName"] == item_name), item)
        if item.get("id") and hasattr(self, "_record_notebook_item"):  # the workload keeps the item by id
            self._record_notebook_item(name, ws["workspace_id"], item["id"], ws["workspace_name"])  # type: ignore[attr-defined]
        if item.get("id"):
            try:
                notebook_files.remember_published(Path(self.root) / "published", item["id"], ws["workspace_id"], doc,  # type: ignore[attr-defined]
                                                  name=item_name)
            except (OSError, ValueError) as exc:  # the publish happened; only the next change check is lost
                _log.warning("Couldn't keep what was published for %r: %s", name, describe(exc))
        return {"name": name, "workspace_id": ws["workspace_id"], "workspace_name": ws["workspace_name"],
                "id": item.get("id"), "item_name": item.get("displayName") or item_name, "note": note,
                "url": f"{FABRIC_APP}/groups/{ws['workspace_id']}/synapsenotebooks/{item.get('id')}"
                       "?experience=fabric-developer"}

    def _fabric_change(self, name: str, workspace_id: str, doc: dict[str, Any] | None,
                       target: tuple[dict[str, Any] | None] | None = None) -> dict[str, Any] | None:
        """How the notebook's Fabric item changed since this app last published it, or None when
        that isn't known (never published from here, or the item is gone). ``target``, when given,
        is the item Publish writes, already looked up (_published_item)."""
        link = self.workload_store.links().get(name) if hasattr(self, "workload_store") else None  # type: ignore[attr-defined]
        if not link or link["workspace_id"] != workspace_id:
            return None
        # Check the item Publish writes: the linked one while it is there; when it is gone, the one with
        # this name, and if someone else made that one, Publish would replace theirs.
        if target is None:
            try:
                target = (self._published_item(name, workspace_id)[0],)
            except FabricError as exc:
                raise RuntimeError(f"Couldn't check whether {name} changed in Fabric ({describe(exc)}), so it wasn't "
                                   "published. Try again.") from exc
        target = target[0]
        if target is None:
            return None  # Publish makes a new item; nothing is replaced
        if target["id"] != link["item_id"]:
            return {"changed": True, "item_id": target["id"], "other_item": True,
                    "fabric": [{"kind": "other item", "position": None, "diff": []}], "mine": []}
        base = notebook_files.published(Path(self.root) / "published", link["item_id"])  # type: ignore[attr-defined]
        if not base:
            return None
        try:
            definition = self.fabric.get_item_definition(workspace_id, link["item_id"], "ipynb")  # type: ignore[attr-defined]
        except FabricError as exc:
            if exc.status == 404:
                return None  # deleted in Fabric: Publish makes it again
            raise RuntimeError(f"Couldn't check whether {name} changed in Fabric ({describe(exc)}), so it wasn't "
                               "published. Try again.") from exc
        return notebook_files.fabric_changes(base, notebook_files.ipynb_of(definition), doc) | {"item_id": link["item_id"]}

    def notebook_publish_check(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                               workspace: dict[str, str] | None = None,
                               location_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Before Publish or Run in Fabric: whether the Fabric item changed since this app last
        published it, what changed there and what changed here. Reads only."""
        lakehouse_ref = lakehouse_ref or location_ref
        try:
            ws = self._publish_workspace(name, lakehouse_ref, workspace)
        except ValueError:
            return {"changed": False, "known": False}
        schemas = (self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])  # type: ignore[attr-defined]
                   if lakehouse_ref else True)
        change = self._fabric_change(name, ws["workspace_id"], fabric_ipynb(cells, lakehouse_ref, schemas))
        return change | {"known": True} if change else {"changed": False, "known": False}

    def run_notebook_in_fabric(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None,
                               compute: str | None = None, workspace: dict[str, str] | None = None,
                               location_ref: dict[str, str] | None = None,
                               replace_fabric_changes: bool = False) -> dict[str, Any]:
        """Publish the notebook, then run it on its workspace's Fabric capacity, with the values set in
        its Parameters bar as the run's parameters (the published code keeps its own)."""
        parameters = self._bar_parameters(name, cells)  # type: ignore[attr-defined]
        item = self._publish(name, cells, lakehouse_ref, compute, workspace, location_ref=location_ref,
                             replace_fabric_changes=replace_fabric_changes)
        lakehouse_ref = lakehouse_ref or location_ref
        job_id = self.fabric_jobs.run_item_job(item["workspace_id"], item["id"], NOTEBOOK_JOB,  # type: ignore[attr-defined]
                                               {"parameters": parameters} if parameters else None)
        # As published: a Python notebook, or Spark (what the cost view counts it as).
        engine = "python" if lakehouse_ref and uses_local_module(cells) and not uses_spark(cells) else "spark"
        return self._log_fabric_run({"kind": "Notebook run", "name": name, "workspace_id": item["workspace_id"],
                                     "workspace_name": item["workspace_name"], "engine": engine,
                                     "item_id": item["id"], "job_id": job_id, "url": item["url"]})

    # ---- the Fabric step: a notebook's last cell, run on Fabric Spark (fabric_step.py)

    def _step_where(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None) -> dict[str, Any]:
        index = fabric_step.step_index(cells)
        if index is None:
            raise ValueError(f"No cell in {name} is marked Run on Fabric.")
        if not lakehouse_ref or not all(_GUID.match(str(lakehouse_ref.get(k) or "")) for k in ("workspace_id", "lakehouse_id")):
            raise ValueError("Fabric can't reach this notebook's lakehouse. Attach a OneLake lakehouse to run "
                             "the last cell on Fabric. Nothing was sent.")
        return {"index": index, "item_name": fabric_step.step_name(name),
                "workspace_id": lakehouse_ref["workspace_id"], "workspace_name": lakehouse_ref.get("workspace_name", ""),
                "lakehouse": lakehouse_ref.get("name", ""),
                "tables": fabric_step.optimized_tables(cells[index].get("source", ""))}

    def notebook_vorder_cell(self, cells: list[dict[str, Any]], tables: list[str], picked: list[str] | None = None,
                             rewrite_all: bool = False) -> dict[str, Any]:
        """For "Insert V-Order cell": which of the lakehouse's ``tables`` the notebook's code names
        (ticked first), and, once tables are ``picked``, the cell's code. Reads only."""
        return {"candidates": fabric_step.vorder_candidates(cells, tables),
                "source": fabric_step.vorder_source(picked, rewrite_all) if picked else None}

    def notebook_fabric_step_check(self, name: str, cells: list[dict[str, Any]],
                                   lakehouse_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Before Run all or running the marked cell: where the Fabric step would run and which tables
        it V-Orders, or why it can't (``problem``). Reads only."""
        try:
            where = self._step_where(name, cells, lakehouse_ref)
            fabric_step.step_ipynb(name, cells[where["index"]], lakehouse_ref or {}, "check")  # code Spark can run
        except ValueError as exc:
            return {"ok": False, "problem": str(exc)}
        return {"ok": True} | where

    def run_notebook_fabric_step(self, name: str, cells: list[dict[str, Any]],
                                 lakehouse_ref: dict[str, str] | None) -> dict[str, Any]:
        """Publish the marked last cell as the "<name> (Fabric step)" Spark notebook in the lakehouse's
        workspace, replacing the one there, and start it on that workspace's Fabric capacity."""
        self._replay_refuse(name, "running its last cell on Fabric")  # type: ignore[attr-defined]
        where = self._step_where(name, cells, lakehouse_ref)
        lakehouse_ref = lakehouse_ref or {}  # _step_where refused a missing one
        request_id = f"fds-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        doc = fabric_step.step_ipynb(name, cells[where["index"]], lakehouse_ref, request_id)
        ws = where["workspace_id"]
        item = self.fabric.save_notebook(ws, where["item_name"], json.dumps(doc).encode())  # type: ignore[attr-defined]
        item_id = item.get("id") or (self._notebook_item(where["item_name"], ws) or {}).get("id")
        if not item_id:
            raise LookupError(f"{where['item_name']} was published but Fabric didn't return its id; try again")
        job_id = self.fabric_jobs.run_item_job(ws, item_id, NOTEBOOK_JOB)  # type: ignore[attr-defined]
        return self._log_fabric_run({
            "kind": "Fabric step", "name": name, "workspace_id": ws, "workspace_name": where["workspace_name"],
            "engine": "spark", "item_id": item_id, "job_id": job_id, "request_id": request_id,
            "lakehouse_id": lakehouse_ref["lakehouse_id"], "tables": where["tables"],
            "url": f"{FABRIC_APP}/groups/{ws}/synapsenotebooks/{item_id}?experience=fabric-developer"})

    def notebook_fabric_step_result(self, request_id: str) -> dict[str, Any]:
        """A Fabric step's status; once it ended, what the cell printed and, for each table it
        OPTIMIZEd, whether the Delta log now shows V-Order since the last write."""
        run = next((r for r in self.fabric_runs(200) if r.get("request_id") == request_id), None)
        if run is None:
            raise KeyError(f"no Fabric step {request_id}")
        out: dict[str, Any] = {k: run.get(k) for k in ("status", "message", "url", "job_id", "workspace_id",
                                                         "item_id", "started_at", "finished_at")}
        out |= {"output": None, "tables": []}
        if run["status"] not in TERMINAL:
            return out
        token = self.auth.storage_token()  # type: ignore[attr-defined]
        try:
            data = FileStore(lambda: token).read_bytes(run["workspace_id"], run["lakehouse_id"],
                                                        fabric_step.result_path(request_id))
            out["output"] = json.loads(data) if data else None
        except Exception as exc:  # the run's status still shows; the output is in Fabric
            _log.warning("Couldn't read the Fabric step's output: %s", describe(exc))
        if run["status"] == "Completed" and run.get("tables"):
            flat = not self._schemas_enabled(run["workspace_id"], run["lakehouse_id"])  # type: ignore[attr-defined]
            for table in run["tables"]:
                schema, _, name = table.rpartition(".")
                try:
                    uri = TableRef(run["workspace_id"], run["lakehouse_id"], schema or "dbo", name, flat=flat).uri
                    h = maintenance.health(uri, storage_options(token))
                    entry = {"table": table, "v_ordered": None, "files": h.get("files"), "last_optimize": h.get("last_optimize")}
                    if h["exists"]:
                        # The files' footers say whether they're V-Ordered; the Delta log only hints.
                        f = maintenance.vorder_files(uri, storage_options(token))
                        entry |= {"v_ordered": f["files"] > 0 and f["v_ordered"] == f["checked"],
                                  "files": f["files"], "checked": f["checked"], "v_ordered_files": f["v_ordered"]}
                    out["tables"].append(entry)
                except Exception as exc:  # one table not readable doesn't hide the others
                    out["tables"].append({"table": table, "v_ordered": None, "message": describe(exc)})
        return out

    def _published_item(self, name: str, workspace_id: str) -> tuple[dict[str, Any] | None, str]:
        """The Fabric item Publish writes for notebook ``name`` (PB-018), and a note to show. It is the
        item this app recorded for it in that workspace, found by id so a rename here or in Fabric
        doesn't make a second item. When that one was deleted, it is the notebook with its name (None:
        Publish creates one). With no recorded item it is None, and save_notebook matches by name as before."""
        link = self.workload_store.links().get(name) if hasattr(self, "workload_store") else None  # type: ignore[attr-defined]
        if link and link.get("workspace_id") == workspace_id and link.get("item_id"):
            item = self.fabric.get_notebook(workspace_id, link["item_id"])  # type: ignore[attr-defined]
            if item is not None:
                return item | {"id": link["item_id"]}, ""
            found = self._notebook_item(name, workspace_id)
            return found, ("" if found else f"The Fabric notebook {name} was published to before was deleted, "
                                            "so a new one was made.")
        return None, ""

    def _fabric_item_name(self, name: str, item: dict[str, Any]) -> str:
        """The name to give ``item`` on publish: the notebook's (without its folders) when it was renamed
        here since it was last published to that item, else the item's own, so a rename in Fabric stays."""
        last = notebook_files.published(Path(self.root) / "published", item["id"]) or {}  # type: ignore[attr-defined]
        local = leaf_name(name)
        return local if last.get("name") and last["name"] != local else item.get("displayName") or local

    def _notebook_item(self, name: str, workspace_id: str) -> dict[str, Any] | None:
        """The notebook item Publish writes: the one named after the notebook, without its folders."""
        item_name = leaf_name(name)
        return next((n for n in self.fabric.list_notebooks(workspace_id)  # type: ignore[attr-defined]
                     if n["displayName"] == item_name), None)

    def _fabric_name_free(self, name: str, ws: dict[str, str]) -> None:
        """Refuses to publish when another notebook here already publishes to the item of the same
        name in that workspace: "Sales/Daily load" and "Ops/Daily load" would both be "Daily load"."""
        if not hasattr(self, "workload_store"):
            return
        item_name = leaf_name(name)
        for other, link in self.workload_store.links().items():  # type: ignore[attr-defined]
            if (other != name and leaf_name(other) == item_name and link.get("workspace_id") == ws["workspace_id"]
                    and self.notebooks._taken(other)):  # type: ignore[attr-defined]  # noqa: SLF001
                raise ValueError(f"{other} already publishes to the Fabric notebook {item_name} in "
                                 f"{ws.get('workspace_name') or 'that workspace'}, and Fabric names it without its folder. "
                                 f"Rename one of them, then publish again. Nothing was published.")

    def notebook_schedules(self, name: str, lakehouse_ref: dict[str, str] | None = None,
                           workspace: dict[str, str] | None = None) -> dict[str, Any]:
        ws = self._publish_workspace(name, lakehouse_ref, workspace)
        item = self._published_item(name, ws["workspace_id"])[0] or self._notebook_item(name, ws["workspace_id"])
        if item is None:
            return {"published": False, "schedules": [], "workspace_id": ws["workspace_id"],
                    "workspace_name": ws["workspace_name"]}
        schedules = self.fabric.list_schedules(ws["workspace_id"], item["id"], NOTEBOOK_JOB)  # type: ignore[attr-defined]
        return {"published": True, "item_id": item["id"], "workspace_id": ws["workspace_id"],
                "workspace_name": ws["workspace_name"],
                "schedules": [s | {"text": describe_schedule(s)} for s in schedules]}

    def add_notebook_schedule(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None,
                              schedule: dict[str, Any], compute: str | None = None,
                              workspace: dict[str, str] | None = None,
                              location_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Publish the notebook (so the schedule runs what is on screen), then add a schedule to it."""
        config = schedule_configuration(schedule.get("kind", "daily"), schedule.get("times"),
                                        schedule.get("every_minutes"), schedule.get("weekdays"),
                                        schedule.get("time_zone") or "UTC")
        item = self._publish(name, cells, lakehouse_ref, compute, workspace, location_ref=location_ref)
        self.fabric_jobs.create_schedule(item["workspace_id"], item["id"], NOTEBOOK_JOB, config)  # type: ignore[attr-defined]
        return self.notebook_schedules(name, lakehouse_ref or location_ref)

    def delete_notebook_schedule(self, name: str, schedule_id: str, lakehouse_ref: dict[str, str] | None = None,
                                 workspace: dict[str, str] | None = None) -> dict[str, Any]:
        ws = self._publish_workspace(name, lakehouse_ref, workspace)
        item = self._published_item(name, ws["workspace_id"])[0] or self._notebook_item(name, ws["workspace_id"])
        if item is None:
            raise LookupError(f"{name} isn't published in {ws['workspace_name'] or 'that workspace'}")
        self.fabric_jobs.delete_schedule(ws["workspace_id"], item["id"], NOTEBOOK_JOB, schedule_id)  # type: ignore[attr-defined]
        return self.notebook_schedules(name, lakehouse_ref)

    # ---- table health and maintenance for the metadata project's tables

    def _env_cfg(self, project: Any, env: str) -> dict[str, Any]:
        return project.environments.get(env, {})

    def _optimize_after_run(self, project: Any, env: str) -> bool:
        cfg = self._env_cfg(project, env)
        if cfg.get("target", "onelake" if env != "dev" else "local") == "local":
            return False
        return bool(cfg.get("optimize_after_run", True))

    def _managed_tables(self, project: Any, include_meta: bool = True) -> list[dict[str, str]]:
        rows = [{"entity": e.name, "lakehouse": project.layer(e.layer).lakehouse, "schema": e.schema,
                 "table": e.table, "layer": e.layer, "retention_days": project.layer(e.layer).retention_days}
                for e in sorted(project.entities.values(), key=lambda x: (project.layer_index(x.layer), x.name))
                if e.layer in project.layer_names()]
        if include_meta:
            rows += [{"entity": f"meta.{t}", "lakehouse": META_LAKEHOUSE, "schema": "meta", "table": t, "layer": "",
                      "retention_days": maintenance.RETENTION_HOURS // 24} for t in META_SCHEMAS]
        return rows

    def meta_health(self, env: str = "dev") -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        store = self._store(project, env)  # type: ignore[attr-defined]
        local = isinstance(store, LocalStore)
        tables = []
        for t in self._managed_tables(project):
            try:
                h = maintenance.health(store.uri(t["lakehouse"], t["schema"], t["table"]), store.options(), local=local)
            except LookupError as exc:  # the lakehouse isn't in this environment yet
                h = {"exists": False, "advice": [], "status": "missing", "message": str(exc)}
            tables.append(t | h)
        cfg = self._env_cfg(project, env)
        return {"env": env, "local": local, "optimize_after_run": self._optimize_after_run(project, env),
                "workspace_id": cfg.get("workspace_id", ""), "tables": tables,
                "maintenance": [r for r in self.fabric_runs(100, refresh=not local)
                                if r.get("kind") == "Table maintenance" and r.get("env") == env][:20]}

    def _maintain(self, store: Any, rows: list[dict[str, str]], env: str) -> list[dict[str, Any]]:
        out = []
        for t in rows:
            label = f"{t['schema']}.{t['table']}"
            try:
                days = t.get("retention_days") or maintenance.RETENTION_HOURS // 24
                if isinstance(store, LocalStore):
                    result = maintenance.compact_local(store.uri(t["lakehouse"], t["schema"], t["table"]), days)
                    out.append({"table": label, "status": "Completed", **result})
                    continue
                lh_id = store.lakehouse_ids[t["lakehouse"]]
                try:  # so Fabric's own engines keep the same history; the job below vacuums with it too
                    maintenance.set_retention(store.uri(t["lakehouse"], t["schema"], t["table"]), days, store.options())
                except Exception as exc:  # noqa: BLE001 - the maintenance job still runs with the right retention
                    _log.warning("Couldn't set Delta retention before table maintenance; the job still sets it: %s",
                                 describe(exc))
                ws = store.workspace_of(t["lakehouse"])
                job_id = self.fabric_jobs.run_table_maintenance(  # type: ignore[attr-defined]
                    ws, lh_id, t["table"], t["schema"], vacuum_retention=maintenance.fabric_retention(days))
                out.append(self._log_fabric_run({
                    "kind": "Table maintenance", "name": f"{t['lakehouse']} {label}", "env": env, "table": label,
                    "workspace_id": ws, "item_id": lh_id, "job_id": job_id}))
            except Exception as exc:  # one table failing doesn't stop the rest
                out.append({"table": label, "status": "Failed", "message": f"{type(exc).__name__}: {exc}"})
        return out

    def meta_optimize(self, env: str = "dev", entities: list[str] | None = None) -> list[dict[str, Any]]:
        """OPTIMIZE and VACUUM tables: Fabric's maintenance job (with V-Order) in OneLake, delta-rs on this PC."""
        project = self._project()  # type: ignore[attr-defined]
        store = self._store(project, env)  # type: ignore[attr-defined]
        rows = [t for t in self._managed_tables(project) if entities is None or t["entity"] in entities]
        if isinstance(store, OneLakeStore):
            rows = [t for t in rows if t["lakehouse"] in store.lakehouse_ids]
        return self._maintain(store, rows, env)

    def meta_set_maintenance(self, env: str, optimize_after_run: bool) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        envs = {k: dict(v) for k, v in project.environments.items()}
        envs.setdefault(env, {})["optimize_after_run"] = bool(optimize_after_run)
        self._write_environments(project, envs)  # type: ignore[attr-defined]
        return self.meta_health(env)

    def _after_run(self, project: Any, env: str, store: Any, results: list[dict[str, Any]]) -> None:
        """Queue Fabric table maintenance for every table a OneLake run wrote."""
        if not isinstance(store, OneLakeStore) or not self._optimize_after_run(project, env):
            return
        written = {r["entity"] for r in results if r.get("status") == "ok" and r.get("rows_written")}
        rows = [t for t in self._managed_tables(project, include_meta=False) if t["entity"] in written]
        jobs = {j["table"]: j for j in self._maintain(store, rows, env)}
        for r in results:
            e = project.entities.get(r["entity"])
            job = jobs.get(f"{e.schema}.{e.table}") if e else None
            if job:
                r["maintenance"] = job.get("status") if job.get("status") == "Failed" else "queued"
                r["maintenance_message"] = job.get("message", "")

    # ---- metadata runs on Fabric capacity (compute = "fabric" in environments.toml)

    def _compute(self, project: Any, env: str) -> str:
        return self._env_cfg(project, env).get("compute", "local")

    def meta_set_compute(self, env: str, compute: str) -> dict[str, Any]:
        """Run an environment's metadata runs on this PC or on its workspace's Fabric capacity."""
        if compute not in ("local", "fabric"):
            raise ValueError("compute is local (this PC) or fabric")
        project = self._project()  # type: ignore[attr-defined]
        cfg = self._env_cfg(project, env)
        if compute == "fabric" and cfg.get("target", "onelake" if env != "dev" else "local") == "local":
            raise ValueError(f"{env.title()} keeps its tables on this PC, which Fabric can't reach. "
                             f"Point {env.title()} at a Fabric workspace first, then run it in Fabric.")
        envs = {k: dict(v) for k, v in project.environments.items()}
        envs.setdefault(env, {})["compute"] = compute
        self._write_environments(project, envs)  # type: ignore[attr-defined]
        return self.meta_project()  # type: ignore[attr-defined]

    @property
    def _bundles_file(self) -> Path:
        return self.root / "fabric_bundles.json"

    def _fabric_bundle(self, project: Any, env: str, store: Any) -> dict[str, str]:
        """Upload the engine and the project to the environment's LH_Meta and publish the runner
        notebook, unless the same bundle is already there. Returns where the runner is."""
        if not isinstance(store, OneLakeStore):
            raise ValueError(f"{env.title()} keeps its tables on this PC, so it can't run in Fabric")
        ws = store.workspace_id
        meta_id = store.lakehouse_ids.get(META_LAKEHOUSE)
        if not meta_id:
            raise NotSetUp(f"{META_LAKEHOUSE} doesn't exist in the {env} workspace yet; promote to {env} first")
        config: dict[str, Any] = {"env": env, "workspace_id": ws, "lakehouse_ids": dict(store.lakehouse_ids),
                                  "lakehouse_workspaces": dict(store.lakehouse_workspaces),
                                  "optimize_after_run": self._optimize_after_run(project, env)}
        if self._uses_keys(project):  # type: ignore[attr-defined]
            keys_store = self._keys_store(project, env, create=env == "dev")  # type: ignore[attr-defined]
            if isinstance(keys_store, OneLakeStore):
                config |= {"keys_workspace_id": keys_store.workspace_id, "keys_lakehouse_ids": dict(keys_store.lakehouse_ids)}
            elif keys_mod.is_service(keys_store):
                client = keys_store.client
                if not getattr(client, "functions_id", ""):
                    raise ValueError(f"{env.title()}'s keys are in the platform on this PC, which Fabric can't reach. "
                                     "Create or join a platform in Fabric first (Platform > Settings).")
                config["platform_keys"] = {"workspace_id": client.workspace_id, "functions_id": client.functions_id}
        if project.links:  # where each link points in this environment, resolved now for the runner
            resolve = self._link_table(project, env)  # type: ignore[attr-defined]
            config["links"] = {alias: resolve(alias) for alias in project.links}
        if store.landing_zones is not None and any(e.spec.get("source", {}).get("landing")
                                                   for e in project.entities.values()):
            config["landing"] = store.landing_map()  # each source's zone, resolved now like links
        files = bundle_files(project.root, config)
        digest = bundle_hash(files)
        try:
            state = jsonfile.read_json(self._bundles_file, {})
        except OSError:
            state = {}
        if not isinstance(state, dict):
            state = {}
        key = f"{env}:{ws}"
        known = state.get(key, {})
        if known.get("hash") != digest or not known.get("runner_id"):
            token = self.auth.storage_token()  # type: ignore[attr-defined]  # once: MSAL isn't thread-safe
            fs = FileStore(lambda: token)
            fs.delete(ws, meta_id, BUNDLE_DIR)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda item: fs.upload(ws, meta_id, f"{BUNDLE_DIR}/{item[0]}", item[1]), files.items()))
            fabric = self.fabric  # type: ignore[attr-defined]
            item = fabric.save_notebook(ws, RUNNER, json.dumps(runner_ipynb(ws, meta_id)).encode())
            runner_id = item.get("id") or (self._notebook_item(RUNNER, ws) or {}).get("id")
            if not runner_id:
                raise LookupError(f"{RUNNER} was published but Fabric didn't return its id; try again")
            known = {"hash": digest, "runner_id": runner_id, "at": _now(), "version": project.version}
            state[key] = known
            self.root.mkdir(parents=True, exist_ok=True)
            jsonfile.write_text(self._bundles_file, json.dumps(state, indent=2))
        return {"workspace_id": ws, "meta_lakehouse_id": meta_id, "runner_id": known["runner_id"]}

    def _check_runnable(self, project: Any, store: Any) -> None:
        """The gates a run checks first, here, so a blocked run fails at once rather than in Fabric."""
        errors = validate(project, dt.date.today())
        if errors:
            raise GateError("save", errors)
        Engine(project, store).check_deployed()

    def _run_in_fabric(self, project: Any, env: str, store: Any, names: list[str] | None,
                       backfill: str | None, cascade: bool) -> list[dict[str, Any]]:
        project = self._released(project, env, store)  # type: ignore[attr-defined]
        self._check_runnable(project, store)
        where = self._fabric_bundle(project, env, store)
        request_id = f"fd-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        label = ", ".join(names) if names else f"all {len(project.entities)} entities"
        job_id = self.fabric_jobs.run_item_job(where["workspace_id"], where["runner_id"], "RunNotebook",  # type: ignore[attr-defined]
                                          run_parameters(names, backfill, cascade, request_id))
        self._log_fabric_run({"kind": "Metadata run", "name": label, "env": env, "workspace_id": where["workspace_id"],
                              "workspace_name": self._env_cfg(project, env).get("workspace_name", ""),
                              "item_id": where["runner_id"], "job_id": job_id, "request_id": request_id,
                              "meta_lakehouse_id": where["meta_lakehouse_id"],
                              "url": f"{FABRIC_APP}/groups/{where['workspace_id']}/synapsenotebooks/{where['runner_id']}"
                                     "?experience=fabric-developer"})
        return [{"entity": label, "status": "submitted", "run_id": "", "env": env, "job_id": job_id,
                 "request_id": request_id, "message": f"Running in Fabric on the {env.title()} workspace's capacity"}]

    def meta_fabric_result(self, request_id: str) -> dict[str, Any]:
        """A Fabric run's status, and its per-entity results once the runner has saved them."""
        run = next((r for r in self.fabric_runs(200) if r.get("request_id") == request_id), None)
        if run is None:
            raise KeyError(f"no Fabric run {request_id}")
        out: dict[str, Any] = {"status": run["status"], "message": run.get("message", ""), "results": None}
        if run["status"] in TERMINAL:
            data = FileStore(lambda: self.auth.storage_token()).read_bytes(  # type: ignore[attr-defined]
                run["workspace_id"], run["meta_lakehouse_id"], f"{RESULTS_DIR}/{request_id}.json")
            if data:
                out["results"] = json.loads(data)["results"]
        return out

    def meta_schedules(self, env: str) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        ws = self._env_cfg(project, env).get("workspace_id", "")
        if self._compute(project, env) != "fabric" or not ws:
            return {"env": env, "compute": "local", "published": False, "schedules": []}
        runner = self._notebook_item(RUNNER, ws)
        schedules = self.fabric.list_schedules(ws, runner["id"], "RunNotebook") if runner else []  # type: ignore[attr-defined]
        return {"env": env, "compute": "fabric", "published": runner is not None, "workspace_id": ws,
                "item_id": runner["id"] if runner else "",
                "schedules": [s | {"text": describe_schedule(s)} for s in schedules]}

    def meta_add_schedule(self, env: str, schedule: dict[str, Any]) -> dict[str, Any]:
        """Schedule "run every entity" in Fabric, from the project as it is now."""
        project = self._project()  # type: ignore[attr-defined]
        if self._compute(project, env) != "fabric":
            raise ValueError(f"Set {env.title()} to run in Fabric first; scheduled runs need Fabric compute")
        config = schedule_configuration(schedule.get("kind", "daily"), schedule.get("times"),
                                        schedule.get("every_minutes"), schedule.get("weekdays"),
                                        schedule.get("time_zone") or "UTC")
        store = self._store(project, env, create=env == "dev")  # type: ignore[attr-defined]
        project = self._released(project, env, store)  # type: ignore[attr-defined]
        self._check_runnable(project, store)
        where = self._fabric_bundle(project, env, store)
        self.fabric_jobs.create_schedule(where["workspace_id"], where["runner_id"], "RunNotebook", config)  # type: ignore[attr-defined]
        return self.meta_schedules(env)

    def meta_delete_schedule(self, env: str, schedule_id: str) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        ws = self._env_cfg(project, env).get("workspace_id", "")
        runner = self._notebook_item(RUNNER, ws) if ws else None
        if runner is None:
            raise LookupError(f"There is no runner notebook in the {env.title()} workspace")
        self.fabric_jobs.delete_schedule(ws, runner["id"], "RunNotebook", schedule_id)  # type: ignore[attr-defined]
        return self.meta_schedules(env)

    # ---- Direct Lake semantic model over a layer

    def _model_layer(self, project: Any, layer: str | None) -> str:
        layer = layer or project.layer_names()[-1]
        if layer not in project.layer_names():
            raise ValueError(f"{layer} is not a layer")
        return layer

    def _kept_table_names(self, project: Any, layer: str, workspace_id: str, model: dict[str, Any],
                          fresh: bool = True) -> dict[str, str]:
        """The names the layer's tables have in the existing model, so updating it keeps them.

        getDefinition is a long-running operation, so the plan (``fresh=False``) reuses the definition read
        last for this model; building always reads it again, since that is what decides the names."""
        cache = self.__dict__.setdefault("_model_definitions", {})
        key = (workspace_id, model["id"])
        if fresh or key not in cache:
            try:
                got = self.fabric.get_item_definition(workspace_id, model["id"], "TMDL")  # type: ignore[attr-defined]
                cache[key] = {p["path"]: base64.b64decode(p.get("payload") or "").decode("utf-8")
                              for p in (got.get("definition") or {}).get("parts", []) if p.get("path", "").endswith(".tmdl")}
            except Exception as exc:  # guessing would rename tables that reports use
                raise RuntimeError(f"Couldn't read {model.get('displayName') or 'the model'}'s definition "
                                   f"({type(exc).__name__}: {exc}), so its table names aren't known. Try again.") from exc
        return semantic.table_names(cache[key], [e.name for e in project.entities.values() if e.layer == layer])

    def meta_model_plan(self, env: str = "test", layer: str | None = None) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        layer = self._model_layer(project, layer)
        cfg = self._env_cfg(project, env)
        local = cfg.get("target", "onelake" if env != "dev" else "local") == "local"
        existing = None
        name = f"SM_{layer.title()}"
        if not local and cfg.get("workspace_id"):
            try:
                existing = next((m for m in self.fabric.list_semantic_models(cfg["workspace_id"])  # type: ignore[attr-defined]
                                 if m["displayName"] == name), None)
            except Exception as exc:  # "none found" here would offer Create over a model that exists
                raise RuntimeError(f"Couldn't list the semantic models in {cfg.get('workspace_name') or 'the workspace'} "
                                   f"({type(exc).__name__}: {exc}), so whether {name} exists isn't known. Try again.") from exc
        kept = self._kept_table_names(project, layer, cfg["workspace_id"], existing, fresh=False) if existing else None
        tables, rels = semantic.plan(project, layer, kept)
        return {"env": env, "layer": layer, "layers": project.layer_names(), "name": name, "local": local,
                "workspace_id": cfg.get("workspace_id", ""), "workspace_name": cfg.get("workspace_name", ""),
                "lakehouse": project.layer(layer).lakehouse,
                "existing": {"id": existing["id"], "url": self._model_url(cfg["workspace_id"], existing["id"])} if existing else None,
                "tables": [{"name": t.name, "entity": t.entity, "source": f"{t.schema}.{t.table}",
                            "kind": "dimension" if t.is_dimension else "fact"} for t in tables],
                "relationships": [r.__dict__ for r in rels]}

    @staticmethod
    def _model_url(workspace_id: str, model_id: str) -> str:
        return f"{FABRIC_APP}/groups/{workspace_id}/datasets/{model_id}/details?experience=fabric-developer"

    def meta_build_model(self, env: str = "test", layer: str | None = None, name: str | None = None,
                         replace: bool = False) -> dict[str, Any]:
        """Create or update a Direct Lake semantic model over the layer's tables in the environment's workspace.

        A model with this name that already exists is replaced only with ``replace``; without it the
        answer is ``{"confirm": {...}}`` and nothing is written, so the user can confirm first."""
        project = self._project()  # type: ignore[attr-defined]
        layer = self._model_layer(project, layer)
        store = self._store(project, env)  # type: ignore[attr-defined]
        if not isinstance(store, OneLakeStore):
            raise ValueError("Direct Lake reads OneLake, and this environment keeps its tables on this PC. "
                             "Build the model from Test or Prod after promoting.")
        lakehouse = project.layer(layer).lakehouse
        if lakehouse not in store.lakehouse_ids:
            raise NotSetUp(f"{lakehouse} doesn't exist in the {env} workspace yet; promote and run first")
        fabric: FabricClient = self.fabric  # type: ignore[attr-defined]
        name = (name or f"SM_{layer.title()}").strip()
        kept = None
        if replace:  # without it an existing model only asks to confirm, so its names aren't needed yet
            existing = next((m for m in fabric.list_semantic_models(store.workspace_id) if m["displayName"] == name), None)
            kept = self._kept_table_names(project, layer, store.workspace_id, existing) if existing else None
        tables, rels = semantic.plan(project, layer, kept)
        skipped, unreadable = [], []
        for t in tables:
            try:
                fields = table_schema(store, lakehouse, t.schema, t.table)
            except TableNotFoundError:  # not written in this environment yet
                fields = None
            except Exception as exc:  # it may exist: leaving it out would drop it from the model
                unreadable.append(f"{t.entity} ({type(exc).__name__}: {exc})")
                continue
            if not fields:
                skipped.append(t.entity)
            t.columns = fields or []
        if unreadable:
            raise RuntimeError(f"Couldn't read {', '.join(unreadable)}, so the model wasn't written; "
                               "it would have left those tables out. Try again.")
        tables = [t for t in tables if t.columns]
        if not tables:
            raise LookupError(f"No {layer} tables have been written in {env} yet; run them first")
        names = {t.name for t in tables}
        columns_left_out = [c for t in tables for c in semantic.left_out(t)]
        usable = {t.name: {c for c, typ in t.columns if semantic.tmdl_type(typ)} for t in tables}
        kept_rels = [r for r in rels if r.from_table in names and r.to_table in names
                     and r.from_column in usable[r.from_table] and r.to_column in usable[r.to_table]]
        # Only those whose tables are both in the model but whose columns aren't: a missing table is already reported.
        relationships_left_out = [f"{r.from_table}.{r.from_column} to {r.to_table}.{r.to_column}" for r in rels
                                  if r not in kept_rels and r.from_table in names and r.to_table in names]
        rels = kept_rels
        props = (fabric.get_lakehouse(store.workspace_of(lakehouse), store.lakehouse_ids[lakehouse]).get("properties") or {})
        endpoint = props.get("sqlEndpointProperties") or {}
        if not endpoint.get("connectionString") or not endpoint.get("id"):
            raise LookupError(f"{lakehouse}'s SQL analytics endpoint isn't ready yet; try again in a minute")
        refreshed = True
        try:  # so the endpoint sees tables this app wrote; the model reads through it
            fabric.refresh_sql_endpoint(store.workspace_of(lakehouse), endpoint["id"])
        except Exception:
            refreshed = False
        parts = semantic.build(tables, rels, endpoint["connectionString"], endpoint["id"],
                               self.model_descriptions(project, layer))  # type: ignore[attr-defined]
        try:
            model = fabric.save_semantic_model(store.workspace_id, name, parts,
                                               f"Direct Lake over {lakehouse}, built by KratosForge from the metadata project",
                                               replace=replace)
        except FileExistsError as exc:  # it exists and the user hasn't confirmed replacing it
            return {"confirm": {"name": name, "id": exc.args[1] if len(exc.args) > 1 else "",
                                "workspace_name": self._env_cfg(project, env).get("workspace_name", "")}}
        model_id = model.get("id") or next((m["id"] for m in fabric.list_semantic_models(store.workspace_id)
                                            if m["displayName"] == name), "")
        return {"name": name, "id": model_id, "created": model.get("created", False), "workspace_id": store.workspace_id,
                "url": self._model_url(store.workspace_id, model_id) if model_id else "",
                "tables": [t.name for t in tables], "relationships": len(rels), "skipped": skipped,
                "columns_left_out": columns_left_out, "relationships_left_out": relationships_left_out,
                "endpoint_refreshed": refreshed}

    # ---- profiling

    def _profile_input(self, project: Any, env: str, name: str, source: bool) -> pl.DataFrame:
        e = project.entities[name]
        store = self._store(project, env)  # type: ignore[attr-defined]
        engine = Engine(project, store)
        if source:
            df = engine._read(e, RunResult("", name, "ok"), full=True)
        else:
            df = engine.read_entity(name)
            if df is None:
                raise LookupError(f"{name} has not been loaded in {env} yet")
        return df

    def meta_profile(self, name: str, env: str = "dev", source: bool = True) -> dict[str, Any]:
        """Profile what the entity reads (source=True), before any cleaning, or the table it writes."""
        project = self._project()  # type: ignore[attr-defined]
        e = project.entities[name]
        started = time.monotonic()
        df = self._profile_input(project, env, name, source)
        # The contract keys only: a [[keys]] business key can be another table's key (customer_id on orders).
        result = profiling.profile(df, list(e.keys), profiling.existing_rules(e.spec) if source else [])
        if not source:  # cleaning applies to what is read, so only checks make sense on the output
            result["suggestions"] = [s for s in result["suggestions"] if s["kind"] != "clean"]
        return result | {"entity": name, "env": env, "source": source, "seconds": round(time.monotonic() - started, 2)}

    def meta_profile_preview(self, name: str, rules: list[dict[str, Any]], env: str = "dev",
                             limit: int = 20) -> dict[str, Any]:
        """Before and after for the chosen cleaning rules: the rows they change, side by side."""
        project = self._project()  # type: ignore[attr-defined]
        e = project.entities[name]
        df = self._profile_input(project, env, name, True)
        cols = sorted({r["column"] for r in rules if r.get("column") in df.columns})
        after, changed = apply_clean(df, rules, list(e.keys))
        if not cols or after.height != df.height:
            return {"columns": cols, "rows": [], "changed": changed}
        diff = pl.lit(False)
        for c in cols:
            diff = diff | ~df[c].cast(pl.String).eq_missing(after[c].cast(pl.String))
        before = df.select(cols).with_row_index("__r")
        after_ = after.select([pl.col(c).alias(f"{c} (after)") for c in cols])
        both = pl.concat([before, after_], how="horizontal").filter(df.select(diff.alias("d"))["d"])
        return {"columns": cols, "changed": changed,
                "rows": json.loads(both.drop("__r").head(limit).cast({c: pl.String for c in both.columns if c != "__r"}).write_json())}

    def meta_add_rules(self, name: str, blocks: list[str]) -> dict[str, Any]:
        """Append the accepted [[clean]] and [[check]] blocks to the entity file, through the save gate."""
        project = self._project()  # type: ignore[attr-defined]
        text = project.entities[name].path.read_text(encoding="utf-8")
        return self.meta_save_entity(name, profiling.append_blocks(text, blocks))  # type: ignore[attr-defined]


def table_schema(store: Any, lakehouse: str, schema: str, table: str) -> list[tuple[str, str]]:
    """(column, Delta type) for a table in the store."""
    from deltalake import DeltaTable
    fields = DeltaTable(store.uri(lakehouse, schema, table), storage_options=store.options()).schema().fields
    return [(f.name, f.type.type if isinstance(f.type.type, str) else str(f.type)) for f in fields]
