"""The audit trail (FD-125): who did what, where, when, and how it ended.

Every engine call that changes something or runs something (a notebook cell, a SQL query, a run,
a publish, a promote, a write to OneLake, a schedule, a setting) adds one line to
``<data>/audit/audit-YYYY-MM.jsonl``. Read-only calls (listing, previewing, polling) don't.

Each line holds:

    at, action, outcome (ok or error), seconds
    who (the signed-in account), tenant, host, os user, app version, compute ("this PC")
    target   the call's short identifying values: workspace, lakehouse, environment, entity,
             notebook name, ids. Code, SQL, cell text and data are never recorded.
    error    the error, with secrets removed
    prev, hash   a SHA-256 chain: each line's hash covers the line before it, so a deleted or
                 edited line shows up in ``fabric-desktop audit --verify``

It is append-only and local to the PC (or to the agent's home on a server). It is evidence for the
person and their organization, not a security boundary: someone who controls the PC can delete
the whole folder, which ``--verify`` can't detect. Ship the files to a log store when that
matters.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .redact import secrets

SCHEMA = "audit@1"
GENESIS = "0" * 64

# Calls that only read or watch. Everything else is recorded, so a new call is audited unless it
# is added here on purpose.
READ_ONLY = frozenset("""
access_find_person access_lakehouse access_review access_who_can_read all_schedules all_shortcuts app_settings
audit_log benchmark_notebooks catalog catalog_tables cost_report endorsements environment fabric_runs history
incidents_list kernel_complete kernel_info kernel_variables mesh_about mesh_map mesh_overview
mesh_workspace_domains meta_alerts meta_compare meta_drift meta_entity meta_entity_shortcuts meta_fabric_result
meta_freshness meta_git_status meta_health meta_key_lookup meta_key_notebook meta_keys meta_model_plan
meta_preflight meta_preview meta_profile_preview meta_project meta_promote_check meta_promote_impact
meta_quarantine meta_run_detail meta_runs meta_runs_all meta_schedules meta_table_history meta_varlib
meta_versions notebook_dependencies notebook_environment notebook_schedules open_notebook pc_schedule_runs
pc_schedules_for platform_admins platform_audit platform_domains platform_glossary platform_key_tables
platform_landing platform_landing_files platform_overview platform_products platform_requests platform_sources
platform_status platform_whoami poll_cell preview_file preview_job preview_notebook preview_table prove_check
prove_list prove_rules_get prove_status receipt_get receipt_redactions receipts_list shortcut_access
shortcut_center shortcut_changes shortcut_check shortcut_connections shortcut_preflight shortcut_readers
shortcut_rules side_by_side_list side_by_side_status table_columns wizard_preview wizard_templates
workload_actions workload_detail workload_preflight workloads_list workspace_marks xray xray_adopt_plan
xray_vacuum_preview
sql_analyze sql_compat_profiles sql_dialects sql_format
git_branches git_commit_detail git_compare git_diff git_file_at git_info git_log git_notebook_info git_op_status git_ops git_path_info git_repos git_scan git_settings git_status side_by_side_writes
""".split())

# Parameters that hold code, queries or data: never recorded, only noted as given.
_CONTENT = frozenset({"code", "source", "sql", "query", "statement", "text", "cells", "content", "ipynb", "body", "value", "values",
                      "rows", "data", "parts", "definition", "notes", "description", "spec", "toml", "changes"})
_MAX = 200


def audited(method: str) -> bool:
    return method not in READ_ONLY and not method.startswith("list_")


def target(params: dict[str, Any]) -> dict[str, Any]:
    """The identifying values of a call's parameters; code, queries and data are left out."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if key.lower() in _CONTENT or any(word in key.lower() for word in ("secret", "password", "token")):
            out[key] = "(given)"
        elif isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, str):
            out[key] = secrets(value) if len(value) <= _MAX else "(given)"
        elif isinstance(value, list) and len(value) <= 20 and all(isinstance(v, str) and len(v) <= _MAX for v in value):
            out[key] = [secrets(v) for v in value]
        elif isinstance(value, dict):
            inner = target(value)
            out[key] = inner if len(json.dumps(inner, default=str)) <= 4 * _MAX else "(given)"
        else:
            out[key] = "(given)"
    return out


def _hash(prev: str, entry: dict[str, Any]) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev + body).encode()).hexdigest()


class AuditLog:
    def __init__(self, folder: Path, app: str = "desktop"):
        self.folder = Path(folder)
        self.app = app
        self._lock = threading.Lock()
        self._prev: str | None = None
        self.host = socket.gethostname()
        try:
            self.os_user = getpass.getuser()
        except Exception:  # no user name on this system
            self.os_user = ""

    def _files(self) -> list[Path]:
        return sorted(self.folder.glob("audit-*.jsonl"))

    def _last_hash(self) -> str:
        for path in reversed(self._files()):
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                try:
                    return str(json.loads(lines[-1])["hash"])
                except (ValueError, KeyError):
                    return GENESIS  # a damaged last line: --verify reports it; keep recording
        return GENESIS

    def record(self, action: str, outcome: str, seconds: float, params: dict[str, Any] | None = None,
               who: str = "", tenant: str = "", compute: str = "this PC", error: str = "") -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        entry: dict[str, Any] = {
            "schema": SCHEMA, "at": now.isoformat(timespec="milliseconds"), "action": action, "outcome": outcome,
            "seconds": round(seconds, 3), "who": who, "tenant": tenant, "host": self.host, "os_user": self.os_user,
            "app": self.app, "version": __version__, "compute": compute, "target": target(params or {}),
        }
        if error:
            entry["error"] = secrets(error)[:1000]
        with self._lock:
            if self._prev is None:
                self._prev = self._last_hash()
            entry["prev"] = self._prev
            entry["hash"] = _hash(self._prev, entry)
            self.folder.mkdir(parents=True, exist_ok=True)
            with (self.folder / f"audit-{now:%Y-%m}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            self._prev = entry["hash"]
        return entry

    def entries(self) -> Iterator[dict[str, Any]]:
        for path in self._files():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    yield json.loads(line)

    def recent(self, limit: int = 200, action: str = "") -> list[dict[str, Any]]:
        rows = [e for e in self.entries() if not action or e.get("action") == action]
        return rows[-limit:][::-1]

    def verify(self) -> dict[str, Any]:
        """Walks the chain: every line's hash must match its content and the line before it."""
        prev, count, problems = GENESIS, 0, []
        for path in self._files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                count += 1
                try:
                    entry = json.loads(line)
                    stored = entry.pop("hash")
                except (ValueError, KeyError):
                    problems.append(f"{path.name} line {number}: not an audit line")
                    continue
                if entry.get("prev") != prev:
                    problems.append(f"{path.name} line {number}: a line before it is missing or changed")
                if _hash(entry.get("prev", ""), entry) != stored:
                    problems.append(f"{path.name} line {number}: this line was changed")
                prev = stored
        return {"lines": count, "ok": not problems, "problems": problems}
