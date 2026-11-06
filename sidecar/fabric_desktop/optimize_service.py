"""The app's calls for setting up Optimize: create, connect to, switch and forget optimization lakehouses.

Creating a lakehouse is a Fabric write and only happens from ``optimize_create``. Connecting to an
existing one never initializes it unless the call says the person confirmed. IT can turn the
whole feature off with the ``allow_optimize_lakehouse`` policy key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Callable

from . import app_settings, jsonfile, measurement, redact, retrying
from .auth import ONELAKE_SCOPES
from .fabric_api import FabricError
from .meta.project import ENVIRONMENTS
from .meta_service import NoMetadataProject
from .optimize import local_runs, roles, schema, setup
from .optimize.outbox import ANALYSIS, LOCAL_EXECUTIONS, Outbox
from .optimize.installation import Installation, now
from .optimize.lakehouse import OneLakeLakehouse
from .optimize.registry import Registry
from .optimize.shared import Shared

_log = logging.getLogger(__name__)
OVERHEAD_SOURCE = "Optimize sync on this PC"


def _record_overhead(shared: Shared, kind: str, began: str, seconds: float, rows: int, commits: int) -> None:
    """What one sync took on this PC and wrote to OneLake, as Measured figures (PR-OPT-009): seconds,
    rows handed to the lakehouse and commits. Its own commit isn't counted in it. Bytes aren't
    measured yet, and no CU figure is given: OneLake reads and writes use capacity, but that use
    hasn't been measured. A failure here never fails the sync."""
    ended = now()
    ids: dict[str, Any] = {"run_id": uuid.uuid4().hex, "workload_id": f"optimize.sync.{kind}", "source": OVERHEAD_SOURCE,
           "collected_at": ended, "period_start": began, "period_end": ended}
    try:
        shared.record_measurements([
            measurement.make(measurement.MEASURED, "optimize_sync_seconds", measurement.SECONDS, round(seconds, 3), **ids),
            measurement.make(measurement.MEASURED, "optimize_sync_rows", measurement.COUNT, rows, **ids),
            measurement.make(measurement.MEASURED, "optimize_sync_commits", measurement.COUNT, commits, **ids)])
    except Exception as exc:  # noqa: BLE001 - the results were written; only their cost wasn't
        _log.info("A sync's own cost wasn't recorded: %s", redact.describe(exc))

DEFAULT_NAME = "FabricDesktop_Central"
CHANGED_SINCE = "The central lakehouse's roles changed since you looked. Check again before confirming."
METHODS = {"optimize_status", "optimize_discover", "optimize_create", "optimize_connect",
           "optimize_switch", "optimize_disconnect", "optimize_new_installation_id", "optimize_set_label",
           "optimize_findings", "optimize_recommendations", "optimize_set_recommendation",
           "optimize_share_local_runs"}


