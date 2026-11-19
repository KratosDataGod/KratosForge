"""Result size and time limits for every tool, on both transports, and loopback only (#2363 hardening).

Like test_integrations_tool_guards.py, each test walks the whole registry, core and built-in
extensions, so a tool added later is covered without anyone remembering to add it here. Each tool's
handler is replaced by one that answers too much or too slowly, and the tool is called with valid
arguments over MCP and over the local API:

- A result past MAX_RESULT_BYTES never goes out: it is refused as ``too_large`` with a hint, and the
  connection keeps working. Resources too.
- A tool that runs past its own time limit is refused as ``timeout`` near that limit, not left
  running until the client gives up. The limit each transport uses is the tool's own.
- Every tool has a time limit, and none over an hour.
- The local API refuses a caller that isn't on this PC, or that names another host, for every tool,
  before its handler runs; and it listens on 127.0.0.1 only.
"""

from __future__ import annotations

import dataclasses
import socket
import threading
import time

import anyio
import pytest
import uvicorn
from mcp import Client
from starlette.testclient import TestClient

from fabric_desktop.integrations import http_server, mcp_server, peer
from fabric_desktop.integrations.core import validate
from fabric_desktop.integrations.http_server import RateLimiter, build_app, server_config
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_tool_guards import spied

PORT = 47615
SLOW_LIMIT = 0.2   # each tool's time limit, shortened for the slow-handler test
SLOW_HANDLER = 5.0  # how long that handler would take


def ENGINE():  # noqa: N802 - a stand-in for the engine's Service; the spied handlers never use it
    return object()


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    # On Windows the local API also asks which account owns the caller's connection. The test client's
    # made-up address and port have no connection to own, so that check (test_integrations_peer.py's
    # subject) is left out here, as test_integrations_http_guard.py does.
    monkeypatch.setattr(peer, "checked", lambda: False)


@pytest.fixture(autouse=True)
def small_results(monkeypatch):
    """A smaller result limit on both transports, so 56 oversized results are quick to make."""
    for module in (mcp_server, http_server):
        monkeypatch.setattr(module, "MAX_RESULT_BYTES", 20_000)


@pytest.fixture
def client(tmp_path):
    store = IntegrationStore(tmp_path / "integrations.sqlite")
    store.set_setting("enabled", True)
    added, token = store.add_client("Script", "other", transport="http")
    return store, added, token


def valid_args(cap) -> dict:
    """The smallest valid arguments: each required field, with a value its schema accepts."""
    args = {}
    props = cap.input_schema.get("properties", {})
    for field in cap.input_schema.get("required", []):
        prop = props[field]
        if "enum" in prop:
            args[field] = prop["enum"][0]
        elif prop.get("pattern", "").startswith("^ex_"):
            args[field] = "ex_" + "a" * 20
        else:
            args[field] = "ab"
    validate(cap.input_schema, args)  # raises if the guess is wrong, so a new schema shape is noticed
    return args


class AllowAll:
    """Lets every call through: approvals are test_integrations_tool_guards.py's subject, limits are this file's."""

    def check(self, capability, arguments, ctx, bind=None, workspaces=None):
        if bind is not None:
            arguments.update(bind())

    def availability(self, capability, ctx):
        return capability.status, capability.reason


def answering(make) -> tuple[Registry, list]:
    """Every capability, with valid arguments and a handler made by ``make(cap)``; nothing asks first."""
    reg, _ = spied()
    out = Registry(AllowAll())
    ran = []
    for cap in reg.all():
        inner = make(cap)

        def handler(svc, ctx, _inner=inner, _id=cap.id, **args):
            ran.append(_id)
            return _inner(ctx)
        out.register(dataclasses.replace(cap, handler=handler))
    return out, ran


def big(ctx):
    return {"blob": "x" * (mcp_server.MAX_RESULT_BYTES + 10)}


def slow(ctx):
    stop = time.monotonic() + SLOW_HANDLER
    while time.monotonic() < stop and not ctx.cancelled:
        time.sleep(0.02)
    return {"late": True}


def api(reg, store, port=PORT):
    """The local API over ``reg``, with no rate limit in the way of calling every tool in a row."""
    return build_app(reg, store, port, ENGINE, limiter=RateLimiter(rate=1e6, burst=10**6))


def rest(app, cap_id, args, token, client_host="127.0.0.1", host=f"127.0.0.1:{PORT}"):
    # Not entered as a context manager: that runs the app's lifespan, which can only run once per app.
    c = TestClient(app, base_url=f"http://{host}", client=(client_host, 50000))
    return c.post(f"/api/v1/capabilities/{cap_id}/invoke", json={"arguments": args},
                  headers={"Authorization": f"Bearer {token}", "Host": host})


# --------------------------------------------------------------------------------- result size

def test_every_tools_oversized_result_is_refused_over_mcp():
    reg, ran = answering(lambda cap: big)

    async def go():
        async with Client(build_server(reg, ENGINE, "cl_t")) as c:
            for cap in reg.all():
                out = await c.call_tool(cap.id, valid_args(cap))
                assert out.is_error and out.structured_content["error"]["code"] == "too_large", cap.id
                assert out.structured_content["error"]["hint"], cap.id
                assert len(out.content[0].text) < 2_000, cap.id  # nothing of the result went out
            assert ran == [cap.id for cap in reg.all()]  # each handler ran once: the refusal is the result's size
            assert (await c.call_tool("fabric_desktop_list_capabilities", {})).is_error  # still connected
    anyio.run(go)


