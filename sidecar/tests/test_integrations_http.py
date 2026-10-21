"""The local HTTP API (integrations/http_server.py): loopback, Host, Origin, token, size and rate
checks; REST invoke with the same permissions as MCP; the OpenAPI document; MCP over Streamable
HTTP with the SDK's own client against a real server; and the app's switch for it."""

import json
import socket
import threading
import time

import anyio
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from fabric_desktop.integrations import http_server
from fabric_desktop.integrations.capabilities import register_core
from fabric_desktop.integrations.http_server import RateLimiter, build_app, openapi
from fabric_desktop.integrations.permissions import PermissionAuthorizer
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.service import Service

PORT = 47615


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


def write_policy(tmp_path, data):
    folder = tmp_path / "programdata" / "FabricDesktop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "policy.json").write_text(json.dumps(data))


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


def slow(svc, ctx, seconds=0.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.check()
        time.sleep(0.02)
    return {"slept": seconds}


def big(svc, ctx):
    return {"text": "x" * 1_200_000}


def registry(store):
    reg = Registry(PermissionAuthorizer(store, wait_seconds=0.2, poll=0.02))
    register_core(reg)
    reg.register(Capability("ext_slow", "Slow", "Takes a while.", schema({"seconds": {"type": "number", "default": 0}}),
                            schema(extra=True), "READ_METADATA", slow, needs_service=False))
    reg.register(Capability("ext_big", "Big", "A big result.", schema(), schema(extra=True), "READ_METADATA", big,
                            needs_service=False))
    reg.register(Capability("ext_preview", "Preview rows", "Rows.", schema({"table": {"type": "string"}}),
                            schema(extra=True), "READ_DATA", lambda s, c, **a: {"rows": []}, approval="ask",
                            needs_service=False))
    return reg


def http_client(store, **kw):
    client, token = store.add_client("Script", "http", transport="http", grants=kw.pop("grants", ["READ_METADATA"]), **kw)
    return client, token


def api(store, limiter=None):
    return TestClient(build_app(registry(store), store, PORT, limiter=limiter), base_url=f"http://127.0.0.1:{PORT}")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---- the guard

def test_health_and_openapi_are_open(store):
    with api(store) as c:
        assert c.get("/api/v1/health").json()["ok"] is True
        doc = c.get("/api/v1/openapi.json").json()
    assert doc["openapi"] == "3.1.0"
    assert "/api/v1/capabilities/fabric_desktop_list_projects/invoke" in doc["paths"]


def test_token_required(store):
    _, token = http_client(store)
    with api(store) as c:
        r = c.get("/api/v1/capabilities")
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert c.get("/api/v1/capabilities", headers=auth("fdt_wrong")).status_code == 401
        assert c.get("/api/v1/capabilities", headers={"Authorization": token}).status_code == 401  # no Bearer
        assert c.get("/api/v1/capabilities", headers=auth(token)).status_code == 200


def test_revoked_or_replaced_token_stops_at_once(store):
    client, token = http_client(store)
    with api(store) as c:
        assert c.get("/api/v1/capabilities", headers=auth(token)).status_code == 200
        new = store.new_token(client["id"])
        assert c.get("/api/v1/capabilities", headers=auth(token)).status_code == 401
        assert c.get("/api/v1/capabilities", headers=auth(new)).status_code == 200
        store.revoke_client(client["id"])
        assert c.get("/api/v1/capabilities", headers=auth(new)).status_code == 401


def test_stdio_client_has_no_token(store):
    client, token = store.add_client("Claude Code", "claude-code")
    assert token is None and not client["has_token"]


@pytest.mark.parametrize("host", ["evil.example:47615", "127.0.0.1:9999", "127.0.0.1", "localhost.evil.example:47615"])
def test_wrong_host_refused(store, host):
    _, token = http_client(store)
    with api(store) as c:
        r = c.get("/api/v1/health", headers={"Host": host})
    assert r.status_code == 403 and "Host" in r.json()["error"]["message"]


@pytest.mark.parametrize("host", ["localhost:47615", "[::1]:47615", "127.0.0.1:47615"])
def test_loopback_hosts_accepted(store, host):
    with api(store) as c:
        assert c.get("/api/v1/health", headers={"Host": host}).status_code == 200


def test_browser_origin_refused(store):
    _, token = http_client(store)
    with api(store) as c:
        for origin in ("https://evil.example", "http://127.0.0.1:47615", "null"):
            r = c.get("/api/v1/capabilities", headers=auth(token) | {"Origin": origin})
            assert r.status_code == 403


def test_remote_address_refused(store):
    _, token = http_client(store)
    app = build_app(registry(store), store, PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("192.168.1.20", 50000)) as c:
        assert c.get("/api/v1/health").status_code == 403


def test_body_size_limit(store):
    _, token = http_client(store)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/ext_slow/invoke", headers=auth(token),
                   content=b'{"arguments": {"x": "' + b"a" * (http_server.MAX_BODY_BYTES + 10) + b'"}}')
    assert r.status_code == 413


