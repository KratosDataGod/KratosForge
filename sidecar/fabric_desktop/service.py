"""Operations the desktop UI calls, shared by the stdio server and the CLI."""

from __future__ import annotations

import json
import logging
import os
import math
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pa_csv

from .audit import AuditLog
from .redact import describe
from .telemetry import Telemetry
from .auth import CAPACITY_SCOPES, EXECUTE_SCOPES, Authenticator
from .fabric_api import FabricClient
from . import app_settings, jsonfile, lakehouse, sql, sql_dialects, sql_exec, sql_format, sqlcompat
from .filenames import file_stem
from .frames import DOWNLOAD_FORMATS, GONE, FrameGone
from .jobs import Job, run_local
from .cloud_service import CloudMixin
from .fabric_run import compute_check
from .dataops_service import DataOpsMixin
from .estate_service import EstateMixin
from .meta_service import MetaMixin
from .meta import diff as diff_mod
from .meta import release
from .schedule_service import ScheduleMixin
from .side_by_side_service import SideBySideMixin
from .prove_service import ProveMixin
from .xray_service import XRayMixin
from .preview_service import PreviewMixin
from .central.service import PlatformMixin
from .central.governance_service import GovernanceMixin
from .shortcut_service import ShortcutMixin
from .workload_service import WorkloadMixin
from .incident_service import IncidentMixin
from .incident_desk import IncidentDeskMixin
from .platform_notify_service import PlatformNotifyMixin
from .git_service import GitMixin
from .pipeline_service import PipelineMixin, PipelineRunMixin
from .integrations.app_service import IntegrationsMixin
from .localdata_service import LocalDataMixin
from .risk_service import RiskMixin
from .usage_service import UsageMixin
from .optimize_service import OptimizeMixin
from .tenant_history_service import TenantHistoryMixin
from .capacity_history_service import CapacityHistoryMixin
from .validate_service import ValidateMixin
from .kernel_proc import ProcessKernel
from .notebook import Kernel, NotebookShelf, check_watches
from .onelake import FileStore, TableCatalog
from .publish import run_job

_log = logging.getLogger(__name__)

# How long a cell start waits for an external tool's run that holds the attached lakehouse.
ATTACH_WAIT_SECONDS = 10.0


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "FabricDesktop"


