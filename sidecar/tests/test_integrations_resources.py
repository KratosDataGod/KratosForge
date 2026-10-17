"""Resources (PR-AI-006): fabric-desktop:// URIs generated from the registry, read through the same
invoke as tools, paged through their query, filtered by permission, and Prove's read-only
capabilities, which never return cell outputs or example rows. Results have secrets removed."""

import json

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from starlette.testclient import TestClient

from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError, redact_result
from fabric_desktop.integrations.http_server import build_app
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.service import Service

LAKEHOUSE = {"name": "LH_Bronze", "workspace_id": "ws1", "workspace_name": "Sales", "lakehouse_id": "lh1"}
CELLS = [{"type": "code", "source": "import polars as pl\ndf = pl.DataFrame({'a': [1]})"},
         {"type": "code", "source": "print(df)"}]

PROOF = {"id": "20261017-120000-abc123", "name": "nb", "state": "done", "started_at": "2026-10-17T12:00:00Z",
         "pc": {"status": "done", "cells": {"0": {"status": "done", "seconds": 1, "outputs": [{"text": "a\n1"}]}}},
         "fabric": {"status": "done"}, "_cancel": False,
         "tables": [{"table": "dbo.t", "verification": {"examples": [{"key": [1], "status": "changed"}]}}],
         "message": "client_secret=abc123secretvalue"}
SBS = {"id": "20261017-130000-def456", "name": "nb", "state": "done", "started_at": "2026-10-17T13:00:00Z",
       "local": {"cells": {"0": {"status": "done", "outputs": [{"text": "rows"}]}}}}


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "data")
    s.notebooks.save("nb", CELLS, LAKEHOUSE)
    s.notebooks.save("My notebook", CELLS, LAKEHOUSE)
    monkeypatch.setattr(s, "list_workspaces", lambda: [{"id": f"ws{i}", "displayName": f"W{i}", "type": "Workspace"}
                                                       for i in range(7)])
    monkeypatch.setattr(s, "prove_list", lambda name=None, limit=20: [PROOF])
    monkeypatch.setattr(s, "prove_status", lambda id: PROOF if id == PROOF["id"] else (_ for _ in ()).throw(LookupError("No proof")))
    monkeypatch.setattr(s, "side_by_side_list", lambda name=None, limit=20: [SBS])
    monkeypatch.setattr(s, "side_by_side_status", lambda id: SBS)
    monkeypatch.setattr(s, "receipt_get", lambda id: {"id": id, "receipt": {"notebook": {"name": "nb"}, "tables": PROOF["tables"]}})
    return s


def registry(authorizer=None):
    reg = Registry(authorizer)
    register_core(reg)
    return reg


def ctx():
    return CallContext(Caller("cl_1", "test"))


def read(reg, svc, uri):
    cap, args = reg.resolve(uri)
    return reg.invoke(svc, cap.id, args, ctx())


# ---- the registry

def test_every_resource_is_read_only_metadata():
    reg = registry()
    assert len(reg.resources()) >= 20
    for cap in reg.resources():
        assert cap.read_only and cap.approval == "never" and cap.permission == "READ_METADATA", cap.id
        assert cap.resource.startswith("fabric-desktop://")


def test_resolve_templates_and_query():
    reg = registry()
    cap, args = reg.resolve("fabric-desktop://notebooks/My%20notebook/validation")
    assert cap.id == "fabric_desktop_validate_notebook" and args == {"name": "My notebook"}
    cap, args = reg.resolve("fabric-desktop://workspaces/ws1/items?type=Lakehouse&limit=5")
    assert cap.id == "fabric_desktop_list_artifacts" and args == {"workspace_id": "ws1", "type": "Lakehouse", "limit": 5}
    cap, args = reg.resolve("fabric-desktop://workspaces/ws1/lakehouses/lh1/tables/dbo.orders/schema")
    assert args == {"workspace_id": "ws1", "lakehouse_id": "lh1", "table": "dbo.orders"}
    assert reg.get("fabric_desktop_get_notebook").resource_uri(name="a/b") == "fabric-desktop://notebooks/a%2Fb"


