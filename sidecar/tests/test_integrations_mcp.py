"""The MCP server (integrations/mcp_server.py), driven by the official MCP SDK's own client: in
memory on both protocol eras, and as the real `fabric-desktop mcp` process over stdio."""

import json
import os
import sys
import time

import anyio
import pytest
from mcp import Client, StdioServerParameters

from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.mcp_server import MAX_RESULT_BYTES, build_server
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.service import Service

MODES = ["legacy", "auto"]  # handshake era (2025-11-25) and 2026-07-28


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path)
    monkeypatch.setattr(s, "list_workspaces", lambda: [{"id": "ws1", "displayName": "Sales", "type": "Workspace"}])
    monkeypatch.setattr(s, "list_items", lambda ws: [{"id": "lh1", "displayName": "LH_Bronze", "type": "Lakehouse"}])
    return s


def registry():
    reg = Registry()
    register_core(reg)
    return reg


def connect(server, mode="auto"):
    return Client(server, mode=mode)


def run(coro_fn):
    return anyio.run(coro_fn)


@pytest.mark.parametrize("mode", MODES)
def test_client_connects_and_discovers_read_only_tools(svc, mode):
    async def go():
        async with connect(build_server(registry(), lambda: svc, "cl_1"), mode) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert "fabric_desktop_list_workspaces" in tools and "fabric_desktop_get_status" in tools
            ws = tools["fabric_desktop_list_workspaces"]
            assert ws.annotations.read_only_hint is True and ws.annotations.destructive_hint is False
            assert ws.input_schema["type"] == "object" and ws.output_schema["type"] == "object"
            assert ws.meta["fabric-desktop/permission"] == "READ_METADATA"
            return c.session.protocol_version
    version = run(go)
    assert version == ("2025-11-25" if mode == "legacy" else "2026-07-28")


@pytest.mark.parametrize("mode", MODES)
def test_read_only_calls_reach_the_existing_services(svc, mode):
    async def go():
        async with connect(build_server(registry(), lambda: svc, "cl_1"), mode) as c:
            out = await c.call_tool("fabric_desktop_list_workspaces", {"limit": 5})
            assert not out.is_error
            assert out.structured_content["items"] == [{"id": "ws1", "name": "Sales", "type": "Workspace", "capacity_id": None}]
            assert json.loads(out.content[0].text) == out.structured_content
            status = await c.call_tool("fabric_desktop_get_status", {})
            assert status.structured_content["caller"] == {"client_id": "cl_1", "channel": "mcp-stdio"}
    run(go)


def test_bad_arguments_come_back_as_a_structured_error(svc):
    async def go():
        async with connect(build_server(registry(), lambda: svc)) as c:
            out = await c.call_tool("fabric_desktop_list_artifacts", {"workspace_id": "ws1", "limit": 9999})
            assert out.is_error
            err = out.structured_content["error"]
            assert err["code"] == "invalid_input" and "at most 500" in err["message"]
            assert "What to do" in out.content[0].text
            missing = await c.call_tool("fabric_desktop_get_artifact_metadata", {"workspace_id": "ws1", "item_id": "nope"})
            assert missing.structured_content["error"]["code"] == "not_found"
            unknown = await c.call_tool("fabric_desktop_drop_everything", {})
            assert unknown.is_error and unknown.structured_content["error"]["code"] == "not_found"
    run(go)


def test_tools_that_change_things_are_not_offered_and_are_refused():
    reg = registry()
    reg.register(Capability("ext_run", "Run code", "Runs code.", schema(), schema(), "EXECUTE_LOCAL_CODE",
                            lambda svc, ctx: {"ran": True}, read_only=False, approval="always", needs_service=False))

    async def go():
        async with connect(build_server(reg)) as c:
            assert "ext_run" not in {t.name for t in (await c.list_tools()).tools}
            out = await c.call_tool("ext_run", {})
            assert out.is_error and out.structured_content["error"]["code"] == "unavailable"
            caps = await c.call_tool("fabric_desktop_list_capabilities", {})
            row = next(x for x in caps.structured_content["capabilities"] if x["id"] == "ext_run")
            assert row["status"] == "unavailable" and row["reason"]
    run(go)


