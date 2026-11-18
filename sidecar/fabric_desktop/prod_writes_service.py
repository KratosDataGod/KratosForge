"""A notebook cell's MERGE, UPDATE, DELETE, VACUUM or RESTORE on a workspace marked Prod waits for the person
to type the workspace's name, as a Warehouse change in Prod does (Chris, 2026-11-17).

Marked Prod: a workspace the open metadata project names as its Prod environment's, or that any project opened
on this PC named so before. Those ids are remembered on this PC (prod_workspaces.json), so the rule holds with no
project open. A workspace no project has ever named Prod isn't known to be Prod, so it isn't asked about.

The cell is refused first (lakehouse.ProdConfirmNeeded, nothing changed) and the refusal is kept for its notebook.
``notebook_prod_pending`` says what waits; ``notebook_prod_confirm`` takes the typed name, which must be the
workspace's name exactly, and allows that workspace for the notebook's next cell run only, started within
ALLOW_SECONDS. Every start of a cell takes the allowance or drops it (service._start_cell), so it never carries
over to a later run. The confirm is audited and runs only once its audit line is written (audit.fails_closed:
it carries ``confirm_name``).

With a project open, OPTIMIZE, VACUUM and RESTORE from code stay refused outright on its Test and Prod workspaces
(meta_service._maintenance_refusal): typing the name doesn't lift that.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import jsonfile
from .redact import describe as describe_error

_log = logging.getLogger(__name__)

METHODS = {"notebook_prod_pending", "notebook_prod_confirm"}
ALLOW_SECONDS = 120          # the typed name lets the next cell run of the notebook start within this
PENDING_SECONDS = 600        # a refusal can be confirmed for this long after it
MAX_REMEMBERED = 500         # remembered Prod workspace ids, newest kept


class ProdWritesMixin:
    _prod_lock = threading.Lock()

    @property
    def prod_workspaces_file(self) -> Path:
        return self.root / "prod_workspaces.json"  # type: ignore[attr-defined]

    def _prod_state(self) -> dict[str, Any]:
        state = self.__dict__.get("_prod_writes")
        if state is None:
            state = self.__dict__.setdefault("_prod_writes", {"pending": {}, "allowed": {}})
        return state

    def _remember_prod_workspaces(self, project: Any) -> set[str]:
        """The project's Prod workspace ids (lower case), added to the ones remembered on this PC."""
        from .meta_service import _env_workspaces
        try:
            cfg = (project.environments or {}).get("prod") or {}
            ids = {i.lower() for i in _env_workspaces(cfg) | {cfg.get("keys_workspace_id") or ""} if i}
        except Exception as exc:  # noqa: BLE001 - a project whose environments can't be read names none
            _log.info("Couldn't read the project's Prod workspaces: %s", describe_error(exc))
            return set()
        if ids:
            with self._prod_lock:
                known = self._remembered_prod()
                if not ids <= set(known):
                    kept = [i for i in known if i not in ids] + sorted(ids)
                    try:
                        jsonfile.write_json(self.prod_workspaces_file, {"prod": kept[-MAX_REMEMBERED:]}, indent=None)
                    except OSError as exc:  # the open project still marks them; only the memory is missed
                        _log.warning("Couldn't remember the Prod workspaces: %s", describe_error(exc))
        return ids

    def _remembered_prod(self) -> list[str]:
        data = jsonfile.read_json(self.prod_workspaces_file, {})
        ids = data.get("prod") if isinstance(data, dict) else None
        return [str(i).lower() for i in ids if isinstance(i, str) and i] if isinstance(ids, list) else []

    def _prod_marked(self, workspace_id: str) -> bool:
        wanted = str(workspace_id or "").lower()
        if not wanted:
            return False
        if wanted in self._remembered_prod():
            return True
        try:
            project = self._project()  # type: ignore[attr-defined]
        except LookupError:  # no project open
            return False
        except Exception as exc:  # noqa: BLE001 - an unreadable project names nothing; the remembered ids still hold
            # A warning: a Prod workspace the broken project names and no earlier one did goes unguarded meanwhile.
            _log.warning("Couldn't read the metadata project for the Prod check, so only the Prod workspaces "
                         "remembered on this PC are asked about: %s", describe_error(exc))
            return False
        return wanted in self._remember_prod_workspaces(project)

    def _prod_write_refusal(self, operation: str, workspace_id: str, workspace_name: str, table: str,
                            notebook: str) -> str | None:
        """lakehouse.prod_guard: why this change waits for the workspace's name to be typed, or None."""
        if not self._prod_marked(workspace_id):
            return None
        shown = workspace_name or workspace_id
        from . import lakehouse
        if notebook == lakehouse.NO_CELL:  # not a cell's own write, during a cell run: typing the name can't allow it
            return (f"{operation.upper()} on {table} would change {shown}, a Prod workspace, while a notebook cell is "
                    "running. Only that cell's own code can be allowed to do that, so nothing was changed. Try again "
                    "when the cell has finished, or make the change in the cell's own code rather than a thread it starts.")
        with self._prod_lock:
            self._prod_state()["pending"][notebook or ""] = {
                "workspace_id": workspace_id, "workspace_name": shown, "operation": operation.upper(),
                "table": table, "at": time.monotonic()}
        return (f"{operation.upper()} on {table} would change {shown}, a Prod workspace, so it waits for you to "
                "type the workspace's name. Nothing was changed. Run this cell on its own to type it.")

    def _take_prod_allowance(self, notebook: str | None) -> frozenset[str]:
        """The workspaces this run of ``notebook`` may change after a typed name; the allowance is used up."""
        with self._prod_lock:
            allowed = self._prod_state()["allowed"].pop(notebook or "", None)
        if not allowed or time.monotonic() > allowed["until"]:
            return frozenset()
        return frozenset({allowed["workspace_id"]})

    def notebook_prod_pending(self, notebook: str = "") -> dict[str, Any] | None:
        """What the notebook's last refused cell would have changed in Prod: {workspace_id, workspace_name,
        operation, table}, or None."""
        with self._prod_lock:
            p = self._prod_state()["pending"].get(notebook or "")
        if not p or time.monotonic() - p["at"] > PENDING_SECONDS:
            return None
        return {k: p[k] for k in ("workspace_id", "workspace_name", "operation", "table")}

    def notebook_prod_confirm(self, notebook: str = "", workspace_id: str = "", confirm_name: str = "") -> dict[str, Any]:
        """The person typed the Prod workspace's name: its next cell run (within ALLOW_SECONDS) may change it."""
        from . import lakehouse
        if notebook == lakehouse.NO_CELL:  # a write that is no cell's: nothing it asked can be allowed
            raise ValueError("This change wasn't made by a notebook cell, so typing the name can't allow it.")
        with self._prod_lock:
            state = self._prod_state()
            p = state["pending"].get(notebook or "")
            if not p or time.monotonic() - p["at"] > PENDING_SECONDS or \
                    str(workspace_id).lower() != p["workspace_id"].lower():
                raise ValueError("Nothing in this notebook is waiting for a Prod workspace's name. Run the cell again.")
            if confirm_name != p["workspace_name"]:
                raise ValueError(f"Type {p['workspace_name']} exactly to allow this change.")
            del state["pending"][notebook or ""]
            state["allowed"][notebook or ""] = {"workspace_id": p["workspace_id"].lower(),
                                                "until": time.monotonic() + ALLOW_SECONDS}
        return {"allowed": True, "workspace_name": p["workspace_name"], "seconds": ALLOW_SECONDS}