@pytest.mark.parametrize("uri", ["fabric-desktop://nope", "https://example.com/notebooks", "fabric-desktop://notebooks/a/b/c",
                                 "fabric-desktop://notebooks/" + "x" * 3000, "fabric-desktop://notebooks/../../etc"])
def test_unknown_uris(uri):
    with pytest.raises(IntegrationError) as e:
        registry().resolve(uri)
    assert e.value.code == "not_found"


@pytest.mark.parametrize("query", ["?bogus=1", "?limit=ten", "?name=other", "?include_source=maybe"])
def test_bad_query(query):
    reg = registry()
    with pytest.raises(IntegrationError) as e:
        cap, args = reg.resolve("fabric-desktop://notebooks/nb" + query)
        reg.invoke(None, cap.id, args, ctx())
    assert e.value.code == "invalid_input"


def test_resource_registration_is_checked():
    reg = registry()
    base = dict(input_schema=schema({"name": {"type": "string"}}, ["name"]), output_schema=schema(extra=True),
                permission="READ_METADATA", handler=lambda s, c, **a: {})
    with pytest.raises(ValueError, match="read-only"):
        reg.register(Capability("x_run", "Run", "Runs.", **base | {"permission": "EXECUTE_LOCAL_CODE"}, read_only=False,
                                approval="always", resource="fabric-desktop://x/{name}"))
    with pytest.raises(ValueError, match="don't ask"):
        reg.register(Capability("x_ask", "Ask", "Asks.", **base, approval="ask", resource="fabric-desktop://x/{name}"))
    with pytest.raises(ValueError, match="must be in the resource URI"):
        reg.register(Capability("x_miss", "Miss", "Missing.", **base, resource="fabric-desktop://x"))
    with pytest.raises(ValueError, match="isn't an input"):
        reg.register(Capability("x_bad", "Bad", "Bad.", **base, resource="fabric-desktop://x/{name}/{other}"))
    with pytest.raises(ValueError, match="URI template"):
        reg.register(Capability("x_web", "Web", "Web.", **base, resource="https://x/{name}"))
    with pytest.raises(ValueError, match="same as"):
        reg.register(Capability("x_dup", "Dup", "Dup.", **base, resource="fabric-desktop://notebooks/{name}"))


# ---- reading

def test_read_notebooks_paged(svc):
    reg = registry()
    out = read(reg, svc, "fabric-desktop://notebooks?limit=1")
    assert out["total"] == 2 and len(out["items"]) == 1 and out["next_offset"] == 1
    nb = read(reg, svc, "fabric-desktop://notebooks/My%20notebook")
    assert nb["name"] == "My notebook" and nb["cells"][0]["source"].startswith("import polars")
    assert "outputs" not in json.dumps(nb)


def test_read_workspaces_paged(svc):
    out = read(registry(), svc, "fabric-desktop://workspaces?offset=5&limit=5")
    assert [w["id"] for w in out["items"]] == ["ws5", "ws6"] and out["next_offset"] is None


def test_notebook_dependencies_and_environment(svc):
    reg = registry()
    deps = read(reg, svc, "fabric-desktop://notebooks/nb/dependencies")
    assert deps["name"] == "nb" and deps["dependencies"]
    env = read(reg, svc, "fabric-desktop://notebooks/nb/environment")
    assert "polars" in json.dumps(env["environment"])


def test_proof_plan(svc):
    plan = read(registry(), svc, "fabric-desktop://notebooks/nb/proof-plan")
    assert plan["name"] == "nb" and "check" in plan and plan["plan"]["pc"]


def test_proofs_leave_out_outputs_and_example_rows(svc):
    reg = registry()
    for out in (read(reg, svc, "fabric-desktop://proofs")["items"][0], read(reg, svc, f"fabric-desktop://proofs/{PROOF['id']}")):
        text = json.dumps(out)
        assert "outputs" not in text and '"key": [1]' not in text and "_cancel" not in text
        assert out["tables"][0]["verification"]["examples"] == "hidden"
        assert "abc123secretvalue" not in text  # results have secrets removed
    sbs = read(reg, svc, f"fabric-desktop://side-by-side/{SBS['id']}")
    assert "outputs" not in json.dumps(sbs)
    receipt = read(reg, svc, "fabric-desktop://receipts/" + "a" * 64)
    assert receipt["hidden"] == ["values"] and receipt["receipt"]["tables"][0]["verification"]["examples"] == "hidden"


