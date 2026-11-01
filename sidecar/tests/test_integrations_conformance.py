"""Conformance (PR-AI-011): the same protocol checks through every way a tool can connect, with the
MCP SDK's own client, and the REST API against its own OpenAPI document.

Transports: in memory, the real ``fabric-desktop mcp`` process over stdio, and the real local API
over Streamable HTTP. Each on both protocol eras (the 2025-11-25 handshake and 2026-07-28). The
checks don't depend on the transport, so a difference between transports fails here.

The manual checklist for each AI tool is docs/product/ai-integration-client-checklist.md.
"""

import contextlib
import json
import os
import re
import socket
import sys
import threading
import time

import anyio
import httpx2
import pytest
import uvicorn
from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPDeprecationWarning, MCPError

from fabric_desktop import __version__
from fabric_desktop.integrations import prompts
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.http_server import build_app
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import ID_PATTERN, Registry
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.service import Service

TRANSPORTS = ["memory", "stdio", "http"]
MODES = {"legacy": "2025-11-25", "auto": "2026-07-28"}
ERROR_KEYS = {"code", "message", "retryable"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """One data folder, as the app and every server process would see it, with one connected tool."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    root = tmp_path / "FabricDesktop"
    store = IntegrationStore.at(root)
    store.set_setting("enabled", True)
    client, token = store.add_client("Conformance", "other-mcp", transport="http", grants=["READ_METADATA"])
    reg = Registry(PermissionAuthorizer(store, wait_seconds=0.2, poll=0.02))
    register_core(reg)
    return {"tmp": tmp_path, "root": root, "store": store, "client": client, "token": token, "registry": reg,
            "service": Service(root=root)}


@pytest.fixture
def http_server(env):
    # Bind the socket here and hand it to uvicorn, so no other test can take the port between
    # choosing it and serving on it; and fail clearly if the server never comes up.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = build_app(env["registry"], env["store"], port, lambda: env["service"])
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the local API server didn't start"
    yield port
    server.should_exit = True
    thread.join(10)
    sock.close()


@contextlib.asynccontextmanager
async def connect(transport, env, mode, port=None):
    """A connected client; the HTTP client it was given is closed with it, so no socket is left open."""
    http = None
    if transport == "memory":
        target = build_server(env["registry"], lambda: env["service"], env["client"]["id"])
    elif transport == "stdio":
        process_env = dict(os.environ, LOCALAPPDATA=str(env["tmp"]), HOME=str(env["tmp"]),
                           ProgramData=str(env["tmp"] / "programdata"), PYTHONPATH=os.pathsep.join(sys.path))
        target = StdioServerParameters(command=sys.executable, env=process_env,
                                       args=["-m", "fabric_desktop.cli", "mcp", "--client", env["client"]["id"]])
    else:
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {env['token']}"}, timeout=30)
        target = streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)
    try:
        async with Client(target, mode=mode) as client:
            yield client
    finally:
        if http is not None:
            await http.aclose()


@pytest.fixture(params=TRANSPORTS)
def transport(request, env):
    port = request.getfixturevalue("http_server") if request.param == "http" else None
    return lambda mode: connect(request.param, env, mode, port)


def run(fn):
    return anyio.run(fn)


def error_of(result):
    assert result.is_error, result
    err = result.structured_content["error"]
    assert ERROR_KEYS <= set(err) and isinstance(err["message"], str) and err["message"]
    return err


# ---- lifecycle

@pytest.mark.parametrize("mode", MODES)
def test_initialize_negotiates_and_describes_the_server(transport, mode):
    async def go():
        async with transport(mode) as c:
            assert c.protocol_version == MODES[mode]
            info = c.server_info
            assert (info.name, info.version) == ("fabric-desktop", __version__)
            assert info.title == "KratosForge"
            caps = c.server_capabilities
            assert caps.tools is not None and caps.resources is not None and caps.prompts is not None
            assert c.instructions and "approval" in c.instructions
            if mode == "legacy":  # ping was removed in 2026-07-28
                with pytest.warns(MCPDeprecationWarning, match="ping"):  # still answered under legacy
                    await c.send_ping()
    run(go)


# ---- discovery

@pytest.mark.parametrize("mode", MODES)
def test_every_tool_has_valid_schemas_and_honest_annotations(transport, mode):
    async def go():
        async with transport(mode) as c:
            tools = (await c.list_tools()).tools
            assert {"fabric_desktop_get_status", "fabric_desktop_list_capabilities"} <= {t.name for t in tools}
            for tool in tools:
                assert ID_PATTERN.match(tool.name) and tool.description and tool.title
                for schema in (tool.input_schema, tool.output_schema):
                    assert schema["type"] == "object"
                    Draft202012Validator.check_schema(schema)
                permission = tool.meta["fabric-desktop/permission"]
                # This tool is granted READ_METADATA only, so only that is listed, and it changes nothing.
                assert permission == "READ_METADATA", tool.name
                assert tool.annotations.read_only_hint is True and tool.annotations.destructive_hint is False
    run(go)


@pytest.mark.parametrize("mode", MODES)
def test_resources_templates_and_prompts_are_listed_and_readable(transport, mode):
    async def go():
        async with transport(mode) as c:
            resources = (await c.list_resources()).resources
            templates = (await c.list_resource_templates()).resource_templates
            assert resources and templates
            for r in resources:
                assert str(r.uri).startswith("fabric-desktop://") and r.mime_type == "application/json"
            for t in templates:
                assert t.uri_template.startswith("fabric-desktop://") and "{" in t.uri_template
            body = await c.read_resource("fabric-desktop://capabilities")
            assert json.loads(body.contents[0].text)["capabilities"]
            with pytest.raises(MCPError) as err:
                await c.read_resource("fabric-desktop://nothing/here")
            assert err.value.error.code == -32002  # resource not found
            listed = {p.name for p in (await c.list_prompts()).prompts}
            assert listed == {p.name for p in prompts.PROMPTS}
            got = await c.get_prompt("check_project", {})
            assert got.messages[0].content.text.startswith(prompts.GROUND_RULES)
            with pytest.raises(MCPError):
                await c.get_prompt("review_notebook", {})  # a required argument is missing
    run(go)


# ---- calls

@pytest.mark.parametrize("mode", MODES)
def test_results_match_their_output_schema(transport, env, mode):
    async def go():
        async with transport(mode) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            for name in ("fabric_desktop_get_status", "fabric_desktop_list_capabilities", "fabric_desktop_list_projects"):
                out = await c.call_tool(name, {})
                assert not out.is_error, out
                Draft202012Validator(tools[name].output_schema).validate(out.structured_content)
                assert json.loads(out.content[0].text) == out.structured_content  # text mirrors the structure
            status = (await c.call_tool("fabric_desktop_get_status", {})).structured_content
            assert status["caller"]["client_id"] == env["client"]["id"]
    run(go)


@pytest.mark.parametrize("mode", MODES)
def test_errors_have_one_shape_and_never_end_the_session(transport, mode):
    async def go():
        async with transport(mode) as c:
            bad = error_of(await c.call_tool("fabric_desktop_get_project", {"id": "everything"}))
            assert bad["code"] == "invalid_input"
            missing = error_of(await c.call_tool("fabric_desktop_get_project", {}))
            assert missing["code"] == "invalid_input"
            unknown = error_of(await c.call_tool("fabric_desktop_format_disk", {}))
            assert unknown["code"] == "not_found" and unknown.get("hint")
            refused = error_of(await c.call_tool("fabric_desktop_execute_python", {"code": "print(1)"}))
            assert refused["code"] in ("permission_denied", "unavailable")
            extra = error_of(await c.call_tool("fabric_desktop_get_status", {"surprise": 1}))
            assert extra["code"] == "invalid_input"
            assert not (await c.call_tool("fabric_desktop_get_status", {})).is_error  # still connected
    run(go)


@pytest.mark.parametrize("mode", MODES)
def test_concurrent_calls_all_complete(transport, mode):
    async def go():
        async with transport(mode) as c:
            results = []

            async def one():
                results.append(await c.call_tool("fabric_desktop_get_status", {}))
            async with anyio.create_task_group() as tg:
                for _ in range(12):
                    tg.start_soon(one)
            assert len(results) == 12 and not any(r.is_error for r in results)
    run(go)


def test_a_restarted_server_carries_on_with_the_same_permissions(env):
    """Each tool starts its own server process; closing it and starting another keeps the tool's
    grants, and revoking the tool in between stops the next process."""
    async def go():
        for _ in range(2):
            async with connect("stdio", env, "auto") as c:
                assert not (await c.call_tool("fabric_desktop_get_status", {})).is_error
        env["store"].revoke_client(env["client"]["id"])
        async with connect("stdio", env, "auto") as c:
            assert error_of(await c.call_tool("fabric_desktop_get_status", {}))["code"] == "permission_denied"
    run(go)


# ---- REST against its own OpenAPI document

def test_rest_follows_its_openapi_document(env, http_server):
    port = http_server
    base = f"http://127.0.0.1:{port}"
    auth = {"Authorization": f"Bearer {env['token']}"}
    spec = httpx2.get(f"{base}/api/v1/openapi.json").json()
    assert spec["openapi"].startswith("3.1") and spec["info"]["version"] == __version__
    error_schema = spec["components"]["schemas"]["Error"]
    invoke = {path: item["post"] for path, item in spec["paths"].items() if path.endswith("/invoke")}
    assert set(invoke) == {f"/api/v1/capabilities/{c.id}/invoke" for c in env["registry"].all()}
    for path in ("/api/v1/health", "/api/v1/capabilities", "/api/v1/resources"):
        assert path in spec["paths"]
        assert httpx2.get(base + path, headers=auth).status_code == 200
    for cap_id in ("fabric_desktop_get_status", "fabric_desktop_list_capabilities"):
        op = invoke[f"/api/v1/capabilities/{cap_id}/invoke"]
        Draft202012Validator.check_schema(op["requestBody"]["content"]["application/json"]["schema"])
        response_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        r = httpx2.post(f"{base}/api/v1/capabilities/{cap_id}/invoke", json={"arguments": {}}, headers=auth)
        assert r.status_code == 200
        Draft202012Validator(response_schema).validate(r.json())
    # Every error, whatever its status, has the documented shape.
    for method, path, body, headers, status in [
            ("post", "/api/v1/capabilities/fabric_desktop_get_project/invoke", {"arguments": {"id": "x"}}, auth, 400),
            ("post", "/api/v1/capabilities/fabric_desktop_nothing/invoke", {}, auth, 404),
            ("get", "/api/v1/capabilities", None, {}, 401),
            ("get", "/api/v1/capabilities", None, {**auth, "Origin": "https://evil.example"}, 403)]:
        r = httpx2.request(method.upper(), base + path, json=body, headers=headers)
        assert r.status_code == status, (path, r.text)
        # An unknown capability has no path of its own; it answers like any invoke.
        op = spec["paths"][path][method] if path in spec["paths"] else invoke["/api/v1/capabilities/fabric_desktop_get_status/invoke"]
        assert str(status) in op["responses"], (path, status)
        Draft202012Validator(error_schema).validate(r.json()["error"])


def test_capability_ids_in_docs_are_real():
    """The client checklist names tools to try; they must exist."""
    from pathlib import Path
    from fabric_desktop.integrations.registry import default_registry
    page = (Path(__file__).resolve().parents[2] / "docs" / "product" / "ai-integration-client-checklist.md").read_text(encoding="utf-8")
    real = {c.id for c in default_registry().all()}
    named = set(re.findall(r"fabric_desktop_[a-z_]+", page))
    assert named and named <= real, named - real