def test_huge_results_are_refused_with_a_hint():
    reg = Registry()
    reg.register(Capability("ext_big", "Big", "Returns a lot.", schema(), schema(extra=True), "READ_DATA",
                            lambda svc, ctx: {"blob": "x" * (MAX_RESULT_BYTES + 10)}, needs_service=False))

    async def go():
        async with connect(build_server(reg)) as c:
            out = await c.call_tool("ext_big", {})
            assert out.structured_content["error"]["code"] == "too_large"
    run(go)


def test_resources_list_and_read():
    async def go():
        async with connect(build_server(registry())) as c:
            uris = {str(r.uri) for r in (await c.list_resources()).resources}
            assert {"fabric-desktop://status", "fabric-desktop://capabilities", "fabric-desktop://notebooks"} <= uris
            body = await c.read_resource("fabric-desktop://capabilities")
            assert json.loads(body.contents[0].text)["capabilities"]
    run(go)


def test_a_failing_service_never_ends_the_connection(svc, monkeypatch):
    monkeypatch.setattr(svc, "list_workspaces", lambda: 1 / 0)

    async def go():
        async with connect(build_server(registry(), lambda: svc)) as c:
            out = await c.call_tool("fabric_desktop_list_workspaces", {})
            assert out.structured_content["error"]["code"] == "internal"
            again = await c.call_tool("fabric_desktop_list_capabilities", {})
            assert not again.is_error
    run(go)


def test_cancelling_a_call_tells_the_handler_to_stop():
    stopped = []
    reg = Registry()

    def slow(svc, ctx):
        while not ctx.cancelled:
            time.sleep(0.01)
        stopped.append(True)
        return {}
    reg.register(Capability("ext_slow", "Slow", "Takes a while.", schema(), schema(extra=True), "READ_METADATA",
                            slow, needs_service=False, timeout=30))

    async def go():
        async with connect(build_server(reg), "legacy") as c:
            with anyio.move_on_after(0.5):
                await c.call_tool("ext_slow", {})
            for _ in range(100):
                if stopped:
                    break
                await anyio.sleep(0.02)
    run(go)
    assert stopped


def test_the_real_stdio_process_speaks_mcp(tmp_path):
    from fabric_desktop.integrations.store import IntegrationStore
    store = IntegrationStore.at(tmp_path / "FabricDesktop")
    store.set_setting("enabled", True)
    client, _ = store.add_client("Test tool", grants=["READ_METADATA"])
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path), HOME=str(tmp_path), ProgramData=str(tmp_path),
               PYTHONPATH=os.pathsep.join(sys.path))

    def params(client_id):
        return StdioServerParameters(command=sys.executable, args=["-m", "fabric_desktop.cli", "mcp", "--client", client_id],
                                     env=env)

    async def go():
        async with Client(params(client["id"]), mode="legacy") as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert "fabric_desktop_get_status" in names
            status = await c.call_tool("fabric_desktop_get_status", {})
            assert status.structured_content["signed_in"] is False
            assert status.structured_content["caller"] == {"client_id": client["id"], "channel": "mcp-stdio"}
        async with Client(params("cl_unknown"), mode="legacy") as c:
            assert {t.name for t in (await c.list_tools()).tools} == {"fabric_desktop_list_capabilities"}
            out = await c.call_tool("fabric_desktop_get_status", {})
            assert out.structured_content["error"]["code"] == "permission_denied"
    run(go)


def test_capabilities_command_prints_machine_readable_json(capsys):
    from fabric_desktop.cli import main
    main(["capabilities"])
    out = json.loads(capsys.readouterr().out)
    assert {c["id"] for c in out["capabilities"]} >= {"fabric_desktop_get_status", "fabric_desktop_list_tables"}
