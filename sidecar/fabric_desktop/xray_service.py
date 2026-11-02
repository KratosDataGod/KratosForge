"""Estate X-ray on Home: scan every lakehouse, fix what it finds, adopt a lakehouse into metadata."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
from typing import Any, Callable

import polars as pl
import pyarrow as pa
from deltalake import DeltaTable

from . import app_settings, jsonfile, maintenance, risk, xray
from .auth import FABRIC_SCOPES
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
            jsonfile.write_text(self._xray_file, json.dumps(result, default=str))
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
        findings = []
        for f in result.get("findings", []):
            fix = fixes.get(_finding_id(f))
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
                      lakehouse_name: str = "", workspace_name: str = "", finding_id: str = "",
                      risk_id: str = "") -> dict[str, Any]:
        """Fabric's table maintenance (OPTIMIZE with V-Order, then VACUUM) on one table.

        The risk check is run again first (FINOPS-004): blocked is refused, and going ahead past
        warnings needs ``risk_id``, the id of the assessment the person saw, and is recorded the way
        risk_acknowledge records it, before the job is submitted. A pass or an unknown result records nothing."""
        uri, options = self._xray_table(workspace_id, lakehouse_id, schema, table)
        days, _why = self._xray_keep_days(workspace_id, lakehouse_name, self._xray_table_days(uri, options))
        current = self._xray_optimize_risk(workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name, False)
        if current["result"] == risk.BLOCKED:
            raise PermissionError("A blocked run can't be started: " + current["summary"])
        if current["result"] == risk.WARNING:
            if not risk_id:
                raise ValueError("Check the risks first: this run has warnings to accept.")
            if current["id"] != risk_id:
                raise ValueError("The findings changed since you looked. Check them again before you go ahead.")
            # Written before the job is submitted, so a failed write starts nothing (fails closed).
            self._risk_ack_write(self._risk_ack_record(current, risk_id))  # type: ignore[attr-defined]
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
        return {"keep_days": days, "why": why,
                "risk": self._xray_optimize_risk(workspace_id, lakehouse_id, table, schema, lakehouse_name,
                                                 workspace_name, refresh)}

    def _xray_optimize_risk(self, workspace_id: str, lakehouse_id: str, table: str, schema: str | None,
                            lakehouse_name: str, workspace_name: str, refresh: bool) -> dict[str, Any]:
        """The risk check for starting Optimize on one table in Fabric; a failure in it is an unknown result."""
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
        return assessed

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
            except Exception as exc:  # noqa: BLE001 - the table is left out of the adoption, with the reason
                _log.warning("Couldn't read %s.%s as a Delta table: %s", t["schema"], t["table"], describe(exc))
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
            jsonfile.write_text(project.entities_dir / f"{name}.toml", text)
            written.append(name)
        return {"adopted": written, "problems": problems, "layer": layer}


def _table_schema(table: DeltaTable) -> pl.Schema:
    return pl.from_arrow(pa.schema(table.schema().to_arrow()).empty_table()).schema  # type: ignore[union-attr]


def _finding_id(f: dict[str, Any]) -> str:
    return "|".join(str(f.get(k) or "") for k in ("kind", "workspace_id", "lakehouse_id", "schema", "table", "person", "notebook"))
