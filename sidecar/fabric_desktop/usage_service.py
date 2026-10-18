"""Measured usage files, mixed into ``Service`` (FINOPS-003): preview and import a usage export,
list and remove imports, and reconcile them with the runs this app started in Fabric.

Everything stays on this PC. Nothing here calls Fabric or Azure: a person exports the file from
the Microsoft tool and picks it in the app. The logic lives in the ``usage`` package.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import capacity as capacity_mod
from . import costs as costs_mod
from . import usage
from .meta.project import ENVIRONMENTS
from .usage import reconcile as reconcile_mod
from .usage.store import Store


class UsageMixin:
    @property
    def _usage(self) -> Store:
        return Store(self.root)  # type: ignore[attr-defined]

    def _usage_read(self, file_name: str, content: str, options: dict[str, Any] | None) -> dict[str, Any]:
        store = self._usage
        return usage.read(content or "", str(file_name or "usage.csv")[:200], options,
                          known_keys=store.keys(), known_slots=store.slots(), imports=store.summaries())

    def usage_preview(self, file_name: str, content: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """What importing a usage file would add, without saving anything."""
        return {k: v for k, v in self._usage_read(file_name, content, options).items() if k != "rows"}

    def usage_import(self, file_name: str, content: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Save a usage file's rows on this PC as measured CU. Checked again first; refused if not ready."""
        preview = self._usage_read(file_name, content, options)
        if not preview["ready"]:
            raise ValueError(preview["problem"] or "This file can't be imported.")
        saved = self._usage.save(preview, dt.datetime.now(dt.timezone.utc))
        return saved | {"already_imported": preview["already_imported"], "duplicates_in_file": preview["duplicates_in_file"]}

    def usage_imports(self) -> list[dict[str, Any]]:
        return self._usage.summaries()

    def usage_import_delete(self, import_id: str) -> dict[str, Any]:
        """Remove an import's local copy. The exported file itself isn't touched."""
        return {"deleted": self._usage.delete(import_id)}

    def _workspace_names(self, runs: list[dict[str, Any]]) -> dict[str, str]:
        names = {r["workspace_id"]: r["workspace_name"] for r in runs if r.get("workspace_id") and r.get("workspace_name")}
        try:
            project = self._project()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - no metadata project
            return names
        for env in ENVIRONMENTS:
            cfg = project.environments.get(env, {})
            if cfg.get("workspace_id") and cfg.get("workspace_name"):
                names.setdefault(cfg["workspace_id"], cfg["workspace_name"])
        return names

    def _usage_reconcile(self, days: int, fabric: list[dict[str, Any]], rows: list[dict[str, Any]],
                         caps: dict[str, Any] | None = None) -> dict[str, Any]:
        now = dt.datetime.now(dt.timezone.utc)
        estimates = {r["fabric_job_cu"]["run_id"]: r["fabric_job_cu"] for r in rows
                     if r["where"] == "fabric" and r["fabric_job_cu"].get("run_id")}
        if caps is None:  # the last capacity reading on this PC; never a call to Fabric from here
            caps = capacity_mod.load(self.root / "capacity_observations.json") or {}  # type: ignore[attr-defined]
        envs: dict[str, list[str]] = {}
        for e in caps.get("environments", []):
            if (e.get("capacity") or {}).get("name"):
                envs.setdefault(e["capacity"]["name"].lower(), []).append(e["env"])
        return reconcile_mod.reconcile(self._usage.all(), fabric, estimates, now - dt.timedelta(days=int(days)), now,
                                       self._workspace_names(fabric), now, envs) | {"imports_list": self._usage.summaries()}

    def usage_reconciliation(self, days: int = 30) -> dict[str, Any]:
        """Imported measured CU against this app's Fabric runs over the last ``days``."""
        cfg = self._cost_settings()  # type: ignore[attr-defined]
        fabric = self.fabric_runs(limit=500, refresh=False)  # type: ignore[attr-defined]
        rows = costs_mod.activities([], fabric, [], cfg)
        return self._usage_reconcile(days, fabric, rows)
