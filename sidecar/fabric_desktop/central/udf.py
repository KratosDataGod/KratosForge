"""UDF_Platform: the User Data Functions item that is the platform's API in Fabric.

Its source is core.py followed by one small function per operation. Each function opens the
SQL_Platform connection (alias "platform") and records changes as the person who called it,
taken from Fabric's own context, never from what the caller says. Report buttons in the
Catalog app (translytical task flows) call the save_* functions, which return text.
"""

from __future__ import annotations

import base64
import json
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
@udf.function()
def ensure_key_table(sqlDB: fn.FabricSqlConnection, name: str) -> bool:
    return _run(sqlDB, "ensure_key_table", name=name)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def issue_keys(sqlDB: fn.FabricSqlConnection, name: str, business_keys: list) -> dict:
    return _run(sqlDB, "issue_keys", name=name, business_keys=business_keys)


@udf.connection(argName="sqlDB", alias="platform")
@udf.function()
def import_keys(sqlDB: fn.FabricSqlConnection, name: str, rows: list) -> dict:
    return _run(sqlDB, "import_keys", name=name, rows=rows)


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
'''


def function_app() -> str:
    """The function app's Python source: the platform logic, then the functions."""
    core = (Path(__file__).with_name("core.py")).read_text(encoding="utf-8")
    return core.replace("from __future__ import annotations\n", "") + _WRAPPERS


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
