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
from .auth import CAPACITY_SCOPES, EXECUTE_SCOPES, FABRIC_SCOPES, Authenticator
from .fabric_api import FabricClient
from . import app_settings, jsonfile, lakehouse, schema_cache, sql, sql_dialects, sql_exec, sql_format, sqlcompat, warehouse
from .learn import evidence
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
from .central.checks_service import ChecksMixin
from .central.entra_service import EntraGroupsMixin
from .central.landing_copy_service import LandingCopyMixin
from .central.requests_service import RequestsMixin
from .central.trust_service import TrustMixin
from .shortcut_service import ShortcutMixin
from .workload_service import WorkloadMixin
from .notebook_batch_service import NotebookBatchMixin
from .incident_service import IncidentMixin
from .incident_desk import IncidentDeskMixin
from .platform_notify_service import PlatformNotifyMixin
from .access_cache_service import AccessCacheMixin
from .git_service import GitMixin
from .pipeline_service import PipelineMixin, PipelineRunMixin
from .fabric_pipeline_runs_service import FabricPipelineRunsMixin
from .workspace_pipelines_service import WorkspacePipelinesMixin
from .fabric_connections_service import FabricConnectionsMixin
from .workspace_copy_jobs_service import WorkspaceCopyJobsMixin
from .workspace_dataflows_service import WorkspaceDataflowsMixin
from .integrations.app_service import IntegrationsMixin
from .localdata_service import LocalDataMixin
from .risk_service import RiskMixin
from .model_bpa_service import ModelBpaMixin
from .usage_service import UsageMixin
from .optimize_service import OptimizeMixin
from .tenant_history_service import TenantHistoryMixin
from .capacity_history_service import CapacityHistoryMixin
from .validate_service import ValidateMixin
from .model_ossie_service import ModelOssieMixin
from .model_directlake_service import ModelDirectLakeMixin
from .powerbi_embed_service import PowerBiEmbedMixin
from .model_dictionary_service import ModelDictionaryMixin
from .environment_service import EnvironmentMixin
from .local_writes_service import LocalWritesMixin
from .notebook_resources_service import NotebookResourcesMixin
from .notebook_files_service import NotebookFilesMixin
from .editor_service import EditorMixin
from .notebook_folders_service import NotebookFoldersMixin
from .notebook_formats_service import NotebookFormatsMixin
from .sql_templates_service import SqlTemplatesMixin
from .sql_queries_service import SqlQueriesMixin
from .warehouse_project_service import WarehouseProjectMixin
from .warehouse_monitoring_service import WarehouseMonitoringMixin
from .notebook_versions_service import NotebookVersionsMixin
from . import notebook_versions
from .progress_service import ProgressMixin
from .learn_service import LearnMixin
from .comments_service import CommentsMixin
from .engines_service import EnginesMixin
from .kernel_proc import ProcessKernel
from .kernel_sessions import KernelSessions
from .debugger import check_breakpoints
from .notebook import WIDGET_GONE, Kernel, NotebookShelf, check_notebook_name, check_watches
from .onelake import FileStore, TableCatalog
from .publish import run_job

_log = logging.getLogger(__name__)
MAX_ITEM_PINS = 50  # items pinned to Home

# How long a cell start waits for an external tool's run that holds the attached lakehouse.
ATTACH_WAIT_SECONDS = 10.0


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "FabricDesktop"



def _lakehouse_key(ref: Any) -> Any:
    """A lakehouse reference's identity: its id, else its name."""
    if isinstance(ref, dict):
        return ref.get("lakehouse_id") or ref.get("name") or None
    return ref or None


# Why OneLake > Catalog's Endorsed items filter is off (docs/troubleshooting.md says the same).
ENDORSED_FILTER_REASON = (
    "Not available here: KratosForge reads endorsement only through Fabric's admin tenant scan, and it "
    "runs that scan only for data products. See it on a product's page (Data mesh > Products; a platform "
    "admin who is also a Fabric administrator chooses Scan now there), or in Fabric's own catalog (Open Fabric's catalog on OneLake > Catalog).")


