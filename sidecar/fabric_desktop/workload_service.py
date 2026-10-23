"""Workloads, as the desktop UI calls them: one list of what the engineer builds and runs.

Reads the stores the app already keeps (the metadata project and its run logs, notebooks,
schedules on this PC, Fabric jobs this app started, Fabric's schedules) and, for the workspaces
this app works in, Fabric's own items. Never pops a sign-in: a source that needs one is
reported as signed out. Actions call the existing run, schedule and cancel operations and are
recorded with how they ended. See docs/workloads/plan.md.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .auth import EXECUTE_SCOPES, FABRIC_SCOPES, STORAGE_SCOPES
from .central.client import FabricPlatform
from .central.service import CATALOG_ENV
from .fabric_api import FabricClient
from .fabric_run import TERMINAL
from .meta import freshness as freshness_mod
from .meta.project import ENVIRONMENTS, impact
from .meta import mesh as mesh_mod
from . import measurement as ms
from .redact import describe, secrets
from .workloads import health, inventory, model, query
from .workloads.store import ManagementStore

_log = logging.getLogger(__name__)

REMOTE_RUNS_MAX_AGE = 120          # seconds a OneLake run log read is reused without asking again
RUNS_STALE_AFTER = 3600            # a cached run log older than this is shown as stale
ITEMS_STALE_AFTER = 24 * 3600      # Fabric item listings
RUN_LIMIT = 2000
AUDIT_RETRY_AFTER = 600            # seconds before lines the platform didn't take are tried again on their own
AUDIT_BATCH = 50
_ACTION_LABELS = {"run": "Run", "run_in_fabric": "Run in Fabric", "run_item": "Run in Fabric",
                  "run_schedule": "Run the schedule now", "cancel": "Cancel the run"}


class WorkloadMixin:
    root: Path
    _wl_scan: dict[str, Any] | None = None
    _wl_scan_lock = threading.Lock()
    _wl_records: dict[str, dict[str, Any]] | None = None
    _wl_audit_lock = threading.Lock()

    @property
    def workload_store(self) -> ManagementStore:
        store = getattr(self, "_wl_store", None)
        if store is None or store.path != self.root / "workloads.sqlite":
            store = self._wl_store = ManagementStore(self.root / "workloads.sqlite")
        return store

    # ---- who and whether signed in, without ever prompting

    def _wl_user(self) -> str:
        try:
            return self.auth.signed_in_user() or ""  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - no client id yet
            _log.info("Signed-in user unknown: %s", describe(exc))
            return ""

    def _wl_token(self, scopes: list[str]) -> str | None:
        try:
            return self.auth.silent_token(scopes)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            _log.info("No silent token: %s", describe(exc))
            return None

    # ---- sources

    def _wl_meta_env(self, project: Any, pname: str, env: str, refresh: bool) -> dict[str, Any]:
        """One environment's run log and deployments, with how they were read."""
        cfg = project.environments.get(env, {})
        local = cfg.get("target", "onelake" if env != "dev" else "local") == "local"
        if not local and not cfg.get("workspace_id"):
            return {"configured": False}
        source = f"meta.run_log in {inventory.ENV_LABELS[env]}" + ("" if local else f" ({cfg.get('workspace_name') or 'workspace'})")
        key = f"meta:{pname}:{env}"
        cached = self.workload_store.cache_get(key)
        if not local:
            age = None
            if cached:
                age = (model.now() - model.parse_time(cached["collected_at"])).total_seconds()
            if cached and not refresh and age is not None and age < REMOTE_RUNS_MAX_AGE:
                return {"configured": True, **cached["payload"],
                        "observation": model.observation(source, "cached", cached["collected_at"],
                                                         max_age_s=RUNS_STALE_AFTER)}
            if not (self._wl_token(STORAGE_SCOPES) and self._wl_token(FABRIC_SCOPES)):
                return self._wl_cached_or(cached, source, "signed_out", "Sign in to read this environment's runs.")
        try:
            runs = self.meta_runs(env, RUN_LIMIT)  # type: ignore[attr-defined]
            deployments = [] if env == "dev" else _rows(self._store(project, env).meta("deployments"))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - offline, no access, lakehouse gone
            state, message = model.classify_error(exc)
            return self._wl_cached_or(cached, source, state, message)
        payload = {"runs": runs, "deployments": deployments}
        if not local:
            self.workload_store.cache_put(key, payload)
        return {"configured": True, **payload, "observation": model.observation(source, "live", model.now())}

    def _wl_cached_or(self, cached: dict[str, Any] | None, source: str, state: str, message: str) -> dict[str, Any]:
        """The last good read, marked as cached and why it couldn't be refreshed; else nothing."""
        if cached:
            obs = model.observation(source, "cached", cached["collected_at"], max_age_s=RUNS_STALE_AFTER,
                                    detail=f"Showing what was read earlier. {message}")
            return {"configured": True, **cached["payload"], "observation": obs}
        return {"configured": True, "runs": [], "deployments": [],
                "observation": model.observation(source, state, None, detail=message)}

    def _wl_meta(self, refresh: bool, assignments: dict[str, Any], pc: list[dict[str, Any]],
                 user: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        try:
            project = self._project()  # type: ignore[attr-defined]
        except LookupError:
            return [], None
        except Exception as exc:  # noqa: BLE001 - a broken project file
            return [], model.observation("Metadata project", "error", None, detail=str(exc))
        summary = project.summary()
        pname = mesh_mod.project_name(project)
        env_data = {env: self._wl_meta_env(project, pname, env, refresh) for env in ENVIRONMENTS}
        runs = [r | {"env": env} for env, d in env_data.items() for r in d.get("runs") or []]
        runs.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        readable = [env for env, d in env_data.items() if d.get("configured")
                    and (d.get("observation") or {}).get("state") in ("live", "cached", "stale")]
        fresh = freshness_mod.report({n: e.spec for n, e in project.entities.items()}, runs, readable)
        fabric_sched = {}
        scan = self._fabric_schedules or {}  # type: ignore[attr-defined]
        for env in ENVIRONMENTS:
            ws = project.environments.get(env, {}).get("workspace_id")
            if ws:
                fabric_sched[env] = [s for s in scan.get("items") or [] if s.get("workspace_id") == ws
                                     and s.get("name") == inventory.RUNNER_NOTEBOOK]
        impacts = {n: impact(project, n) for n in project.entities}
        records = inventory.meta_workloads(summary, version=project.version, impacts=impacts, env_data=env_data,
                                           freshness=fresh, pc_schedules=pc, fabric_schedules=fabric_sched,
                                           assignments=assignments, user=user)
        return records, model.observation(f"Metadata project {pname}", "live", model.now())

    def _wl_notebooks(self) -> list[dict[str, Any]]:
        out = []
        for n in self.notebooks.list():  # type: ignore[attr-defined]
            try:
                nb = self.notebooks.load(n["name"])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - a damaged file still shows, without its settings
                nb = {"name": n["name"]}
            out.append(nb | {"modified": n["modified"]})
        return out

    # ---- Fabric items in the workspaces this app works in, read in the background

    def _wl_items(self, refresh: bool) -> tuple[list[dict[str, Any]] | None, dict[str, Any], bool]:
        """(items or None, observation, scanning). Starts a scan when asked, or when there's none yet."""
        with self._wl_scan_lock:
            scan = self._wl_scan
            scanning = bool(scan and scan.get("pending"))
        cached = self.workload_store.cache_get("fabric_items")
        token = self._wl_token(FABRIC_SCOPES)
        if token and not scanning and (refresh or cached is None):
            self._wl_start_scan(token)
            scanning = True
        source = "Fabric items in the workspaces you work in"
        if cached is None:
            state = "unknown" if token else "signed_out"
            detail = "Reading them now." if scanning else ("Sign in to list them." if not token else "")
            return None, model.observation(source, state, None, detail=detail), scanning
        payload = cached["payload"]
        errors = payload.get("errors") or []
        detail = (f"{len(errors)} of {payload.get('workspaces', 0)} workspaces couldn't be read: "
                  + "; ".join(f"{e['workspace']}: {e['message']}" for e in errors[:3])) if errors else ""
        fresh_now = scan and not scan.get("pending") and scan.get("collected_at") == cached["collected_at"] \
            and (model.now() - model.parse_time(cached["collected_at"])).total_seconds() < REMOTE_RUNS_MAX_AGE
        state = "partial" if errors else ("live" if fresh_now else "cached")
        obs = model.observation(source, state, cached["collected_at"], max_age_s=ITEMS_STALE_AFTER, detail=detail)
        return payload.get("items") or [], obs, scanning

    def _wl_start_scan(self, token: str) -> None:
        with self._wl_scan_lock:
            if self._wl_scan and self._wl_scan.get("pending"):
                return
            self._wl_scan = {"pending": True}
        threading.Thread(target=self._wl_scan_items, args=(token,), daemon=True).start()

    def _wl_scan_items(self, token: str, client: FabricClient | None = None) -> dict[str, Any]:
        client = client or FabricClient(lambda: token)
        errors: list[dict[str, str]] = []
        items: list[dict[str, Any]] = []
        try:
            workspaces = self._schedule_workspaces(False, token)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            state, message = model.classify_error(exc)
            with self._wl_scan_lock:
                self._wl_scan = {"pending": False, "error": message, "state": state}
            return self._wl_scan

        def listing(ws: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]] | None, str]:
            try:
                return ws, client.list_items(ws["id"]), ""
            except Exception as exc:  # noqa: BLE001 - one workspace's failure doesn't hide the others
                return ws, None, model.classify_error(exc)[1]

        with ThreadPoolExecutor(max_workers=8) as pool:
            for ws, found, message in pool.map(listing, workspaces):
                if found is None:
                    errors.append({"workspace": ws["name"], "workspace_id": ws["id"], "message": message})
                    continue
                items += [{"workspace_id": ws["id"], "workspace_name": ws["name"], "id": it["id"],
                           "displayName": it.get("displayName", ""), "type": it.get("type", ""),
                           "description": it.get("description", "")}
                          for it in found if it.get("type") in inventory.SUPPORTED_FABRIC_TYPES]
        collected = model.iso(model.now())
        self.workload_store.cache_put("fabric_items", {"items": items, "errors": errors, "workspaces": len(workspaces)},
                                      "partial" if errors else "live", collected_at=collected)
        with self._wl_scan_lock:
            self._wl_scan = {"pending": False, "collected_at": collected}
        return self._wl_scan

    # ---- the inventory

    def _wl_collect(self, refresh: bool = False) -> dict[str, Any]:
        user = self._wl_user()
        store = self.workload_store
        assignments = store.assignments()
        signed_in = bool(self._wl_token(FABRIC_SCOPES))
        fabric_runs = self.fabric_runs(500, refresh=refresh and signed_in)[::-1]  # type: ignore[attr-defined]  # oldest first
        pc = self.pc_schedules.listing()  # type: ignore[attr-defined]
        pc_runs = self.pc_schedules.runs(1000)  # type: ignore[attr-defined]
        items, items_obs, scanning = self._wl_items(refresh)
        fabric_sched = [s for s in ((self._fabric_schedules or {}).get("items") or [])]  # type: ignore[attr-defined]
        sources = []
        meta_records, meta_obs = self._wl_meta(refresh, assignments, pc, user)
        if meta_obs:
            sources.append(meta_obs)
        notebooks = self._wl_notebooks()
        links = store.links()
        nb_records = inventory.notebook_workloads(
            notebooks, links=links, fabric_runs=fabric_runs, fabric_items=items, fabric_items_obs=items_obs,
            fabric_schedules=fabric_sched, pc_schedules=pc, pc_runs=pc_runs, assignments=assignments, user=user,
            signed_in=signed_in)
        linked = {r["link"]["item_id"] for r in nb_records if r.get("link")}
        fab_records = inventory.fabric_workloads(items or [], items_obs=items_obs, linked=linked,
                                                 fabric_schedules=fabric_sched, fabric_runs=fabric_runs,
                                                 assignments=assignments)
        records = meta_records + nb_records + fab_records
        for r in records:
            r.update(health.assess(r))
        for env_obs in {d["observation"]["source"]: d["observation"] for r in meta_records
                        for d in r["deployments"]}.values():
            sources.append(env_obs)
        sources.append(model.observation("Notebooks and schedules on this PC", "live", model.now()))
        sources.append(items_obs)
        self._wl_records = {r["id"]: r for r in records}
        self._wl_reconcile(fabric_runs, pc_runs)
        self._wl_audit_retry()
        return {"records": records, "sources": sources, "scanning": scanning, "user": user, "signed_in": signed_in}

    def workloads_list(self, view: str = "all", query_text: str = "", kind: str = "", env: str = "",
                       location: str = "", group: str = "project", offset: int = 0, limit: int = 100,
                       refresh: bool = False) -> dict[str, Any]:
        """One page of workloads with the counts for each view and how fresh each source is."""
        data = self._wl_collect(refresh)
        page = query.select(data["records"], view=view, query=query_text, kind=kind, env=env, location=location,
                            group=group, offset=offset, limit=limit, user=data["user"])
        return page | {"sources": data["sources"], "scanning": data["scanning"], "user": data["user"],
                       "signed_in": data["signed_in"]}

    def _wl_record(self, wid: str, refresh: bool = False) -> dict[str, Any]:
        model.parse_id(wid)
        if refresh or not self._wl_records or wid not in self._wl_records:
            self._wl_collect(refresh)
        record = (self._wl_records or {}).get(wid)
        if record is None:
            raise LookupError("That workload isn't in the inventory any more. It may have been renamed, "
                              "deleted, or be in a workspace this app no longer reads.")
        return record

    def workload_detail(self, id: str, refresh: bool = False) -> dict[str, Any]:
        """Everything about one workload: deployments, findings, runs, changes, impact, actions."""
        w = dict(self._wl_record(id, refresh))
        store = self.workload_store
        runs = list(w["runs"])
        if w["kind"] == "fabric_item":
            runs, obs = self._wl_fabric_history(w)
            w["deployments"] = [dict(w["deployments"][0], observation=obs,
                                     latest_run=runs[0] if runs else w["deployments"][0].get("latest_run"))]
            w.update(health.assess(w))
        actions = store.actions(id, 50)
        w["runs"] = runs[:50]
        w["actions"] = actions
        w["assignment_history"] = store.history(id, 50)
        w["platform_audit"] = self.workload_audit_state()
        w["timeline"] = self._wl_timeline(w, actions)
        w["consumers"] = self._wl_consumers(w)
        w["available_actions"] = self._wl_available(w)
        return w

    def workload_capacity_use(self, id: str, days: int = 30) -> dict[str, Any]:
        """The capacity used in the last ``days`` by the runs the workload record holds: the ones this
        app started or scheduled, not Fabric's own job history (workload_detail reads that). Each
        Fabric run counts with its measured CU when an imported usage row matched it, else the app's
        estimate, else as unknown (left out of the total, not counted as 0). Runs on this PC start no
        Fabric job; their OneLake reads and writes still use some capacity, which isn't counted.
        Reads only what this PC holds: the workload's runs, usage imports and the run history."""
        w = self._wl_record(id)
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(days))
        # A run whose start time can't be read isn't known to be in the period, so it isn't counted.
        recent = [r for r in w.get("runs") or [] if (model.parse_time(r.get("started_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= since]
        fabric = [r for r in recent if r.get("where") == "fabric"]
        with_id = [r for r in fabric if r.get("job_id")]
        measured, est, problem = self._risk_cu(with_id)  # type: ignore[attr-defined]
        by_job_m = {m.get("run_id"): m for m in measured}
        by_job_e = {e.get("run_id"): e for e in est}
        parts = []
        for r in fabric:
            j = r.get("job_id")
            parts.append(by_job_m.get(j) or by_job_e.get(j) or ms.unknown(
                "fabric_job_cu", ms.CU_SECONDS, "No job id or estimate for this run.", run_id=j))
        return {"id": id, "days": int(days), "fabric_runs": len(fabric), "pc_runs": len(recent) - len(fabric),
                "measured_runs": len(measured),
                "measured": ms.total(measured, "fabric_job_cu", ms.CU_HOURS) if measured else None,
                "total": ms.total(parts, "fabric_job_cu", ms.CU_HOURS) if parts else None,
                "problem": problem}

    def _wl_fabric_history(self, w: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        d = w["definition"]
        token = self._wl_token(FABRIC_SCOPES)
        source = "Fabric job history"
        if not token:
            return list(w["runs"]), model.observation(source, "signed_out", None, detail="Sign in to read its runs.")
        try:
            jobs = FabricClient(lambda: token).list_job_instances(d["workspace_id"], d["item_id"])
        except Exception as exc:  # noqa: BLE001
            state, message = model.classify_error(exc)
            return list(w["runs"]), model.observation(source, state, None, detail=message)
        runs = [inventory.fabric_job(j, d.get("workspace_name", "")) for j in jobs[:50]]
        return runs, model.observation(source, "live", model.now())

    def _wl_timeline(self, w: dict[str, Any], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Runs, promotions, definition edits, actions and ownership changes, newest first."""
        events: list[dict[str, Any]] = []
        for r in w["runs"][:30]:
            events.append({"at": r.get("started_at"), "kind": "run", "state": r["state"],
                           "text": f"Run {r['state'].replace('_', ' ')}" + (f" in {r['env'].title()}" if r.get("env") else "")
                                   + f" · {r.get('ran_on')}", "source": r.get("source")})
        for d in w["deployments"]:
            v = d.get("version") or {}
            if v.get("at"):
                events.append({"at": v["at"], "kind": "deployment", "state": "ok",
                               "text": f"Promoted {str(v.get('from') or '').title()} → {d.get('env_label')} · version "
                                       f"{str(v.get('deployed'))[:8]}", "source": "meta.deployments"})
        path = (w.get("definition") or {}).get("path")
        modified = (w.get("definition") or {}).get("modified")
        if path:
            try:
                modified = Path(path).stat().st_mtime
            except OSError:
                modified = None
        if modified:
            events.append({"at": model.iso(model.parse_time(modified)), "kind": "definition", "state": "ok",
                           "text": "Definition changed", "source": "file on this PC"})
        for a in actions:
            events.append({"at": a["at"], "kind": "action", "state": a["status"],
                           "text": f"{_ACTION_LABELS.get(a['action'], a['action'])} by {a['by'] or 'you'} · {a['status']}",
                           "source": "actions from KratosForge"})
        for h in w.get("assignment_history") or []:
            events.append({"at": h["at"], "kind": "ownership", "state": "ok",
                           "text": f"{model.ROLE_LABELS.get(h['field'], h['field'].title())}: "
                                   f"{h['before'] or 'none'} → {h['after'] or 'none'} by {h['by'] or 'you'}",
                           "source": "on this PC"})
        events.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
        return events[:100]

    def _wl_consumers(self, w: dict[str, Any]) -> list[dict[str, Any]]:
        """Known consumers of the products this entity feeds, from the platform catalog."""
        feeds = (w.get("dependencies") or {}).get("products") or []
        if not feeds:
            return []
        try:
            products = self.platform_products()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - no platform: the products are still listed, without consumers
            _log.info("No platform catalog; products are listed without consumers: %s", describe(exc))
            return []
        pname = w["definition"].get("project")
        wanted = {f"{pname}.{f['product']}" for f in feeds} | {f["product"] for f in feeds}
        out = []
        for p in products.get("products") or []:
            if p.get("id") in wanted or p.get("name") in wanted:
                for c in p.get("consumers") or []:
                    out.append({"product": p.get("name") or p.get("id"), "consumer": c if isinstance(c, str)
                                else c.get("project") or c.get("consumer_project") or str(c), "declared": True})
        return out

    # ---- ownership and links

    def workload_assign(self, id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Assign owner roles, criticality or tags on this PC. A role declared in Git is changed there."""
        w = self._wl_record(id)
        declared = [r for r, o in (w.get("owners") or {}).items() if o.get("source") == "declared" and r in changes]
        if declared:
            o = w["owners"][declared[0]]
            raise ValueError(f"{model.ROLE_LABELS[declared[0]]} is set in the {o['where']}, so change it there. "
                             "What's in the project's files wins over this PC.")
        store, user = self.workload_store, self._wl_user()
        before = store.assignments([id]).get(id, {})
        store.assign(id, changes, by=user)
        after = store.assignments([id]).get(id, {})
        for field in changes:
            old, new = (before.get(field) or {}).get("value"), (after.get(field) or {}).get("value")
            if old != new:
                self._wl_audit(id, "assign", "changed", {"name": w["name"], "target": field,
                               "before": _audit_text(old), "after": _audit_text(new), "identity": user})
        self._wl_records = None
        return self.workload_detail(id)

    def _record_notebook_item(self, name: str, workspace_id: str, item_id: str, workspace_name: str = "") -> None:
        """Called when this app publishes or runs a notebook in Fabric: remember the item by its id."""
        try:
            self.workload_store.record_link(name, workspace_id, item_id, workspace_name, how="recorded",
                                            by=self._wl_user())
        except Exception as exc:  # noqa: BLE001 - never fail a publish over this
            _log.warning("Couldn't record notebook %r's Fabric item: %s", name, describe(exc))

    def workload_link(self, notebook: str, workspace_id: str, item_id: str, workspace_name: str = "") -> dict[str, Any]:
        self.notebooks.load(notebook)  # type: ignore[attr-defined]  # it exists
        user = self._wl_user()
        before, after = self.workload_store.record_link(notebook, workspace_id, item_id, workspace_name,
                                                        how="manual", by=user)
        if before != after:
            self._wl_audit(model.notebook_id(notebook), "link", "changed",
                           {"name": notebook, "target": "its Fabric item", "before": before, "after": after,
                            "identity": user})
        self._wl_records = None
        return self.workload_detail(model.notebook_id(notebook))

    def workload_unlink(self, notebook: str) -> dict[str, Any]:
        user = self._wl_user()
        before = self.workload_store.unlink(notebook, by=user)
        if before:
            self._wl_audit(model.notebook_id(notebook), "unlink", "changed",
                           {"name": notebook, "target": "its Fabric item", "before": before, "identity": user})
        self._wl_records = None
        return {"unlinked": notebook}

    # ---- actions

    def _wl_available(self, w: dict[str, Any]) -> list[dict[str, Any]]:
        """Actions this workload supports right now, one per deployment where it applies."""
        out = []
        for d in w["deployments"]:
            for a in d.get("actions") or []:
                if a == "cancel":
                    for r in w["runs"]:
                        if r["state"] == "running" and r.get("job_id") and r.get("where") == "fabric":
                            out.append({"action": "cancel", "label": "Cancel the run", "env": d.get("env"),
                                        "job_id": r["job_id"], "where": d["location"]["label"]})
                    continue
                if a == "run_schedule":
                    for s in d.get("schedules") or []:
                        if s.get("where") == "This PC":
                            out.append({"action": a, "label": f"Run the schedule now ({s.get('text')})",
                                        "env": d.get("env"), "schedule_id": s.get("id"), "where": "This PC"})
                    continue
                out.append({"action": a, "label": _ACTION_LABELS[a] + (f" in {d['env_label']}" if d.get("env_label") else ""),
                            "env": d.get("env"), "where": d["location"]["label"]})
        return out

    def workload_preflight(self, id: str, action: str, env: str | None = None, assess: bool = True) -> dict[str, Any]:
        """What an action will do before it does it: environment, identity, target, consequence, and
        the risk check's findings (FINOPS-004) unless ``assess`` is false."""
        w = self._wl_record(id)
        allowed = [a for a in self._wl_available(w) if a["action"] == action and (a.get("env") or None) == (env or None)]
        if not allowed:
            return {"allowed": False, "reason": f"{w['name']} doesn't support that here."}
        dep = next(d for d in w["deployments"] if (d.get("env") or None) == (env or None)
                   and action in (d.get("actions") or []))
        user = self._wl_user()
        signed_in = bool(self._wl_token(FABRIC_SCOPES))
        warnings = []
        last = dep.get("latest_run")
        if last and last["state"] == "failed" and last.get("message"):
            warnings.append(f"The last run failed: {last['message']}. A retry may fail the same way until that's fixed.")
        d = w["definition"]
        if w["kind"] == "meta_entity":
            v = dep.get("version") or {}
            where = dep["location"]["label"]
            target = f"{d.get('table')} in the {dep['env_label']} environment ({dep.get('tables_in')})"
            consequence = (f"Runs {w['name']} in {dep['env_label']} the same way Data mesh > Run does, on {where}. "
                           "It reads its sources and writes its table; nothing else is changed.")
            if v.get("behind"):
                warnings.append(f"{dep['env_label']} has version {str(v.get('deployed'))[:8]}; your newer changes are "
                                "not included until you promote them.")
            if w.get("errors"):
                return {"allowed": False, "reason": "The definition has problems: " + "; ".join(w["errors"][:2])}
            needs_sign_in = dep["location"]["kind"] == "fabric" or dep.get("tables_in") not in ("This PC only",)
        elif action == "run_in_fabric":
            target = f"the notebook item in {dep['location'].get('workspace_name') or 'its workspace'}"
            consequence = ("Publishes the saved notebook to its Fabric item (replacing what is there), then runs it on "
                           "that workspace's capacity.")
            where, needs_sign_in = dep["location"]["label"], True
        elif action == "run_item":
            target = f"{w['name']} in {d.get('workspace_name')}"
            consequence = (f"Starts {w['kind_label'].lower()} {w['name']} in Fabric now, on its workspace's capacity, "
                           "exactly as Fabric's own Run does. Whatever it writes, it writes.")
            where, needs_sign_in = dep["location"]["label"], True
        elif action == "run_schedule":
            target = f"the notebook {w['name']} on this PC"
            consequence = "Starts the schedule's run now, in the background, as Task Scheduler would."
            where, needs_sign_in = "This PC", False
        else:  # cancel
            target = f"the running job of {w['name']}"
            consequence = "Asks Fabric to cancel the job. Work it already committed stays."
            where, needs_sign_in = dep["location"]["label"], True
        if needs_sign_in and not signed_in:
            return {"allowed": False, "reason": "Sign in first: this action uses Fabric."}
        identity = dep["identity"]["label"] if dep["location"]["kind"] != "fabric" else \
            f"You{f' ({user})' if user else ''}: a run started here runs as the person who starts it"
        return {"allowed": True, "action": action, "label": _ACTION_LABELS.get(action, action), "workload": w["name"],
                "env": env, "env_label": dep.get("env_label") or "", "where": where, "identity": identity,
                "target": target, "consequence": consequence, "warnings": warnings,
                "recorded": "Recorded on this workload as submitted, then as succeeded or failed when the run finishes"
                            + (", and on Platform > Audit." if self.workload_audit_state()["platform"] != "none" else "."),
                "risk": self._risk_for_preflight(w, action, env) if assess else None}  # type: ignore[attr-defined]

    def workload_act(self, id: str, action: str, env: str | None = None, schedule_id: str = "",
                     job_id: str = "") -> dict[str, Any]:
        """Do a supported action through the service that already does it, and record the outcome."""
        pre = self.workload_preflight(id, action, env, assess=False)
        if not pre.get("allowed"):
            raise PermissionError(pre.get("reason") or "Not allowed")
        w = self._wl_record(id)
        d = w["definition"]
        store = self.workload_store
        user = self._wl_user()
        record = store.start_action(id, action, env or "", {"target": pre["target"], "where": pre["where"]},
                                    pre["identity"], user)
        self._wl_audit_action(record, w["name"])
        try:
            if w["kind"] == "meta_entity" and action == "run":
                results = self.meta_run([w["name"]], env or "dev")  # type: ignore[attr-defined]
                sub = next((r for r in results if r.get("status") == "submitted"), None)
                if sub:
                    record = store.update_action(record["id"], ref={"job_id": sub.get("job_id"),
                                                                     "request_id": sub.get("request_id")},
                                                 detail=sub.get("message", ""))
                else:
                    bad = [r for r in results if r.get("status") not in ("ok", "skipped")]
                    record = store.update_action(record["id"], "failed" if bad else "succeeded",
                                                 detail="; ".join(f"{r.get('entity')}: {r.get('status')} {r.get('message') or ''}".strip()
                                                                  for r in results)[:2000],
                                                 ref={"run_ids": [r.get("run_id") for r in results]})
                outcome: Any = results
            elif action == "run_in_fabric":
                nb = self.notebooks.load(w["name"])  # type: ignore[attr-defined]
                out = self.run_notebook_in_fabric(w["name"], nb["cells"], nb.get("lakehouse"),  # type: ignore[attr-defined]
                                                  nb.get("compute"), nb.get("workspace"))
                record = store.update_action(record["id"], ref={"job_id": out.get("job_id")})
                outcome = out
            elif action == "run_item":
                out = self.run_fabric_item_now(d["workspace_id"], d["item_id"], d["job_type"], w["name"],  # type: ignore[attr-defined]
                                               d.get("workspace_name", ""))
                record = store.update_action(record["id"], ref={"job_id": out.get("job_id")})
                outcome = out
            elif action == "run_schedule":
                if not schedule_id:
                    raise ValueError("Say which schedule to run")
                outcome = self.run_pc_schedule_now(schedule_id)  # type: ignore[attr-defined]
                record = store.update_action(record["id"], ref={"schedule_id": schedule_id})
            elif action == "cancel":
                loc = next(x for x in w["deployments"] if x["location"]["kind"] == "fabric")["location"]
                outcome = self.cancel_fabric_run(loc["workspace_id"], loc["item_id"], job_id)  # type: ignore[attr-defined]
                record = store.update_action(record["id"], ref={"job_id": job_id},
                                             detail="Fabric accepted the cancel request; waiting for the job to stop.")
            else:
                raise ValueError(f"unknown action {action}")
        except Exception as exc:
            failed = store.update_action(record["id"], "failed", detail=f"{type(exc).__name__}: {exc}")
            self._wl_audit_action(failed, w["name"])
            self._wl_records = None
            raise
        if record["status"] != "submitted":
            self._wl_audit_action(record, w["name"])
        self._wl_records = None
        return {"action": record, "result": outcome}

    def _wl_reconcile(self, fabric_runs: list[dict[str, Any]], pc_runs: list[dict[str, Any]]) -> None:
        """Settle submitted actions from what the run stores now say. Never guesses."""
        store = self.workload_store
        open_actions = store.actions(open_only=True, limit=200)
        if not open_actions:
            return
        by_job = {r.get("job_id"): r for r in fabric_runs if r.get("job_id")}
        for a in open_actions:
            ref = a.get("ref") or {}
            if ref.get("job_id"):
                run = by_job.get(ref["job_id"])
                if run and run.get("status") in TERMINAL:
                    state = model.run_state(run["status"], "fabric")
                    if a["action"] == "cancel":
                        status = "succeeded" if state == "cancelled" else "failed"
                        detail = ("The job stopped." if status == "succeeded"
                                  else f"The job ended as {run['status']} before the cancel took effect.")
                    else:
                        status = {"ok": "succeeded", "cancelled": "cancelled", "skipped": "cancelled"}.get(state, "failed")
                        detail = run.get("message") or f"Fabric says {run['status']}"
                    self._wl_audit_action(store.update_action(a["id"], status, detail=detail))
            elif ref.get("schedule_id"):
                done = [r for r in pc_runs if r.get("schedule_id") == ref["schedule_id"]
                        and r.get("trigger") == "manual" and str(r.get("started") or "") >= str(a["at"])[:19]]
                if done:
                    r = done[-1]
                    self._wl_audit_action(store.update_action(a["id"], "succeeded" if r.get("status") == "ok" else "failed",
                                                              detail=r.get("message") or r.get("status", "")))

    def workload_actions(self, id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.workload_store.actions(id or None, limit)

    # ---- the platform's audit (plan item 76): every action and change above, as the platform sees it

    def _wl_audit_action(self, record: dict[str, Any], name: str = "") -> None:
        """One line per state an action reaches: submitted, then how it ended."""
        target = record.get("target") or {}
        self._wl_audit(record["wid"], record["action"], record["status"],
                       {"id": record["id"], "name": name, "env": record.get("env"), "where": target.get("where"),
                        "target": target.get("target"), "identity": record.get("identity"),
                        "detail": record.get("detail"), "at": record.get("ended_at") or record.get("at")},
                       key=f"{record['id']}:{record['status']}")

    def _wl_audit(self, wid: str, action: str, status: str, entry: dict[str, Any], key: str = "") -> None:
        """Keep the line on this PC, then send what's waiting. Never fails the action: the action
        happened whether or not the platform heard about it."""
        try:
            entry = {k: secrets(str(v)) for k, v in entry.items() if v not in (None, "")}
            entry.setdefault("at", model.iso(model.now()))
            self.workload_store.audit_queue(key or uuid.uuid4().hex, wid, action, status, entry)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Couldn't keep a workload audit line: %s", describe(exc))
            return
        self._wl_audit_send()

    def _wl_audit_platform(self) -> Any:
        """The platform the audit lines go to, which never asks anyone to sign in. None when there
        is no platform yet: the lines wait for one, and this doesn't make one."""
        cfg = self._platform_config().get(CATALOG_ENV, {})  # type: ignore[attr-defined]
        if not cfg.get("functions_id"):
            local = self.root / "platform" / f"platform-{CATALOG_ENV}.sqlite"
            return self._catalog() if local.exists() else None  # type: ignore[attr-defined]

        def token() -> str:
            t = self._wl_token(EXECUTE_SCOPES)
            if not t:
                raise PermissionError("Signed out. They're sent after you sign in.")
            return t
        return FabricPlatform(token, cfg["workspace_id"], cfg["functions_id"])

    def _wl_audit_send(self) -> int:
        """Send waiting lines oldest first; stop at the first the platform doesn't take, so they
        keep their order. One send at a time; a call while one runs leaves its lines to that one or
        the next. Returns how many went."""
        if not self._wl_audit_lock.acquire(blocking=False):
            return 0
        try:
            return self._wl_audit_send_locked()
        finally:
            self._wl_audit_lock.release()

    def _wl_audit_send_locked(self) -> int:
        store = self.workload_store
        try:
            waiting = store.audit_waiting(AUDIT_BATCH)
            if not waiting:
                return 0
            platform = self._wl_audit_platform()
        except Exception as exc:  # noqa: BLE001
            _log.warning("Couldn't reach the platform for workload audit lines: %s", describe(exc))
            return 0
        if platform is None:
            return 0
        sent = 0
        for line in waiting:
            try:
                platform.call("log_workload_action", workload=line["workload"], action=line["action"],
                              status=line["status"], entry=line["entry"])
            except Exception as exc:  # noqa: BLE001 - kept on this PC and tried again
                store.audit_result(line["key"], _audit_problem(exc))
                break
            store.audit_result(line["key"])
            sent += 1
        return sent

    def _wl_audit_retry(self) -> None:
        """Try waiting lines again in the background, at most every AUDIT_RETRY_AFTER seconds."""
        state = self.workload_audit_state()
        if not state["waiting"] or state["platform"] == "none":
            return
        last = model.parse_time(state["tried_at"]) if state["tried_at"] else None
        if last and (model.now() - last).total_seconds() < AUDIT_RETRY_AFTER:
            return
        threading.Thread(target=self._wl_audit_send, name="workload-audit", daemon=True).start()

    def workload_audit_state(self) -> dict[str, Any]:
        """How many workload audit lines this PC still holds for the platform, and why."""
        state = self.workload_store.audit_state()
        on_fabric = bool(self._platform_config().get(CATALOG_ENV, {}).get("functions_id"))  # type: ignore[attr-defined]
        local = (self.root / "platform" / f"platform-{CATALOG_ENV}.sqlite").exists()
        return state | {"platform": "fabric" if on_fabric else "this_pc" if local else "none"}


def _audit_text(value: Any) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value or "")


def _audit_problem(exc: BaseException) -> str:
    """Why the platform didn't take a line, in words an admin can act on."""
    text = describe(exc)
    if "log_workload_action" in text or isinstance(exc, LookupError):
        return ("a platform admin may need to run Platform > Settings > Finish or repair once, so the platform "
                "records workload actions. " + text)
    return text


def _rows(df: Any) -> list[dict[str, Any]]:
    try:
        return [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in r.items()}
                for r in df.to_dicts()]
    except Exception as exc:  # noqa: BLE001
        _log.warning("Couldn't convert rows for the workload view: %s", describe(exc))
        return []