def test_rate_limit_per_client(store):
    _, token = http_client(store)
    _, other = http_client(store)
    with api(store, limiter=RateLimiter(rate=0.0001, burst=3)) as c:
        codes = [c.get("/api/v1/capabilities", headers=auth(token)).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        assert c.get("/api/v1/capabilities", headers=auth(other)).status_code == 200


def test_rate_limiter_refills():
    now = [0.0]
    lim = RateLimiter(rate=1, burst=2, clock=lambda: now[0])
    assert [lim.allow("a") for _ in range(3)] == [True, True, False]
    now[0] += 1.0
    assert lim.allow("a") and not lim.allow("a")


# ---- REST invoke

def test_invoke_ok(store):
    _, token = http_client(store)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/ext_slow/invoke", headers=auth(token), json={"arguments": {"seconds": 0}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["capability"] == "ext_slow" and body["result"] == {"slept": 0}


def test_invoke_without_body(store):
    _, token = http_client(store)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/fabric_desktop_list_capabilities/invoke", headers=auth(token))
    assert r.status_code == 200 and r.json()["result"]["capabilities"]


@pytest.mark.parametrize("content", [b"not json", b"[1]", b'{"arguments": [1]}', b'{"args": {}}'])
def test_invoke_bad_body(store, content):
    _, token = http_client(store)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/ext_slow/invoke", headers=auth(token) | {"Content-Type": "application/json"},
                   content=content)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_input"


def test_invoke_invalid_arguments(store):
    _, token = http_client(store)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/ext_slow/invoke", headers=auth(token), json={"arguments": {"seconds": "x"}})
    assert r.status_code == 400


def test_invoke_unknown_capability(store):
    _, token = http_client(store)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/nope/invoke", headers=auth(token), json={})
    assert r.status_code == 404


def test_invoke_needs_permission_and_approval(store):
    _, token = http_client(store)
    _, data_token = http_client(store, grants=["READ_METADATA", "READ_DATA"])
    with api(store) as c:
        r = c.post("/api/v1/capabilities/ext_preview/invoke", headers=auth(token), json={"arguments": {"table": "t"}})
        assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
        r = c.post("/api/v1/capabilities/ext_preview/invoke", headers=auth(data_token), json={"arguments": {"table": "t"}})
        assert r.status_code == 428
        approval_id = r.json()["error"]["details"]["approval_id"]
        store.decide(approval_id, True)
        r = c.post("/api/v1/capabilities/ext_preview/invoke", headers=auth(data_token), json={"arguments": {"table": "t"}})
        assert r.status_code == 200
        # single use
        r = c.post("/api/v1/capabilities/ext_preview/invoke", headers=auth(data_token), json={"arguments": {"table": "t"}})
        assert r.status_code == 428


def test_no_endpoint_changes_settings_or_approvals(store):
    _, token = http_client(store)
    with api(store) as c:
        doc = c.get("/api/v1/openapi.json").json()
        for path in ("/api/v1/approvals", "/api/v1/settings", "/api/v1/clients", "/api/v1/integrations_decide"):
            assert c.post(path, headers=auth(token), json={}).status_code in (404, 405)
    names = " ".join(doc["paths"])
    assert "decide" not in names and "approv" not in names and "settings" not in names


def test_disabled_integrations_refused(store):
    _, token = http_client(store)
    store.set_setting("enabled", False)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/ext_slow/invoke", headers=auth(token), json={})
    assert r.status_code == 503 and r.json()["error"]["code"] == "integrations_disabled"


def test_result_too_large(store):
    _, token = http_client(store)
    with api(store) as c:
        r = c.post("/api/v1/capabilities/ext_big/invoke", headers=auth(token), json={})
    assert r.status_code == 413


def test_capabilities_reflect_this_token(store):
    _, token = http_client(store)
    with api(store) as c:
        caps = {cap["id"]: cap for cap in c.get("/api/v1/capabilities", headers=auth(token)).json()["capabilities"]}
    assert caps["ext_slow"]["status"] in ("available", "preview")
    assert caps["ext_preview"]["status"] == "unavailable"


def test_openapi_matches_registry(store):
    reg = registry(store)
    doc = openapi(reg, PORT)
    ops = {p.rsplit("/", 2)[-2] for p in doc["paths"] if p.endswith("/invoke")}
    assert ops == {c.id for c in reg.all()}
    op = doc["paths"]["/api/v1/capabilities/ext_preview/invoke"]["post"]
    assert op["x-fabric-desktop"]["permission"] == "READ_DATA" and op["x-fabric-desktop"]["approval"] == "ask"
    assert doc["servers"] == [{"url": f"http://127.0.0.1:{PORT}"}]
    json.dumps(doc)  # serialisable