def test_proof_ids_cant_reach_files(svc):
    reg = registry()
    with pytest.raises(IntegrationError) as e:
        reg.invoke(svc, "fabric_desktop_get_receipt", {"id": "../../secrets"}, ctx())
    assert e.value.code == "invalid_input"


def test_unknown_proof_is_not_found(svc):
    with pytest.raises(IntegrationError) as e:
        read(registry(), svc, "fabric-desktop://proofs/20990101-000000-zzzzzz")
    assert e.value.code == "not_found"


def test_redact_result_walks_everything():
    out = redact_result({"a": ["Authorization: Bearer abcdefghijklmnop", {"b": "password=hunter22"}], "n": 3, "t": ("x",)})
    assert "abcdefghijklmnop" not in json.dumps(out) and "hunter22" not in json.dumps(out)
    assert out["n"] == 3 and out["t"] == ["x"]


# ---- MCP

def test_mcp_lists_resources_and_templates(svc):
    async def go():
        async with Client(build_server(registry(), lambda: svc)) as c:
            static = {str(r.uri) for r in (await c.list_resources()).resources}
            templates = {t.uri_template for t in (await c.list_resource_templates()).resource_templates}
            assert "fabric-desktop://notebooks" in static and "fabric-desktop://proofs" in static
            assert "fabric-desktop://notebooks/{name}" in templates
            assert not static & templates
            body = await c.read_resource("fabric-desktop://notebooks/nb/validation")
            assert json.loads(body.contents[0].text)["name"] == "nb"
            with pytest.raises(MCPError) as e:
                await c.read_resource("fabric-desktop://nope")
            assert e.value.error.code == -32002
            with pytest.raises(MCPError) as e:
                await c.read_resource("fabric-desktop://notebooks/missing")
            assert e.value.error.data["code"] == "not_found"
    anyio.run(go)


def test_mcp_resources_follow_permissions(tmp_path, svc):
    store = IntegrationStore(tmp_path / "i.sqlite")
    store.set_setting("enabled", True)
    reg = registry(PermissionAuthorizer(store, wait_seconds=0.1, poll=0.02))

    async def go(client_id, expect_notebooks):
        async with Client(build_server(reg, lambda: svc, client_id)) as c:
            static = {str(r.uri) for r in (await c.list_resources()).resources}
            assert ("fabric-desktop://notebooks" in static) is expect_notebooks
            assert "fabric-desktop://capabilities" in static  # always readable, says why nothing else is
            if not expect_notebooks:
                with pytest.raises(MCPError) as e:
                    await c.read_resource("fabric-desktop://notebooks")
                assert e.value.error.data["code"] == "permission_denied"
    client, _ = store.add_client("Claude Code", "claude-code", grants=["READ_METADATA"])
    anyio.run(go, client["id"], True)
    anyio.run(go, "cl_unknown", False)


# ---- REST

def test_rest_resources(tmp_path, svc):
    store = IntegrationStore(tmp_path / "i.sqlite")
    store.set_setting("enabled", True)
    _, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    reg = registry(PermissionAuthorizer(store, wait_seconds=0.1, poll=0.02))
    with TestClient(build_app(reg, store, 47615, lambda: svc), base_url="http://127.0.0.1:47615") as c:
        h = {"Authorization": f"Bearer {token}"}
        rows = c.get("/api/v1/resources", headers=h).json()["resources"]
        assert {"uri": "fabric-desktop://notebooks/{name}", "template": True} .items() <= next(
            r for r in rows if r["capability"] == "fabric_desktop_get_notebook").items()
        r = c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://notebooks?limit=1"}, headers=h)
        assert r.status_code == 200 and r.json()["result"]["total"] == 2
        assert c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://nope"}, headers=h).status_code == 404
        assert c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://notebooks"}).status_code == 401
