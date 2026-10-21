"""One keys database for Dev, Test and Prod, in its own Keys workspace (Preview).

Chris chose "One for all" (2026-10-19): a business key gets one surrogate key everywhere, so a
key issued in Dev is the same key in Prod. The Keys workspace holds:

    SQL_Keys    SQL database: the key tables (keys.*) and the platform admins who may add one
    UDF_Keys    User Data Functions: the same functions as UDF_Platform, connected to SQL_Keys

Both items sit in one workspace, so the functions never reach a database in another workspace.
Until the Keys workspace is in Fabric, the shared keys live on this PC (platform/keys.sqlite).

Moving in never changes a Prod key. ``plan`` says what a move would do and writes nothing. The
move itself closes every environment's keys first (no run can issue one while it copies), copies
Prod's keys with their numbers and checks them row by row, then gives each Dev or Test business key
Prod's key, or a new one if Prod doesn't have it, so Dev and Test tables need one re-run. Each
environment's keys are only read, and stay closed afterwards with a note saying where keys are now.
"""

from __future__ import annotations

from typing import Any, Callable

from . import core, udf
from .client import FabricPlatform, PlatformKeys

SQL = "SQL_Keys"
FUNCTIONS = "UDF_Keys"
SQL_TYPE = "SQLDatabase"
UDF_TYPE = "UserDataFunction"
DEFAULT_NAME = "KratosForge Keys"
IMPORT_ROWS = 5000