# ---- a real server: disconnect cancels, and MCP over Streamable HTTP with the SDK client

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(store):
    # Bind here and hand the socket to uvicorn, so no other test can take the port before it serves.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    reg = registry(store)
    seen = []
    original = reg.invoke

    def recording(service, cap_id, args, ctx):
        seen.append(ctx)
        return original(service, cap_id, args, ctx)
    reg.invoke = recording  # type: ignore[method-assign]
    server = uvicorn.Server(uvicorn.Config(build_app(reg, store, port), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the local API server didn't start"
    yield port, seen
    server.should_exit = True
    thread.join(10)
    sock.close()


def test_client_disconnect_cancels_call(store, live):
    port, seen = live
    _, token = http_client(store)
    with pytest.raises(httpx2.ReadTimeout):
        httpx2.post(f"http://127.0.0.1:{port}/api/v1/capabilities/ext_slow/invoke", headers=auth(token),
                    json={"arguments": {"seconds": 5}}, timeout=0.5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (seen and seen[-1].cancelled):
        time.sleep(0.05)
    assert seen and seen[-1].cancelled


def mcp_transport(port, token):
    return streamable_http_client(f"http://127.0.0.1:{port}/mcp",
                                  http_client=httpx2.AsyncClient(headers=auth(token), timeout=30))


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_mcp_over_streamable_http(store, live, mode):
    port, _ = live
    client, token = http_client(store)

    async def main():
        async with Client(mcp_transport(port, token), mode=mode) as mcp:
            tools = {t.name for t in (await mcp.list_tools()).tools}
            assert "fabric_desktop_list_capabilities" in tools and "ext_slow" in tools
            assert "ext_preview" not in tools  # needs READ_DATA, which this token doesn't have
            result = await mcp.call_tool("ext_slow", {"seconds": 0})
            assert not result.is_error and result.structured_content == {"slept": 0}
    anyio.run(main)
    assert store.client(client["id"])["last_used_at"]


def test_mcp_over_http_needs_token(store, live):
    port, _ = live
    r = httpx2.post(f"http://127.0.0.1:{port}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    r = httpx2.post(f"http://127.0.0.1:{port}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Accept": "application/json, text/event-stream", "Origin": "https://evil.example"})
    assert r.status_code == 403


# ---- the app's switch (app_service)

def test_http_switch_follows_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    svc = Service(root=tmp_path / "data")
    started = []
    monkeypatch.setattr(type(svc), "_http_start", lambda self: started.append(True))
    svc.integrations_set_enabled(True)
    status = svc.integrations_set_http(True)
    assert status["http"]["switch"] is True and started == [True]
    write_policy(tmp_path, {"integrations_http": False})
    with pytest.raises(PermissionError):
        svc.integrations_set_http(True)
    with pytest.raises(ValueError):
        svc.integrations_set_http(False, port=80)


def test_http_client_gets_token_and_url(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    svc = Service(root=tmp_path / "data")
    svc.integrations_set_enabled(True)
    added = svc.integrations_add_client("Script", "http", transport="http")
    assert added["token"].startswith("fdt_")
    assert "http://127.0.0.1:47615/mcp" in added["client"]["config"]["http"]["text"]
    renewed = svc.integrations_new_token(added["client"]["id"])
    assert renewed["token"] != added["token"]
    assert svc.integration_store.client_by_token(added["token"]) is None


def test_http_rpc_methods_are_allowlisted():
    from fabric_desktop import server
    assert {"integrations_set_http", "integrations_new_token"} <= server.METHODS
    assert "integrations_sync_http" not in server.METHODS


def test_serve_refuses_when_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: tmp_path / "data")
    assert http_server.serve(free_port()) == 2
    write_policy(tmp_path, {"integrations_http": False})
    assert http_server.serve(free_port()) == 2


def test_engine_starts_and_stops_the_child_process(tmp_path, monkeypatch):
    import os
    import sys
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    svc = Service(root=tmp_path / "FabricDesktop")
    port = free_port()
    svc.integrations_set_enabled(True)
    added = svc.integrations_add_client("Script", "http", transport="http")
    try:
        status = svc.integrations_set_http(True, port=port)["http"]
        assert status["running"] and status["listening"] and status["port"] == port
        r = httpx2.post(f"http://127.0.0.1:{port}/api/v1/capabilities/fabric_desktop_list_capabilities/invoke",
                        headers=auth(added["token"]), timeout=20)
        assert r.status_code == 200 and r.json()["ok"]
        svc.integrations_set_enabled(False)  # the main switch stops the API too
        assert not svc.integrations_status()["http"]["running"]
    finally:
        svc._http_stop()