class OptimizeMixin:
    root: Any
    auth: Any
    fabric: Any

    @property
    def optimize_installation(self) -> Installation:
        return Installation(self.root / "optimize")

    @property
    def optimize_registry(self) -> Registry:
        return Registry(self.root / "optimize")

    @property
    def optimize_outbox(self) -> Outbox:
        """One Outbox per engine: opening it checks the whole file (sqlitefile.check), so it isn't
        reopened on every call."""
        path = self.root / "optimize" / "outbox.sqlite"
        box = getattr(self, "_optimize_outbox", None)
        if box is None or box.path != path:
            box = self._optimize_outbox = Outbox(path)
        return box

    def _optimize_allowed(self) -> None:
        if not app_settings.value("allow_optimize_lakehouse"):
            raise PermissionError("Your organization has turned off shared optimization lakehouses "
                                  "(allow_optimize_lakehouse in its policy file).")

    def _optimize_lake(self, workspace_id: str, lakehouse_id: str, schemas: bool | None = None,
                       token: Callable[[], str] | None = None) -> Any:
        """``token`` is a storage token taken earlier, for callers off the calling thread (sign-in
        tokens are fetched on the thread that handles the call)."""
        if schemas is None:
            schemas = self._schemas_enabled(workspace_id, lakehouse_id)  # type: ignore[attr-defined]
        return OneLakeLakehouse(workspace_id, lakehouse_id, schemas, token or self.auth.storage_token,
                                self.root / "optimize" / "cache")

    def optimize_status(self) -> dict[str, Any]:
        """What Optimize knows on this PC, without going online."""
        reg = self.optimize_registry
        active = reg.active()
        return {"allowed": bool(app_settings.value("allow_optimize_lakehouse")), "default_name": DEFAULT_NAME,
                "installation_id": self.optimize_installation.id, "label": self.optimize_installation.label,
                "active": active, "connections": reg.all(),
                "outbox": self.optimize_outbox.status(active["lakehouse_id"]) if active else None}

    def optimize_discover(self, workspace_id: str) -> list[dict[str, Any]]:
        """The workspace's lakehouses, each marked with whether it already holds Optimize's tables."""
        out = []
        for lh in self.fabric.list_lakehouses(workspace_id):
            entry = {"lakehouse_id": lh["id"], "lakehouse_name": lh.get("displayName", ""), "optimize": False}
            try:
                names = self._optimize_lake(workspace_id, lh["id"]).table_names()
                entry["optimize"] = "fdopt_schema_version" in names
                entry["other_tables"] = sum(1 for n in names if schema.family_of(n) is None)
            except Exception as exc:
                entry["error"] = "No access" if setup.denied(exc) else "Couldn't be read"
            out.append(entry)
        return sorted(out, key=lambda e: (not e["optimize"], e["lakehouse_name"].lower()))

    def optimize_inspect(self, workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
        """What the lakehouse holds and what connecting to it would do. Reads only."""
        return setup.inspect(self._optimize_lake(workspace_id, lakehouse_id))

    def optimize_create(self, workspace_id: str, name: str = DEFAULT_NAME, workspace_name: str = "") -> dict[str, Any]:
        """Create a lakehouse with Optimize's tables in it, and connect this PC to it."""
        self._optimize_allowed()
        name = (name or DEFAULT_NAME).strip()
        if self.fabric.find_lakehouse(workspace_id, name):
            raise ValueError(f"This workspace already has a lakehouse called {name}. Connect to it instead, "
                             "or pick another name.")
        lh = self.fabric.create_lakehouse(workspace_id, name)
        lake = self._optimize_lake(workspace_id, lh["id"], schemas=True)
        try:
            setup.initialize(lake, self.optimize_installation, approve=True)
        except Exception as exc:
            raise RuntimeError(f"{name} was created, but setting it up didn't finish ({redact.secrets(str(exc))}). Connect to it to "
                               "finish: nothing in it is lost.") from exc
        return self._optimize_register(lake, workspace_id, workspace_name, lh["id"], name, created=True)

    def optimize_connect(self, workspace_id: str, lakehouse_id: str, workspace_name: str = "",
                         lakehouse_name: str = "", initialize: bool = False) -> dict[str, Any]:
        """Connect this PC to an existing lakehouse. One that has no Optimize tables yet, an older
        schema or an unfinished setup is changed only with ``initialize`` (the person confirmed);
        otherwise the answer says what connecting would do."""
        self._optimize_allowed()
        lake = self._optimize_lake(workspace_id, lakehouse_id)
        state = setup.inspect(lake)
        if state["state"] == setup.CONFLICT:
            raise ValueError(state["message"])
        if state["state"] in setup.CAN_INITIALIZE:
            if not initialize:
                return {"connected": False, "needs_confirmation": True, "inspection": state}
            setup.initialize(lake, self.optimize_installation, approve=True)
        out = self._optimize_register(lake, workspace_id, workspace_name, lakehouse_id, lakehouse_name)
        return out | {"sync": self._optimize_flush_quietly()}

    def _optimize_register(self, lake: Any, workspace_id: str, workspace_name: str, lakehouse_id: str,
                           lakehouse_name: str, created: bool = False) -> dict[str, Any]:
        access = setup.check_access(lake, self.optimize_installation)
        state = access.get("state") or {}
        conn = self.optimize_registry.add({
            "workspace_id": workspace_id, "workspace_name": workspace_name, "lakehouse_id": lakehouse_id,
            "lakehouse_name": lakehouse_name, "schemas": lake.schemas, "schema_version": state.get("schema_version"),
            "access": "write" if access["write"] else "read" if access["read"] else "none", "checked_at": now()})
        return {"connected": True, "created": created, "connection": conn, "access": access}

    def optimize_switch(self, lakehouse_id: str) -> dict[str, Any]:
        active = self.optimize_registry.activate(lakehouse_id)
        return {"active": active, "sync": self._optimize_flush_quietly()}

    def optimize_disconnect(self, lakehouse_id: str) -> dict[str, Any]:
        """Forget the lakehouse on this PC. Nothing in the lakehouse is changed or deleted, and results
        still waiting for it on this PC stay, to be sent if this PC connects to it again."""
        self.optimize_registry.forget(lakehouse_id)
        return {"disconnected": lakehouse_id, "active": self.optimize_registry.active()}

    def optimize_new_installation_id(self) -> dict[str, Any]:
        return {"installation_id": self.optimize_installation.reset()}

    def optimize_set_label(self, label: str) -> dict[str, Any]:
        self.optimize_installation.set_label(label)
        return {"label": self.optimize_installation.label}

    # The shared results (optimize/shared.py)

    def _optimize_shared(self, token: Callable[[], str] | None = None) -> Shared:
        self._optimize_allowed()
        conn = self.optimize_registry.active()
        if not conn:
            raise ValueError("Connect this PC to an optimization lakehouse first (Settings > Optimize lakehouses).")
        lake = self._optimize_lake(conn["workspace_id"], conn["lakehouse_id"], conn.get("schemas", True),
                                  **({"token": token} if token else {}))
        return Shared(lake, self.optimize_installation, access=conn.get("access") or "none")

    # Results waiting on this PC (optimize/outbox.py, PR-OPT-006)

    @staticmethod
    def _optimize_transient(exc: BaseException) -> bool:
        """Whether a failed write is worth trying again later: no network, throttling, a lost commit
        race or a dropped upload. A permission error, a refused write or bad input is not."""
        if isinstance(exc, (PermissionError, ValueError)) or setup.denied(exc):
            return False
        return retrying.is_retryable(exc) or retrying.data_upload_dropped(exc)

    @staticmethod
    def _optimize_send(shared: Shared, kind: str, payload: dict[str, Any]) -> int:
        began, start, rows, commits = now(), time.monotonic(), shared.rows_sent, shared.commits
        if kind == ANALYSIS:
            out = shared.record_analysis(payload["run"], payload.get("artifacts") or (),
                                         payload.get("findings") or (), payload.get("recommendations") or ())
            sent = int(out["findings"])
        else:
            sent = shared.record_local_executions(payload.get("rows") or [])
        _record_overhead(shared, kind, began, time.monotonic() - start, shared.rows_sent - rows, shared.commits - commits)
        return sent

    def _optimize_flush(self, token: Callable[[], str] | None = None) -> dict[str, Any]:
        """Send what is waiting for the active lakehouse, oldest first. Stops at the first failure
        that may pass (the lakehouse is probably still out of reach) or a permission error (it may be
        granted later); an item the lakehouse refuses as it is gets dropped and counted, so it can't
        hold up the rest."""
        out: dict[str, Any] = {"sent": 0, "dropped": 0, "waiting": 0}
        conn = self.central_lakehouse()
        if not conn or conn.get("access") != "write":
            return out
        box, lakehouse_id = self.optimize_outbox, conn["lakehouse_id"]
        # Every send is started by a person (connect, switch, share) or a finished scan, never a
        # timer, so everything waiting is tried now; the wait between tries is for a background sync.
        items = box.due(lakehouse_id, include_waiting=True)
        if items:
            shared = self._optimize_shared(token)
            for item in items:
                try:
                    self._optimize_send(shared, item["kind"], item["payload"])
                except Exception as exc:
                    if self._optimize_transient(exc) or isinstance(exc, PermissionError) or setup.denied(exc):
                        box.failed(item["id"], str(exc))
                        out["error"] = redact.secrets(str(exc))
                        break
                    box.dropped(item["id"], str(exc))
                    out["dropped"] += 1
                    continue
                box.sent(item["id"])
                out["sent"] += 1
        out["waiting"] = box.status(lakehouse_id)["pending"]
        return out

    def _optimize_flush_quietly(self, token: Callable[[], str] | None = None) -> dict[str, Any] | None:
        """``_optimize_flush`` for callers whose own work mustn't fail because of it."""
        try:
            return self._optimize_flush(token)
        except Exception as exc:  # noqa: BLE001 - connecting or switching stands without it
            return {"error": redact.secrets(str(exc))}

    def _optimize_write(self, kind: str, payload: dict[str, Any],
                        token: Callable[[], str] | None = None) -> dict[str, Any]:
        """Write to the active lakehouse, after anything still waiting for it. A failure that may pass
        keeps the payload on this PC for the next write, connect or switch; any other failure raises."""
        conn = self.central_lakehouse()
        if not conn:
            raise ValueError("Connect this PC to an optimization lakehouse first (Settings > Optimize lakehouses).")
        sync = self._optimize_flush_quietly(token)
        try:
            return {"sent": self._optimize_send(self._optimize_shared(token), kind, payload), "queued": False,
                    "sync": sync}
        except Exception as exc:
            if not self._optimize_transient(exc):
                raise
            self.optimize_outbox.add(conn["lakehouse_id"], kind, payload, str(exc))
            return {"sent": 0, "queued": True, "error": redact.secrets(str(exc)), "sync": sync}

    # ---------- the central lakehouse, for other features ----------

    def central_lakehouse(self) -> dict[str, Any] | None:
        """The central lakehouse this PC uses (workspace_id, lakehouse_id, lakehouse_name, schemas,
        access), for features that keep their own table family or files there. None when the
        organization's policy turns it off or this PC isn't connected to one."""
        if not app_settings.value("allow_optimize_lakehouse"):
            return None
        conn = self.optimize_registry.active()
        return dict(conn) if conn else None

    def central_family(self, family: schema.Family, approve: bool = False) -> tuple[Any, Installation]:
        """The central lakehouse and this installation, ready for ``family``'s tables: write with
        setup.write(lake, installation, name, rows, family=family), read with
        lake.rows(family.table(name)). When the family's tables (or Optimize's shared ones) are missing,
        unfinished or older, it sets them up only with ``approve``, which the caller passes after the
        person confirmed; without it, it raises with what setting up would do. Raises when there's no
        central lakehouse, this person can only read it, or the tables there are newer or not the
        family's."""
        self._optimize_allowed()
        conn = self.optimize_registry.active()
        if not conn:
            raise ValueError("Connect this PC to a central lakehouse first (Settings > Optimize lakehouses).")
        if conn.get("access") != "write":
            raise PermissionError("You can read the central lakehouse but not write to it. "
                                  "Ask for Contributor on its workspace.")
        lake = self._optimize_lake(conn["workspace_id"], conn["lakehouse_id"], conn.get("schemas", True))
        for fam in (setup.CORE, family):
            state = setup.inspect(lake, fam)
            if state["state"] == setup.READY:
                continue
            if state["state"] not in setup.CAN_INITIALIZE:
                raise PermissionError(state["message"])
            if not approve:
                raise PermissionError(f"{state['message']} Confirm to continue.")
            setup.initialize(lake, self.optimize_installation, approve=True, family=fam)
        return lake, self.optimize_installation

    def central_restrict(self, family: schema.Family, role_name: str, members: list[str],
                         approve: bool = False, shown: list[str] | None = None) -> dict[str, Any]:
        """Keep ``family``'s tables from everyone who reads the central lakehouse through DefaultReader,
        and let ``members`` (Entra object ids) read them through ``role_name`` (roles.restrict_plan).
        Without ``approve`` it changes nothing and raises with what it would change. With it, the
        roles are read again; if ``shown`` (the changes the person confirmed) no longer matches, it
        refuses, otherwise the change is made, refused by Fabric if the roles changed after that
        read, and fdopt_audit records it. Needs a lakehouse with
        schemas, OneLake security turned on, and the family's tables set up (central_family). Untested
        on a live tenant: the ETag and If-Match handling follow Fabric's docs only."""
        conn = self.optimize_registry.active()
        if conn and not conn.get("schemas", True):
            raise ValueError("The central lakehouse has no schemas, so its tables can't be kept from DefaultReader "
                             "without listing every other table, which would hide tables added later. Use a central "
                             "lakehouse with schemas for tables only some people may read.")
        lake, installation = self.central_family(family)
        api = self._scoped(ONELAKE_SCOPES)  # type: ignore[attr-defined]
        ws, item = conn["workspace_id"], conn["lakehouse_id"]
        tenant = self.auth.signed_in_tenant() or ""

        def plan() -> dict[str, Any]:
            try:
                current, etag = api.list_data_access_roles_tagged(ws, item)
            except Exception as exc:  # noqa: BLE001 - OneLake security off, or no rights to its roles
                raise PermissionError("Couldn't read the central lakehouse's OneLake security roles. Turn on OneLake "
                                      "security for it, or ask its workspace Admin. " + redact.secrets(str(exc))[:200]) from exc
            return roles.restrict_plan(current, family, role_name, members, tenant, lake.schema_names()) | {"etag": etag}

        found = plan()
        summary = {"family": family.prefix, "role": role_name, "changes": found["changes"],
                   "default_reader_paths": found["default_reader_paths"], "family_path": found["family_path"]}
        if not found["changes"]:
            return summary | {"changed": False}
        if not approve:
            ask = PermissionError("; ".join(found["changes"]) + ". Confirm to continue.")
            ask.changes = found["changes"]  # type: ignore[attr-defined]  # pass back as shown=
            raise ask
        if shown is None:
            raise ValueError("Pass the changes the person confirmed as shown= (the error's changes).")
        found = plan()  # read again just before writing: the roles may have changed since the person looked
        if not found["changes"]:
            return summary | {"changes": [], "changed": False}
        if list(shown) != found["changes"]:
            raise PermissionError(CHANGED_SINCE)
        try:
            api.put_data_access_roles(ws, item, found["roles"], etag=found["etag"])
        except FabricError as exc:
            if exc.code == "PreconditionFailed" or exc.status == 412:
                raise PermissionError(CHANGED_SINCE) from exc
            raise
        setup.write(lake, installation, "audit", [{
            "record_id": f"{installation.id}:restrict:{family.prefix}:{now()}", "at": now(), "action": "restrict",
            "target": f"{found['family_path']} role {role_name}", "outcome": "ok",
            "message": "; ".join(found["changes"])}])
        return summary | {"changes": found["changes"], "default_reader_paths": found["default_reader_paths"],
                          "changed": True}

    def _optimize_workspaces(self) -> set[str]:
        """The workspaces the signed-in person can open in Fabric: results about items anywhere
        else aren't shown to them."""
        return {w["id"] for w in self.fabric.list_workspaces() if w.get("id")}

    def optimize_findings(self) -> list[dict[str, Any]]:
        """Problems found by every installation, one per problem, for items the person can open."""
        return self._optimize_shared().findings(self._optimize_workspaces())

    def optimize_recommendations(self, include_closed: bool = False) -> list[dict[str, Any]]:
        """Recommendations at their latest state, for items the person can open."""
        return self._optimize_shared().recommendations(self._optimize_workspaces(), include_closed=include_closed)

    def optimize_share_local_runs(self, approve: bool = False, confirm: str = "") -> dict[str, Any]:
        """Add this PC's work (notebook writes to OneLake tables, scheduled notebook runs, metadata runs,
        proof runs) to the central lakehouse's fdopt_local_execution, so Optimize counts what ran here.
        Without ``approve`` it writes nothing and raises with what it would add, and ``confirm`` in what
        it would add. It writes only with ``approve`` and that ``confirm``: if the runs to add changed
        since (a run finished in between), it asks again instead of sharing runs the person wasn't
        shown. Runs already added aren't added again."""
        shared = self._optimize_shared()
        me = self.optimize_installation.id
        notebooks = {n["name"] for n in self.notebooks.list()}  # type: ignore[attr-defined]
        rows = (local_runs.from_history(self.history(limit=100000), me)  # type: ignore[attr-defined]
                + local_runs.from_schedule_runs(self.pc_schedules.runs(limit=100000), notebooks, me)  # type: ignore[attr-defined]
                + self._optimize_metadata_runs(me)
                + local_runs.from_proofs(self._optimize_proofs(), me))
        offline = None
        try:
            done = shared.local_execution_ids()
        except Exception as exc:
            if not self._optimize_transient(exc):
                raise
            # Out of reach: what's already there can't be read, so every run not already waiting on this
            # PC counts as new; rows that turn out to be there already are skipped when they're sent.
            done, offline = set(), redact.secrets(str(exc))
        waiting = self.optimize_outbox.waiting_execution_ids((self.optimize_registry.active() or {}).get("lakehouse_id", ""))
        new = [r for r in rows if r["execution_id"] not in done and r["execution_id"] not in waiting]
        kinds = {k: sum(r["workload_type"] == k for r in new)
                 for k in ("notebook_write", "notebook_run", "metadata_run", "proof_run")}
        writes = kinds["notebook_write"]
        out = {"new": len(new), "notebook_writes": writes, "notebook_runs": kinds["notebook_run"],
               "metadata_runs": kinds["metadata_run"], "proof_runs": kinds["proof_run"],
               "already": len(rows) - len(new), "waiting": sum(r["execution_id"] in waiting for r in rows),
               "shared": 0, **({"offline": offline} if offline else {})}
        if not new:
            return out
        out["confirm"] = hashlib.sha256("\n".join(sorted(r["execution_id"] for r in new)).encode()).hexdigest()[:32]
        if not approve or confirm != out["confirm"]:
            name = (self.optimize_registry.active() or {}).get("lakehouse_name") or "the central lakehouse"
            parts = [f"{n} {what}{'s' if n != 1 else ''}" for n, what in (
                (writes, "notebook write"), (kinds["notebook_run"], "scheduled notebook run"),
                (kinds["metadata_run"], "metadata run"), (kinds["proof_run"], "proof run")) if n]
            changed = "The runs to add changed since you looked. " if approve and confirm else ""
            ask = PermissionError(
                f"{changed}Adds {len(new)} run{'s' if len(new) != 1 else ''} from this PC to {name} ({', '.join(parts)}): "
                "when each ran, how long it took, rows written, CPU time and peak memory where they were measured, "
                "and the table, project or notebook name. No code or data, and everyone who can read the "
                "lakehouse sees them. Confirm to continue.")
            ask.would = out  # type: ignore[attr-defined]
            raise ask
        sent = self._optimize_write(LOCAL_EXECUTIONS, {"rows": new})
        return out | {"shared": sent["sent"], "queued": len(new) if sent["queued"] else 0,
                      **({"error": sent["error"]} if sent.get("error") else {})}

    def _optimize_proofs(self) -> list[dict[str, Any]]:
        """Prove's saved proofs, one line each; a line that can't be read is left out, not the rest."""
        try:
            lines = jsonfile.read_text(self._proofs_file).splitlines()  # type: ignore[attr-defined]
        except OSError:
            return []
        out = []
        for line in filter(str.strip, lines):
            try:
                record = json.loads(line)
            except ValueError as exc:  # one damaged proof is left out
                _log.info("A saved proof couldn't be read, so it isn't shared: %s", redact.describe(exc))
                continue
            if isinstance(record, dict):
                out.append(record)
        return out

    def _optimize_metadata_runs(self, installation_id: str) -> list[dict[str, Any]]:
        """Runs of the open metadata project in its environments kept on this PC. An environment in
        Fabric isn't read: that would be a OneLake read, and those runs ran in Fabric or by an agent."""
        try:
            project = self._project()  # type: ignore[attr-defined]
        except NoMetadataProject:
            return []
        except Exception as exc:  # noqa: BLE001 - a damaged project leaves its runs out, not the rest
            _log.info("The metadata project couldn't be read, so its runs aren't shared: %s", redact.describe(exc))
            return []
        rows: list[dict[str, Any]] = []
        for env in ENVIRONMENTS:
            if project.environments.get(env, {}).get("target", "onelake" if env != "dev" else "local") != "local":
                continue
            try:
                log = self._store(project, env).meta("run_log")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - a damaged or missing run log leaves that env's runs out
                _log.info("The %s run log couldn't be read: %s", env, redact.describe(exc))
                continue
            rows += [r | {"env": r.get("env") or env} for r in log.iter_rows(named=True)]
        return local_runs.from_run_log(rows, project.root.name, installation_id)

    def optimize_set_recommendation(self, recommendation_id: str, state: str, reason: str = "") -> dict[str, Any]:
        """Accept, dismiss, reopen or otherwise move a recommendation on, for everyone using the lakehouse."""
        return self._optimize_shared().set_recommendation_state(recommendation_id, state, reason,
                                                               self._optimize_workspaces())
