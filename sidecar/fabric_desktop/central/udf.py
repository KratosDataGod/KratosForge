"""UDF_Platform: the User Data Functions item that is the platform's API in Fabric.

Its source is core.py and policy.py, run as a module of their own, followed by one small
function per operation. Each function opens the
SQL_Platform connection (alias "platform") and records changes as the person who called it,
taken from Fabric's own context, never from what the caller says. Report buttons in the
Catalog app (translytical task flows) call the save_* functions, which return text.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

ALIAS = "platform"
ITEM = "UDF_Platform"

_WRAPPERS = '''

# ---------------------------------------------------------------- User Data Functions
import fabric.functions as fn

udf = fn.UserDataFunctions()


def _run(sqlDB, op, ctx=None, **kwargs):
    conn = sqlDB.connect()
    try:
        db = Db(conn, "tsql")
        fn_ = OPERATIONS[op]
        if ctx is not None and "by" in fn_.__code__.co_varnames[:fn_.__code__.co_argcount]:
            kwargs["by"] = ctx.executing_user.get("PreferredUsername") or ctx.executing_user.get("Oid", "")
        return fn_(db, **kwargs)
    except PlatformError as exc:
        raise fn.UserThrownError(str(exc), {"operation": op})
    finally:
        conn.close()


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def ensure_schema(sqlDB: fn.FabricSqlConnection) -> dict:
    return _run(sqlDB, "ensure_schema")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def ensure_key_table(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, name: str,
                     business_key: str = "", description: str = "", domain_id: str = "") -> bool:
    return _run(sqlDB, "ensure_key_table", ctx, name=name, business_key=business_key, description=description,
                domain_id=domain_id)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def issue_keys(sqlDB: fn.FabricSqlConnection, name: str, business_keys: list) -> dict:
    return _run(sqlDB, "issue_keys", name=name, business_keys=business_keys)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def import_keys(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, name: str, rows: list) -> dict:
    return _run(sqlDB, "import_keys", ctx, name=name, rows=rows)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def close_keys(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, reason: str = "") -> dict:
    return _run(sqlDB, "close_keys", ctx, reason=reason)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def stage_keys(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, staging: bool,
               live: bool = False) -> dict:
    return _run(sqlDB, "stage_keys", ctx, staging=staging, live=live)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def keys_state(sqlDB: fn.FabricSqlConnection) -> dict:
    return _run(sqlDB, "keys_state")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def clear_keys(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext) -> dict:
    return _run(sqlDB, "clear_keys", ctx)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def key_stats(sqlDB: fn.FabricSqlConnection, name: str) -> dict:
    return _run(sqlDB, "key_stats", name=name)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def lookup_key(sqlDB: fn.FabricSqlConnection, name: str, value: str) -> dict:
    return _run(sqlDB, "lookup_key", name=name, value=value) or {}


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def export_keys(sqlDB: fn.FabricSqlConnection, name: str, after: int) -> list:
    return _run(sqlDB, "export_keys", name=name, after=after)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_domains(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_domains")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_domain(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, domain: dict) -> dict:
    return _run(sqlDB, "save_domain", ctx, domain=domain)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def delete_domain(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, did: str) -> str:
    _run(sqlDB, "delete_domain", ctx, did=did)
    return f"Deleted {did}"


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def import_fabric_domains(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, domains: list) -> dict:
    return _run(sqlDB, "import_fabric_domains", ctx, domains=domains)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def register_project(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, project: dict) -> dict:
    return _run(sqlDB, "register_project", ctx, project=project)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_projects(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_projects")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def register_product(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, product: dict, ports: list,
                     env: str) -> dict:
    return _run(sqlDB, "register_product", ctx, product=product, ports=ports, env=env)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_products(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_products")


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def resolve_port(sqlDB: fn.FabricSqlConnection, product_id: str, port: str, env: str) -> dict:
    return _run(sqlDB, "resolve_port", product_id=product_id, port=port, env=env) or {}


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def register_link(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, consumer: str, alias: str,
                  product_id: str, port: str, reads: dict) -> dict:
    return _run(sqlDB, "register_link", ctx, consumer=consumer, alias=alias, product_id=product_id, port=port,
                reads=reads)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_enrichment(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, target: str, fields: dict) -> dict:
    return _run(sqlDB, "save_enrichment", ctx, target=target, fields=fields)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def enrichment(sqlDB: fn.FabricSqlConnection, project: str) -> dict:
    return _run(sqlDB, "enrichment", project=project)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_glossary(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_glossary")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_term(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, term: dict) -> dict:
    return _run(sqlDB, "save_term", ctx, term=term)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def audit(sqlDB: fn.FabricSqlConnection, limit: int) -> list:
    return _run(sqlDB, "audit", limit=limit)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def log_workload_action(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, workload: str, action: str,
                        status: str, entry: dict = None) -> dict:
    return _run(sqlDB, "log_workload_action", ctx, workload=workload, action=action, status=status, entry=entry)


# ---- who may change what, key tables, sources and requests

@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def whoami(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext) -> dict:
    return _run(sqlDB, "whoami", ctx)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_admins(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_admins")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def claim_admin(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext) -> dict:
    return _run(sqlDB, "claim_admin", ctx)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def add_admin(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, user: str) -> list:
    return _run(sqlDB, "add_admin", ctx, user=user)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def remove_admin(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, user: str) -> list:
    return _run(sqlDB, "remove_admin", ctx, user=user)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_key_tables(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_key_tables")


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_sources(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_sources")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_source(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, source: dict) -> list:
    return _run(sqlDB, "save_source", ctx, source=source)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def delete_source(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, name: str) -> list:
    return _run(sqlDB, "delete_source", ctx, name=name)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def adopt_sources(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, systems: list,
                  project: str = "") -> dict:
    return _run(sqlDB, "adopt_sources", ctx, systems=systems, project=project)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def request(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, kind: str, target: str,
            project: str = "", reason: str = "", detail: dict = None) -> dict:
    return _run(sqlDB, "request", ctx, kind=kind, target=target, project=project, reason=reason, detail=detail or {})


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def list_requests(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, status: str = "") -> list:
    return _run(sqlDB, "list_requests", ctx, status=status)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def decide_request(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str, approve: bool,
                   note: str = "") -> dict:
    return _run(sqlDB, "decide_request", ctx, id=id, approve=approve, note=note)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def withdraw_request(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str) -> dict:
    return _run(sqlDB, "withdraw_request", ctx, id=id)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def record_request_progress(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str,
                            made: dict) -> dict:
    return _run(sqlDB, "record_request_progress", ctx, id=id, made=made or {})


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_grants(sqlDB: fn.FabricSqlConnection, product_id: str = "") -> list:
    return _run(sqlDB, "list_grants", product_id=product_id)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def revoke_grant(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, product_id: str, project: str) -> list:
    return _run(sqlDB, "revoke_grant", ctx, product_id=product_id, project=project)


# Report buttons (translytical task flows) need functions that return text.

@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def set_description(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, target: str, description: str) -> str:
    _run(sqlDB, "save_enrichment", ctx, target=target, fields={"description": description})
    return f"Saved the description of {target}"


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def set_tags(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, target: str, tags: str) -> str:
    _run(sqlDB, "save_enrichment", ctx, target=target, fields={"tags": tags})
    return f"Saved the tags of {target}"


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def set_classification(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, target: str,
                       classification: str) -> str:
    _run(sqlDB, "save_enrichment", ctx, target=target, fields={"classification": classification})
    return f"{target} is now {classification}"

@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def record_shortcuts(sqlDB: fn.FabricSqlConnection, lakehouses: list, shortcuts: list) -> dict:
    return _run(sqlDB, "record_shortcuts", lakehouses=lakehouses, shortcuts=shortcuts)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def log_shortcut_change(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, kind: str, shortcut: dict,
                        before: dict = None, after: dict = None, note: str = "") -> dict:
    return _run(sqlDB, "log_shortcut_change", ctx, kind=kind, shortcut=shortcut, before=before, after=after, note=note)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def record_shortcut_health(sqlDB: fn.FabricSqlConnection, results: list) -> list:
    return _run(sqlDB, "record_shortcut_health", results=results)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_seen_shortcuts(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "list_seen_shortcuts")


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def shortcut_changes(sqlDB: fn.FabricSqlConnection, days: int = 30, limit: int = 500, shortcut: str = "") -> list:
    return _run(sqlDB, "shortcut_changes", days=days, limit=limit, shortcut=shortcut)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def shortcut_rules(sqlDB: fn.FabricSqlConnection) -> dict:
    return _run(sqlDB, "shortcut_rules")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_shortcut_rules(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, rules: dict) -> dict:
    return _run(sqlDB, "save_shortcut_rules", ctx, rules=rules)


# ---- governance policies (policy.py)

@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def ensure_policy_schema(sqlDB: fn.FabricSqlConnection) -> list:
    return _run(sqlDB, "ensure_policy_schema")


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def create_policy(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, policy: dict) -> dict:
    return _run(sqlDB, "create_policy", ctx, policy=policy)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_policy_draft(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str, policy: dict,
                      note: str = "") -> dict:
    return _run(sqlDB, "save_policy_draft", ctx, id=id, policy=policy, note=note)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def discard_policy_draft(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str) -> dict:
    return _run(sqlDB, "discard_policy_draft", ctx, id=id)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def publish_policy(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str, note: str = "") -> dict:
    return _run(sqlDB, "publish_policy", ctx, id=id, note=note)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def set_policy_status(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, id: str, status: str,
                      note: str = "") -> dict:
    return _run(sqlDB, "set_policy_status", ctx, id=id, status=status, note=note)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def policies_ready(sqlDB: fn.FabricSqlConnection) -> bool:
    return _run(sqlDB, "policies_ready")


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_policies(sqlDB: fn.FabricSqlConnection, query: str = "", category: str = "", status: str = "") -> list:
    return _run(sqlDB, "list_policies", query=query, category=category, status=status)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def get_policy(sqlDB: fn.FabricSqlConnection, id: str) -> dict:
    return _run(sqlDB, "get_policy", id=id)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def policy_history(sqlDB: fn.FabricSqlConnection, id: str, limit: int = 200) -> list:
    return _run(sqlDB, "policy_history", id=id, limit=limit)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def policy_version(sqlDB: fn.FabricSqlConnection, id: str, version: int) -> dict:
    return _run(sqlDB, "policy_version", id=id, version=version)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def save_assignment(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, policy: str, scope: str,
                    target: str = "", settings: dict = None, note: str = "") -> dict:
    return _run(sqlDB, "save_assignment", ctx, policy=policy, scope=scope, target=target, settings=settings or {},
                note=note)


@udf.connection(argName="sqlDB", alias="platform")
@udf.context(argName="ctx")
@udf.function()
def remove_assignment(sqlDB: fn.FabricSqlConnection, ctx: fn.UserDataFunctionContext, policy: str, scope: str,
                      target: str = "") -> list:
    return _run(sqlDB, "remove_assignment", ctx, policy=policy, scope=scope, target=target)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_assignments(sqlDB: fn.FabricSqlConnection, policy: str = "", scope: str = "", target: str = "") -> list:
    return _run(sqlDB, "list_assignments", policy=policy, scope=scope, target=target)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def policy_applicability(sqlDB: fn.FabricSqlConnection, id: str) -> dict:
    return _run(sqlDB, "policy_applicability", id=id)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def resolve_policies(sqlDB: fn.FabricSqlConnection, project: str = "", entity: str = "", domain: str = "",
                     subdomain: str = "", local: list = None, context: dict = None, today: str = "") -> dict:
    return _run(sqlDB, "resolve_policies", project=project, entity=entity, domain=domain, subdomain=subdomain,
                local=local or [], context=context or {}, today=today)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def policy_snapshot(sqlDB: fn.FabricSqlConnection, policies: list = None) -> dict:
    return _run(sqlDB, "policy_snapshot", policies=policies or None)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def policy_options(sqlDB: fn.FabricSqlConnection) -> dict:
    return _run(sqlDB, "policy_options")


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def list_policy_exceptions(sqlDB: fn.FabricSqlConnection, policy: str = "", active: bool = False,
                           today: str = "") -> list:
    return _run(sqlDB, "list_policy_exceptions", policy=policy, active=active, today=today)

'''


def function_app() -> str:
    """The function app's Python source: the platform logic (core.py, then policy.py), then the functions.

    The logic runs in a module of its own inside the app, so the functions below, which share
    its operations' names, never replace the ones it calls itself (claim_admin calls whoami)."""
    here = Path(__file__).parent
    core = (here / "core.py").read_text(encoding="utf-8")
    # policy.py uses core's names; in one module they're already there, so its import of them goes.
    policy = re.sub(r"from \.core import \([^)]*\)\n", "", (here / "policy.py").read_text(encoding="utf-8"))
    policy = policy.replace("from __future__ import annotations\n", "")
    logic = core + "\n\n# " + "-" * 60 + " policy.py\n" + policy
    return ("import types as _types\n\n"
            f"_SOURCE = {logic!r}\n"
            "_platform = _types.ModuleType(\"platform_logic\")\n"
            "exec(compile(_SOURCE, \"platform_logic.py\", \"exec\"), _platform.__dict__)\n"
            "Db, PlatformError, OPERATIONS = _platform.Db, _platform.PlatformError, _platform.OPERATIONS\n"
            + _WRAPPERS)


def definition(workspace_id: str, sql_database_id: str) -> dict[str, str]:
    """The item's definition files: the source, and its connection to SQL_Platform as alias "platform"."""
    return {
        "function_app.py": function_app(),
        "definition.json": json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/userDataFunction/definition/1.0.0/schema.json",
            "runtime": "PYTHON",
            "connectedDataSources": [{"alias": ALIAS, "artifactId": sql_database_id, "workspaceId": workspace_id,
                                      "sourceType": "SqlDatabase"}],
            "functionsMetadata": []}, indent=2),
    }


def parts(files: dict[str, str]) -> list[dict[str, str]]:
    return [{"path": p, "payload": base64.b64encode(t.encode()).decode(), "payloadType": "InlineBase64"}
            for p, t in files.items()]
