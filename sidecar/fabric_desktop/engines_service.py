"""The notebook's Engine picker (Preview), mixed into ``Service``.

Python (this PC) is the notebook's own kernel, as before: cells keep running through ``start_cell``.
Every other engine runs through one ``engines.sessions.SessionManager``:

- ``engine_catalog`` lists the engines (registry.py) and ``engine_connections`` the Databricks
  connections this PC has: connection profiles, and the Databricks CLI's own profiles. A
  connection is named by a key; its host and sign-in are always read here, never taken from the app.
- ``engine_prepare`` says what choosing an engine would do, starting nothing: the target and its
  state (reading a warehouse or cluster never starts it), what it bills, what a switch loses, whether
  the person must confirm, which cells it can't run, and an advisory risk check. The check never
  blocks; when it can't read something it says "couldn't run".
- ``engine_select`` opens the session in the background (a cluster can take minutes to start), with
  a ``RemoteAuthorization`` from the person's confirm or the profile's policy (ADR-003). Nothing here
  picks a remote engine on its own, and a cell an engine can't run is never moved to another one:
  the app offers "Run this cell on Python" for that one cell, which the person chooses.
- ``engine_profiles``, ``engine_discover``, ``engine_connection_save`` and ``engine_connection_remove``
  are Settings > Connections (Preview): the saved profiles, the CLIs' own profiles and connections,
  what a Databricks workspace offers (GETs only, so nothing starts or bills), and adding a CLI
  profile or connection to a profile by name. The host and sign-in are read from the CLI's file
  here; the app only names which one, so a profile never holds a host or secret the app made up.
- ``databricks_runs`` and ``databricks_run`` are Monitor > Databricks (Preview): recent job runs and
  Lakeflow pipeline updates, and one run's tasks and errors (engines/databricks_jobs.py). GETs only.
- ``databricks_billing`` is Optimize > Cost (Preview): DBUs billed, from system.billing.usage. It runs
  one query on a SQL warehouse, which may start and bill, so it first answers ``needs_confirm`` with the
  warehouse's state, and runs only with ``confirmed`` (a RemoteAuthorization for that warehouse).
  ``databricks_billing_last`` is the last read kept on this PC.
- ``engine_start_cell`` and ``engine_poll_cell`` run one cell at a time per notebook, like
  ``start_cell`` and ``poll_cell``; ``engine_cancel`` and ``engine_close`` stop them.

The choice lasts while the app is open. Sessions close when the notebook goes back to Python
(this PC), when it closes, or when the app quits.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from . import connprofiles, redact
from .engines import authorization as auth
from .engines import registry
from .engines.capabilities import check_cell
from .engines.contract import CellRequest, SessionConfig
from .engines.sessions import SessionManager, SwitchNeeded

METHODS = {"engine_catalog", "engine_connections", "engine_prepare", "engine_select", "engine_state",
           "engine_start_cell", "engine_poll_cell", "engine_cancel", "engine_close",
           "engine_profiles", "engine_discover", "engine_connection_save", "engine_connection_remove",
           "databricks_runs", "databricks_run", "databricks_billing", "databricks_billing_last"}
# Answered at once even while a cell or a session start runs (server.QUICK).
QUICK = {"engine_state", "engine_poll_cell", "engine_cancel"}

KERNEL = "local-python"          # the notebook's own kernel; runs through start_cell, not here
BILLS = {"databricks_dbu": "bills DBUs", "fabric_capacity": "uses Fabric capacity", "none": ""}
_MAGIC = re.compile(r"^\s*(%%sql|%sql)[^\n]*\n?", re.IGNORECASE)
_WRITES = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|OPTIMIZE|VACUUM)\b|"
                     r"\.(saveAsTable|insertInto|save)\(|\.write\b", re.IGNORECASE)


def cell_request(source: str) -> CellRequest:
    """The engine request for a cell's source: %%sql (or Databricks' %sql) is Spark SQL without its
    magic line; Python that uses ``spark`` is PySpark; anything else is Python."""
    first = source.lstrip().split("\n", 1)[0].strip()
    if re.match(r"^%%sql\b", first, re.IGNORECASE) or first.lower() == "%sql":
        return CellRequest(_MAGIC.sub("", source.lstrip(), count=1), "sql", "spark")
    return CellRequest(source, "pyspark" if re.search(r"\bspark\b", source) else "python")


_COMPUTE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_NAME_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,255}$")


def _connection_view(profile_id: str, c: dict[str, Any]) -> dict[str, Any]:
    """One connection of a profile as Settings shows it: where it points and how it signs in."""
    cred = c.get("credential") or {}
    compute = c.get("compute") or {}
    return {"id": c.get("id", ""), "platform": c.get("platform", ""), "label": c.get("label", ""),
            "key": f"profile:{profile_id}:{c.get('id', '')}" if c.get("platform") == "databricks" else "",
            "host": c.get("host", ""), "account": c.get("account", ""), "method": cred.get("method", ""),
            "cli": cred.get("profile") or cred.get("connection") or "",
            "warehouse_id": compute.get("warehouse_id", "") or c.get("warehouse", ""),
            "cluster_id": compute.get("cluster_id", ""), "catalog": c.get("catalog", "") or c.get("database", ""),
            "schema": c.get("schema", "")}


def _kind_for(conns: list[dict[str, Any]]) -> str:
    """The profile kind its connections make: none is local, one platform is that one, more is hybrid."""
    used = {c.get("platform") for c in conns}
    return "local" if not used else (used.pop() if len(used) == 1 else "hybrid")  # type: ignore[return-value]


def _new_id(existing: set[str]) -> str:
    import uuid
    while True:
        pid = "p-" + uuid.uuid4().hex[:12]
        if pid not in existing:
            return pid


class _Job:
    """A session start or one cell, running in its own thread; polls read it."""

    def __init__(self, status: str = "running") -> None:
        self.status = status
        self.result: dict[str, Any] = {}
        self.error = ""
        self.notes: list[str] = []
        self.started = time.monotonic()
        self.done = threading.Event()


def _with_notes(out: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """``out`` with ``notes`` (a session that couldn't be closed, say) when there are any."""
    return out | {"notes": list(notes)} if notes else out


class EnginesMixin:
    _engines_lock = threading.Lock()

    # ---- state

    def _engine_manager(self) -> SessionManager:
        with self._engines_lock:
            if getattr(self, "_engines", None) is None:
                self._engines = SessionManager(self._engine_adapter)
                self._engine_jobs: dict[str, _Job] = {}
                self._engine_cells: dict[str, _Job] = {}
                self._engine_choice: dict[str, dict[str, Any]] = {}
            return self._engines

    def _engine_adapter(self, engine_id: str) -> Any:
        if engine_id == registry.FABRIC_SPARK.id:
            from .engines.fabric_spark import FabricSparkAdapter
            from .auth import FABRIC_SCOPES
            refreshed = getattr(self, "_refreshed", None)  # Service's: a new token after a 401, when it can
            return FabricSparkAdapter(token=lambda: self.auth.fabric_token(),  # type: ignore[attr-defined]
                                      refresh=refreshed(FABRIC_SCOPES) if callable(refreshed) else None)
        return registry.adapter(engine_id)

    def _profiles(self) -> connprofiles.ProfileStore:
        from .service import data_dir
        root = Path(data_dir()) / "profiles"
        root.mkdir(parents=True, exist_ok=True)
        return connprofiles.ProfileStore(root)

    # ---- what there is

    def engine_catalog(self) -> dict[str, Any]:
        """Every engine, with what bills and whether it can run cells in this version."""
        out = registry.catalog()
        for e in out["engines"]:
            e["bills"] = BILLS.get(e.get("billing", "none"), "")
            e["kernel"] = e["id"] == KERNEL
        return out

    def engine_connections(self) -> dict[str, Any]:
        """Databricks connections on this PC: saved connection profiles, then the Databricks CLI's
        profiles. Hosts and compute IDs only; no secret ever leaves here."""
        found: list[dict[str, Any]] = []
        store = self._profiles()
        for p in store.list():
            try:
                profile = store.load(str(p["id"]))
            except connprofiles.ProfileError:
                continue
            for c in profile.get("connections") or []:
                if c.get("platform") != "databricks":
                    continue
                found.append(self._connection_row(f"profile:{profile['id']}:{c['id']}",
                                                  f"{profile.get('name') or profile['id']} · {c.get('label') or c['id']}",
                                                  "profile", c, profile.get("execution") or {}))
        cli = connprofiles.databricks_cli_profiles()
        for entry in cli.get("profiles") or []:
            if not entry.get("host"):
                continue
            conn = connprofiles.connection_from_cli_profile(entry)
            row = self._connection_row(f"cli:{entry['name']}", f"{entry['name']} (Databricks CLI)", "cli", conn, {})
            row["warning"] = entry.get("warning", "")
            found.append(row)
        return {"connections": found, "cli_config": cli.get("path", "")}

    @staticmethod
    def _connection_row(key: str, label: str, source: str, conn: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
        compute = conn.get("compute") or {}
        return {"key": key, "label": label, "source": source, "host": conn.get("host", ""),
                "warehouse_id": compute.get("warehouse_id", ""), "cluster_id": compute.get("cluster_id", ""),
                "catalog": conn.get("catalog", ""), "schema": conn.get("schema", ""),
                "remote_compute": execution.get("remote_compute", "ask")}

    def _connection(self, key: str) -> tuple[dict[str, Any], dict[str, Any], str]:
        """(connection, execution policy, profile name) for a key from engine_connections."""
        if key.startswith("cli:"):
            name = key[4:]
            entry = next((p for p in connprofiles.databricks_cli_profiles().get("profiles") or []
                          if p.get("name") == name), None)
            if entry is None:
                raise ValueError(f"There's no Databricks CLI profile {name!r} on this PC.")
            return connprofiles.connection_from_cli_profile(entry), {}, f"cli:{name}"
        parts = key.split(":", 2)
        if len(parts) != 3 or parts[0] != "profile":
            raise ValueError("Pick a connection from the list.")
        profile = self._profiles().load(parts[1])
        conn = next((c for c in profile.get("connections") or [] if c.get("id") == parts[2]), None)
        if conn is None:
            raise ValueError(f"The profile {profile.get('name')!r} has no connection {parts[2]!r}.")
        return conn, profile.get("execution") or {}, str(profile.get("name") or profile["id"])

    # ---- Settings > Connections (Preview)

    def engine_profiles(self) -> dict[str, Any]:
        """The saved profiles with their connections, which one is active, and the Databricks CLI's
        profiles and Snowflake CLI's connections that could be added. References only: hosts,
        accounts, compute IDs and CLI names. Read-only and offline."""
        store = self._profiles()
        active = store.active()
        rows: list[dict[str, Any]] = []
        listed = store.list()
        if not any(p.get("id") == "default" for p in listed):
            listed.insert(0, {"id": "default"})
        for p in listed:
            pid = str(p.get("id") or "")
            try:
                profile = store.load(pid)
            except connprofiles.ProfileError as exc:
                rows.append({"id": pid, "name": str(p.get("name") or pid), "kind": "", "active": pid == active,
                             "connections": [], "problems": exc.problems})
                continue
            rows.append({"id": profile["id"], "name": profile.get("name") or profile["id"], "kind": profile.get("kind", ""),
                         "active": profile["id"] == active, "problems": [],
                         "connections": [_connection_view(profile["id"], c) for c in profile.get("connections") or []]})
        databricks = connprofiles.databricks_cli_profiles()
        snowflake = connprofiles.snowflake_cli_connections()
        return {"profiles": rows, "active": active,
                "databricks_cli": {k: databricks.get(k) for k in ("path", "found", "profiles", "error") if k in databricks},
                "snowflake_cli": {k: snowflake.get(k) for k in ("path", "found", "default", "connections", "notes", "error")
                                  if k in snowflake}}

    def _discovery_client(self, connection: dict[str, Any]) -> Any:
        from .engines import databricks_auth, databricks_sql
        return databricks_sql.Client(databricks_auth.provider_for(connection))

    def engine_discover(self, connection: str, catalogs: bool = True) -> dict[str, Any]:
        """What a Databricks workspace offers you: who you're signed in as, warehouses, clusters and
        whether Databricks Connect can use each, and Unity Catalog. Every request is a GET, so
        nothing starts and nothing bills DBUs. ``connection`` is a key from engine_connections."""
        from .engines import databricks_auth, databricks_discovery
        conn, _, _ = self._connection(connection)
        try:
            client = self._discovery_client(conn)
        except databricks_auth.DatabricksAuthError as exc:
            return {"schema": databricks_discovery.SCHEMA, "host": conn.get("host", ""), "signed_in": False,
                    "parts": {}, "suggested": {"warehouse_id": "", "cluster_id": ""},
                    "error": redact.secrets(str(exc))}
        return redact.walk(databricks_discovery.Discovery(client, catalogs=bool(catalogs)).run())

    def _runs_reader(self, connection: str) -> Any:
        from .engines import databricks_jobs
        conn, _, _ = self._connection(connection)
        return databricks_jobs.Runs(self._discovery_client(conn))

    def databricks_runs(self, connection: str, days: int = 1, mine: bool = True) -> dict[str, Any]:
        """Job runs and Lakeflow pipeline updates of the last ``days`` days, newest first, from the
        workspace of ``connection`` (a key from engine_connections). ``mine`` keeps the signed-in
        person's own. Every request is a GET: nothing starts and nothing bills DBUs."""
        from .engines import databricks_auth, databricks_discovery, databricks_sql
        try:
            reader = self._runs_reader(connection)
            who = ""
            if mine:
                me = databricks_discovery._object(reader.client.get("/api/2.0/preview/scim/v2/Me"), "the signed-in user")
                who = str(me.get("userName") or "")
            out = reader.list(days=int(days), mine=who)
        except databricks_auth.DatabricksAuthError as exc:
            return {"signed_in": False, "error": redact.secrets(str(exc)), "rows": []}
        except (databricks_sql.DatabricksError, databricks_discovery.OddReply) as exc:
            return {"signed_in": True, "error": "Couldn't read who you're signed in as: " + redact.secrets(str(exc)), "rows": []}
        return redact.walk(out | {"user": who})

    def databricks_run(self, connection: str, run_id: str) -> dict[str, Any]:
        """One job run with its tasks, the failed ones' errors and notebook paths, and its parameters."""
        return redact.walk(self._runs_reader(connection).run(str(run_id)))

    def _billing_root(self) -> Path:
        from .service import data_dir
        return Path(data_dir()) / "databricks-billing"

    def databricks_billing(self, connection: str, warehouse_id: str = "", days: int = 7,
                           confirmed: bool = False) -> dict[str, Any]:
        """DBUs billed in the last ``days`` days by job, pipeline, warehouse and cluster. Without
        ``confirmed`` nothing runs: the answer says which warehouse would run the query and its state
        (a GET). With it, the query runs on that warehouse under a confirmed authorization."""
        from .engines import databricks_auth, databricks_billing, databricks_discovery, databricks_sql
        conn, _, _ = self._connection(connection)
        warehouse = warehouse_id or str((conn.get("compute") or {}).get("warehouse_id") or "")
        if not warehouse:
            raise ValueError("Pick a SQL warehouse to read billing with.")
        if not _COMPUTE_ID.fullmatch(warehouse):
            raise ValueError("That warehouse id isn't one Databricks gives (letters, digits, - . and _).")
        databricks_billing.query(int(days))   # refuses a number of days it doesn't offer
        if not confirmed:
            try:
                info = databricks_discovery.warehouse_row(self._discovery_client(conn).warehouse(warehouse))
            except (databricks_auth.DatabricksAuthError, databricks_sql.DatabricksError, databricks_discovery.OddReply) as exc:
                return {"needs_confirm": True, "warehouse": {"id": warehouse}, "error": redact.secrets(str(exc))}
            return {"needs_confirm": True, "warehouse": info}
        signer = getattr(self, "_auth", None)
        who = ""
        if signer is not None and hasattr(signer, "signed_in_user"):
            who = signer.signed_in_user() or ""
        authorization = auth.confirmed(registry.DATABRICKS_SQL.id, warehouse, who or "this PC's user")
        try:
            result = databricks_billing.read(self._engine_adapter(registry.DATABRICKS_SQL.id), conn, warehouse,
                                             int(days), authorization)
        except databricks_auth.DatabricksAuthError as exc:
            return {"ok": False, "error": redact.secrets(str(exc))}
        if result.get("ok"):
            databricks_billing.save(self._billing_root(), str(conn.get("host") or ""), result)
        return redact.walk(result)

    def databricks_billing_last(self, connection: str) -> dict[str, Any]:
        """The last billing read for this connection's workspace, kept on this PC; empty when none."""
        from .engines import databricks_billing
        conn, _, _ = self._connection(connection)
        return databricks_billing.last(self._billing_root(), str(conn.get("host") or "")) or {}

    def engine_connection_save(self, profile: str = "", profile_name: str = "", databricks_cli: str = "",
                               snowflake_cli: str = "", warehouse_id: str = "", cluster_id: str = "",
                               catalog: str = "", schema: str = "", label: str = "", replace: str = "") -> dict[str, Any]:
        """Add a connection to ``profile`` (or a new profile called ``profile_name``) that signs in
        with a Databricks CLI profile or a Snowflake CLI connection, named here and read from the
        CLI's own file: its host or account comes from there, never from the app. ``replace`` names
        a connection of the profile to change instead. The profile is validated, so no value that
        looks like a secret is ever written."""
        if bool(databricks_cli) == bool(snowflake_cli):
            raise ValueError("Pick one Databricks CLI profile or one Snowflake CLI connection.")
        for what, value in (("warehouse", warehouse_id), ("cluster", cluster_id)):
            if value and not _COMPUTE_ID.fullmatch(value):
                raise ValueError(f"That {what} id isn't one Databricks gives (letters, digits, - . and _).")
        for what, value in (("catalog", catalog), ("schema", schema), ("label", label)):
            if value and not _NAME_TEXT.fullmatch(value):
                raise ValueError(f"The {what} must be one line of up to 255 characters.")
        if databricks_cli:
            entry = next((p for p in connprofiles.databricks_cli_profiles().get("profiles") or []
                          if p.get("name") == databricks_cli), None)
            if entry is None or not entry.get("host"):
                raise ValueError(f"There's no Databricks CLI profile {databricks_cli!r} with a workspace on this PC.")
            conn = connprofiles.connection_from_cli_profile(entry)
            conn["compute"] = {"warehouse_id": warehouse_id, "cluster_id": cluster_id, "serverless": False}
            conn |= {"catalog": catalog, "schema": schema}
        else:
            entry = next((c for c in connprofiles.snowflake_cli_connections().get("connections") or []
                          if c.get("name") == snowflake_cli), None)
            if entry is None:
                raise ValueError(f"There's no Snowflake CLI connection {snowflake_cli!r} on this PC.")
            conn = connprofiles.connection_from_cli_connection(entry)
        conn["label"] = label
        store = self._profiles()
        if profile:
            data = store.load(profile)
        else:
            if not profile_name.strip():
                raise ValueError("Name the new profile.")
            existing = {str(p.get("id")) for p in store.list()}
            data = {"id": _new_id(existing), "name": profile_name.strip(), "kind": "local", "connections": []}
        if replace and not any(c.get("id") == replace for c in data.get("connections") or []):
            raise ValueError(f"The profile has no connection {replace!r} to change.")
        conns = [c for c in data.get("connections") or [] if c.get("id") != replace] if replace else \
            list(data.get("connections") or [])
        taken = {str(c.get("id")) for c in conns}
        base, n = conn["id"], 2
        while conn["id"] in taken:
            conn["id"] = f"{base[:36]}-{n}"
            n += 1
        conns.append(conn)
        data["connections"] = conns
        data["kind"] = _kind_for(conns)
        if data.get("validation"):
            data["validation"] = {"status": "not_checked", "checked_at": None, "problems": []}
        saved = store.save(data)
        return {"profile": saved["id"], "connection": conn["id"], "key": f"profile:{saved['id']}:{conn['id']}"}

    def engine_connection_remove(self, profile: str, connection: str) -> dict[str, Any]:
        """Take one connection out of a profile. The CLI's own profile or connection stays as it is."""
        store = self._profiles()
        data = store.load(profile)
        conns = [c for c in data.get("connections") or [] if c.get("id") != connection]
        if len(conns) == len(data.get("connections") or []):
            raise ValueError(f"The profile has no connection {connection!r}.")
        data["connections"] = conns
        data["kind"] = _kind_for(conns)
        for key in ("defaults",):
            if (data.get(key) or {}).get("connection") == connection:
                data[key]["connection"] = ""
        for section in ("environments", "deploy"):
            for mapping in (data.get(section) or {}).values():
                if isinstance(mapping, dict):
                    mapping.pop(connection, None)
        store.save(data)
        return {"profile": profile, "removed": connection}

    # ---- before choosing

    def _engine_target(self, engine: str, connection: str, workspace_id: str, lakehouse_id: str,
                       lakehouse_name: str) -> dict[str, Any]:
        """Where the engine runs, and the options its session opens with."""
        if engine == registry.FABRIC_SPARK.id:
            if not (workspace_id and lakehouse_id):
                raise ValueError("Attach a lakehouse to this notebook first: Fabric Spark runs on its workspace.")
            return {"target": f"{workspace_id}/{lakehouse_id}", "name": lakehouse_name or lakehouse_id,
                    "options": {"workspace_id": workspace_id, "lakehouse_id": lakehouse_id}, "policy": {},
                    "profile": "", "host": ""}
        conn, policy, profile = self._connection(connection) if connection else ({}, {}, "")
        if not conn:
            raise ValueError("Pick a Databricks connection first.")
        compute = conn.get("compute") or {}
        key = "warehouse_id" if engine == registry.DATABRICKS_SQL.id else "cluster_id"
        target = str(compute.get(key) or "")
        if not target:
            what = "SQL warehouse" if key == "warehouse_id" else "cluster"
            raise ValueError(f"The connection names no {what}. Add its ID to the connection (or the CLI profile's "
                             f"{key}).")
        return {"target": target, "name": target, "options": {"connection": conn, key: target},
                "policy": policy, "profile": profile, "host": conn.get("host", "")}

    def _engine_read_target(self, engine: str, t: dict[str, Any]) -> dict[str, Any]:
        """The warehouse's or cluster's name and state. Reading never starts it."""
        from .engines.databricks_auth import provider_for
        from .engines.databricks_sql import Client
        client = Client(provider_for(t["options"]["connection"]))
        if engine == registry.DATABRICKS_SQL.id:
            w = client.warehouse(t["target"])
            return {"name": w.get("name") or t["target"], "state": w.get("state", ""),
                    "auto_stop": w.get("auto_stop_mins")}
        c = client.cluster(t["target"])
        return {"name": c.get("cluster_name") or t["target"], "state": c.get("state", ""),
                "auto_stop": c.get("autotermination_minutes")}

    def engine_prepare(self, notebook: str, engine: str, connection: str = "", workspace_id: str = "",
                       lakehouse_id: str = "", lakehouse_name: str = "", cells: list[str] | None = None) -> dict[str, Any]:
        """What choosing ``engine`` for ``notebook`` would do. Starts nothing and changes nothing."""
        desc = registry.get(engine)
        if not desc.adapter:
            raise ValueError(f"{desc.label} can't run cells in this version. {desc.planned}")
        mgr = self._engine_manager()
        current = mgr.engine_of(notebook) or KERNEL
        transition = {"needed": False, "loses": [], "decisions": [], "message": ""}
        if current != engine and current != KERNEL:
            transition = mgr.transition(notebook, engine) if engine != KERNEL else {
                "needed": True, "loses": [f"variables and tables in the {registry.get(current).label} session"],
                "decisions": ["close"], "message": ""}
        elif current == KERNEL and engine != KERNEL:
            transition = {"needed": True, "decisions": ["close", "keep"], "message": "",
                          "loses": ["nothing: the Python session on this PC stays open, and comes back when you "
                                    "switch back"]}
        out: dict[str, Any] = {"engine": desc.as_dict() | {"bills": BILLS.get(desc.billing, "")}, "from": current,
                               "transition": transition, "confirm": False, "target": None, "cells": []}
        for i, source in enumerate(cells or []):
            req = cell_request(source)
            verdict = check_cell(desc, req.language, req.dialect)
            if not verdict["ok"] or verdict["level"] in ("simulated", "translated"):
                out["cells"].append({"index": i, "language": req.language, **verdict})
        if not desc.remote:
            return out
        t = self._engine_target(engine, connection, workspace_id, lakehouse_id, lakehouse_name)
        policy = t["policy"]
        mode = policy.get("remote_compute", "ask")
        out["policy"] = {"mode": mode, "profile": t["profile"]}
        out["confirm"] = mode != "policy" or engine not in (policy.get("allowed_remote_engines") or [])
        out["target"] = {"id": t["target"], "name": t["name"], "host": t["host"], "state": ""}
        out["grant_minutes"] = auth.CONFIRM_MINUTES
        out["risk"] = self._engine_risk(engine, desc, t, out, cells or [])
        return out

    def _engine_risk(self, engine: str, desc: Any, t: dict[str, Any], out: dict[str, Any],
                     cells: list[str]) -> dict[str, Any]:
        """Advice only: never blocks. A part that can't be read says "couldn't run"."""
        lines: list[dict[str, str]] = []
        if out["policy"]["mode"] == "never":
            lines.append({"outcome": "warning", "text": f"The profile {t['profile']!r} doesn't allow remote compute, "
                                                        "so the session will be refused."})
        if engine == registry.FABRIC_SPARK.id:
            from . import app_settings
            if not app_settings.value("allow_fabric_runs"):
                lines.append({"outcome": "warning", "text": "Your organization's policy doesn't allow runs in "
                                                            "Fabric from KratosForge, so the session will be refused."})
            lines.append({"outcome": "info", "text": "A Spark session uses Fabric capacity from its start until it "
                                                     "closes or times out."})
        else:
            try:
                state = self._engine_read_target(engine, t)
                out["target"].update(name=state["name"], state=state["state"])
                what = "warehouse" if engine == registry.DATABRICKS_SQL.id else "cluster"
                if state["state"] not in ("RUNNING", "STARTING", "PENDING", "RESIZING"):
                    lines.append({"outcome": "info", "text": f"The {what} is {state['state'].lower() or 'stopped'}, so "
                                                             "it will start and bill DBUs."})
                if not state["auto_stop"]:
                    lines.append({"outcome": "warning", "text": f"The {what} has auto-stop turned off, so it keeps "
                                                                "billing DBUs until someone stops it."})
                else:
                    lines.append({"outcome": "info", "text": f"It stops by itself after {state['auto_stop']} idle "
                                                             "minutes."})
            except Exception as exc:  # noqa: BLE001 - advice that can't be read says so
                # Only the HTTP status or the error's kind: an error's text can quote a request.
                status = re.search(r"HTTP \d{3}", str(exc))
                lines.append({"outcome": "unknown", "text": "Couldn't run: couldn't read the "
                              f"{'warehouse' if engine == registry.DATABRICKS_SQL.id else 'cluster'} "
                              f"({status.group(0) if status else type(exc).__name__})."})
        writes = [i for i, s in enumerate(cells) if _WRITES.search(_MAGIC.sub("", s.lstrip(), count=1))]
        if writes:
            lines.append({"outcome": "warning", "text": f"{len(writes)} cell{'s' if len(writes) != 1 else ''} may "
                                                        f"change data on {out['target']['name']} (cell "
                                                        f"{', '.join(str(i + 1) for i in writes[:5])})."})
        else:
            lines.append({"outcome": "info", "text": "No cell looks like it writes data."})
        if out["cells"]:
            lines.append({"outcome": "warning", "text": f"{len(out['cells'])} cell"
                          f"{'s' if len(out['cells']) != 1 else ''} can't run on {desc.label} or run translated."})
        outcomes = {x["outcome"] for x in lines}
        result = "warning" if "warning" in outcomes else ("unknown" if "unknown" in outcomes else "pass")
        return {"outcome": result, "lines": lines}

    # ---- choosing

    def engine_select(self, notebook: str, engine: str, decision: str | None = None, confirmed: bool = False,
                      connection: str = "", workspace_id: str = "", lakehouse_id: str = "",
                      lakehouse_name: str = "") -> dict[str, Any]:
        """Make ``engine`` the notebook's engine. Its session opens in the background: poll engine_state."""
        mgr = self._engine_manager()
        if engine == KERNEL:
            current = mgr.engine_of(notebook)
            if current is not None and decision != "close":
                raise SwitchNeeded({"message": "Switching back closes the session.", "decisions": ["close"]})
            mgr.close(notebook)
            self._engine_choice.pop(notebook, None)
            self._engine_jobs.pop(notebook, None)
            return _with_notes({"engine": KERNEL, "status": "ready"}, mgr.notes(notebook))
        desc = registry.get(engine)
        if not desc.adapter:
            raise ValueError(f"{desc.label} can't run cells in this version. {desc.planned}")
        authorization = None
        config_options: dict[str, Any] = {}
        target = ""
        choice: dict[str, Any] = {"engine": engine, "label": desc.label, "remote": desc.remote,
                                  "bills": BILLS.get(desc.billing, ""), "target": None}
        if desc.remote:
            t = self._engine_target(engine, connection, workspace_id, lakehouse_id, lakehouse_name)
            target, config_options = t["target"], t["options"]
            authorization = auth.from_policy(t["policy"], t["profile"], engine, target) if t["policy"] else None
            if authorization is None:
                if not confirmed:
                    raise PermissionError(f"{desc.label} runs on remote compute that may be billed. Confirm it first.")
                who = ""
                signer = getattr(self, "_auth", None)
                if signer is not None:
                    who = signer.signed_in_user() or ""
                authorization = auth.confirmed(engine, target, who or "this PC's user")
            choice["target"] = {"id": target, "name": t["name"], "host": t["host"]}
            choice["expires_at"] = authorization.as_dict()["expires_at"]
            choice["reason"] = authorization.reason
        current = mgr.engine_of(notebook)
        if current is not None and current != engine:
            t = mgr.transition(notebook, engine)
            if t["needed"] and decision not in t["decisions"]:
                raise SwitchNeeded(t)
        job = _Job("starting")
        self._engine_jobs[notebook] = job
        self._engine_choice[notebook] = choice | {"status": "starting"}

        def start() -> None:
            try:
                mgr.switch(notebook, engine, decision if current not in (None, engine) else None,
                           SessionConfig(notebook, options=config_options), target, authorization)
                job.notes = mgr.notes(notebook)
                job.status = "ready"
            except Exception as exc:  # noqa: BLE001 - the person reads it from engine_state
                job.status, job.error = "error", redact.secrets(str(exc))
            finally:
                job.done.set()

        threading.Thread(target=start, daemon=True, name=f"engine-start-{notebook}").start()
        return {"engine": engine, "status": "starting"}

    def engine_state(self, notebook: str) -> dict[str, Any]:
        """The notebook's engine and whether its session is starting, ready or failed."""
        self._engine_manager()
        choice = self._engine_choice.get(notebook)
        if not choice:
            return {"engine": KERNEL, "status": "ready"}
        job = self._engine_jobs.get(notebook)
        status = job.status if job else "ready"
        out = dict(choice) | {"status": status}
        if job and job.error:
            out["error"] = job.error
        notes: list[str] = []
        if job and job.notes:
            notes, job.notes = job.notes, []    # said once, not on every poll
        return _with_notes(out, notes)

    # ---- running cells

    def engine_start_cell(self, notebook: str, source: str) -> dict[str, Any]:
        """Run one cell on the notebook's engine, in the background: poll engine_poll_cell."""
        mgr = self._engine_manager()
        state = self.engine_state(notebook)
        if state["engine"] == KERNEL:
            raise ValueError("This notebook runs on Python (this PC); its cells run in the kernel.")
        if state["status"] != "ready":
            raise ValueError(state.get("error") or f"{state['label']} is still starting.")
        running = self._engine_cells.get(notebook)
        if running and running.status == "running":
            raise RuntimeError("A cell is already running on this engine. Wait for it, or Cancel it.")
        req = cell_request(source)
        verdict = mgr.check(notebook, req)
        if not verdict["ok"]:
            return {"status": "unsupported", "reason": verdict["reason"], "engine": state["engine"]}
        job = _Job()
        self._engine_cells[notebook] = job

        def run() -> None:
            try:
                job.result = mgr.execute(notebook, req).as_dict()
                job.status = job.result["status"]
            except Exception as exc:  # noqa: BLE001 - the cell shows it as its error
                job.status = "error"
                job.result = {"status": "error", "outputs": [{"type": "error", "ename": type(exc).__name__,
                                                              "evalue": redact.secrets(str(exc))}],
                              "seconds": time.monotonic() - job.started, "rows": None, "engine": state["engine"]}
            finally:
                job.done.set()

        threading.Thread(target=run, daemon=True, name=f"engine-cell-{notebook}").start()
        return {"status": "running", "engine": state["engine"]}

    def engine_poll_cell(self, notebook: str, wait: float = 0.4) -> dict[str, Any]:
        self._engine_manager()
        job = self._engine_cells.get(notebook)
        if job is None:
            return {"status": "idle"}
        job.done.wait(timeout=max(0.0, min(float(wait), 5.0)))
        if job.status == "running":
            return {"status": "running", "seconds": round(time.monotonic() - job.started, 1)}
        return dict(job.result)

    def engine_cancel(self, notebook: str) -> dict[str, Any]:
        return {"cancelled": self._engine_manager().cancel(notebook)}

    def engine_close(self, notebook: str) -> dict[str, Any]:
        """Close the notebook's engine sessions (kept ones too); it goes back to Python (this PC)."""
        mgr = self._engine_manager()
        mgr.close(notebook)
        self._engine_choice.pop(notebook, None)
        self._engine_jobs.pop(notebook, None)
        return _with_notes({"engine": KERNEL, "status": "ready"}, mgr.notes(notebook))
