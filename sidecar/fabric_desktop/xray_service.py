"""Estate X-ray on Home: scan every lakehouse, fix what it finds, adopt a lakehouse into metadata."""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re
import threading
from typing import Any, Callable

import polars as pl
import pyarrow as pa
from deltalake import DeltaTable

from . import app_settings, http_session, maintenance, risk, xray
from .auth import FABRIC_SCOPES, POWERBI_DATAFLOW_SCOPES
from .fabric_api import FabricClient
from .fabric_run import TERMINAL
from .meta_service import NoMetadataProject
from .meta.templates import _columns_toml, _dtype_name
from .onelake import FileStore, TableRef, storage_options
from .redact import describe

_log = logging.getLogger(__name__)


def _entity_name(text: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return name if name and not name[0].isdigit() else f"t_{name}"


def guess_keys(columns: list[str]) -> list[str]:
    """The column that most looks like the table's own id."""
    lower = [c.lower() for c in columns]
    for want in ("id",):
        if want in lower:
            return [columns[lower.index(want)]]
    ids = [c for c in columns if c.lower().endswith(("_id", "id", "_key", "key", "_code"))]
    return ids[:1] or columns[:1]


def adopted_entity_text(name: str, owner: str, uri: str, label: str, schema: dict[str, pl.DataType],
                        keys: list[str]) -> str:
    """An entity that loads an existing lakehouse table as it is (a full copy each run, or new rows by a timestamp)."""
    columns = {c: _dtype_name(t) for c, t in schema.items()}
    wm = next((c for c in columns if c.lower() in ("modified_at", "updated_at", "last_modified", "modifieddate",
                                                    "updated", "last_updated")), "")
    load = f'mode = "incremental"\nwatermark = {json.dumps(wm)}' if wm else 'mode = "full"'
    checks = "".join(f'\n[[check]]\ncolumn = {json.dumps(k)}\nnot_null = true\nunique = true\non_fail = "stop"\n' for k in keys[:1])
    return f"""name = {json.dumps(name)}
owner = {json.dumps(owner or "me")}
description = {json.dumps(f"Adopted from {label}")}

[source]
table = {json.dumps(uri)}   # read as it is; the original table is never changed
from = {json.dumps(label)}

[load]
{load}

[contract]
keys = {json.dumps(keys)}

[contract.columns]
{_columns_toml(columns)}
{checks}"""



def _layer_workspaces(project: Any) -> list[tuple[str, Any]]:
    """(workspace id, layer) for every environment: a layer with a designation (workspace =
    "serve" in layers.toml) lives in that designation's workspace. The environment's own workspace
    counts for every layer too, since a designated layer may have lived there before: keeping a
    layer's history in one workspace too many never deletes anything Restore needs."""
    out = []
    for cfg in project.environments.values():
        spaces = cfg.get("workspaces") or {}
        for name in project.layer_names():
            layer = project.layer(name)
            ws = (spaces.get(layer.workspace) or {}).get("id") if layer.workspace else None
            out += [(w, layer) for w in {ws, cfg.get("workspace_id")} if w]
    return out

class XRayMixin:
    _xray_state: dict[str, Any] | None = None
    _xray_lock = threading.Lock()

    @property
    def _xray_file(self):
        return self.root / "xray.json"  # type: ignore[attr-defined]

    def _xray_saved(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._xray_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    _xray_gate: threading.Event | None = None

    def xray(self, refresh: bool = False, auto: bool = False) -> dict[str, Any]:
        """The last scan, and whether one is running ({"running": True, "progress": {...}}).

        ``auto`` starts the first scan on this PC by itself, once ever; after that a scan
        runs only when asked (``refresh``), so opening Home never starts one."""
        if auto and not refresh and not self._xray_saved() and not self._settings().get("xray_auto_scanned"):  # type: ignore[attr-defined]
            self._save_settings(xray_auto_scanned=True)  # type: ignore[attr-defined]
            refresh = True
        with self._xray_lock:
            state = self._xray_state
            running = bool(state and state.get("running"))
            if refresh and not running:
                fabric_token = self.auth.fabric_token()  # type: ignore[attr-defined]  # MSAL stays on this thread
                storage_token = self.auth.storage_token()  # type: ignore[attr-defined]
                self._xray_state = state = {"running": True, "paused": False, "progress": {"phase": "Starting", "done": 0, "total": 0}}
                self._xray_gate = threading.Event()
                self._xray_gate.set()
                threading.Thread(target=self._xray_run, args=(fabric_token, storage_token, self._xray_gate), daemon=True).start()
                running = True
        saved = self._xray_saved()
        out: dict[str, Any] = {"running": running, "paused": bool(running and (state or {}).get("paused")),
                               "progress": (state or {}).get("progress"), "error": (state or {}).get("error"), "result": saved}
        if saved:
            out["result"] = self._xray_annotate(saved, refresh=not running)
        return out

    def xray_pause(self, paused: bool = True) -> dict[str, Any]:
        """Pause a running scan where it is (calls already under way finish), or carry on."""
        with self._xray_lock:
            state = self._xray_state
            if state and state.get("running") and self._xray_gate is not None:
                state["paused"] = bool(paused)
                (self._xray_gate.clear if paused else self._xray_gate.set)()
        return self.xray()

    def _xray_progress(self, p: dict[str, Any]) -> None:
        with self._xray_lock:
            if self._xray_state:
                self._xray_state["progress"] = p

    def _xray_run(self, fabric_token: str, storage_token: str, gate: threading.Event | None = None) -> None:
        options = storage_options(storage_token)
        files = FileStore(lambda: storage_token)

        def health(ws: str, lh: str, schema: str | None, table: str) -> dict[str, Any]:
            ref = TableRef(ws, lh, schema or "dbo", table, flat=schema is None)
            return maintenance.health(ref.uri, options)

        scanner = xray.Scanner(FabricClient(lambda: fabric_token), files.list, health, self._xray_progress, gate=gate)
        try:
            result = scanner.scan()
            try:
                report = self.cost_report(30)  # type: ignore[attr-defined]
                local = {n["name"] for n in self.notebooks.list()}  # type: ignore[attr-defined]
                result["findings"] = xray.rank(result["findings"] + xray.cost_findings(report | {"days": 30}, local))
            except Exception as exc:  # cost needs the run logs; the rest stands without it
                _log.info("No cost findings for X-ray: %s", describe(exc))
            result["counts"] = xray.counts(result["findings"])
            result["shared"] = self._xray_share(result, lambda: storage_token)
            self._xray_file.parent.mkdir(parents=True, exist_ok=True)
            self._xray_file.write_text(json.dumps(result, default=str), encoding="utf-8")
            state: dict[str, Any] = {"running": False}
        except Exception as exc:
            state = {"running": False, "error": f"{type(exc).__name__}: {exc}"}
        with self._xray_lock:
            self._xray_state = state

    def _xray_share(self, result: dict[str, Any], storage_token: Callable[[], str]) -> dict[str, Any] | None:
        """Add the scan's lakehouse and table findings to the central lakehouse this PC is connected
        to, so Optimize > Opportunities shows them for everyone using it (optimize.Shared). Nothing
        when the PC isn't connected, the policy turns it off, or the lakehouse is open read-only.
        Never fails the scan: what went wrong is kept with the result. ``storage_token`` is the one
        the scan took on the calling thread; this runs on the scan's own thread."""
        conn = self.central_lakehouse()  # type: ignore[attr-defined]
        if not conn or conn.get("access") != "write":
            return None
        try:
            records = xray.optimize_records(result)
            self._xray_progress({"phase": f"Adding findings to {conn.get('lakehouse_name') or 'the central lakehouse'}",
                                 "done": 0, "total": len(records["findings"])})
            out = self._optimize_shared(storage_token).record_analysis(  # type: ignore[attr-defined]
                records["run"], records["artifacts"], records["findings"], records["recommendations"])
            return {"lakehouse": conn.get("lakehouse_name"), "findings": out["findings"]}
        except Exception as exc:  # noqa: BLE001 - the scan stands without it
            _log.info("X-ray findings weren't added to the central lakehouse: %s", describe(exc))
            return {"lakehouse": conn.get("lakehouse_name"), "error": describe(exc)}

    def _xray_annotate(self, result: dict[str, Any], refresh: bool = True) -> dict[str, Any]:
        """Which lakehouses the metadata project already manages, and which findings have a fix under way or done."""
        managed: set[tuple[str, str]] = set()
        adopted: set[str] = set()
        try:
            project = self._project()  # type: ignore[attr-defined]
            managed |= {(ws, layer.lakehouse) for ws, layer in _layer_workspaces(project)}
            adopted = {str(e.spec.get("source", {}).get("table", "")) for e in project.entities.values()}
        except Exception as exc:
            _log.info("No metadata project for X-ray: %s", describe(exc))
        fixes = self._xray_fixes(result.get("scanned"), refresh)
        rows = []
        for r in result.get("lakehouse_rows", []):
            prefix = f"abfss://{r['workspace_id']}@"
            n_adopted = sum(1 for a in adopted if a.startswith(prefix) and f"/{r['lakehouse_id']}/" in a)
            rows.append(r | {"managed": (r["workspace_id"], r["lakehouse_name"]) in managed, "adopted": n_adopted})
        removed = self._settings().get("xray_removed") or {}  # type: ignore[attr-defined]
        findings = []
        for f in result.get("findings", []):
            fix = fixes.get(_finding_id(f))
            if _finding_id(f) in removed:  # a leftover removed since this scan: nothing to wait for
                fix = {"state": "fixed", "removed_at": removed[_finding_id(f)]}
            findings.append(f | {"id": _finding_id(f), "fixed": bool(fix and fix["state"] == "fixed"), "fix": fix})
        return result | {"lakehouse_rows": rows, "findings": findings}

    def _xray_keep_days(self, workspace_id: str, lakehouse_name: str, table_days: int | None) -> tuple[int, str]:
        """The days of history a VACUUM on this table must keep, and why: the longest of Fabric's
        default, the table's own retention setting, and the Keep history days of the metadata
        layer whose lakehouse it is (so Restore keeps working as far back as that layer promises)."""
        keep = [(maintenance.RETENTION_HOURS // 24, "Fabric's default")]
        if table_days:
            keep.append((table_days, "the table's own retention setting"))
        try:
            project = self._project()  # type: ignore[attr-defined]
            keep += [(layer.retention_days, f"Keep history on the {layer.name} layer")
                     for ws, layer in _layer_workspaces(project) if ws == workspace_id and layer.lakehouse == lakehouse_name]
        except NoMetadataProject:  # no metadata project: nothing more to keep
            pass
        except Exception as exc:  # a project that can't be read may promise more: guessing 7 days could delete it
            raise RuntimeError(f"Couldn't read the metadata project ({type(exc).__name__}: {exc}), so the days of "
                               "history its layers keep aren't known. Nothing was started; fix the project and try again.") from exc
        days, why = max(keep, key=lambda k: k[0])
        return days, why

    def _xray_table(self, workspace_id: str, lakehouse_id: str, schema: str | None, table: str) -> tuple[str, dict[str, str]]:
        ref = TableRef(workspace_id, lakehouse_id, schema or "dbo", table, flat=schema is None)
        return ref.uri, storage_options(self.auth.storage_token())  # type: ignore[attr-defined]

    def _xray_table_days(self, uri: str, options: dict[str, str]) -> int | None:
        try:
            return maintenance.kept_days(DeltaTable(uri, storage_options=options).metadata().configuration)
        except Exception as exc:  # its own retention setting may be longer than the layer's: don't guess
            raise RuntimeError(f"Couldn't read the table's retention setting ({type(exc).__name__}: {exc}), so the days "
                               "of history it keeps aren't known. Nothing was started; try again when the table can be read.") from exc

    def _xray_mark_submitted(self, finding_id: str, job_id: str, workspace_id: str, item_id: str) -> None:
        """The fix's Fabric job, so the finding shows "submitted" until the job finishes."""
        if finding_id:
            subs = dict(self._settings().get("xray_submitted") or {})  # type: ignore[attr-defined]
            subs[finding_id] = {"job_id": job_id, "workspace_id": workspace_id, "item_id": item_id,
                                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
            self._save_settings(xray_submitted=subs)  # type: ignore[attr-defined]

    def _xray_fixes(self, scanned: str | None, refresh: bool = True) -> dict[str, dict[str, Any]]:
        """What became of each finding's fix job, from the Fabric runs log: "submitted" while it runs,
        "fixed" once it completed, "failed" with Fabric's reason. A job that finished before the
        scan began is superseded by that scan: if the scan still found the problem, it shows again."""
        subs = self._settings().get("xray_submitted") or {}  # type: ignore[attr-defined]
        if not subs:
            return {}
        runs = {r.get("job_id"): r for r in self.fabric_runs(limit=500, refresh=refresh)}  # type: ignore[attr-defined]
        out = {}
        for fid, sub in subs.items():
            run = runs.get(sub.get("job_id"))
            if run is None:  # gone from the runs log: what became of it isn't known, so the finding shows as found
                continue
            status = run.get("status") or "NotStarted"
            if status in TERMINAL:
                ended = str(run.get("finished_at") or sub.get("submitted_at") or "")[:19]
                if scanned and ended < str(scanned)[:19]:  # both UTC; seconds are enough
                    continue
            state = "fixed" if status == "Completed" else "failed" if status in TERMINAL else "submitted"
            out[fid] = {"state": state, "job_id": sub.get("job_id"), "status": status, "message": run.get("message") or ""}
        return out

    def xray_optimize(self, workspace_id: str, lakehouse_id: str, table: str, schema: str | None = None,
                      lakehouse_name: str = "", workspace_name: str = "", finding_id: str = "") -> dict[str, Any]:
        """Fabric's table maintenance (OPTIMIZE with V-Order, then VACUUM) on one table."""
        uri, options = self._xray_table(workspace_id, lakehouse_id, schema, table)
        days, _why = self._xray_keep_days(workspace_id, lakehouse_name, self._xray_table_days(uri, options))
        job_id = self.fabric_jobs.run_table_maintenance(  # type: ignore[attr-defined]
            workspace_id, lakehouse_id, table, schema, vacuum_retention=maintenance.fabric_retention(days))
        label = f"{schema}.{table}" if schema else table
        self._xray_mark_submitted(finding_id, job_id, workspace_id, lakehouse_id)
        return self._log_fabric_run({"kind": "Table maintenance", "name": f"{lakehouse_name} {label}".strip(),  # type: ignore[attr-defined]
                                     "table": label, "workspace_id": workspace_id, "workspace_name": workspace_name,
                                     "item_id": lakehouse_id, "job_id": job_id})

    def xray_optimize_check(self, workspace_id: str, lakehouse_id: str, table: str, schema: str | None = None,
                            lakehouse_name: str = "", workspace_name: str = "", refresh: bool = False) -> dict[str, Any]:
        """What Optimize on one table would do, and the risk check (FINOPS-004) for starting it in
        Fabric: the policy, the sign-in and the workspace's capacity. Read only."""
        uri, options = self._xray_table(workspace_id, lakehouse_id, schema, table)
        days, why = self._xray_keep_days(workspace_id, lakehouse_name, self._xray_table_days(uri, options))
        label = f"{schema}.{table}" if schema else table
        reads: list[str] = []
        facts: dict[str, Any] = {
            "workload_id": f"xray:{workspace_id}/{lakehouse_id}/{label}", "workload": f"{lakehouse_name} {label}".strip(),
            "action": "run_in_fabric", "label": "Optimize", "env": None, "env_label": "", "where": "fabric",
            "needs_sign_in": True, "policy": {"allow_fabric_runs": bool(app_settings.value("allow_fabric_runs"))},
            "signed_in": bool(self._wl_token(FABRIC_SCOPES)),  # type: ignore[attr-defined]
            "definition_errors": [], "version": None, "schedules": [], "runs": [], "measured": [], "estimated": [],
            "reads": reads,
        }
        try:
            facts["capacity"] = self._risk_capacity(workspace_id, workspace_name, refresh, reads)  # type: ignore[attr-defined]
            assessed = risk.analyze(facts, self._risk_settings())  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the check is advice; the confirm still shows
            assessed = {"result": risk.UNKNOWN, "label": risk.LABELS[risk.UNKNOWN], "findings": [], "can_proceed": True,
                        "needs_acknowledgement": False, "enforced": False, "summary": f"The risk check couldn't run: {exc}"[:300]}
        return {"keep_days": days, "why": why, "risk": assessed}

    def xray_vacuum_preview(self, workspace_id: str, lakehouse_id: str, table: str, schema: str | None = None,
                            lakehouse_name: str = "") -> dict[str, Any]:
        """What Vacuum would delete from one table, found by a dry run that deletes nothing: how
        many files, how much storage, and how many days of history it keeps (and why)."""
        uri, options = self._xray_table(workspace_id, lakehouse_id, schema, table)
        days, why = self._xray_keep_days(workspace_id, lakehouse_name, self._xray_table_days(uri, options))
        doomed = maintenance.vacuum_preview(uri, days, options)
        folder = f"Tables/{schema}/{table}" if schema else f"Tables/{table}"
        size = None
        try:
            listed = FileStore(self.auth.storage_token).list(workspace_id, lakehouse_id, folder, recursive=True)  # type: ignore[attr-defined]
            sizes = {e["path"][len(folder) + 1:]: e["size"] for e in listed if not e["is_dir"]}
            size = sum(sizes.get(f, 0) for f in doomed)
        except Exception as exc:  # the count still stands without the sizes
            _log.warning("Couldn't size the files a vacuum would delete: %s", describe(exc))
        return {"files": len(doomed), "bytes": size, "keep_days": days, "why": why}

    def xray_vacuum(self, workspace_id: str, lakehouse_id: str, table: str, schema: str | None = None,
                    lakehouse_name: str = "", workspace_name: str = "", finding_id: str = "") -> dict[str, Any]:
        """VACUUM alone, as Fabric's table maintenance job, keeping the history this table promises."""
        uri, options = self._xray_table(workspace_id, lakehouse_id, schema, table)
        days, why = self._xray_keep_days(workspace_id, lakehouse_name, self._xray_table_days(uri, options))
        job_id = self.fabric_jobs.run_table_maintenance(  # type: ignore[attr-defined]
            workspace_id, lakehouse_id, table, schema, vacuum_retention=maintenance.fabric_retention(days), optimize=False)
        label = f"{schema}.{table}" if schema else table
        self._xray_mark_submitted(finding_id, job_id, workspace_id, lakehouse_id)
        run = self._log_fabric_run({"kind": "Table maintenance", "name": f"Vacuum {lakehouse_name} {label}".replace("  ", " ").strip(),  # type: ignore[attr-defined]
                                    "table": label, "workspace_id": workspace_id, "workspace_name": workspace_name,
                                    "item_id": lakehouse_id, "job_id": job_id})
        return run | {"keep_days": days, "why": why}

    # ---- leftover Dataflow Gen2 staging lakehouses

    def _staging_check(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        """Whether one staging lakehouse can be removed, read live from Fabric and OneLake (never
        from the scan): it must still exist with a staging name, a newer staging lakehouse must
        exist in the same workspace, and nothing may have been written to its Tables or Files for
        STAGING_QUIET_DAYS, and every dataflow in the workspace must show it doesn't stage there
        (_staging_dataflows). Anything that can't be read keeps it. Read only."""
        if not _GUID.fullmatch(str(workspace_id or "")):
            raise ValueError("Pick a workspace: its Fabric id is needed.")
        now = dt.datetime.now(dt.timezone.utc)
        group = [{"workspace_id": workspace_id, "workspace_name": "", "lakehouse_id": lh["id"], "lakehouse_name": lh["displayName"]}
                 for lh in FabricClient(self.auth.fabric_token).list_lakehouses(workspace_id)]  # type: ignore[attr-defined]
        me = next((lh for lh in group if lh["lakehouse_id"] == lakehouse_id), None)
        if me is None:
            raise ValueError("That lakehouse isn't in the workspace any more. Scan again.")
        name = me["lakehouse_name"]
        made = xray.staging_created(name)
        out: dict[str, Any] = {"lakehouse_name": name, "created": made.isoformat(timespec="seconds") if made else None,
                               "kept": None, "files": None, "bytes": None, "last_write": None,
                               "quiet_days": xray.STAGING_QUIET_DAYS, "allowed": bool(app_settings.value("allow_staging_cleanup")),
                               "ok": False, "why": []}
        if not made:
            out["why"].append(f"{name} isn't named like a Dataflow Gen2 staging lakehouse, so it isn't removed from here.")
            return out
        newest, older = xray.staging_split([lh for lh in group if xray.is_staging(lh["lakehouse_name"])])
        out["kept"] = newest["lakehouse_name"] if newest else None
        if lakehouse_id not in {lh["lakehouse_id"] for lh in older}:
            out["why"].append(f"{name} is the newest staging lakehouse in this workspace, so dataflows may be staging in it.")
        files = FileStore(self.auth.storage_token)  # type: ignore[attr-defined]
        entries: list[dict[str, Any]] = []
        try:
            for folder in ("Tables", "Files"):
                entries += [e for e in files.list(workspace_id, lakehouse_id, folder, recursive=True)]
        except Exception as exc:  # noqa: BLE001 - not knowing what's in it keeps it
            out["why"].append(f"Its tables and files couldn't be read ({describe(exc)}), so whether it's still used isn't known.")
            return out
        out["files"] = sum(1 for e in entries if not e["is_dir"])
        out["bytes"] = sum(e["size"] for e in entries if not e["is_dir"])
        stamps = [e["modified"] for e in entries if e.get("modified")]
        last = max(stamps) if stamps else made.isoformat(timespec="seconds")
        out["last_write"] = last if stamps else None
        idle = xray._days_since(last, now)
        if idle is None or idle < xray.STAGING_QUIET_DAYS:
            out["why"].append(f"Something was written to it {idle} day{'s' if idle != 1 else ''} ago" if stamps and idle is not None
                              else f"It was made {idle} day{'s' if idle != 1 else ''} ago" if idle is not None
                              else "When it was last written isn't known")
            out["why"][-1] += f", less than {xray.STAGING_QUIET_DAYS} days, so a dataflow may still be using it."
        out["dataflows"] = self._staging_dataflows(workspace_id, lakehouse_id, name, _when(last), out["why"])
        if not out["allowed"]:
            out["why"].append("Your organization turned off removing staging lakehouses (allow_staging_cleanup in its policy file).")
        out["ok"] = not out["why"]
        return out

    def _staging_dataflows(self, workspace_id: str, lakehouse_id: str, name: str, last_write: dt.datetime | None,
                           why: list[str]) -> list[dict[str, Any]]:
        """The proof that no dataflow in the workspace stages in this lakehouse: every dataflow has
        a completed refresh that started after the lakehouse was last written (so a dataflow staging
        there would have written to it since), and no dataflow's definition names it. Fabric doesn't
        say which staging lakehouse a dataflow uses, so a dataflow that can't be shown not to use it
        (never refreshed, history gone, definition unreadable) keeps it. Adds the reasons to ``why``;
        returns one row per dataflow. Read only: getDefinition is a POST but changes nothing."""
        fabric = FabricClient(self.auth.fabric_token)  # type: ignore[attr-defined]
        try:
            flows = [it for it in fabric.list_items(workspace_id) if it.get("type") == "Dataflow"]
        except Exception as exc:  # noqa: BLE001 - not knowing the dataflows keeps it
            why.append(f"The workspace's dataflows couldn't be listed ({describe(exc)}), so whether one stages in it isn't known.")
            return []
        if not flows:
            why.append("Fabric lists no Dataflow Gen2 items in this workspace, and older dataflows aren't listed by its API, "
                       "so whether one stages in it isn't known.")
            return []
        try:
            fabric_ids = {str(f.get("id") or "").lower() for f in flows}
            unlisted = [d for d in self._powerbi_dataflows(workspace_id) if str(d.get("objectId") or "").lower() not in fabric_ids]
        except Exception as exc:  # noqa: BLE001 - the older dataflows unknown: one may stage there
            why.append(f"Power BI's list of the workspace's dataflows couldn't be read ({describe(exc)}). It's the only list "
                       "with older dataflows (Gen1, and Gen2 without CI/CD), so whether one stages in it isn't known.")
            unlisted = []
        if unlisted:
            names = ", ".join(sorted(str(d.get("name") or d.get("objectId")) for d in unlisted))
            why.append(f"Older dataflows Fabric's API doesn't describe are in this workspace ({names}), and which staging "
                       "lakehouse they use can't be shown.")
        if last_write is None:
            why.append("When it was last written isn't known, so refreshes can't show it's unused.")
        rows = []
        for flow in flows:
            row = {"name": flow.get("displayName") or "", "item_id": flow.get("id") or "", "last_refresh": None,
                   "names_it": None, "ok": False}
            rows.append(row)
            try:
                text = _definition_text(fabric.get_item_definition(workspace_id, row["item_id"]))
                row["names_it"] = any(re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text, re.IGNORECASE)
                                      for word in (lakehouse_id, name))
            except Exception as exc:  # noqa: BLE001 - an unread definition might name it
                why.append(f"The definition of {row['name']} couldn't be read ({describe(exc)}).")
                continue
            if row["names_it"]:
                why.append(f"The definition of {row['name']} names it.")
                continue
            try:
                done = [j for j in fabric.list_job_instances(workspace_id, row["item_id"]) if j.get("status") == "Completed"]
            except Exception as exc:  # noqa: BLE001 - no history: no proof
                why.append(f"The refreshes of {row['name']} couldn't be read ({describe(exc)}).")
                continue
            started = max((t for t in (_when(j.get("startTimeUtc")) for j in done) if t), default=None)
            row["last_refresh"] = started.isoformat(timespec="seconds") if started else None
            if started is None:
                why.append(f"{row['name']} has no completed refresh Fabric still lists, so it may stage there when it next runs.")
            elif last_write is not None and started < last_write + STAGING_MARGIN:
                why.append(f"{row['name']} last refreshed before the lakehouse was last written, so it may stage there.")
            else:
                row["ok"] = last_write is not None
        return rows

    def _powerbi_dataflows(self, workspace_id: str) -> list[dict[str, Any]]:
        """Every dataflow Power BI lists in the workspace: Gen1 and Dataflow Gen2 without CI/CD, which
        Fabric's items API leaves out ({"objectId", "name"}). Raises when it can't be read."""
        token = self.auth._token(POWERBI_DATAFLOW_SCOPES)  # type: ignore[attr-defined]
        r = http_session.session().get(f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/dataflows",
                                       headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        return list(r.json().get("value") or [])

    def xray_staging_preview(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        """What removing one leftover staging lakehouse would delete, and anything that stops it."""
        return self._staging_check(workspace_id, lakehouse_id)

    def xray_remove_staging(self, workspace_id: str, lakehouse_id: str, lakehouse_name: str,
                            workspace_name: str = "", finding_id: str = "") -> dict[str, Any]:
        """Delete one leftover staging lakehouse in Fabric, after checking again everything the
        preview checked. ``lakehouse_name`` is the name the person confirmed: a lakehouse renamed
        since is not deleted."""
        check = self._staging_check(workspace_id, lakehouse_id)
        if check["lakehouse_name"] != lakehouse_name:
            raise ValueError(f"The lakehouse is now called {check['lakehouse_name']}, not {lakehouse_name}. Nothing was deleted; scan again.")
        if not check["ok"]:
            raise ValueError(f"{lakehouse_name} wasn't deleted. " + " ".join(check["why"]))
        FabricClient(self.auth.fabric_token).delete_lakehouse(workspace_id, lakehouse_id)  # type: ignore[attr-defined]
        removed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _log.info("Removed leftover staging lakehouse %s (%s) in workspace %s; kept %s",
                  lakehouse_name, lakehouse_id, workspace_name or workspace_id, check["kept"])
        if finding_id:
            cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).isoformat(timespec="seconds")
            removed = {k: v for k, v in (self._settings().get("xray_removed") or {}).items()  # type: ignore[attr-defined]
                       if str(v) >= cutoff}  # long gone from every scan: no need to keep them
            removed[finding_id] = removed_at
            self._save_settings(xray_removed=removed)  # type: ignore[attr-defined]
        return check | {"removed_at": removed_at}

    # ---- adopt a lakehouse into the metadata project

    def _source_table(self, workspace_id: str, lakehouse_id: str, schema: str | None, table: str) -> tuple[str, DeltaTable]:
        ref = TableRef(workspace_id, lakehouse_id, schema or "dbo", table, flat=schema is None)
        return ref.uri, DeltaTable(ref.uri, storage_options=self._source_options())  # type: ignore[attr-defined]

    def xray_adopt_plan(self, workspace_id: str, lakehouse_id: str, lakehouse_name: str,
                        workspace_name: str = "") -> dict[str, Any]:
        """The lakehouse's tables, each with the entity it would become and the columns it has."""
        project = self._project()  # type: ignore[attr-defined]
        layer = project.layer_names()[0]
        files = FileStore(self.auth.storage_token)  # type: ignore[attr-defined]
        scanner = xray.Scanner(None, files.list, lambda *_a: {})
        tables, _ = scanner._tables({"workspace_id": workspace_id, "workspace_name": workspace_name,
                                     "lakehouse_id": lakehouse_id, "lakehouse_name": lakehouse_name})
        out = []
        for t in tables:
            try:
                uri, dt_ = self._source_table(workspace_id, lakehouse_id, t["schema"], t["table"])
                cols = list(_table_schema(dt_))
            except Exception as exc:
                out.append({"schema": t["schema"], "table": t["table"], "ok": False, "why": "Not a Delta table"})
                continue
            name = f"{layer}.{_entity_name(t['table'])}"
            already = next((e.name for e in project.entities.values() if e.spec.get("source", {}).get("table") == uri), None)
            out.append({"schema": t["schema"], "table": t["table"], "ok": not already, "columns": cols,
                        "keys": guess_keys(cols), "name": name, "exists": name in project.entities,
                        "why": f"Already adopted as {already}" if already else ""})
        return {"layer": layer, "layers": project.layer_names(), "tables": out}

    def xray_adopt(self, workspace_id: str, lakehouse_id: str, lakehouse_name: str, workspace_name: str,
                   tables: list[dict[str, Any]], layer: str | None = None) -> dict[str, Any]:
        """Write one entity per picked table; each loads from the original, which is never changed."""
        project = self._project()  # type: ignore[attr-defined]
        layer = layer or project.layer_names()[0]
        if layer not in project.layer_names():
            raise ValueError(f"{layer} is not a layer")
        try:
            owner = (self.auth.signed_in_user() or "").split("@")[0]  # type: ignore[attr-defined]
        except Exception:
            owner = ""
        written, problems = [], []
        for t in tables:
            name = f"{layer}.{_entity_name(t.get('name', '').split('.')[-1] or t['table'])}"
            if name in project.entities:
                problems.append(f"{name} already exists")
                continue
            try:
                uri, table = self._source_table(workspace_id, lakehouse_id, t.get("schema"), t["table"])
                schema = _table_schema(table)
            except Exception as exc:
                problems.append(f"{t['table']}: {type(exc).__name__}: {exc}")
                continue
            keys = [k for k in t.get("keys") or [] if k in schema] or guess_keys(list(schema))
            where = f"{t['schema']}.{t['table']}" if t.get("schema") else t["table"]
            label = f"{workspace_name} / {lakehouse_name} / {where}".strip(" /")
            text = adopted_entity_text(name, owner, uri, label, dict(schema), keys)
            (project.entities_dir / f"{name}.toml").write_text(text, encoding="utf-8")
            written.append(name)
        return {"adopted": written, "problems": problems, "layer": layer}


def _table_schema(table: DeltaTable) -> pl.Schema:
    return pl.from_arrow(pa.schema(table.schema().to_arrow()).empty_table()).schema  # type: ignore[union-attr]


_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
STAGING_MARGIN = dt.timedelta(hours=1)  # a refresh must start this long after the last write: clocks differ


def _when(iso: Any) -> dt.datetime | None:
    """An ISO time as UTC, or None."""
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _definition_text(definition: dict[str, Any]) -> str:
    """Every part of an item definition, decoded, as one text to search. A definition with no
    parts is unreadable, not proof of anything."""
    parts = (definition.get("definition") or definition).get("parts") or []
    out = []
    for part in parts:
        payload = part.get("payload") or ""
        try:
            out.append(base64.b64decode(payload).decode("utf-8", "replace") if part.get("payloadType", "InlineBase64") == "InlineBase64" else str(payload))
        except ValueError:
            out.append(str(payload))
        out.append(str(part.get("path") or ""))
    if not parts:
        raise ValueError("Fabric returned no definition parts")
    return "\n".join(out)


def _finding_id(f: dict[str, Any]) -> str:
    return "|".join(str(f.get(k) or "") for k in ("kind", "workspace_id", "lakehouse_id", "schema", "table", "person", "notebook"))
