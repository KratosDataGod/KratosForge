"""Fabric Excellence Assessment of one workspace (FEA-105, engine call only, no screen yet).

``excellence_assess`` reads only, with the signed-in person's own access, and nothing new: the
optimization engine's reads (``optimization_assess``: the workspace, its items, notebook and pipeline
definitions, job history), the workspace's role assignments, the capacities the person can see, and
each lakehouse's properties (``GET .../lakehouses/{id}``, the read the app makes before writing a
table, for the architecture controls; at most ``MAX_LAKEHOUSE_READS`` per assessment).
Notebook and pipeline definitions are inspected while the engine has them (excellence.inspect_items):
the evidence keeps the rules that matched and where, never the code.
A read that fails is reported in ``reads`` and leaves the controls that need it *not evaluated*;
it never stops the assessment and never counts as a pass. No Fabric job starts and nothing is
written to Fabric, OneLake or this PC.

``demo=True`` assesses a labelled demonstration fixture with no Fabric call. See
docs/optimize/excellence-assessment.md.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import delta_profile, maintenance, xray
from .excellence import adapters, assess, catalog, catalog_architecture, catalog_performance, inspect_items, profiles
from .auth import STORAGE_SCOPES
from .onelake import FileStore, TableRef, read_storage_options
from .opportunities_service import _guid
from .redact import describe

METHODS = {"excellence_assess"}
MAX_LAKEHOUSE_READS = 50  # lakehouse properties read per assessment; the rest are not evaluated, and reads says so
MAX_TABLE_READS = 100  # Delta logs read per assessment; the lakehouses with tables past it aren't passed
TABLE_READ_WORKERS = 4
FIXTURE_ROLES = [
    {"role": "Admin", "principal": {"id": "demo-admin-1", "type": "Group", "displayName": "Sales platform admins"}},
    {"role": "Admin", "principal": {"id": "demo-app-1", "type": "ServicePrincipal", "displayName": "orders-sync app"}},
    {"role": "Contributor", "principal": {"id": "demo-guest-1", "type": "User", "displayName": "Partner analyst",
                                          "userDetails": {"userPrincipalName": "analyst_partner.example#EXT#@demo.example"}}},
    {"role": "Member", "principal": {"id": "demo-eng-1", "type": "Group", "displayName": "Sales data engineers"}},
    {"role": "Viewer", "principal": {"id": "demo-view-1", "type": "Group", "displayName": "Sales readers"}},
]
# The demonstration fixture's lakehouses (LH_Bronze, LH_Silver), as GET .../lakehouses/{id} would give them.
FIXTURE_LAKEHOUSES: dict[str, dict[str, Any] | None] = {
    "f1f1f1f1-0000-4000-8000-000000000301": {"displayName": "LH_Bronze", "properties": {"defaultSchema": "dbo"}},
    "f1f1f1f1-0000-4000-8000-000000000302": {"displayName": "LH_Silver", "properties": {}},
}


def _fixture_table(name: str, files: int, avg_mb: float, since_vacuum: int, features: list[str] | None = None,
                   vacuumed: bool = True) -> dict[str, Any]:
    avg = int(avg_mb * 1024 * 1024)
    return catalog_performance.table_facts(name, {"exists": True, "files": files, "bytes": files * avg,
                                                  "avg_file_bytes": avg, "versions_since_vacuum": since_vacuum,
                                                  "last_vacuum": "2026-10-01T02:00:00+00:00" if vacuumed else None},
                                           features or [])


# The demonstration fixture's tables, as the Delta logs would give them. Made up, like the rest of the
# fixture: LH_Bronze has small files and a VACUUM backlog, LH_Silver a table the SQL endpoint can't read.
FIXTURE_TABLES: dict[str, dict[str, Any] | None] = {
    "f1f1f1f1-0000-4000-8000-000000000301": {"tables": [
        _fixture_table("dbo.orders_raw", 1840, 0.6, 212, vacuumed=False),
        _fixture_table("dbo.customers_raw", 12, 48.0, 9)]},
    "f1f1f1f1-0000-4000-8000-000000000302": {"tables": [
        _fixture_table("dbo.orders", 24, 180.0, 6, [delta_profile.DELETION_VECTORS]),
        _fixture_table("dbo.customer_notes", 8, 64.0, 3, [delta_profile.CM_ID])]},
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _items(opt: dict[str, Any]) -> list[dict[str, Any]] | None:
    ws = (opt.get("workspaces") or [{}])[0]
    if ws.get("problem"):
        return None
    return [{"id": w["item_id"], "type": w["type"], "displayName": w.get("name")} for w in opt.get("workloads") or []]


def _lakehouses(fabric: Any, wid: str, items: list[dict[str, Any]] | None,
                reads: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """Each lakehouse's properties, read one by one. A lakehouse that couldn't be read (or is past
    MAX_LAKEHOUSE_READS) has no answer, so its controls are not evaluated; ``reads`` says how many."""
    lakehouses = [i for i in items or [] if i.get("type") == "Lakehouse"]
    if not lakehouses:
        return {}
    got: dict[str, dict[str, Any] | None] = {}
    problems: list[str] = []
    for lh in lakehouses[:MAX_LAKEHOUSE_READS]:
        try:
            got[str(lh["id"])] = fabric.get_lakehouse(wid, lh["id"])
        except Exception as exc:  # noqa: BLE001 - that lakehouse's controls are then not evaluated
            problems.append(f"{lh.get('displayName') or lh['id']} ({describe(exc)})")
    left = len(lakehouses) - MAX_LAKEHOUSE_READS
    notes = []
    if problems:
        notes.append(f"{len(problems)} of {len(lakehouses)} couldn't be read: " + "; ".join(problems[:3])
                     + (f" and {len(problems) - 3} more" if len(problems) > 3 else "") + ".")
    if left > 0:
        notes.append(f"{left} past the first {MAX_LAKEHOUSE_READS} weren't read.")
    reads.append({"read": "lakehouse properties", "ok": not notes, "problem": " ".join(notes) or None})
    return got


class OneLakeTables:
    """The Estate X-ray's table reads, for one assessment: a lakehouse's folders, and one Delta
    table's log (maintenance.health and delta_profile.features_used on the same loaded log)."""

    def __init__(self, token: str):
        self.files = FileStore(lambda: token)
        self.options = read_storage_options(token)

    def list(self, workspace_id: str, lakehouse_id: str, directory: str) -> list[dict[str, Any]]:
        return self.files.list(workspace_id, lakehouse_id, directory)

    def read(self, workspace_id: str, lakehouse_id: str, schema: str | None,
             table: str) -> tuple[dict[str, Any], list[str]]:
        from deltalake import DeltaTable
        from deltalake.exceptions import TableNotFoundError

        uri = TableRef(workspace_id, lakehouse_id, schema or "dbo", table, flat=schema is None).uri
        try:
            loaded = DeltaTable(uri, storage_options=self.options)
        except TableNotFoundError:
            return {"exists": False}, []
        return maintenance.health(uri, self.options, table=loaded), delta_profile.features_used(loaded)


