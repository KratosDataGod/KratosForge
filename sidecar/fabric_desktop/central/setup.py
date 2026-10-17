"""Creating the platform in Fabric, or joining one that exists.

For each environment, one Platform workspace (the central team's) holds:

    LH_Landing     lakehouse: the one landing zone, raw files from every source; each team builds its own Bronze
    LH_Meta        lakehouse: run history from every project (meta.runs) and the catalog's home in OneLake
    SQL_Platform   SQL database: surrogate keys (keys.*), source systems (config.*), the catalog (catalog.*)
    UDF_Platform   User Data Functions: the platform's API, run against SQL_Platform
    VL_Platform    variable library: these items' ids, so notebooks can find them

Every step is safe to run again: what exists is kept, what's missing is created. A step that
fails says what to do and the rest still run, so a half-made platform can be finished later.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import udf
from .client import FabricPlatform

ENVIRONMENTS = ("dev", "test", "prod")
META = "LH_Meta"
LANDING = "LH_Landing"
SQL = "SQL_Platform"
FUNCTIONS = udf.ITEM
VARLIB = "VL_Platform"
SQL_TYPE = "SQLDatabase"
UDF_TYPE = "UserDataFunction"
DEFAULT_NAME = "Fabric Desktop Platform - {Env}"


def default_name(env: str) -> str:
    return DEFAULT_NAME.replace("{Env}", env.title())


def _varlib(cfg: dict[str, Any]) -> dict[str, str]:
    rows = [("PLATFORM_WORKSPACE_ID", cfg.get("workspace_id", "")), ("PLATFORM_META_LAKEHOUSE_ID", cfg.get("meta_id", "")),
            ("PLATFORM_LANDING_LAKEHOUSE_ID", cfg.get("landing_id", "")),
            ("PLATFORM_SQL_DATABASE_ID", cfg.get("sql_id", "")), ("PLATFORM_FUNCTIONS_ID", cfg.get("functions_id", ""))]
    schema = "https://developer.microsoft.com/json-schemas/fabric/item/variableLibrary/definition"
    return {"variables.json": json.dumps({"$schema": f"{schema}/variables/1.0.0/schema.json", "variables": [
        {"name": n, "note": "Set by Fabric Desktop by KratosBI's platform setup", "type": "String", "value": v} for n, v in rows]},
        indent=2),
        "settings.json": json.dumps({"$schema": f"{schema}/settings/1.0.0/schema.json", "valueSetsOrder": []}, indent=2)}


def setup(fabric: Any, execute_token: Callable[[], str], env: str, name: str = "", capacity_id: str = "",
          existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create (or finish) the Platform workspace of one environment. Returns its config and each step."""
    if env not in ENVIRONMENTS:
        raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
    cfg: dict[str, Any] = dict(existing or {})
    steps: list[dict[str, str]] = []

    def step(label: str, fn: Callable[[], str]) -> bool:
        try:
            steps.append({"step": label, "status": "done", "detail": fn()})
            return True
        except Exception as exc:  # noqa: BLE001 - each step reports its own problem and the rest go on
            steps.append({"step": label, "status": "failed", "detail": str(exc)})
            return False

    def workspace() -> str:
        if cfg.get("workspace_id"):
            return f"Kept {cfg.get('workspace_name') or cfg['workspace_id']}"
        wanted = (name or default_name(env)).strip()
        found = next((w for w in fabric.list_workspaces() if w.get("displayName") == wanted), None)
        ws = found or fabric.create_workspace(wanted, capacity_id, f"{env.title()} platform for Fabric Desktop by KratosBI: "
                                              "surrogate keys, catalog and run history for every team")
        cfg.update(workspace_id=ws["id"], workspace_name=ws.get("displayName") or wanted)
        return ("Found " if found else "Created ") + cfg["workspace_name"]

    if not step("Platform workspace", workspace):
        return {"env": env, "config": cfg, "steps": steps, "ready": False}
    ws = cfg["workspace_id"]

    def meta() -> str:
        lh = fabric.find_lakehouse(ws, META)
        created = lh is None
        lh = lh or fabric.create_lakehouse(ws, META, "Run history from every Fabric Desktop by KratosBI project")
        cfg["meta_id"] = lh["id"]
        return ("Created " if created else "Kept ") + META

    def landing() -> str:
        lh = fabric.find_lakehouse(ws, LANDING)
        created = lh is None
        lh = lh or fabric.create_lakehouse(ws, LANDING, "The landing zone: raw files from every source system, "
                                           "in Files/<source>/<entity>/")
        cfg["landing_id"] = lh["id"]
        return ("Created " if created else "Kept ") + LANDING

    def sql() -> str:
        item = fabric.save_item(ws, SQL_TYPE, SQL, description="Surrogate keys, source systems and the catalog")
        cfg["sql_id"] = item["id"]
        return ("Created " if item.get("created") else "Kept ") + SQL

    def functions() -> str:
        item = fabric.save_item(ws, UDF_TYPE, FUNCTIONS, udf.definition(ws, cfg["sql_id"]),
                                "The platform's functions: keys and catalog")
        cfg["functions_id"] = item["id"]
        return ("Created " if item.get("created") else "Updated ") + FUNCTIONS

    def schema() -> str:
        client = FabricPlatform(execute_token, ws, cfg["functions_id"])
        out = client.call("ensure_schema")
        made = out.get("created", []) if isinstance(out, dict) else []
        # Whoever sets the platform up is its first admin; a platform that has admins keeps them.
        me = client.call("whoami")
        if me.get("can_claim"):
            client.call("claim_admin")
        return (f"Created {len(made)} tables" if made else "Tables were already there") + (
            ", you're its first admin" if me.get("can_claim") else "")

    def varlib() -> str:
        item = fabric.save_variable_library(ws, VARLIB, _varlib(cfg), "Where the platform's items are")
        cfg["varlib_id"] = item.get("id", "")
        return ("Created " if item.get("created") else "Updated ") + VARLIB

    step(META, meta)
    step(LANDING, landing)
    if step(SQL, sql) and step(FUNCTIONS, functions):
        if not step("Platform tables", schema):
            steps[-1]["detail"] += (f". If {FUNCTIONS} has no connection yet, open it in Fabric, choose Manage "
                                    f"connections, add {SQL} with the alias \"{udf.ALIAS}\", publish, then Finish setup.")
    step(VARLIB, varlib)
    ready = all(s["status"] == "done" for s in steps)
    return {"env": env, "config": cfg, "steps": steps, "ready": ready}


def join(fabric: Any, env: str, workspace_id: str) -> dict[str, Any]:
    """Use a Platform workspace the central team already made: find its items by name."""
    if env not in ENVIRONMENTS:
        raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
    ws = fabric.get_workspace(workspace_id)
    items = fabric.list_items(workspace_id)

    def find(item_type: str, name: str) -> str:
        return next((i["id"] for i in items if i.get("type") == item_type and i.get("displayName") == name), "")

    cfg = {"workspace_id": workspace_id, "workspace_name": ws.get("displayName", ""),
           "meta_id": find("Lakehouse", META), "landing_id": find("Lakehouse", LANDING), "sql_id": find(SQL_TYPE, SQL), "functions_id": find(UDF_TYPE, FUNCTIONS),
           "varlib_id": find("VariableLibrary", VARLIB)}
    missing = [n for n, k in ((META, "meta_id"), (LANDING, "landing_id"), (SQL, "sql_id"), (FUNCTIONS, "functions_id")) if not cfg[k]]
    if missing:
        raise LookupError(f"{cfg['workspace_name'] or workspace_id} isn't a complete Platform workspace: "
                          f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing. Ask your platform "
                          "team to finish its setup, or check that you picked the right workspace.")
    return cfg