def setup(fabric: Any, execute_token: Callable[[], str], name: str = "", capacity_id: str = "",
          existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create (or finish) the Keys workspace. Safe to run again: what exists is kept."""
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
        wanted = (name or DEFAULT_NAME).strip()
        found = next((w for w in fabric.list_workspaces() if w.get("displayName") == wanted), None)
        ws = found or fabric.create_workspace(wanted, capacity_id, "Surrogate keys for Dev, Test and Prod: "
                                              "one key per business key everywhere")
        cfg.update(workspace_id=ws["id"], workspace_name=ws.get("displayName") or wanted)
        return ("Found " if found else "Created ") + cfg["workspace_name"]

    def sql() -> str:
        item = fabric.save_item(cfg["workspace_id"], SQL_TYPE, SQL, description="Surrogate keys for every environment")
        cfg["sql_id"] = item["id"]
        return ("Created " if item.get("created") else "Kept ") + SQL

    def functions() -> str:
        item = fabric.save_item(cfg["workspace_id"], UDF_TYPE, FUNCTIONS, udf.definition(cfg["workspace_id"], cfg["sql_id"]),
                                "Issues surrogate keys from SQL_Keys")
        cfg["functions_id"] = item["id"]
        return ("Created " if item.get("created") else "Updated ") + FUNCTIONS

    def schema() -> str:
        client = FabricPlatform(execute_token, cfg["workspace_id"], cfg["functions_id"])
        out = client.call("ensure_schema")
        made = out.get("created", []) if isinstance(out, dict) else []
        me = client.call("whoami")
        if me.get("can_claim"):
            client.call("claim_admin")
        return (f"Created {len(made)} tables" if made else "Tables were already there") + (
            ", you're its first admin" if me.get("can_claim") else "")

    if step("Keys workspace", workspace) and step(SQL, sql) and step(FUNCTIONS, functions):
        if not step("Key tables", schema):
            steps[-1]["detail"] += (f". If {FUNCTIONS} has no connection yet, open it in Fabric, choose Manage "
                                    f"connections, add {SQL} with the alias \"{udf.ALIAS}\", publish, then Finish setup.")
    return {"config": cfg, "steps": steps, "ready": all(s["status"] == "done" for s in steps)}


def join(fabric: Any, workspace_id: str) -> dict[str, Any]:
    """Use a Keys workspace someone already made: find its items by name."""
    ws = fabric.get_workspace(workspace_id)
    items = fabric.list_items(workspace_id)

    def find(item_type: str, item: str) -> str:
        return next((i["id"] for i in items if i.get("type") == item_type and i.get("displayName") == item), "")

    cfg = {"workspace_id": workspace_id, "workspace_name": ws.get("displayName", ""),
           "sql_id": find(SQL_TYPE, SQL), "functions_id": find(UDF_TYPE, FUNCTIONS)}
    missing = [n for n, k in ((SQL, "sql_id"), (FUNCTIONS, "functions_id")) if not cfg[k]]
    if missing:
        raise LookupError(f"{cfg['workspace_name'] or workspace_id} isn't a complete Keys workspace: "
                          f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing. Ask your platform "
                          "team to finish its setup, or check that you picked the right workspace.")
    return cfg


def _real(rows: list[list[Any]]) -> list[list[Any]]:
    return [r for r in rows if int(r[0]) != core.UNKNOWN_KEY]


def copy_keeping_numbers(rows: list[list[Any]], name: str, shared: PlatformKeys) -> dict[str, Any]:
    """Copy keys in with their numbers, then check every one. A clash is refused, never renumbered."""
    rows = _real(rows)
    for i in range(0, len(rows), IMPORT_ROWS):
        shared.client.call("import_keys", name=name, rows=rows[i:i + IMPORT_ROWS])
    have = {int(sk): bk for sk, bk, _ in shared.export(name)}
    differ = [int(sk) for sk, bk, _ in rows if have.get(int(sk)) != str(bk)]
    return {"table": name, "copied": len(rows), "renumbered": 0, "match": not differ, "differ": differ[:10]}


def add_renumbering(rows: list[list[Any]], name: str, shared: PlatformKeys) -> dict[str, Any]:
    """Give every business key a key in the shared database: the one it already has there, or a new one.
    Reports how many business keys now have a different number than they had before."""
    rows = _real(rows)
    keys, _ = shared.issue(name, [str(bk) for _, bk, _ in rows])
    missing = [bk for _, bk, _ in rows if str(bk) not in keys]
    renumbered = sum(1 for sk, bk, _ in rows if keys.get(str(bk)) not in (None, int(sk)))
    return {"table": name, "copied": len(rows), "renumbered": renumbered, "match": not missing,
            "differ": missing[:10]}


def snapshot(sources: list[tuple[str, Any]]) -> dict[tuple[str, str], tuple[int, int]]:
    """(env, key table) -> (keys, next key), to prove nothing was issued while the move ran."""
    return {(env, kt["name"]): (int(kt.get("keys") or 0), int(kt.get("next") or 1))
            for env, client in sources for kt in client.call("list_key_tables")}


def plan(sources: list[tuple[str, Any]]) -> dict[str, Any]:
    """What a move would do, reading only: each key table's keys, and how many Dev or Test keys
    would get a different number (Prod's for a business key Prod has, a new one for any other)."""
    ordered = sorted(sources, key=lambda s: s[0] != "prod")
    prod: dict[str, dict[str, int]] = {}
    tables = []
    for env, client in ordered:
        source = PlatformKeys(client, env)
        for kt in client.call("list_key_tables"):
            rows = _real(source.export(kt["name"]))
            if env == "prod":
                prod[kt["name"]] = {str(bk): int(sk) for sk, bk, _ in rows}
                change = 0
            else:
                have = prod.get(kt["name"], {})
                change = sum(1 for sk, bk, _ in rows if have.get(str(bk)) != int(sk))
            tables.append({"env": env, "table": kt["name"], "keys": len(rows), "renumbered": change})
    return {"tables": tables, "renumbered": sum(t["renumbered"] for t in tables)}


def share(sources: list[tuple[str, Any]], shared: Any) -> dict[str, Any]:
    """Move every environment's key tables into the shared database. ``sources`` are (env, platform
    client) pairs, each database once; Prod's go first so its numbers never change."""
    target = PlatformKeys(shared, "shared")
    report: list[dict[str, Any]] = []
    for env, client in sorted(sources, key=lambda s: s[0] != "prod"):
        source = PlatformKeys(client, env)
        for kt in client.call("list_key_tables"):
            shared.call("ensure_key_table", name=kt["name"], business_key=kt.get("business_key", ""),
                        description=kt.get("description", ""))
            rows = source.export(kt["name"])
            move = copy_keeping_numbers if env == "prod" else add_renumbering
            report.append({"env": env} | move(rows, kt["name"], target))
    return {"tables": report, "ok": all(r["match"] for r in report),
            "renumbered": sum(r["renumbered"] for r in report)}