def _table_names(store: Any, wid: str, lh: str) -> list[tuple[str | None, str]]:
    """(schema, table) for every folder under Tables that is or should be a table, as X-ray finds them:
    a folder with a _delta_log is a table of a lakehouse without schemas, any other is a schema."""
    out: list[tuple[str | None, str]] = []
    for top in [e for e in store.list(wid, lh, "Tables") if e["is_dir"]]:
        inner = store.list(wid, lh, f"Tables/{top['name']}")
        if any(e["name"] == "_delta_log" for e in inner):
            out.append((None, top["name"]))
        else:
            out += [(top["name"], e["name"]) for e in inner if e["is_dir"]]
    return out


def _summary(problems: list[str], total: int, what: str) -> str:
    return (f"{len(problems)} of {total} {what} couldn't be read: " + "; ".join(problems[:3])
            + (f" and {len(problems) - 3} more" if len(problems) > 3 else "") + ".")


class ExcellenceMixin:
    def _excellence_onelake(self) -> Any:
        """The reader for the lakehouses' tables, with the person's own storage sign-in. Silent only:
        an assessment never shows a sign-in prompt, so without a storage sign-in no table is read."""
        token = self.auth.silent_token(STORAGE_SCOPES)  # type: ignore[attr-defined]
        if not token:
            raise PermissionError("no OneLake sign-in yet; open a lakehouse's tables once, then assess again")
        return OneLakeTables(token)

    def _table_layout(self, wid: str, items: list[dict[str, Any]] | None,
                      reads: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
        """Each lakehouse's tables, as catalog_performance.table_layout takes them. A lakehouse whose
        Tables folder couldn't be listed has no answer (not evaluated); a table whose log couldn't be
        read, or past MAX_TABLE_READS, is listed as not read. ``reads`` says what went wrong."""
        lakehouses = [i for i in items or [] if i.get("type") == "Lakehouse"]
        if not lakehouses:
            return {}
        try:
            store = self._excellence_onelake()
        except Exception as exc:  # noqa: BLE001 - the table controls are then not evaluated
            reads.append({"read": "table layout", "ok": False,
                          "problem": f"OneLake couldn't be reached ({describe(exc)}), so no table was read."})
            return {}
        got: dict[str, dict[str, Any] | None] = {}
        listed: list[tuple[str, str | None, str]] = []
        problems: list[str] = []
        for lh in lakehouses[:MAX_LAKEHOUSE_READS]:
            lid, name = str(lh["id"]), str(lh.get("displayName") or lh["id"])
            if xray.is_staging(name):
                got[lid] = {"tables": [], "staging": True}
                continue
            try:
                names = _table_names(store, wid, lid)
            except Exception as exc:  # noqa: BLE001 - that lakehouse's controls are then not evaluated
                problems.append(f"{name} ({describe(exc)})")
                continue
            got[lid] = {"tables": [], "past_budget": 0}
            listed += [(lid, schema, table) for schema, table in names]

        def read(entry: tuple[str, str | None, str]) -> tuple[str, dict[str, Any]]:
            lid, schema, table = entry
            label = f"{schema}.{table}" if schema else table
            try:
                health, features = store.read(wid, lid, schema, table)
                return lid, catalog_performance.table_facts(label, health, features)
            except Exception as exc:  # noqa: BLE001 - that table is listed as not read
                return lid, catalog_performance.table_facts(label, None, None, error=describe(exc))

        with ThreadPoolExecutor(max_workers=TABLE_READ_WORKERS) as pool:
            done = list(pool.map(read, listed[:MAX_TABLE_READS]))
        for lid, facts in done:
            got[lid]["tables"].append(facts)  # type: ignore[index]
        for lid, _, _ in listed[MAX_TABLE_READS:]:
            got[lid]["past_budget"] += 1  # type: ignore[index]
        notes = []
        if problems:
            notes.append(_summary(problems, min(len(lakehouses), MAX_LAKEHOUSE_READS), "lakehouses' Tables folders"))
        unread = [f["name"] for _, f in done if not f["read"]]
        if unread:
            notes.append(f"{len(unread)} of {len(done)} table logs couldn't be read.")
        if len(listed) > MAX_TABLE_READS:
            notes.append(f"{len(listed) - MAX_TABLE_READS} tables past the first {MAX_TABLE_READS} weren't read.")
        if len(lakehouses) > MAX_LAKEHOUSE_READS:
            notes.append(f"{len(lakehouses) - MAX_LAKEHOUSE_READS} lakehouses past the first {MAX_LAKEHOUSE_READS} "
                         "weren't read.")
        reads.append({"read": "table layout", "ok": not notes, "problem": " ".join(notes) or None,
                      "tables": len(done)})
        return got

    def excellence_assess(self, workspace_id: str = "", days: int = 30, profile: str = profiles.DEFAULT,
                          demo: bool = False) -> dict[str, Any]:
        """Assess one workspace (and its capacity, when you can see it) against the excellence controls.

        Reads only. ``profile`` is a built-in profile id. With ``demo`` a labelled fixture is assessed."""
        prof = profiles.get(str(profile or profiles.DEFAULT))
        at = _now()
        reads: list[dict[str, Any]] = []
        info_read = True
        if demo:
            opt = self._optimization_assess(demo=True, inspect=inspect_items.inspect)  # type: ignore[attr-defined]
            ws_raw = dict(opt["workspaces"][0])
            ws = {"id": ws_raw["id"], "displayName": ws_raw["name"], "capacityId": ws_raw["capacity_id"],
                  "description": "Sales reporting: orders, customers and products (demonstration fixture)."}
            assignments: list[dict[str, Any]] | None = FIXTURE_ROLES
            capacities: list[dict[str, Any]] | None = [{"id": ws["capacityId"], "displayName": "Demo F64",
                                                        "sku": "F64", "state": "Active", "region": "West Europe"}]
            at = str(opt.get("assessed_at") or at)
            lakehouses = dict(FIXTURE_LAKEHOUSES)
            tables = dict(FIXTURE_TABLES)
        else:
            wid = _guid(workspace_id, "workspace")
            fabric = self.fabric  # type: ignore[attr-defined]
            info_read = True
            try:
                got = fabric.get_workspace(wid)
                ws = {**got, "id": got.get("id") or wid}
                reads.append({"read": "workspace settings", "ok": True, "problem": None})
            except Exception as exc:  # noqa: BLE001 - its controls are then not evaluated
                ws, info_read = {"id": wid}, False
                reads.append({"read": "workspace settings", "ok": False,
                              "problem": f"Couldn't be read ({describe(exc)}); its capacity isn't known either."})
            try:
                opt = self._optimization_assess(workspace_id=wid, days=days,  # type: ignore[attr-defined]
                                                inspect=inspect_items.inspect)
            except Exception as exc:  # noqa: BLE001 - its controls are then not evaluated
                opt = {"workspaces": [{"id": wid, "problem": f"Couldn't be read ({describe(exc)})."}],
                       "workloads": [], "opportunities": [], "scope": {}}
            reads.append({"read": "items, definitions and job history", "ok": not opt["workspaces"][0].get("problem"),
                          "problem": opt["workspaces"][0].get("problem"), "budget": opt["scope"].get("budget")})
            try:
                assignments = fabric.list_role_assignments(wid)
                reads.append({"read": "role assignments", "ok": True, "problem": None})
            except Exception as exc:  # noqa: BLE001 - the role controls are then not evaluated
                assignments = None
                reads.append({"read": "role assignments", "ok": False,
                              "problem": f"Couldn't be read ({describe(exc)}). Listing roles needs a workspace role "
                                         "that can see them."})
            try:
                capacities = fabric.list_capacities()
                reads.append({"read": "capacities", "ok": True, "problem": None})
            except Exception as exc:  # noqa: BLE001 - the capacity controls are then not evaluated
                capacities = None
                reads.append({"read": "capacities", "ok": False, "problem": f"Couldn't be read ({describe(exc)})."})
            lakehouses = _lakehouses(fabric, wid, _items(opt), reads)
            tables = self._table_layout(wid, _items(opt), reads)
        cap_id = str(ws.get("capacityId") or "").lower()
        records = (adapters.workspace(ws, _items(opt), collected_at=at, info_read=info_read)
                   + adapters.roles(ws, assignments, collected_at=at)
                   + adapters.capacity(cap_id, capacities, collected_at=at)
                   + adapters.optimization(opt, collected_at=at)
                   + adapters.inspection(opt, collected_at=at)
                   + catalog_architecture.evidence(ws, _items(opt), opt, lakehouses, collected_at=at)
                   + catalog_performance.table_layout(str(ws["id"]), [i for i in _items(opt) or []
                                                                      if i.get("type") == "Lakehouse"],
                                                      tables, collected_at=at))
        scope = {"workspaces": [{"id": str(ws["id"]), "name": ws.get("displayName")}],
                 "capacities": [cap_id] if cap_id else []}
        result = assess.assess(records, catalog.registry(), scope=scope, assessed_at=at, profile=prof, fixture=demo)
        result["reads"] = reads
        if cap_id and not any(r["kind"] == "capacity.state" for r in result["evidence"]):
            result["reads"].append({"read": "capacity", "ok": False,
                                    "problem": "The workspace's capacity isn't visible to you, so its controls "
                                               "aren't evaluated. That isn't a sign anything is wrong with it."})
        return result
