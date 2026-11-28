"""Mutation batch 367: integrations/mcp_server.py (docs/quality/mutation-testing.md). The survivors were
the result limit checked only against the module's own constant, the listen stream limit, how a tool
is described (Preview, open world), the JSON text sent back, the client's name, what a cancelled call
or resource read does, and the error code of a resource read that fails. The handlers are called
directly or through the MCP SDK's own client in memory; nothing reaches Fabric."""

from __future__ import annotations

import time
from types import SimpleNamespace

import anyio
import mcp_types as types
import pytest
from mcp.shared.exceptions import MCPError

from fabric_desktop.integrations import mcp_server
from fabric_desktop.integrations.core import IntegrationError
from fabric_desktop.integrations.mcp_server import Changes, build_server
from fabric_desktop.integrations.registry import Capability, Registry, schema


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


def handler(server, method):
    return server._request_handlers[method].handler


def cap(cid: str, fn, **kw) -> Capability:
    return Capability(cid, cid.title(), f"{cid} does it.", schema(), schema(extra=True), "READ_METADATA", fn,
                      needs_service=False, **kw)


CTX = SimpleNamespace()  # no session: the client's name isn't known


# ---- results


def test_a_result_one_byte_over_a_million_is_refused_and_a_million_is_sent():
    def sized(n):
        overhead = len(mcp_server._text({"blob": ""}).encode())
        return {"blob": "x" * (n - overhead)}
    reg = Registry()
    reg.register(cap("ext_over", lambda svc, ctx: sized(1_000_001)))
    reg.register(cap("ext_at", lambda svc, ctx: sized(1_000_000)))
    call = handler(build_server(reg), "tools/call")

    async def go():
        over = await call(CTX, types.CallToolRequestParams(name="ext_over", arguments={}))
        assert over.is_error and over.structured_content["error"]["code"] == "too_large"
        assert over.content[0].text.startswith("too_large: The result is 1000 KB, more than the 1000 KB allowed.")
        assert not (await call(CTX, types.CallToolRequestParams(name="ext_at", arguments={}))).is_error
    anyio.run(go)


def test_results_are_json_indented_by_one_with_text_as_it_is():
    assert mcp_server._text({"a": "é", "n": 1}) == '{\n "a": "é",\n "n": 1\n}'


# ---- tools and the server's options


def test_a_preview_tool_says_so_and_a_stable_one_doesnt():
    c = cap("ext_t", lambda svc, ctx: {})
    assert mcp_server._tool(c, "preview").description == "ext_t does it. (Preview: not yet tried on a live Fabric tenant.)"
    assert mcp_server._tool(c, "stable").description == "ext_t does it."


def test_only_a_tool_that_reaches_fabric_is_open_world():
    assert mcp_server._tool(cap("ext_l", lambda s, c: {}, locations=("local",)), "stable").annotations.open_world_hint is False
    assert mcp_server._tool(cap("ext_f", lambda s, c: {}, locations=("fabric",)), "stable").annotations.open_world_hint is True


def test_list_streams_are_limited_to_64():
    assert Changes(Registry()).listen._max_subscriptions == 64


def test_the_initialization_options_say_the_lists_can_change():
    opts = mcp_server.initialization_options(build_server(Registry()))
    assert opts.capabilities.tools.list_changed is True
    assert opts.capabilities.resources.list_changed is True


# ---- the client's name


def test_the_clients_name_is_its_name_and_version_when_it_gives_them():
    def ctx(info):
        return SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(client_info=info)))
    assert mcp_server._client_name(ctx(SimpleNamespace(name="Codex", version="1.2"))) == "Codex 1.2"
    assert mcp_server._client_name(ctx(SimpleNamespace(name="Codex", version=None))) == "Codex"
    assert mcp_server._client_name(ctx(None)) == ""


# ---- cancellation


def test_a_cancelled_call_stops_at_once_and_tells_the_handler():
    seen = []

    def slow(svc, ctx):
        while not ctx.cancelled:
            time.sleep(0.01)
        seen.append("stopped")
        return {}
    reg = Registry()
    reg.register(cap("ext_slow", slow, timeout=30))
    call = handler(build_server(reg), "tools/call")

    async def go():
        with anyio.move_on_after(0.3) as scope:
            await call(CTX, types.CallToolRequestParams(name="ext_slow", arguments={}))
        assert scope.cancelled_caught
        for _ in range(300):
            if seen:
                break
            await anyio.sleep(0.01)
    anyio.run(go)
    assert seen == ["stopped"]


def test_a_cancelled_resource_read_is_left_behind_at_once():
    reg = Registry()
    reg.register(cap("ext_slow_res", lambda svc, ctx: time.sleep(1.5) or {}, resource="fabric-desktop://slow"))
    read = handler(build_server(reg), "resources/read")

    async def go():
        start = time.monotonic()
        with anyio.move_on_after(0.2) as scope:
            await read(CTX, types.ReadResourceRequestParams(uri="fabric-desktop://slow"))
        assert scope.cancelled_caught
        return time.monotonic() - start
    assert anyio.run(go) < 1.0


# ---- resource errors


def test_a_resource_reads_error_code_says_what_went_wrong():
    def bad(svc, ctx):  # an error with a hint: the message carries it
        raise IntegrationError("invalid_input", "No such page.", hint="Use a number.")

    def broken(svc, ctx):
        raise RuntimeError("boom")
    reg = Registry()
    reg.register(cap("ext_bad", bad, resource="fabric-desktop://bad"))
    reg.register(cap("ext_broken", broken, resource="fabric-desktop://broken"))
    read = handler(build_server(reg), "resources/read")

    async def error(uri):
        with pytest.raises(MCPError) as raised:
            await read(CTX, types.ReadResourceRequestParams(uri=uri))
        return raised.value.error

    async def go():
        assert (await error("fabric-desktop://nowhere")).code == mcp_server.RESOURCE_NOT_FOUND == -32002
        bad = await error("fabric-desktop://bad")
        assert bad.code == types.INVALID_PARAMS and bad.message == "No such page. What to do: Use a number."
        assert (await error("fabric-desktop://broken")).code == types.INTERNAL_ERROR
    anyio.run(go)


def test_a_resource_of_exactly_a_million_bytes_is_sent():
    overhead = len(mcp_server._text({"blob": ""}).encode())
    reg = Registry()
    reg.register(cap("ext_res_at", lambda svc, ctx: {"blob": "x" * (1_000_000 - overhead)}, resource="fabric-desktop://at"))
    reg.register(cap("ext_res_over", lambda svc, ctx: {"blob": "x" * (1_000_001 - overhead)}, resource="fabric-desktop://over"))
    read = handler(build_server(reg), "resources/read")

    async def go():
        got = await read(CTX, types.ReadResourceRequestParams(uri="fabric-desktop://at"))
        assert len(got.contents[0].text.encode()) == 1_000_000
        with pytest.raises(MCPError, match="more than 1000 KB"):
            await read(CTX, types.ReadResourceRequestParams(uri="fabric-desktop://over"))
    anyio.run(go)
