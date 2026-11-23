"""Mutation batch 245: the UDF_Platform definition files, read the way Fabric reads them."""

from __future__ import annotations

import json

from fabric_desktop.central import udf


def _metadata() -> dict[str, dict]:
    files = udf.definition("ws-1", "sql-1")
    return {f["name"]: f for f in json.loads(files["resources/functions.json"])["functionsMetadata"]}


def test_each_function_binds_the_sql_database_and_the_caller_as_fabric_expects():
    funcs = _metadata()
    reading = funcs["keys_state"]  # no caller context
    assert [b["type"] for b in reading["bindings"]] == ["HttpTrigger", "FabricItem"]
    item = reading["bindings"][1]
    assert item["alias"] == udf.ALIAS and item["subType"] == "FabricSqlConnection"
    assert item["itemType"] == udf.SQL_ITEM_TYPE and item["direction"] == "In"
    writing = funcs["issue_keys"]
    assert [b["type"] for b in writing["bindings"]] == ["HttpTrigger", "FabricItem", "UserDataFunctionContext"]
    assert writing["bindings"][2]["direction"] == "In"
    # Only the caller's own arguments are function parameters, with their types.
    assert writing["fabricProperties"]["fabricFunctionParameters"] == [
        {"dataType": "str", "name": "name"}, {"dataType": "list", "name": "businessKeys"}]
    assert reading["fabricProperties"]["fabricFunctionParameters"] == []


def test_return_types_are_the_functions_own_and_helpers_are_not_listed():
    funcs = _metadata()
    assert funcs["ensure_key_table"]["fabricProperties"]["fabricFunctionReturnType"] == "bool"
    assert funcs["export_keys"]["fabricProperties"]["fabricFunctionReturnType"] == "list"
    assert funcs["delete_domain"]["fabricProperties"]["fabricFunctionReturnType"] == "str"
    assert not any(name.startswith("_") for name in funcs)


def test_every_function_is_listed_with_its_public_endpoint_on():
    files = udf.definition("ws-1", "sql-1")
    listed = json.loads(files["definition.json"])["functions"]
    assert {f["name"] for f in listed} == set(_metadata())
    assert all(f["isPublicEndpointEnabled"] is True for f in listed)
