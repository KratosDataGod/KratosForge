"""The capability registry (integrations/registry.py, capabilities.py): what external tools can
discover, how it is checked, and invoke() as the one way in."""

import dataclasses

import pytest

from fabric_desktop.integrations import capabilities
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError, validate
from fabric_desktop.integrations.registry import (PERMISSIONS, Capability, ReadOnlyAuthorizer, Registry,
                                                  default_registry, schema)
from fabric_desktop.service import Service


def ctx():
    return CallContext(Caller("cl_test", "test"), timeout=5)


def cap(**changes):
    base = Capability("ext_hello", "Say hello", "Greets someone.", schema({"who": {"type": "string"}}, ["who"]),
                      schema({"text": {"type": "string"}}, ["text"]), "READ_METADATA",
                      lambda svc, ctx, who: {"text": f"hello {who}"}, source="sample.hello", needs_service=False)
    return dataclasses.replace(base, **changes)


def test_every_core_capability_is_complete_and_valid():
    reg = default_registry()
    described = reg.describe()
    assert len(described) >= 13
    for d in described:
        assert d["id"].startswith("fabric_desktop_")
        assert d["permission"] in PERMISSIONS
        assert d["title"] and d["description"] and d["input_schema"]["type"] == "object"
        assert d["output_schema"]["type"] == "object"
        assert d["source"] == "core"
        if not d["input_schema"].get("required"):  # callable with no arguments: its defaults pass its own checks
            validate(d["input_schema"], {})


def test_anything_that_changes_or_runs_asks_first():
    for c in default_registry().all():
        if not c.read_only or c.permission not in ("READ_METADATA", "READ_DATA"):
            assert c.approval == "always" or c.id == "fabric_desktop_cancel_execution", c.id


def test_the_requested_read_only_tools_exist():
    ids = {c.id for c in default_registry().all()}
    assert {"fabric_desktop_get_status", "fabric_desktop_list_capabilities", "fabric_desktop_list_projects",
            "fabric_desktop_get_project", "fabric_desktop_list_workspaces", "fabric_desktop_list_artifacts",
            "fabric_desktop_get_artifact_metadata", "fabric_desktop_validate_project",
            "fabric_desktop_validate_notebook"} <= ids


@pytest.mark.parametrize("changes, problem", [
    ({"id": "Bad-Id"}, "lower case"),
    ({"permission": "ROOT"}, "permission must be"),
    ({"approval": "maybe"}, "approval must be"),
    ({"read_only": True, "permission": "EXECUTE_LOCAL_CODE"}, "read-only capability needs"),
    ({"read_only": False, "permission": "READ_DATA"}, "can't use a read permission"),
    ({"read_only": False, "permission": "WRITE_ONELAKE", "destructive": True}, "must ask for approval"),
    ({"status": "unavailable"}, "say why"),
    ({"locations": ("mars",)}, "locations"),
    ({"input_schema": {"type": "string"}}, "input_schema"),
    ({"title": " "}, "title and description"),
    ({"timeout": 0}, "timeout"),
    ({"source": "Bad Source!"}, "valid extension id"),
])
def test_malformed_capabilities_are_refused_when_registered(changes, problem):
    with pytest.raises(ValueError, match=problem):
        Registry().register(cap(**changes))


def test_extensions_cannot_replace_core_or_each_other():
    reg = Registry()
    reg.register(cap(id="fabric_desktop_x", source="core"))
    with pytest.raises(ValueError, match="core capability"):
        reg.register(cap(id="fabric_desktop_x"), replace=True)
    reg.register(cap())
    with pytest.raises(ValueError, match="already registered by"):
        reg.register(cap(source="other.ext"), replace=True)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(cap())
    reg.register(cap(title="Say hi"), replace=True)
    assert reg.get("ext_hello").title == "Say hi"


def test_dynamic_registration_notifies_and_unregisters_by_source():
    reg = Registry()
    seen = []
    stop = reg.on_change(lambda: seen.append(len(reg.all())))
    reg.register(cap())
    reg.register(cap(id="ext_bye"))
    assert reg.unregister_source("sample.hello") == ["ext_hello", "ext_bye"]
    assert reg.all() == [] and seen == [1, 2, 0]
    stop()
    reg.register(cap())
    assert seen == [1, 2, 0]
    with pytest.raises(ValueError):
        reg.unregister_source("core")


def test_invoke_validates_runs_and_maps_errors():
    reg = Registry()
    reg.register(cap())
    assert reg.invoke(None, "ext_hello", {"who": "Chris"}, ctx()) == {"text": "hello Chris"}
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {}, ctx())
    assert err.value.code == "invalid_input"
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "nope", {}, ctx())
    assert err.value.code == "not_found"


def test_invoke_needs_the_engine_for_service_capabilities():
    reg = Registry()
    reg.register(cap(needs_service=True))
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "x"}, ctx())
    assert err.value.code == "unavailable"


def test_unavailable_capabilities_are_listed_with_a_reason_and_refused():
    reg = Registry()
    reg.register(cap(status="unavailable", reason="Needs Fabric capacity"))
    [d] = reg.describe()
    assert (d["status"], d["reason"]) == ("unavailable", "Needs Fabric capacity")
    assert reg.describe(include_unavailable=False) == []
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "x"}, ctx())
    assert err.value.code == "unavailable"


def test_until_permissions_exist_anything_that_runs_code_is_refused():
    reg = Registry()
    reg.register(cap(read_only=False, permission="EXECUTE_LOCAL_CODE", approval="always"))
    [d] = reg.describe()
    assert d["status"] == "unavailable" and d["reason"] == ReadOnlyAuthorizer.REASON
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "x"}, ctx())
    assert err.value.code == "unavailable"


def test_invoke_applies_the_capability_timeout():
    import time
    reg = Registry()
    reg.register(cap(handler=lambda svc, ctx, who: time.sleep(2), timeout=0.2))
    with pytest.raises(IntegrationError) as err:
        reg.invoke(None, "ext_hello", {"who": "x"}, ctx())
    assert err.value.code == "timeout"


def test_list_capabilities_is_itself_a_capability(tmp_path):
    reg = Registry()
    capabilities.register_core(reg)
    out = reg.invoke(None, "fabric_desktop_list_capabilities", {}, ctx())
    assert {p["id"] for p in out["permissions"]} == set(PERMISSIONS)
    assert any(c["id"] == "fabric_desktop_list_workspaces" for c in out["capabilities"])


def test_core_capabilities_call_the_existing_services(tmp_path, monkeypatch):
    svc = Service(root=tmp_path)
    monkeypatch.setattr(svc, "list_workspaces", lambda: [{"id": "ws1", "displayName": "Sales"}])
    out = default_registry().invoke(svc, "fabric_desktop_list_workspaces", {"limit": 10}, ctx())
    assert out["items"][0]["name"] == "Sales" and out["total"] == 1
    with pytest.raises(IntegrationError) as err:
        default_registry().invoke(svc, "fabric_desktop_list_artifacts", {"workspace_id": "../../etc"}, ctx())
    assert err.value.code == "invalid_input"