class Service(PipelineMixin, PipelineRunMixin, GitMixin, IncidentDeskMixin, IncidentMixin, PlatformNotifyMixin, WorkloadMixin, ShortcutMixin, GovernanceMixin, PlatformMixin, MetaMixin, CloudMixin, DataOpsMixin, EstateMixin, ScheduleMixin, XRayMixin, PreviewMixin, SideBySideMixin, ProveMixin, IntegrationsMixin, LocalDataMixin, UsageMixin, RiskMixin, OptimizeMixin, TenantHistoryMixin, CapacityHistoryMixin, ValidateMixin):
    def __init__(self, client_id: str | None = None, root: Path | None = None, headless: bool = False):
        self.headless = headless  # a scheduled run: never open a sign-in window
        self._client_id = client_id  # given by the agent; otherwise from app settings
        self.root = root or data_dir()
        self._auth: Authenticator | None = None
        self.kernel = self._new_kernel()
        self.notebooks = NotebookShelf(self.root / "notebooks", self._notebook_places)
        self._schema_cache: dict[str, bool] = {}
        self._tables: dict[str, Any] | None = None
        self._tables_lock = threading.Lock()

    @property
    def auth(self) -> Authenticator:
        # The app ID can change on the Settings page; a new one needs a new sign-in.
        client_id = self._client_id or app_settings.value("client_id")
        if self._auth is None or getattr(self._auth, "client_id", client_id) != client_id:
            self._auth = Authenticator(client_id, interactive=not self.headless)
        return self._auth

    @property
    def audit(self) -> AuditLog:
        """This PC's audit trail (audit.py): one line per call that changes or runs something."""
        if getattr(self, "_audit_log", None) is None:
            self._audit_log = AuditLog(self.root / "audit", app="agent" if self.headless else "desktop")
        return self._audit_log

    @property
    def telemetry(self) -> Telemetry:
        """Opt-in usage counts (telemetry.py); the agent never sends any."""
        if getattr(self, "_telemetry", None) is None:
            self._telemetry = Telemetry(self.root / "telemetry")
        return self._telemetry

    def telemetry_status(self) -> dict[str, Any]:
        """Settings, Usage data: on or off, where it goes and the exact text waiting to be sent."""
        return self.telemetry.status()

    def telemetry_new_install_id(self) -> dict[str, Any]:
        return {"install_id": self.telemetry.new_install_id()}

    def audit_log(self, limit: int = 200, action: str = "") -> dict[str, Any]:
        """The newest audit lines, newest first, and whether the chain is intact."""
        return {"entries": self.audit.recent(limit, action), "check": self.audit.verify(), "folder": str(self.audit.folder)}

    @property
    def fabric(self) -> FabricClient:
        return FabricClient(self.auth.fabric_token)

    @property
    def fabric_jobs(self) -> FabricClient:
        """For starting and scheduling jobs, which Fabric allows only with Item.Execute.All."""
        return FabricClient(lambda: self.auth._token(EXECUTE_SCOPES))

    @property
    def history_file(self) -> Path:
        return self.root / "history.jsonl"

    # Each public method is one UI action.

    def environment(self) -> dict[str, Any]:
        self._start_retention_sweep()
        self._start_usage_send()
        user = tenant = None
        try:
            user = self.auth.signed_in_user()
            tenant = self.auth.signed_in_tenant()
        except Exception as exc:  # no client ID yet, or offline: the UI offers sign-in
            _log.info("Not signed in: %s", describe(exc))
        return {"python": platform.python_version(), "user": user, "tenant": tenant}

    def _start_retention_sweep(self) -> None:
        """Once per engine start, in the background: delete local copies of lakehouse data unused for
        longer than the organization allows (retention.py)."""
        if getattr(self, "_swept", False):
            return
        self._swept = True

        def run() -> None:
            import logging

            from . import retention
            try:
                result = retention.sweep(self.root, app_settings.value("local_retention_days"))
                if result["removed"]:
                    logging.getLogger(__name__).info("Deleted %d local copies (%d bytes) unused for %d days",
                                                     len(result["removed"]), result["bytes"], result["days"])
            except Exception:  # noqa: BLE001 - a cache sweep must never stop the app
                logging.getLogger(__name__).exception("Local copy cleanup failed")

        threading.Thread(target=run, name="retention-sweep", daemon=True).start()

    def _start_usage_send(self) -> None:
        """Once per engine start, in the background: send finished days of usage data, if it's on."""
        if getattr(self, "_usage_sent", False) or self.headless:
            return
        self._usage_sent = True

        def run() -> None:
            import logging
            try:
                self.telemetry.send_due()
            except Exception as exc:  # noqa: BLE001 - offline or blocked: the days wait for the next start
                logging.getLogger(__name__).info("Usage data not sent: %s", type(exc).__name__)

        threading.Thread(target=run, name="usage-send", daemon=True).start()

    def _new_kernel(self, process: bool | None = None) -> Any:
        """The notebook kernel: in a process of its own (kernel_proc) when that setting is on, else
        inside the engine."""
        if process is None:
            process = bool(app_settings.value("kernel_process"))
        load = lambda name: self.notebooks.load(name)["cells"]  # noqa: E731
        if process:
            kernel = ProcessKernel(token=self._token_for, load_notebook=load, variable_libraries=self._notebook_variables)
            _apply_limits(kernel)
            return kernel
        return Kernel(token=self._token_for, load_notebook=load, variable_libraries=self._notebook_variables)

    def _kernel_busy(self) -> bool:
        snap = self.kernel.poll()
        return snap.get("status") == "running" or self.proof_busy()

    def kernel_info(self) -> dict[str, Any]:
        """Where notebook cells run: in the engine, or in a process of their own (and its id)."""
        process = isinstance(self.kernel, ProcessKernel)
        return {"process": process, "pid": self.kernel.pid if process else os.getpid()}

    def stop_kernel_process(self) -> dict[str, Any]:
        """Stop: end the kernel's own process (variables are cleared). With the kernel inside the
        engine this does nothing, and the app ends the engine instead."""
        if self.proof_busy():
            raise RuntimeError("A proof is using the kernel. Cancel the proof first.")
        return self.kernel.stop()

    def app_settings(self) -> dict[str, Any]:
        return {**app_settings.load(), "policy_file": str(app_settings.policy_path()),
                "policy_problems": app_settings.policy_problems()}

    def save_app_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        if "kernel_process" in changes and bool(changes["kernel_process"]) != isinstance(self.kernel, ProcessKernel):
            if self._kernel_busy():
                raise RuntimeError("A cell or proof is running. Change this when it has finished.")
            app_settings.save(changes)
            old, self.kernel = self.kernel, self._new_kernel(bool(changes["kernel_process"]))
            if isinstance(old, ProcessKernel):
                old.close()
        else:
            app_settings.save(changes)
        if isinstance(self.kernel, ProcessKernel):
            _apply_limits(self.kernel)
        if "telemetry" in changes and not changes["telemetry"]:
            self.telemetry.discard()
        return self.app_settings()

    def sign_out(self) -> dict[str, Any]:
        """Forgets this PC's sign-in. Scheduled runs on this PC stop until someone signs in again."""
        self.auth.sign_out()
        self._tables = None
        return {"user": None}

    def sign_in(self) -> dict[str, Any]:
        self.auth.fabric_token()
        self.auth.storage_token()
        return {"user": self.auth.signed_in_user(), "tenant": self.auth.signed_in_tenant()}

    def readiness_check(self) -> list[dict[str, Any]]:
        """Home's Ready to work list. Reads only, and never opens a sign-in window."""
        from . import readiness
        try:
            user = self.auth.signed_in_user()
        except Exception:  # noqa: BLE001 - no client ID yet: shown as not signed in
            user = None
        fabric = self._quiet_fabric() if user else None  # type: ignore[attr-defined]
        if user and fabric is None:
            user = None  # the saved sign-in has expired: the row offers Sign in

        def capacities() -> list[dict[str, Any]]:
            token = self.auth.silent_token(CAPACITY_SCOPES)
            if not token:
                raise PermissionError("not consented")
            return FabricClient(lambda: token).list_capacities()

        return readiness.checks(user, fabric.list_workspaces if fabric else list, capacities, self.app_settings())

    def readiness_write_test(self, workspace_id: str, lakehouse_id: str, workspace_name: str) -> dict[str, Any]:
        """Write, read back and delete one small file in a lakehouse's Files area (asked for first)."""
        from . import readiness
        from .onelake import FileStore
        return readiness.write_test(FileStore(self.auth.storage_token), workspace_id, lakehouse_id, workspace_name)

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.fabric.list_workspaces()

    def list_lakehouses(self, workspace_id: str) -> list[dict[str, Any]]:
        return self.fabric.list_lakehouses(workspace_id)

    def list_capacities(self) -> list[dict[str, Any]]:
        """Capacities a new workspace can go on, active ones first."""
        try:
            caps = FabricClient(lambda: self.auth._token(CAPACITY_SCOPES)).list_capacities()
        except Exception as exc:
            if "InsufficientScopes" in str(exc) or "AADSTS65001" in str(exc):
                raise PermissionError(
                    "Your sign-in can't list capacities (Capacity.Read.All), so pick the workspace's capacity in Fabric "
                    "instead. docs/permissions.md explains why and what IT can change.") from exc
            raise
        return sorted(caps, key=lambda c: (c.get("state") != "Active", c.get("displayName", "").lower()))

    def catalog(self, all_types: bool = False) -> dict[str, Any]:
        """Every lakehouse (or, with all_types, every item) in every workspace the user can open.

        Rows carry ``item_id``; lakehouse rows also carry ``lakehouse_id``, so a row can be
        attached to a notebook as it is."""
        token = self.auth.fabric_token()  # once: the MSAL cache is not shared across threads
        workspaces = FabricClient(lambda: token).list_workspaces()
        with self._tables_lock:  # a reloaded catalog reads table names afresh
            if self._tables and not self._tables["pending"]:
                self._tables = None

        def listing(ws: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
            client = FabricClient(lambda: token)
            try:
                if all_types:
                    return ws, client.list_items(ws["id"])
                return ws, [lh | {"type": "Lakehouse"} for lh in client.list_lakehouses(ws["id"])]
            except Exception:  # e.g. a paused capacity; list the rest
                return ws, None

        items, skipped = [], []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for ws, found in pool.map(listing, workspaces):
                if found is None:
                    skipped.append(ws["displayName"])
                    continue
                for it in found:
                    row = {"workspace_id": ws["id"], "workspace_name": ws["displayName"], "item_id": it["id"],
                           "name": it["displayName"], "type": it.get("type", ""), "description": it.get("description", "")}
                    if row["type"] == "Lakehouse":
                        row["lakehouse_id"] = it["id"]
                    items.append(row)
        return {"workspaces": [{"id": w["id"], "displayName": w["displayName"], "type": w.get("type", "Workspace")} for w in workspaces],
                "items": items, "skipped": skipped, "favorites": sorted(self._favorites())}

    def catalog_tables(self, lakehouses: list[dict[str, str]]) -> dict[str, Any]:
        """Table names per lakehouse id ("schema.table"), so the catalog can search inside lakehouses.

        Runs in the background like endorsements: {"pending": True} until every lakehouse is read."""
        with self._tables_lock:
            if self._tables is not None:
                return dict(self._tables)
            token = self.auth.storage_token()
            self._tables = {"pending": True, "items": {}}

        def read(ref: dict[str, str]) -> tuple[str, list[str]]:
            catalog = TableCatalog(lambda: token)
            try:
                return ref["lakehouse_id"], [f"{schema}.{t}" for schema in catalog.list_schemas(ref["workspace_id"], ref["lakehouse_id"])
                                             for t in catalog.list_tables(ref["workspace_id"], ref["lakehouse_id"], schema)]
            except Exception:
                return ref["lakehouse_id"], []

        def run() -> None:
            with ThreadPoolExecutor(max_workers=8) as pool:
                found = dict(pool.map(read, lakehouses))
            with self._tables_lock:
                self._tables = {"pending": False, "items": found}

        threading.Thread(target=run, daemon=True).start()
        return {"pending": True, "items": {}}

    def endorsements(self, workspace_ids: list[str]) -> dict[str, Any]:  # noqa: ARG002
        """Endorsement needs Fabric's admin scan and an admin-only permission, which Fabric Desktop
        doesn't ask for; Fabric's own catalog, embedded on the same page, shows it."""
        return {"pending": False, "available": False, "items": {},
                "reason": "Endorsement shows in Fabric's catalog below. KratosForge doesn't ask for admin permissions."}

    @property
    def favorites_file(self) -> Path:
        return self.root / "favorites.json"

    def _favorites(self) -> set[str]:
        return set(jsonfile.read_json(self.favorites_file, []))

    def set_favorite(self, lakehouse_id: str, favorite: bool) -> list[str]:
        """Stars are kept on this PC; Fabric has no public API for its own favorites."""
        favorites = self._favorites()
        (favorites.add if favorite else favorites.discard)(lakehouse_id)
        jsonfile.write_json(self.favorites_file, sorted(favorites), indent=None)
        return sorted(favorites)

    def list_items(self, workspace_id: str) -> list[dict[str, Any]]:
        return self.fabric.list_items(workspace_id)

    @property
    def workspace_marks_file(self) -> Path:
        return self.root / "workspaces.json"

    def workspace_marks(self) -> dict[str, list[str]]:
        """Workspace ids starred as favorites and pinned to the top, kept on this PC.

        Pins keep the order they were pinned in."""
        marks = jsonfile.read_json(self.workspace_marks_file, {})
        return {"favorites": list(marks.get("favorites", [])), "pins": list(marks.get("pins", []))}

    def set_workspace_mark(self, workspace_id: str, mark: str, on: bool) -> dict[str, list[str]]:
        if mark not in ("favorites", "pins"):
            raise ValueError(f"unknown workspace mark {mark!r}")
        marks = self.workspace_marks()
        ids = [i for i in marks[mark] if i != workspace_id]
        marks[mark] = ids + [workspace_id] if on else ids
        jsonfile.write_json(self.workspace_marks_file, marks, indent=None)
        return marks

    def create_lakehouse(self, workspace_id: str, name: str) -> dict[str, Any]:
        return self.fabric.create_lakehouse(workspace_id, name)

    def list_tables(self, workspace_id: str, lakehouse_id: str) -> dict[str, list[str]]:
        catalog = TableCatalog(self.auth.storage_token)
        return {s: catalog.list_tables(workspace_id, lakehouse_id, s)
                for s in catalog.list_schemas(workspace_id, lakehouse_id)}

    def preview_job(self, path: str, limit: int = 100) -> dict[str, Any]:
        data = run_local(Job.load(path))
        return {"rows": data.num_rows, "columns": data.schema.names,
                "sample": data.slice(0, limit).to_pylist()}

    def run_job(self, path: str) -> dict[str, Any]:
        record = run_job(Job.load(path), self.fabric, self.auth.storage_token,
                         work_dir=self.root, history_file=self.history_file, guard=self._job_guard)
        return record.__dict__

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.history_file.exists():
            return []
        lines = self.history_file.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:]][::-1]

    def list_files(self, workspace_id: str, lakehouse_id: str, directory: str = "Files") -> list[dict[str, Any]]:
        return FileStore(self.auth.storage_token).list(workspace_id, lakehouse_id, directory)

    # Notebook kernel. ``lakehouse`` is the notebook's attached lakehouse, sent with
    # every cell so a restarted sidecar picks it up again.

    def run_cell(self, code: str, lakehouse_ref: dict[str, str] | None = None) -> dict[str, Any]:
        self._replay_guard(None)
        self._attach(lakehouse_ref)
        return self.kernel.run(code)

    # The app runs cells with start_cell, then polls: output shows while the cell runs, and
    # interrupt_cell stops it without clearing variables.
    def start_cell(self, code: str, lakehouse_ref: dict[str, str] | None = None,
                   notebook: str | None = None, time_limit: bool = True,
                   watches: list[str] | None = None) -> dict[str, Any]:
        """``time_limit`` False runs this cell without the cell time limit (Run again without the limit).
        ``watches`` are expressions shown in the Variables pane, evaluated after the cell."""
        if self.proof_busy():
            raise RuntimeError("A proof is running this notebook's kernel. Wait for it, or cancel it.")
        self._replay_guard(notebook)  # prove_service: a replay's cells run only inside it
        # Busy is checked before attaching, under the lock external runs take: a refused start must
        # not re-point the running cell, and an external run can't re-point this one as it starts.
        if not self.attach_lock.acquire(timeout=ATTACH_WAIT_SECONDS):
            raise RuntimeError("An external tool is running against a lakehouse. Try again when it has finished.")
        try:
            if self.kernel.poll().get("status") == "running":
                raise RuntimeError("A cell is already running. Wait for it, or Cancel it.")
            self._attach(lakehouse_ref)
            self.kernel.notebook_name = notebook or ""
            if isinstance(self.kernel, ProcessKernel):
                return self.kernel.start(code, time_limit=time_limit, watches=check_watches(watches))
            return self.kernel.start(code, check_watches(watches))
        finally:
            self.attach_lock.release()

    def poll_cell(self, version: int = -1, wait: float = 0.0) -> dict[str, Any]:
        return self.kernel.poll(version, wait)

    def interrupt_cell(self) -> dict[str, Any]:
        return self.kernel.interrupt()

    def kernel_variables(self) -> list[dict[str, Any]]:
        return self.kernel.variables()

    def kernel_complete(self, code: str, cursor: int | None = None) -> dict[str, Any]:
        return self.kernel.complete(code, cursor)

    # ---- the result viewer behind a cell's table output (frames.py)

    def notebook_frame_page(self, frame: str, offset: int = 0, limit: int = 100, sort: int | None = None,
                            desc: bool = False, q: str = "") -> dict[str, Any]:
        """A page of a cell's whole result, searched and sorted over every row."""
        try:
            return self.kernel.frame_page(frame=frame, offset=offset, limit=limit, sort=sort, desc=desc, q=q)
        except (FrameGone, RuntimeError) as exc:
            if isinstance(exc, FrameGone) or str(exc) == GONE:
                return {"frame": frame, "gone": True, "message": GONE}
            raise

    def notebook_frame_chart(self, frame: str, kind: str = "bar", x: int | None = None, y: int | None = None,
                             agg: str = "sum", series: int | None = None, bins: int = 20) -> dict[str, Any]:
        """The Chart view's numbers, aggregated over every row of a cell's result."""
        try:
            return self.kernel.frame_chart(frame=frame, kind=kind, x=x, y=y, agg=agg, series=series, bins=bins)
        except (FrameGone, RuntimeError) as exc:
            if isinstance(exc, FrameGone) or str(exc) == GONE:
                return {"frame": frame, "gone": True, "message": GONE}
            raise

    def notebook_frame_profile(self, frame: str) -> dict[str, Any]:
        """Inspect: a profile of every row of a cell's result (display(df, summary=True))."""
        try:
            return self.kernel.frame_profile(frame=frame)
        except (FrameGone, RuntimeError) as exc:
            if isinstance(exc, FrameGone) or str(exc) == GONE:
                return {"frame": frame, "gone": True, "message": GONE}
            raise

    def notebook_frame_download(self, frame: str, format: str = "csv", name: str = "") -> dict[str, Any]:
        """Write a cell's whole result to a new file in Downloads (CSV, Excel or Parquet); never replaces a file."""
        if format not in DOWNLOAD_FORMATS:
            raise ValueError(f"Download as {', '.join(DOWNLOAD_FORMATS)}.")
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        base = f"{file_stem(name or 'notebook')} result {time.strftime('%Y-%m-%d %H%M%S')}"
        path = sql_exec._reserve(folder, base, "." + format)
        try:
            return self.kernel.frame_download(frame=frame, format=format, path=str(path))
        except BaseException as exc:
            sql_exec._discard(path.with_name(path.name + ".partial"), path)
            if isinstance(exc, FrameGone) or str(exc) == GONE:
                return {"frame": frame, "gone": True, "message": GONE}
            raise

    def _token_for(self, audience: str) -> str:
        """notebookutils.credentials.getToken on this PC: a token for you, from your sign-in."""
        from .notebookutils_local import _AUDIENCES
        if audience == "storage":
            return self.auth.storage_token()
        if audience == "pbi":
            return self.auth.fabric_token()
        scopes = _AUDIENCES.get(audience) or [audience.rstrip("/") + "/.default"]
        return self.auth._token(scopes)

    @property
    def attach_lock(self) -> threading.RLock:
        """Held while the engine's one attached lakehouse is set for a run (external runs hold it
        for the whole run); reentrant, as an external run_sql attaches through run_sql."""
        if getattr(self, "_attach_lock", None) is None:
            self._attach_lock = threading.RLock()
        return self._attach_lock

    def _attach(self, lakehouse_ref: dict[str, str] | None) -> None:
        # Bounded like start_cell: an external tool's query holds the lock while it runs, and the
        # engine's one pipe must not freeze behind it.
        if not self.attach_lock.acquire(timeout=ATTACH_WAIT_SECONDS):
            raise RuntimeError("An external tool is running against a lakehouse. Try again when it has finished.")
        try:
            # A cell running in the engine reads the attached lakehouse as it goes; a process
            # kernel has its own copy from when the cell started.
            kernel = getattr(self, "kernel", None)
            current = lakehouse._attached
            attached = (current.workspace_id, current.lakehouse_id) if current else None
            wanted = (lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"]) if lakehouse_ref else None
            if (wanted != attached and kernel is not None and not isinstance(kernel, ProcessKernel)
                    and kernel.poll().get("status") == "running"):
                raise RuntimeError(f"A cell is running against {current.name if current else 'no lakehouse'}. "
                                   "Wait for it, or Cancel it, before using another lakehouse.")
            lakehouse.attach(None if not lakehouse_ref else lakehouse.Attached(
                workspace_id=lakehouse_ref["workspace_id"], lakehouse_id=lakehouse_ref["lakehouse_id"],
                name=lakehouse_ref["name"], workspace_name=lakehouse_ref.get("workspace_name") or "",
                storage_token=lambda: self.auth.storage_token(),
                work_dir=self.root, history_file=self.history_file,
                schemas_enabled=lambda: self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])))
        finally:
            self.attach_lock.release()

    # SQL query page: Spark SQL over one lakehouse's tables, run on this PC.

    def sql_dialects(self) -> list[dict[str, Any]]:
        """The dialects the SQL page can edit, and which of them run on this PC."""
        return [sql_dialects.get(key) for key in sql_dialects.DIALECTS]

    def sql_format(self, query: str, dialect: str = "spark") -> dict[str, Any]:
        """Format SQL without running it; the original comes back with a warning when that isn't safe."""
        return sql_format.format_sql(query, dialect)

    def sql_analyze(self, query: str, target: str = "spark") -> dict[str, Any]:
        """Check SQL against a Fabric engine's documented rules, without running it or calling Fabric."""
        return sqlcompat.analyze(query, target)

    def sql_compat_profiles(self) -> dict[str, Any]:
        """What each engine supports, the rule set's version and the rules, for the panel and the docs."""
        return {"rule_version": sqlcompat.RULE_VERSION, "profiles": sqlcompat.profiles(),
                "rules": [{"id": r.id, "category": r.category, "targets": list(r.targets), "confidence": r.confidence,
                           "severity": r.severity} for r in sqlcompat.RULES.values()]}

    def sql_compat_export(self, query: str, target: str = "spark", name: str = "") -> dict[str, Any]:
        """Write the compatibility report, with the SQL it is about, as JSON in the Downloads folder.

        Never replaces a file: a name that is taken gets a number."""
        report = sqlcompat.analyze(query, target)
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        base = f"{file_stem(name)} compatibility {report['target']['id']} {time.strftime('%Y-%m-%d %H%M%S')}"
        text = sqlcompat.report_json(report, query)
        for n in range(1, 1000):
            path = folder / (f"{base}.json" if n == 1 else f"{base} ({n}).json")
            try:
                with open(path, "x", encoding="utf-8") as f:
                    f.write(text)
            except FileExistsError:
                continue
            return {"path": str(path), "outcome": report["outcome"]["status"], "counts": report["counts"]}
        raise ValueError("Couldn't find a free file name in Downloads for the report.")

    # Managed runs (SQL-003, sql_exec.py): each query in a process of its own, with Stop, a time
    # limit, a memory limit, pages read from a snapshot and streamed export. run_sql below stays for
    # older callers (external tools, integrations) and returns the first rows in one call.

    @property
    def sql_runs(self) -> sql_exec.SqlExecutions:
        if getattr(self, "_sql_runs", None) is None:
            self._sql_runs = sql_exec.SqlExecutions(
                self.root, token=lambda: self.auth.storage_token(), audit=self._audit_sql_run,
                schemas_enabled=lambda ref: self._schemas_enabled(ref["workspace_id"], ref["lakehouse_id"]))
        return self._sql_runs

    def _audit_sql_run(self, action: str, outcome: str, seconds: float, params: dict[str, Any]) -> None:
        auth = getattr(self, "_auth", None)  # only an account already signed in; never sign in here
        who = (auth.signed_in_user() or "") if auth is not None else ""
        tenant = (auth.signed_in_tenant() or "") if auth is not None else ""
        self.audit.record(action, outcome, seconds, params, who=who, tenant=tenant)

    def sql_start(self, query: str, lakehouse_ref: dict[str, Any], dialect: str = "spark",
                  allow: list[str] | None = None) -> dict[str, Any]:
        """Queue a query on this PC. ``allow`` adds "local_mutation" after the person confirmed it;
        lakehouse writes and Fabric changes are never allowed here (sql_policy.py)."""
        return self.sql_runs.start(query, lakehouse_ref, dialect, allow)

    def sql_status(self, execution: str) -> dict[str, Any]:
        return self.sql_runs.status(execution)

    def sql_executions(self) -> list[dict[str, Any]]:
        """This session's runs, newest first: states and timings, never the SQL."""
        return self.sql_runs.recent()

    def sql_page(self, execution: str, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        try:
            return self.sql_runs.page(execution, offset, limit)
        except sql_exec.RerunNeeded as exc:
            return {"id": execution, "rerun_needed": True, "message": str(exc)}

    def sql_cancel(self, execution: str) -> dict[str, Any]:
        """Stop one run: ends that run's own process and nothing else."""
        return self.sql_runs.cancel(execution)

    def sql_export(self, execution: str, format: str = "csv", name: str = "") -> dict[str, Any]:
        return self.sql_runs.export(execution, format, name)

    def sql_export_status(self, export: str) -> dict[str, Any]:
        return self.sql_runs.export_status(export)

    def sql_export_cancel(self, export: str) -> dict[str, Any]:
        return self.sql_runs.export_cancel(export)

    def run_sql(self, query: str, lakehouse_ref: dict[str, str], limit: int = 1000, dialect: str = "spark",
                sandbox: bool = False) -> dict[str, Any]:
        self._attach(lakehouse_ref)
        started = time.monotonic()
        result, row_count = sql.head(query, dialect, sandbox=sandbox, limit=limit)  # only the first rows are kept
        columns = [c.to_pylist() for c in result.columns]  # by position: names can repeat
        return {"columns": [{"name": f.name, "type": str(f.type)} for f in result.schema],
                "rows": [[_json_value(v) for v in row] for row in zip(*columns)],
                "row_count": row_count, "seconds": round(time.monotonic() - started, 2)}

    def save_sql(self, query: str, lakehouse_ref: dict[str, str], table: str, mode: str = "new",
                 dialect: str = "spark") -> dict[str, Any]:
        """Write the query result as a table. "new" (the default) writes nothing when the table
        exists and says so, so the page can ask before replacing it."""
        self._attach(lakehouse_ref)
        if mode == "new":
            if lakehouse.table_exists(table):
                return {"table": table, "exists": True}
            mode = "overwrite"
        written = lakehouse.write_table(sql.run(query, dialect), table, mode)
        return {"table": written.table, "rows": written.rows, "message": repr(written)}

    def export_sql_csv(self, query: str, lakehouse_ref: dict[str, str], dialect: str = "spark") -> dict[str, Any]:
        """Write the full result to the Downloads folder, a batch at a time (sql.Stream)."""
        self._attach(lakehouse_ref)
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        # The lakehouse name is cleaned as sql_compat_export cleans its name, so it can't leave the
        # folder (HD-038, finding EN-11), and an existing file is never replaced.
        base = f"{file_stem(lakehouse_ref.get('name', ''))} query {time.strftime('%Y-%m-%d %H%M%S')}"
        rows = 0
        with sql.Stream(query, dialect) as result:
            for n in range(1, 1000):
                path = folder / (f"{base}.csv" if n == 1 else f"{base} ({n}).csv")
                try:
                    sink = open(path, "xb")
                except FileExistsError:
                    continue
                break
            else:
                raise ValueError("Couldn't find a free file name in Downloads for the export.")
            with sink, pa_csv.CSVWriter(sink, result.schema or pa.schema([])) as out:
                for batch in result:
                    out.write_batch(batch)
                    rows += batch.num_rows
        return {"path": str(path), "rows": rows}

    def table_columns(self, lakehouse_ref: dict[str, str], table: str) -> list[dict[str, str]]:
        self._attach(lakehouse_ref)
        # Delta's own type names (string, long, timestamp), as Fabric shows them.
        return [{"name": f.name, "type": f.type.type if isinstance(f.type.type, str) else str(f.type)}
                for f in lakehouse.open_table(table).schema().fields]

    def _schemas_enabled(self, workspace_id: str, lakehouse_id: str) -> bool:
        """Fabric reports a defaultSchema only for lakehouses created with schemas. Asked once per lakehouse.
        A failed lookup raises: guessing wrong puts a table in Tables/dbo/x or Tables/x, the wrong folder."""
        if lakehouse_id not in self._schema_cache:
            try:
                props = self.fabric.get_lakehouse(workspace_id, lakehouse_id).get("properties") or {}
            except Exception as exc:
                raise RuntimeError(f"Couldn't ask Fabric whether this lakehouse uses schemas ({type(exc).__name__}: {exc}), "
                                   "so its table folders aren't known. Nothing was read or written; try again.") from exc
            self._schema_cache[lakehouse_id] = bool(props.get("defaultSchema"))
        return self._schema_cache[lakehouse_id]

    def restart_kernel(self) -> dict[str, Any]:
        self.kernel.restart()
        return {"restarted": True}

    # Notebook files, stored as .ipynb under the app data folder.

    def list_notebooks(self) -> list[dict[str, Any]]:
        return self.notebooks.list()

    def new_notebook(self, place: str | None = None) -> dict[str, Any]:
        """A new notebook in the app's own folder, or in the notebooks repository at ``place``."""
        return self._with_place(self.notebooks.new(place=place))

    def open_notebook(self, name: str) -> dict[str, Any]:
        nb = self.notebooks.load(name)
        return self._with_place(nb) | {"check": compute_check(nb["cells"], nb["lakehouse"]),
                                       "replay": self.notebooks.extra(name, "replay")}  # a replay notebook (prove_service)

    def _with_place(self, nb: dict[str, Any]) -> dict[str, Any]:
        """Adds where the notebook is saved and, in a notebooks repository, where its data is per
        environment (the repository's locations.toml). A lakehouse attached to the notebook wins."""
        place = self.notebooks.place_of(nb["name"])
        return nb | {"place": place, "locations": self._git_locations_of(place["root"]) if place else None}

    def notebook_places(self) -> dict[str, Any]:
        """Where a notebook can be saved: the app's own folder and each notebooks repository."""
        return {"places": [{"root": None, "label": "This PC"}] +
                [{"root": p["root"], "label": p["label"]} for p in self.notebooks.places()]}

    def move_notebook(self, name: str, place: str | None) -> dict[str, Any]:
        """Moves a notebook's file into a notebooks repository (or back to the app's own folder).
        The moved file shows as a change in that repository; nothing is committed."""
        return self.notebooks.move(name, place)

    def save_notebook(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                      compute: str | None = None, workspace: dict[str, str] | None = None) -> dict[str, Any]:
        """Save the notebook, and say whether it can run on this PC and in Fabric as it now stands."""
        return (self.notebooks.save(name, cells, lakehouse_ref, compute, workspace)
                | {"check": compute_check(cells, lakehouse_ref)})

    def notebook_diff(self, name: str, other: str | None = None) -> dict[str, Any]:
        """How the notebook's cells differ, by source only, from ``other`` (another notebook) or,
        without one, from its last Git commit (when the notebooks folder is in Git)."""
        path = self.notebooks.path(name)
        new = json.loads(path.read_text(encoding="utf-8"))
        if other:
            old = json.loads(self.notebooks.path(other).read_text(encoding="utf-8"))
        else:
            text = release.committed_file(path)
            if text is None:
                return {"name": name, "against": "head", "cells": [],
                        "problem": "This notebook isn't in a Git commit, so there's nothing to compare it with."}
            old = json.loads(text)
        return {"name": name, "against": other or "head", "cells": diff_mod.notebook_changes(old, new), "problem": None}

    def rename_notebook(self, name: str, new_name: str) -> dict[str, Any]:
        out = self.notebooks.rename(name, new_name)
        self.workload_store.rename_link(name, new_name)  # its Fabric item stays linked
        return out

    def delete_notebook(self, name: str) -> dict[str, Any]:
        self.notebooks.delete(name)
        return {"deleted": name}

    def publish_notebook(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                         compute: str | None = None, workspace: dict[str, str] | None = None,
                         location_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Save the notebook, then create or update it as a notebook item in its workspace: the one
        picked for it (saved with the notebook), else the attached lakehouse's workspace.

        A notebook that uses fabric_desktop.lakehouse is published as a Fabric Python notebook
        with a setup cell, so it also runs there (Run in Fabric, schedules)."""
        return self._publish(name, cells, lakehouse_ref, compute, workspace, location_ref=location_ref)


def _json_value(value: Any) -> Any:
    """A cell value the UI can parse: JSON has no NaN, dates or decimals."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


def _apply_limits(kernel: ProcessKernel) -> None:
    """The cell time and memory limits from Settings (or the organization's policy)."""
    minutes = float(app_settings.value("cell_time_limit_minutes") or 0)
    gb = float(app_settings.value("kernel_memory_limit_gb") or 0)
    kernel.time_limit = minutes * 60 or None
    kernel.memory_limit = int(gb * 1024 ** 3) or None