def test_every_tools_oversized_result_is_refused_over_the_local_api(client):
    store, _, token = client
    reg, ran = answering(lambda cap: big)
    app = api(reg, store)
    for cap in reg.all():
        res = rest(app, cap.id, valid_args(cap), token)
        assert res.status_code == 413 and res.json()["error"]["code"] == "too_large", (cap.id, res.status_code)
        assert len(res.content) < 2_000 and res.json()["error"]["hint"], cap.id
    assert sorted(ran) == sorted(c.id for c in reg.all())


def test_every_oversized_resource_is_refused_over_mcp():
    reg, _ = answering(lambda cap: big)
    resources = reg.resources()
    assert resources, "the registry has resources"

    async def go():
        async with Client(build_server(reg, ENGINE, "cl_t")) as c:
            listed = {str(r.uri) for r in (await c.list_resources()).resources}
            for uri in sorted(u for u in listed if "{" not in u):
                with pytest.raises(Exception) as err:
                    await c.read_resource(uri)
                assert "KB" in str(err.value), uri
            assert listed, "some resources are listed"
    anyio.run(go)


# ---------------------------------------------------------------------------------- time limits

def test_every_tool_has_a_time_limit_of_an_hour_or_less():
    reg, _ = spied()
    for cap in reg.all():
        assert 0 < cap.timeout <= 3600, (cap.id, cap.timeout)
    assert {d["id"]: d["timeout_seconds"] for d in reg.describe()} == {c.id: c.timeout for c in reg.all()}


def shortened(reg: Registry) -> Registry:
    out = Registry(AllowAll())
    for cap in reg.all():
        out.register(dataclasses.replace(cap, timeout=SLOW_LIMIT))
    return out


def test_every_tool_that_runs_past_its_limit_times_out_over_mcp():
    reg, ran = answering(lambda cap: slow)
    reg = shortened(reg)

    async def go():
        async with Client(build_server(reg, ENGINE, "cl_t")) as c:
            for cap in reg.all():
                started = time.monotonic()
                out = await c.call_tool(cap.id, valid_args(cap))
                took = time.monotonic() - started
                assert out.is_error and out.structured_content["error"]["code"] == "timeout", cap.id
                assert out.structured_content["error"]["retryable"] is True, cap.id
                assert SLOW_LIMIT <= took < SLOW_LIMIT + 2, (cap.id, took)
    anyio.run(go)
    assert sorted(ran) == sorted(c.id for c in reg.all())


def test_every_tool_that_runs_past_its_limit_times_out_over_the_local_api(client):
    store, _, token = client
    reg, _ = answering(lambda cap: slow)
    app = api(shortened(reg), store)
    for cap in reg.all():
        started = time.monotonic()
        res = rest(app, cap.id, valid_args(cap), token)
        took = time.monotonic() - started
        assert res.json()["error"]["code"] == "timeout" and res.json()["error"]["retryable"] is True, cap.id
        assert SLOW_LIMIT <= took < SLOW_LIMIT + 2, (cap.id, took)


# -------------------------------------------------------------------------------- loopback only

NOT_THIS_PC = ("192.168.1.20", "10.0.0.5", "172.17.0.1", "8.8.8.8", "fe80::1", "2001:db8::1", "0.0.0.0",  # noqa: S104 - a caller address, not a bind
               "::ffff:192.168.1.20")
OTHER_HOSTS = ("example.com", "kratosforge.local", f"192.168.1.20:{PORT}", f"127.0.0.1:{PORT + 1}",
               f"localhost.evil.example:{PORT}", f"127.0.0.1.nip.io:{PORT}")


def test_every_tool_refuses_a_caller_not_on_this_pc_before_anything_runs(client):
    store, _, token = client
    reg, ran = answering(lambda cap: lambda ctx: {"ran": True})
    app = api(reg, store)
    for cap in reg.all():
        args = valid_args(cap)
        for remote in NOT_THIS_PC:
            res = rest(app, cap.id, args, token, client_host=remote)
            assert res.status_code == 403 and res.json()["ok"] is False, (cap.id, remote, res.status_code)
        for host in OTHER_HOSTS:
            res = rest(app, cap.id, args, token, host=host)
            assert res.status_code in (400, 403) and res.json()["ok"] is False, (cap.id, host, res.status_code)
    assert ran == []
    # and the same caller from this PC gets through, so the refusals above aren't for another reason
    cap = reg.get("fabric_desktop_get_status")
    assert rest(app, cap.id, valid_args(cap), token).status_code == 200 and ran == [cap.id]


def test_mcp_over_http_refuses_a_caller_not_on_this_pc(client):
    store, _, token = client
    reg, ran = answering(lambda cap: lambda ctx: {"ran": True})
    app = api(reg, store)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "fabric_desktop_get_status", "arguments": {}}}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    for remote in NOT_THIS_PC:
        res = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=(remote, 50000)).post(
            "/mcp", json=body, headers=headers)
        assert res.status_code == 403, (remote, res.status_code)
    assert ran == []


def other_address() -> str | None:
    """An address of this machine that isn't loopback, if it has one."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # no packet is sent: this only picks a route
            addr = s.getsockname()[0]
        except OSError:
            return None
    return None if addr.startswith("127.") else addr


def test_the_local_api_listens_on_loopback_only(client, monkeypatch):
    store, _, token = client
    monkeypatch.setattr(http_server, "BODY_SECONDS", 0.5)
    reg, _ = answering(lambda cap: lambda ctx: {"ran": True})
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    config = server_config(api(reg, store, port), port)
    assert config.host == "127.0.0.1"
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        addr = other_address()
        if addr is None:
            pytest.skip("this machine has no address but loopback")
        with pytest.raises(OSError):
            socket.create_connection((addr, port), timeout=2).close()
    finally:
        server.should_exit = True
        thread.join(10)
