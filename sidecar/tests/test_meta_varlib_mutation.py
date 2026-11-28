"""Mutation batch 48: meta/varlib.py (docs/quality/mutation-testing.md).

Pins what the existing VL_Sources tests left open: the Keys workspace rows appear only when an
environment names a Keys workspace, and the definition files are written with two-space indents.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fabric_desktop.meta import varlib


def _project(environments: dict) -> SimpleNamespace:
    crm = SimpleNamespace(id="1", code="CRM", name="Customer export",
                          connections={"dev": {"server_host": "dev-sql"}, "prod": {"server_host": "prod-sql"}})
    return SimpleNamespace(environments=environments, source_systems=[crm])


def test_no_keys_workspace_rows_when_no_environment_names_one():
    names = [r["name"] for r in varlib.values(_project({"dev": {"workspace_id": "ws-dev"}}))]
    assert names == ["ENVIRONMENT", "CRM_server_host"]


def test_keys_workspace_rows_when_one_environment_names_one():
    rows = varlib.values(_project({"prod": {"keys_workspace_id": "ws-keys", "keys_workspace_name": "Keys"}}))
    by_name = {r["name"]: r for r in rows}
    assert [r["name"] for r in rows] == ["ENVIRONMENT", "KEYS_WORKSPACE_ID", "KEYS_WORKSPACE_NAME", "CRM_server_host"]
    assert by_name["KEYS_WORKSPACE_ID"] == {"name": "KEYS_WORKSPACE_ID", "note": "The workspace that holds LH_Keys",
                                         "dev": "", "test": "", "prod": "ws-keys"}
    assert by_name["KEYS_WORKSPACE_NAME"]["prod"] == "Keys"


def test_definition_files_use_two_space_indents():
    parts = varlib.definition(_project({}))
    assert set(parts) == {"variables.json", "valueSets/Test.json", "valueSets/Prod.json", "settings.json"}
    for text in parts.values():
        assert text == json.dumps(json.loads(text), indent=2)
    assert json.loads(parts["valueSets/Prod.json"])["variableOverrides"] == [
        {"name": "ENVIRONMENT", "value": "prod"}, {"name": "CRM_server_host", "value": "prod-sql"}]
