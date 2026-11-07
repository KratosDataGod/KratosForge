"""The MCP server (integrations/mcp_server.py): the edges test_integrations_mcp.py leaves out. A service
that can't be made, a resource too large to send, list-change notices to a connection that has gone, a
refresh that fails, a client that doesn't say its name, and `fabric-desktop mcp` setting itself up. Driven
by the MCP SDK's own client in memory; nothing reaches Fabric."""

from __future__ import annotations

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from fabric_desktop.integrations import mcp_server
from fabric_desktop.integrations.mcp_server import MAX_RESULT_BYTES, Changes, ServiceHolder, build_server
from fabric_desktop.integrations.registry import Capability, Registry, schema


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


def run(fn):
    return anyio.run(fn)


def test_with_no_service_factory_there_is_no_service():
    assert ServiceHolder(None).get() is None
    made = []
    holder = ServiceHolder(lambda: made.append(1) or "svc")
    assert holder.get() == holder.get() == "svc" and made == [1]  # made once


def test_a_service_that_cant_be_made_is_an_error_result_and_the_connection_stays():
    reg = Registry()
    reg.register(Capability("ext_needs", "Needs", "Needs the engine.", schema(), schema(extra=True), "READ_METADATA",
                            lambda svc, ctx: {"svc": str(svc)}))
    reg.register(Capability("ext_free", "Free", "Doesn't.", schema(), schema(extra=True), "READ_METADATA",
                            lambda svc, ctx: {"ok": True}, needs_service=False))

    def factory():
        raise RuntimeError("the engine's data folder is locked")

    async def go():
        async with Client(build_server(reg, factory)) as c:
            out = await c.call_tool("ext_needs", {})
            assert out.is_error and out.structured_content["error"]["code"]
            assert not (await c.call_tool("ext_free", {})).is_error
    run(go)


def test_a_resource_too_large_to_send_says_to_read_a_smaller_page():
    reg = Registry()
    reg.register(Capability("ext_big", "Big", "A lot.", schema(), schema(extra=True), "READ_METADATA",
                            lambda svc, ctx: {"blob": "x" * (MAX_RESULT_BYTES + 10)}, needs_service=False,
                            resource="fabric-desktop://big"))

    async def go():
        async with Client(build_server(reg)) as c:
            with pytest.raises(MCPError, match="more than 1000 KB") as raised:
                await c.read_resource("fabric-desktop://big")
            assert raised.value.error.data == {"code": "too_large", "hint": "Add ?limit= to read a smaller page."}
    run(go)


class Gone:
    async def send_tool_list_changed(self):
        raise ConnectionResetError("closed")

    async def send_resource_list_changed(self):  # pragma: no cover - the first call already failed
        pass


class Here:
    def __init__(self):
        self.sent = []

    async def send_tool_list_changed(self):
        self.sent.append("tools")

    async def send_resource_list_changed(self):
        self.sent.append("resources")


def test_a_connection_that_has_gone_is_dropped_and_the_rest_are_told(capsys):
    reg = Registry()
    refreshed = []

    def refresh():
        refreshed.append(1)
        if len(refreshed) == 1:
            raise OSError("extensions folder unreadable")
        reg.register(Capability("ext_new", "New", "Turned on.", schema(), schema(extra=True), "READ_METADATA",
                                lambda svc, ctx: {}, needs_service=False))
    changes = Changes(reg, refresh, poll=0.01)
    gone, here = Gone(), Here()
    changes.connections.add(gone)
    changes.connections.add(here)

    async def go():
        assert await changes.check() is False  # the refresh failed: keep serving what's registered
        assert await changes.check() is True  # a capability was added
        assert await changes.check() is False  # nothing new since
    run(go)
    assert "capabilities: refresh failed: OSError: extensions folder unreadable" in capsys.readouterr().err
    assert here.sent == ["tools", "resources"] and list(changes.connections) == [here]


def test_a_client_that_doesnt_say_its_name_is_unnamed(caplog):
    class Session:
        @property
        def client_params(self):
            raise AttributeError("not on this protocol version")
    ctx = type("Ctx", (), {"session": Session()})()
    assert mcp_server._client_name(ctx) == ""
    assert mcp_server._client_name(type("Ctx", (), {"session": type("S", (), {"client_params": None})()})()) == ""


def test_the_mcp_command_sets_up_permissions_audit_and_extensions_then_serves_stdio(tmp_path, monkeypatch, capsys):
    from fabric_desktop.integrations.capabilities import register_core
    from fabric_desktop.integrations.permissions import PermissionAuthorizer

    def fresh():
        reg = Registry()
        register_core(reg)
        return reg
    reg = fresh()
    monkeypatch.setattr("fabric_desktop.integrations.mcp_server.default_registry", lambda: reg)
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: tmp_path / "data")
    started = []
    monkeypatch.setattr(mcp_server.anyio, "run", lambda main: started.append(main))
    mcp_server.run_stdio("cl_7")
    assert len(started) == 1 and isinstance(reg.authorizer, PermissionAuthorizer) and reg.audit is not None
    err = capsys.readouterr().err
    assert "KratosForge MCP server" in err and "(client cl_7)" in err


def test_the_engine_an_ai_tool_starts_never_opens_a_sign_in_window(monkeypatch):
    made = []

    class Service:
        def __init__(self, **kw):
            made.append(kw)
    monkeypatch.setattr("fabric_desktop.service.Service", Service)
    mcp_server._engine_service()
    assert made == [{"headless": True}]