class Service(NotebookBatchMixin, TrustMixin, AccessCacheMixin, LocalWritesMixin, NotebookResourcesMixin, PipelineMixin, PipelineRunMixin, FabricPipelineRunsMixin, WorkspacePipelinesMixin, FabricConnectionsMixin, WorkspaceDataflowsMixin, WorkspaceCopyJobsMixin, GitMixin, IncidentDeskMixin, IncidentMixin, PlatformNotifyMixin, WorkloadMixin, LandingCopyMixin, RequestsMixin, EntraGroupsMixin, ShortcutMixin, ChecksMixin, GovernanceMixin, PlatformMixin, MetaMixin, CloudMixin, DataOpsMixin, EstateMixin, ScheduleMixin, XRayMixin, PreviewMixin, SideBySideMixin, ProveMixin, IntegrationsMixin, LocalDataMixin, UsageMixin, RiskMixin, ModelBpaMixin, OptimizeMixin, TenantHistoryMixin, CapacityHistoryMixin, ValidateMixin, NotebookFilesMixin, NotebookFoldersMixin, NotebookVersionsMixin, NotebookFormatsMixin, SqlTemplatesMixin, SqlQueriesMixin, WarehouseProjectMixin, WarehouseMonitoringMixin, ProgressMixin, EnvironmentMixin, CommentsMixin, ModelOssieMixin, ModelDirectLakeMixin, ModelDictionaryMixin, EditorMixin, EnginesMixin, PowerBiEmbedMixin, LearnMixin):
    def __init__(self, client_id: str | None = None, root: Path | None = None, headless: bool = False):
        self.headless = headless  # a scheduled run: never open a sign-in window
        self._client_id = client_id  # given by the agent; otherwise from app settings
        self.root = root or data_dir()
        self._auth: Authenticator | None = None
        self._auth_lock = threading.Lock()
        self.sessions = KernelSessions(self._new_kernel, lambda: bool(app_settings.value("kernel_process")),
                                       max_sessions=lambda: int(app_settings.value("kernel_max_sessions") or 0),
                                       idle_minutes=lambda: float(app_settings.value("kernel_idle_minutes") or 0),
                                       prepare=lambda notebook, kernel: self._prepare_kernel(notebook, kernel))
        self.notebooks = NotebookShelf(self.root / "notebooks", self._notebook_places)
        self._schema_cache: dict[str, bool] = {}
        self._tables: dict[str, Any] | None = None
        self._tables_lock = threading.Lock()

    @property
    def auth(self) -> Authenticator:
        # The app ID can change on the Settings page; a new one needs a new sign-in.
        client_id = self._client_id or app_settings.value("client_id")
        # Built once: building it asks Microsoft for its sign-in endpoints (and on Windows starts the
        # sign-in broker), so calls arriving together, as a tool's do, must not each build one.
        with self._auth_lock:
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
        return FabricClient(self.auth.fabric_token, refresh=self._refreshed(FABRIC_SCOPES))

    @property
    def fabric_jobs(self) -> FabricClient:
        """For starting and scheduling jobs, which Fabric allows only with Item.Execute.All."""
        return FabricClient(lambda: self.auth._token(EXECUTE_SCOPES), refresh=self._refreshed(EXECUTE_SCOPES))

    def _refreshed(self, scopes: list[str]) -> Any:
        """How to get a new token after a 401, when the signed-in identity can; None to ask again."""
        refreshed = getattr(self.auth, "refreshed_token", None)
        return (lambda: refreshed(scopes)) if callable(refreshed) else None

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

    def _checks(self, name: str | None) -> dict[str, Any]:
        """What compute_check (and Prove's and Validate's checks) need to follow the code that
        notebook ``name`` runs: the other notebooks by name, and its resources folder for %run -b."""
        return {"load": self._notebook_of, "resources": self._resources_dir(name) or None}  # type: ignore[attr-defined]

    def _notebook_of(self, name: str) -> dict[str, Any]:
        """Another notebook by name, for compute_check: its cells and its own lakehouse."""
        nb = self.notebooks.load(name)
        return {"cells": nb["cells"], "lakehouse": nb.get("lakehouse")}

    def _cells_of(self, name: str) -> list[dict[str, Any]]:
        """Another notebook's cells, by the name ``%run`` gives it: what the kernel runs, and what
        compute_check follows."""
        return self.notebooks.load(name)["cells"]

    def _new_kernel(self, process: bool | None = None) -> Any:
        """The notebook kernel: in a process of its own (kernel_proc) when that setting is on, else
        inside the engine."""
        if process is None:
            process = bool(app_settings.value("kernel_process"))
        load = self._cells_of
        if process:
            kernel = ProcessKernel(token=self._token_for, load_notebook=load, variable_libraries=self._notebook_variables)
            _apply_limits(kernel)
            return kernel
        return Kernel(token=self._token_for, load_notebook=load, variable_libraries=self._notebook_variables)

    @property
    def kernel(self) -> Any:
        """The open notebook's kernel (kernel_sessions): proofs, side by side and replays use it."""
        return self.sessions.kernel()

    @kernel.setter
    def kernel(self, kernel: Any) -> None:
        self.sessions.put(None, kernel)

    def _kernel_busy(self) -> bool:
        return self.sessions.any_running() or self.proof_busy()

    def kernel_info(self, notebook: str | None = None) -> dict[str, Any]:
        """Where notebook cells run: in the engine, or in a process of their own (and its id)."""
        kernel = self.sessions.kernel(notebook)
        process = isinstance(kernel, ProcessKernel)
        return {"process": process, "pid": kernel.pid if process else os.getpid()}

    def kernel_sessions(self) -> dict[str, Any]:
        """The Sessions list: each notebook's session, and sessions stopped since the last look."""
        return {"process": self.sessions.in_process, "sessions": self.sessions.describe(),
                "shared": self.sessions.joined(),
                "notes": self.sessions.take_notes(), "max_sessions": int(app_settings.value("kernel_max_sessions") or 0),
                "idle_minutes": float(app_settings.value("kernel_idle_minutes") or 0)}

    def stop_kernel_process(self, notebook: str | None = None) -> dict[str, Any]:
        """Stop: end the notebook's own process (variables are cleared). With the kernel inside the
        engine this does nothing, and the app ends the engine instead."""
        if self.proof_busy() and (notebook is None or notebook == self.sessions.active):
            raise RuntimeError("A proof is using the kernel. Cancel the proof first.")
        return self.sessions.stop(self.sessions.active if notebook is None else notebook)

    def kernel_session_share(self, notebook: str, host: str) -> dict[str, Any]:
        """``notebook`` joins ``host``'s session, as Fabric's high-concurrency sessions do: same
        ``spark``, temp views and settings, one cell at a time, variables of its own. Like Fabric,
        both need the same default lakehouse and environment. ``notebook``'s own session ends."""
        if self.proof_busy():
            raise RuntimeError("A proof is using a notebook's kernel. Share sessions when it has finished.")
        host = self.sessions.host_of(host) or host
        lakehouse_of = self._saved_lakehouse
        if lakehouse_of(notebook) != lakehouse_of(host):
            raise RuntimeError(f"{notebook} and {host} have different default lakehouses. Notebooks in a shared "
                               "session use the same one, as in Fabric; change one of them first.")
        if self.environments.of(notebook) != self.environments.of(host):
            raise RuntimeError(f"{notebook} and {host} use different environments. Notebooks in a shared session "
                               "use the same one, as in Fabric; change one of them first.")
        return self.sessions.share(notebook, host)

    def _saved_lakehouse(self, name: str) -> Any:
        """A notebook's saved default lakehouse, by id (or name), for a shared session's match."""
        try:
            ref = self.notebooks.load(name).get("lakehouse")
        except (OSError, KeyError, ValueError):
            return None  # a notebook that isn't saved yet has none
        return _lakehouse_key(ref)

    def kernel_session_leave(self, notebook: str) -> dict[str, Any]:
        """``notebook`` leaves its shared session; its next cell starts a session of its own."""
        return self.sessions.leave(notebook)

    def app_settings(self) -> dict[str, Any]:
        return {**app_settings.load(), "policy_file": str(app_settings.policy_path()),
                "policy_problems": app_settings.policy_problems()}

    def save_app_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        if "kernel_process" in changes and bool(changes["kernel_process"]) != isinstance(self.kernel, ProcessKernel):
            if self._kernel_busy():
                raise RuntimeError("A cell or proof is running. Change this when it has finished.")
            app_settings.save(changes)
            for old in self.sessions.switch(bool(changes["kernel_process"])):
                if isinstance(old, ProcessKernel):
                    old.close()
        else:
            app_settings.save(changes)
        for kernel in self.sessions.kernels():
            if isinstance(kernel, ProcessKernel):
                _apply_limits(kernel)
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
        """KratosForge reads endorsement only through Power BI's admin tenant scan, and that scan (Scan now on a
        data product's page, a Fabric administrator only) reads just the platform's product items into
        catalog.endorsement, keyed by item id. Nothing reads it for every item listed here, so the filter
        stays off and says where endorsement does show."""
        return {"pending": False, "available": False, "items": {}, "reason": ENDORSED_FILTER_REASON}

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
        marks = jsonfile.read_json(self.workspace_marks_file, {})  # item_pins and anything else stay as they are
        ids = [i for i in marks.get(mark, []) if i != workspace_id]
        marks[mark] = ids + [workspace_id] if on else ids
        jsonfile.write_json(self.workspace_marks_file, marks, indent=None)
        return self.workspace_marks()

    def item_pins(self) -> list[dict[str, str]]:
        """Items pinned to Home, in the order they were pinned: notebooks on this PC (``kind``
        "notebook", ``id`` its name) and Fabric items (``kind`` the item type, ``id`` its item id).
        Kept on this PC beside the workspace pins: Fabric has no API for pins."""
        pins = jsonfile.read_json(self.workspace_marks_file, {}).get("item_pins", [])
        return [p for p in pins if isinstance(p, dict) and p.get("kind") and p.get("id")]

    def set_item_pin(self, kind: str, id: str, on: bool, name: str = "", workspace_id: str = "",  # noqa: A002
                     workspace_name: str = "") -> list[dict[str, str]]:
        kind, id = str(kind or "").strip(), str(id or "").strip()  # noqa: A001
        if not kind or not id or len(kind) > 64 or len(id) > 256:
            raise ValueError("Say what to pin: a kind and an id")
        if kind == "notebook":
            check_notebook_name(id)
        marks = jsonfile.read_json(self.workspace_marks_file, {})
        pins = [p for p in marks.get("item_pins", []) if isinstance(p, dict) and (p.get("kind"), p.get("id")) != (kind, id)]
        if on:
            if len(pins) >= MAX_ITEM_PINS:
                raise ValueError(f"Up to {MAX_ITEM_PINS} items can be pinned. Unpin one first.")
            pins.append({"kind": kind, "id": id, "name": str(name or id)[:256],
                         "workspace_id": str(workspace_id or "")[:64], "workspace_name": str(workspace_name or "")[:256]})
        marks["item_pins"] = pins
        jsonfile.write_text(self.workspace_marks_file, json.dumps(marks))
        return self.item_pins()

    def _rename_item_pin(self, kind: str, old: str, new: str) -> None:
        marks = jsonfile.read_json(self.workspace_marks_file, {})
        pins = marks.get("item_pins") or []
        hit = [p for p in pins if isinstance(p, dict) and p.get("kind") == kind and p.get("id") == old]
        if not hit:
            return
        for p in hit:
            p["id"] = new
            p["name"] = new
        jsonfile.write_text(self.workspace_marks_file, json.dumps(marks))

    def create_lakehouse(self, workspace_id: str, name: str) -> dict[str, Any]:
        return self.fabric.create_lakehouse(workspace_id, name)

    def list_tables(self, workspace_id: str, lakehouse_id: str) -> dict[str, list[str]]:
        catalog = TableCatalog(self.auth.storage_token)
        tables = {s: catalog.list_tables(workspace_id, lakehouse_id, s)
                  for s in catalog.list_schemas(workspace_id, lakehouse_id)}
        schema_cache.record_tables(self.root, lakehouse_id, tables)
        return tables

    def cached_schemas(self, lakehouse_id: str) -> dict[str, Any]:
        """The table and column names last read from this lakehouse, for completion while offline."""
        return schema_cache.load(self.root, lakehouse_id)

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

    def run_cell(self, code: str, lakehouse_ref: dict[str, str] | None = None,
                 notebook: str | None = None) -> dict[str, Any]:
        self._replay_guard(None)
        self._attach(lakehouse_ref)
        kernel = self.sessions.kernel(notebook)
        kernel.onelake_writes = self._onelake_writes(notebook)
        return kernel.run(code)

    # The app runs cells with start_cell, then polls: output shows while the cell runs, and
    # interrupt_cell stops it without clearing variables.
    def start_cell(self, code: str, lakehouse_ref: dict[str, str] | None = None,
                   notebook: str | None = None, time_limit: bool = True,
                   watches: list[str] | None = None, breakpoints: list[int] | None = None,
                   step: bool = False) -> dict[str, Any]:
        """``time_limit`` False runs this cell without the cell time limit (Run again without the limit).
        ``watches`` are expressions shown in the Variables pane, evaluated after the cell.
        ``breakpoints`` (line numbers) or ``step`` (pause at the first line) run it under the
        debugger; ``debug_cell`` resumes it."""
        return self._start_cell(code, lakehouse_ref, notebook, time_limit, watches, breakpoints, step)

    def _start_cell(self, code: str, lakehouse_ref: dict[str, str] | None = None, notebook: str | None = None,
                    time_limit: bool = True, watches: list[str] | None = None, breakpoints: list[int] | None = None,
                    step: bool = False, write_check: Any = None) -> dict[str, Any]:
        """start_cell, with ``write_check`` asked before each write the cell makes (a cell a paired
        VS Code window started, editor_service.py). The app's own cells have none."""
        breakpoints = check_breakpoints(breakpoints)
        if self.proof_busy():
            raise RuntimeError("A proof is running this notebook's kernel. Wait for it, or cancel it.")
        self._replay_guard(notebook)  # prove_service: a replay's cells run only inside it
        host = self.sessions.host_of(notebook)
        if host and lakehouse_ref and _lakehouse_key(lakehouse_ref) != self._saved_lakehouse(host):
            raise RuntimeError(f"{notebook} shares {host}'s session but now has another default lakehouse. Change it "
                               f"back to {host}'s, or leave the shared session (Sessions).")
        # Busy is checked before attaching, under the lock external runs take: a refused start must
        # not re-point the running cell, and an external run can't re-point this one as it starts.
        if not self.attach_lock.acquire(timeout=ATTACH_WAIT_SECONDS):
            raise RuntimeError("An external tool is running against a lakehouse. Try again when it has finished.")
        try:
            self.sessions.reap_idle()
            session = self.sessions.session(notebook)
            kernel = session.kernel
            if kernel.poll().get("status") == "running":
                if self.sessions.in_process or not notebook or notebook == kernel.notebook_name:
                    raise RuntimeError("A cell is already running. Wait for it, or Cancel it.")
                raise RuntimeError(f"A cell is already running in {kernel.notebook_name}. Notebooks share one kernel "
                                   "inside the engine; turn on Run notebooks in their own process in Settings "
                                   "to run them at the same time.")
            self._attach(lakehouse_ref, notebook)
            session.touch()
            kernel.notebook_name = notebook or ""
            # Values set in the Widgets bar, so a cell that creates a widget and reads it gets them.
            kernel.widget_values = dict((self._saved_inputs(notebook) or {}).get("widgets") or {})
            kernel.resources_dir = self._resources_dir(notebook)
            kernel.onelake_writes = self._onelake_writes(notebook)
            kernel.write_check = write_check
            if isinstance(kernel, ProcessKernel):
                return kernel.start(code, time_limit=time_limit, watches=check_watches(watches),
                                    breakpoints=breakpoints, step=bool(step))
            return kernel.start(code, check_watches(watches), breakpoints, bool(step))
        finally:
            self.attach_lock.release()

    # ``notebook`` names whose kernel to ask; without it, the open notebook's.
    # Looking at a notebook's session never makes one: a new session can stop another notebook's
    # idle one to make room (Settings cap), clearing its variables just because a notebook opened.

    def poll_cell(self, version: int = -1, wait: float = 0.0, notebook: str | None = None) -> dict[str, Any]:
        session = self.sessions.session(notebook, create=False)
        if session is None:
            return {"status": "idle", "execution_count": 0, "version": 0, "seconds": 0, "outputs": []}
        return session.kernel.poll(version, wait)

    def interrupt_cell(self, notebook: str | None = None) -> dict[str, Any]:
        session = self.sessions.session(notebook, create=False)
        return session.kernel.interrupt() if session is not None else {"interrupted": False}

    def debug_cell(self, action: str, expression: str | None = None, notebook: str | None = None) -> dict[str, Any]:
        """A cell paused under the debugger: continue, step, next, out, or eval ``expression`` where it is."""
        session = self.sessions.session(notebook, create=False)
        if session is None:
            raise RuntimeError("No cell is running under the debugger.")
        return session.kernel.debug(action, expression)

    def kernel_variables(self, notebook: str | None = None) -> list[dict[str, Any]]:
        session = self.sessions.session(notebook, create=False)
        return session.kernel.variables() if session is not None else []

    def kernel_complete(self, code: str, cursor: int | None = None, notebook: str | None = None) -> dict[str, Any]:
        return self.sessions.kernel(notebook).complete(code, cursor)

    def kernel_inspect(self, code: str, cursor: int | None = None, call: bool = False) -> dict[str, Any] | None:
        """Hover docs or signature help for the notebook editor (intellisense.py); runs no code."""
        return self.kernel.inspect(code, cursor, bool(call))

    def notebook_syntax(self, source: str) -> list[dict[str, Any]]:
        """The first Python syntax error in a cell, for the editor's red underline."""
        from .intellisense import syntax_problems
        return syntax_problems(source)

    def notebook_format(self, sources: list[str]) -> list[dict[str, Any]]:
        """Format cells (Python with ruff, %%sql with the SQL formatter) without running them."""
        from .code_format import format_cells
        if not isinstance(sources, list):
            raise ValueError("sources must be a list of cell sources")
        return format_cells(sources)

    def notebook_plotly_js(self) -> dict[str, Any]:
        """plotly.js for the app to draw plotly outputs with, read once per app start (chart_output.py)."""
        from .chart_output import plotly_js
        return plotly_js()

    def notebook_practices(self, sources: list[str], scheduled: bool = False,
                           settings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Best-practice findings for a notebook's code cells, read on this PC without running them."""
        from .notebook_practices import check
        return check(sources, bool(scheduled), settings)

    def notebook_practice_rules(self) -> list[dict[str, Any]]:
        """The notebook best-practice rules, for a template to turn on or off."""
        from .notebook_practices import rules
        return rules()

    def kernel_variable_detail(self, name: str) -> dict[str, Any]:
        return self.kernel.variable_detail(str(name))

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

    def notebook_frame_query_profile(self, frame: str) -> dict[str, Any]:
        """Query profile: the query behind a cell's spark or %%sql result, run again under DuckDB's
        profiler, step by step (rows, time, share of the total). Reads only. ``notes`` are what the
        profile measured that a best-practice rule explains (learn/evidence.py, GD-04)."""
        try:
            profile = self.kernel.frame_query_profile(frame=frame)
            if not profile.get("gone"):
                profile["notes"] = evidence.notes(profile)
            return profile
        except (FrameGone, RuntimeError) as exc:
            if isinstance(exc, FrameGone) or str(exc) == GONE:
                return {"frame": frame, "gone": True, "message": GONE}
            raise

    def notebook_frame_wrangle(self, frame: str, steps: list[dict[str, Any]] | None = None, name: str = "df") -> dict[str, Any]:
        """Data Wrangler: Polars code for the cleaning steps and a preview of it on a cell's result.
        Reads only; the notebook changes when the person adds the code as a cell and runs it."""
        try:
            return self.kernel.frame_wrangle(frame=frame, steps=list(steps or []), name=name)
        except (FrameGone, RuntimeError) as exc:
            if isinstance(exc, FrameGone) or str(exc) == GONE:
                return {"frame": frame, "gone": True, "message": GONE}
            raise

    def notebook_ipywidget_event(self, model: str, event: dict[str, Any] | None = None, root: str | None = None,
                                 notebook: str | None = None) -> dict[str, Any]:
        """An ipywidgets control changed in a cell's output: set it and run its handlers in that notebook's
        kernel. Handlers are user code, so they wait for a proof and a replay as a cell does."""
        if self.proof_busy():
            raise RuntimeError("A proof is running this notebook's kernel. Wait for it, or cancel it.")
        self._replay_guard(notebook)
        session = self.sessions.session(notebook, create=False)  # a stopped session's widgets are gone
        if session is None:
            return {"gone": True, "message": WIDGET_GONE}
        return session.kernel.ipywidget_event(model=str(model), event=dict(event or {}), root=root)

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

    def _attach(self, lakehouse_ref: dict[str, str] | None, notebook: str | None = None) -> None:
        # Bounded like start_cell: an external tool's query holds the lock while it runs, and the
        # engine's one pipe must not freeze behind it.
        if not self.attach_lock.acquire(timeout=ATTACH_WAIT_SECONDS):
            raise RuntimeError("An external tool is running against a lakehouse. Try again when it has finished.")
        try:
            # A cell running in the engine reads the attached lakehouse as it goes; a process
            # kernel has its own copy from when the cell started.
            sessions = getattr(self, "sessions", None)
            current = lakehouse._attached
            attached = (current.workspace_id, current.lakehouse_id) if current else None
            wanted = (lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"]) if lakehouse_ref else None
            if (wanted != attached and sessions is not None and not sessions.in_process
                    and sessions.any_running()):
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

    def sql_practices(self, query: str, target: str = "warehouse",
                      settings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Best-practice findings for T-SQL, read on this PC without running it (sqlcompat/practices.py)."""
        from .sqlcompat import practices
        return practices.check(query, target, settings)

    def sql_practice_rules(self) -> list[dict[str, Any]]:
        """The SQL best-practice rules, for a template to turn on or off."""
        from .sqlcompat import practices
        return practices.rules()

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
                schemas_enabled=lambda ref: self._schemas_enabled(ref["workspace_id"], ref["lakehouse_id"]),
                local_engine=lambda project: self.local_data.engine(project),
                warehouse_token=lambda: self.auth.warehouse_token(),
                resolve_warehouse=lambda ref: warehouse.resolve(self.fabric, ref))
        return self._sql_runs

    def _audit_sql_run(self, action: str, outcome: str, seconds: float, params: dict[str, Any]) -> None:
        auth = getattr(self, "_auth", None)  # only an account already signed in; never sign in here
        who = (auth.signed_in_user() or "") if auth is not None else ""
        tenant = (auth.signed_in_tenant() or "") if auth is not None else ""
        self.audit.record(action, outcome, seconds, params, who=who, tenant=tenant)

    def sql_start(self, query: str, lakehouse_ref: dict[str, Any] | None = None, dialect: str = "spark",
                  allow: list[str] | None = None, target: str = "lakehouse", project: str = "",
                  confirm: bool = False, dry_run: bool = False,
                  warehouse_ref: dict[str, Any] | None = None, confirm_name: str = "",
                  estimated_plan: bool = False, precondition: dict[str, Any] | None = None) -> dict[str, Any]:
        """Queue a query on this PC. ``allow`` adds "local_mutation" after the person confirmed it;
        lakehouse writes and Fabric changes are never allowed here (sql_policy.py).

        ``target="local"`` runs it in local data project ``project``: it may change the project's
        own tables, and ``confirm`` lets DROP, TRUNCATE and DELETE or UPDATE without WHERE run.
        ``target="warehouse"`` (Preview) runs one T-SQL query that only reads on ``warehouse_ref``
        (``workspace_id``, ``item_id``, ``kind`` "warehouse" or "endpoint"), in Fabric: it uses capacity.
        With ``allow=["warehouse_write"]``, ``confirm`` and ``confirm_name`` (the warehouse's name, as
        the person confirmed it) it may change the warehouse (WH-10, sql_exec.py); with ``precondition``
        (a deploy change's, from wh_project_deploy_plan) only if that object is still as the preview saw it.

        ``dry_run`` only says what each statement would do; nothing runs. ``estimated_plan`` (warehouse,
        a query that reads) returns Fabric's estimated plan as showplan XML instead of running it."""
        return self.sql_runs.start(query, lakehouse_ref, dialect, allow, target=target, project=project,
                                   confirm=confirm, dry_run=dry_run, warehouse_ref=warehouse_ref,
                                   confirm_name=confirm_name, estimated_plan=estimated_plan, precondition=precondition)

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
        columns = [{"name": f.name, "type": f.type.type if isinstance(f.type.type, str) else str(f.type)}
                   for f in lakehouse.open_table(table).schema().fields]
        schema_cache.record_columns(self.root, lakehouse_ref["lakehouse_id"], table, columns)
        return columns

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

    def restart_kernel(self, notebook: str | None = None) -> dict[str, Any]:
        self.sessions.kernel(notebook).restart()
        return {"restarted": True}

    # Notebook files, stored as .ipynb under the app data folder.

    def list_notebooks(self) -> list[dict[str, Any]]:
        return self.notebooks.list()

    def new_notebook(self, place: str | None = None, folder: str = "") -> dict[str, Any]:
        """A new notebook in the app's own folder, or in the notebooks repository at ``place``;
        inside ``folder`` ("Sales") when given (notebook_folders)."""
        nb = self.notebooks.new(place=place, folder_name=folder or "")
        self.sessions.active = nb["name"]
        return self._with_place(nb)

    def open_notebook(self, name: str) -> dict[str, Any]:
        nb = self.notebooks.load(name)
        self.sessions.active = name  # its session, if it has one, is the one Run, Stop and proofs use
        self.sessions.reap_idle()
        return self._with_place(nb) | {"check": compute_check(nb["cells"], nb["lakehouse"], **self._checks(name)),
                                       "replay": self.notebooks.extra(name, "replay"),  # a replay notebook (prove_service)
                                       "onelake_writes": self.notebooks.extra(name, "onelake_writes")}

    def _onelake_writes(self, name: str | None) -> str:
        """How the notebook's cells write to OneLake: its own choice, else the Settings default."""
        mode = None
        if name:
            try:
                mode = self.notebooks.extra(name, "onelake_writes")
            except (OSError, ValueError):  # not saved yet
                mode = None
        return mode if mode in lakehouse.UPLOADS else app_settings.value("onelake_writes")

    def notebook_onelake_writes_set(self, name: str, mode: str | None) -> dict[str, Any]:
        """Keeps the notebook's own OneLake writes choice: "local_first" (write here, upload in the
        background, the cell ends when it's in), "direct" (each write returns once it's in OneLake),
        or None to follow Settings. Takes effect from the next cell run."""
        if mode not in (None, "", *lakehouse.UPLOADS):
            raise ValueError(f"OneLake writes must be one of {', '.join(lakehouse.UPLOADS)}")
        self.notebooks.set_extra(name, "onelake_writes", mode or None)
        return {"onelake_writes": mode or None, "effective": self._onelake_writes(name)}

    def notebook_inputs(self, name: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
        """The Parameters and Widgets bar for these cells, with the values saved for the notebook."""
        from .notebook_inputs import inputs
        return inputs(cells, self._saved_inputs(name))

    def _saved_inputs(self, name: str | None) -> dict[str, Any] | None:
        """The bar's values kept with the notebook; None for a notebook that isn't saved yet."""
        if not name:
            return None
        try:
            return self.notebooks.extra(name, "inputs")
        except (OSError, ValueError):  # not saved yet, or a name no file can have
            return None

    def _bar_parameters(self, name: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
        """The Parameters bar's values, kept with the notebook, as a Fabric run's parameters
        (notebook_inputs.fabric_parameters); ValueError for a value the bar shows in red."""
        from .notebook_inputs import fabric_parameters
        return fabric_parameters(_with_ids(cells), self._saved_inputs(name))

    def _bar_suffixes(self, name: str, cells: list[dict[str, Any]]) -> list[str]:
        """For a run on this PC the engine starts itself (side by side): the code to add after each
        cell, by position, as runCell adds it (nbinputs.js)."""
        from .notebook_inputs import inputs
        ided = _with_ids(cells)
        suffix = inputs(ided, self._saved_inputs(name))["suffix"]
        return [suffix.get(c["cell_id"], "") for c in ided]

    def notebook_inputs_set(self, name: str, cells: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, Any]:
        """Keeps the bar's values with the notebook (its code doesn't change); empty values forget them."""
        from .notebook_inputs import check_values, inputs
        values = check_values(values)
        kept = {group: given for group, given in values.items() if given}
        self.notebooks.set_extra(name, "inputs", kept or None)
        return inputs(cells, kept)

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
                      compute: str | None = None, workspace: dict[str, str] | None = None,
                      disk_stamp: str | None = None) -> dict[str, Any]:
        """Save the notebook, and say whether it can run on this PC and in Fabric as it now stands.

        ``disk_stamp`` (from open_notebook or the last save): when another program, such as VS Code,
        changed the file since, nothing is saved and ``changed_on_disk`` comes back. Both the file's
        version and these cells are kept under Versions first, so whichever the person picks, or
        if they pick neither, nothing is lost. ``outside_copy`` is where a file that isn't a
        notebook any more (Git conflict markers) was copied."""
        out = self.notebooks.save(name, cells, lakehouse_ref, compute, workspace, disk_stamp)
        if out.get("changed_on_disk"):
            copy = self._keep_both_sides(name, cells, lakehouse_ref, compute, workspace, bool(out.get("unreadable")))
            return out | ({"outside_copy": copy} if copy else {})
        self._keep_version(name)
        return out | {"check": compute_check(cells, lakehouse_ref, **self._checks(name))}

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
        return out | self._notebook_renamed(name, new_name)

    def _notebook_renamed(self, name: str, new_name: str) -> dict[str, Any]:
        """After a notebook's file was renamed (or moved into a folder, notebook_folders): what is kept
        by its name follows it. Every step runs even when another fails, since the file has already
        moved: returns {"schedules_moved": [ids], "problems": [{"notebook", "step", "error"}]}."""
        out: dict[str, Any] = {"schedules_moved": [], "problems": []}
        steps = [
            ("session", lambda: self.sessions.rename(name, new_name)),  # its session (and variables) go with it
            ("environment", lambda: self.environments.rename_notebook(name, new_name)),
            ("Fabric link", lambda: self.workload_store.rename_link(name, new_name)),  # its Fabric item stays linked
            ("versions", lambda: notebook_versions.rename(self._versions_root, name, new_name)),
            ("schedules", lambda: out.__setitem__("schedules_moved", self.pc_schedules.rename_notebook(name, new_name))),
            ("pin", lambda: self._rename_item_pin("notebook", name, new_name)),
        ]
        for step, run in steps:
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - logged and reported; the other steps still run
                _log.warning("Couldn't move the %s of %r to %r: %s", step, name, new_name, describe(exc))
                out["problems"].append({"notebook": new_name, "was": name, "step": step, "error": describe(exc)})
        return out

    def delete_notebook(self, name: str) -> dict[str, Any]:
        """Kept for the API: the same as notebook_delete (to Recently deleted)."""
        return self.notebook_delete(name)

    def publish_notebook(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                         compute: str | None = None, workspace: dict[str, str] | None = None,
                         location_ref: dict[str, str] | None = None, replace_fabric_changes: bool = False) -> dict[str, Any]:
        """Save the notebook, then create or update it as a notebook item in its workspace: the one
        picked for it (saved with the notebook), else the attached lakehouse's workspace.

        A notebook that uses fabric_desktop.lakehouse is published as a Fabric Python notebook
        with a setup cell, so it also runs there (Run in Fabric, schedules). When someone changed the
        item in Fabric since it was last published from here, it stops unless ``replace_fabric_changes``."""
        return self._publish(name, cells, lakehouse_ref, compute, workspace, location_ref=location_ref,
                             replace_fabric_changes=replace_fabric_changes)


def sql_result(result: pa.Table, limit: int, started: float) -> dict[str, Any]:
    """A query result as run_sql returns it: the first ``limit`` rows, and the seconds since ``started``
    (time.monotonic)."""
    columns = [c.to_pylist() for c in result.slice(0, limit).columns]  # by position: names can repeat
    return {"columns": [{"name": f.name, "type": str(f.type)} for f in result.schema],
            "rows": [[_json_value(v) for v in row] for row in zip(*columns)],
            "row_count": result.num_rows, "seconds": round(time.monotonic() - started, 2)}


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


def _with_ids(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c if c.get("cell_id") else {**c, "cell_id": f"cell-{n}"} for n, c in enumerate(cells)]
