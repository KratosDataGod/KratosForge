"""An audit line for every call, whatever happens to it, on every route in (PR-AI-010 hardening).

Like test_integrations_tool_guards.py, the tests walk the whole registry (core and built-in
extensions), so a tool added later is covered. Each tool's handler is a spy that answers, fails,
answers too much or runs too long on demand, and the real audit sink (IntegrationAudit.record)
writes the lines. For each call the line must name the right tool, client, channel and outcome, and
carry an execution id only when the call is about one.

What writes a line, pinned here:

- Every call that reaches ``Registry.invoke``, on every transport: done, bad arguments, an unknown
  tool, refused by permissions, waiting for approval, timed out, cancelled, failed. One line each
  (an approved call writes a "started" line too: test_integrations_audit.py).
- A result too big to send is recorded as the call ended, ``ok``: the handler ran and finished; the
  transport then refused to send it (the caller sees too_large, nothing of the result went out).
- A REST call refused before invoke for its body (not JSON, the wrong shape), a resource URI that
  names nothing, and a call refused because the server is busy: one line each, for that client.
- A request the guard refuses (not this PC, wrong Host, a browser Origin, too big, no or a bad
  token, rate limited): one ``http_request`` line, at most one per reason and caller every
  REFUSED_EVERY seconds, the next line counting the rest.
- Not recorded: listings (tools/list, GET /capabilities) and the open endpoints, which aren't calls;
  and a JSON-RPC message the MCP SDK refuses before KratosForge sees it (params of the wrong shape).

No line ever holds a secret, code or long text from the arguments, the URI or an error message.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import anyio
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from starlette.testclient import TestClient

from fabric_desktop.integrations import http_server, mcp_server, peer
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError, validate
from fabric_desktop.integrations.http_server import RateLimiter, build_app
from fabric_desktop.integrations.mcp_server import build_server
from fabric_desktop.integrations.permissions import OFFERED, PermissionAuthorizer
from fabric_desktop.integrations.registry import Registry
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_framing_fuzz import Stdio
from test_integrations_limits import AllowAll, valid_args
from test_integrations_tool_guards import spied

PORT = 47615
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
EXECUTION = "ex_" + "b" * 20
_EXECUTION_ID = re.compile(r"ex_[0-9a-f]{20}")
SHORT = 0.1  # a tool's time limit for the timeout tests
TRANSPORTS = ("direct", "memory", "rest")
CHANNEL = {"direct": "test", "memory": "mcp-stdio", "rest": "api"}
UNKNOWN = ("fabric_desktop_nope", "x", "FABRIC_DESKTOP_GET_STATUS", "fabric_desktop_get_status ", "ext_été")


def ENGINE():  # noqa: N802 - a stand-in for the engine's Service; the spied handlers never use it
    return object()


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setattr(peer, "checked", lambda: False)  # the test client has no socket to own
    for module in (mcp_server, http_server):
        monkeypatch.setattr(module, "MAX_RESULT_BYTES", 20_000)


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations" / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


class Boom(Exception):
    """Not one of the engine's exceptions, so it is shaped as ``internal``."""


def minimal(schema):
    """The smallest value ``schema`` accepts, for the JSON Schema keywords the capabilities use."""
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return minimal(schema["anyOf"][0])
    kind = schema.get("type", "object")
    kind = kind[0] if isinstance(kind, list) else kind
    if kind == "object":
        props = schema.get("properties", {})
        return {key: minimal(props.get(key, {})) for key in schema.get("required", [])}
    return {"string": "x", "integer": 0, "number": 0, "boolean": False, "array": [], "null": None}[kind]


class Trail:
    """The real audit sink, also keeping each line it wrote, in order."""

    def __init__(self, store):
        self.audit = IntegrationAudit(store)
        self.lines: list[dict] = []
        self._lock = threading.Lock()

    def record(self, *args, **kwargs):
        entry = self.audit.record(*args, **kwargs)
        with self._lock:
            self.lines.append(entry)
        return entry

    def mark(self) -> int:
        return len(self.lines)

    def since(self, mark: int, count: int | None = None, timeout: float = 15.0) -> list[dict]:
        """The lines written after ``mark``; with ``count``, waiting up to ``timeout`` for that many
        (a cancelled call's worker writes its line after the caller has gone)."""
        deadline = time.monotonic() + timeout
        while count is not None and len(self.lines) - mark < count and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.lines[mark:]


class Tools:
    """Every capability, its handler a spy whose behaviour is ``self.mode``; the real audit sink."""

    def __init__(self, store, authorizer=None, timeout: float | None = None, timeouts: dict | None = None):
        base, _ = spied()
        self.mode = "ok"
        self.ran: list[str] = []
        self.calls: list[tuple[str, dict]] = []  # (tool, the arguments its handler got)
        self._lock = threading.Lock()
        self.reg = Registry(authorizer or AllowAll())
        for cap in base.all():
            changes = {"handler": self._handler(cap)}
            if timeout is not None or cap.id in (timeouts or {}):
                changes["timeout"] = (timeouts or {}).get(cap.id, timeout)
            self.reg.register(dataclasses.replace(cap, **changes))
        self.trail = Trail(store)
        self.reg.audit = self.trail.record

    def _handler(self, cap):
        cap_id = cap.id

        def answer(**extra):
            # An answer the tool's output schema accepts (the MCP client checks it), plus ``extra``
            # where the schema takes more fields.
            out = minimal(cap.output_schema)
            return out if cap.output_schema.get("additionalProperties") is False else out | extra

        def run(svc, ctx, **args):
            with self._lock:
                self.ran.append(cap_id)
                self.calls.append((cap_id, args))
            mode = self.mode
            if mode == "boom":
                raise Boom(f"broke; Authorization: Bearer {JWT}")
            if mode == "big":
                return {"blob": "x" * 30_000}
            if mode == "execution":
                return answer(execution_id=EXECUTION)
            if mode == "slow":
                # Stops the documented way, ctx.check(): a handler that returns instead when it sees
                # the flag can finish before run_call notices, and is then recorded as the call ended.
                stop = time.monotonic() + 30
                while time.monotonic() < stop:
                    ctx.check()
                    time.sleep(0.01)
                return answer(late=True)
            return answer(ran=cap_id)
        return run

    def caps(self):
        return self.reg.all()


def code_of_mcp(result) -> str:
    return result.structured_content["error"]["code"] if result.is_error else "ok"


def code_of_rest(res) -> str:
    data = res.json()
    return "ok" if data["ok"] else data["error"]["code"]


def rest_client(app, client_host="127.0.0.1"):
    # Not entered as a context manager: the REST routes don't need the app's lifespan.
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=(client_host, 50000))


def api(tools, store, limiter=None):
    return build_app(tools.reg, store, PORT, ENGINE, limiter=limiter or RateLimiter(rate=1e6, burst=10**6))


def post(c, cap_id, args, token, **kw):
    return c.post(f"/api/v1/capabilities/{cap_id}/invoke", json={"arguments": args},
                  headers={"Authorization": f"Bearer {token}"}, **kw)


def run_calls(transport, tools, store, client, token, calls):
    """Make each (tool, arguments, handler mode) call in turn over ``transport``; what the caller saw."""
    seen: list[str] = []
    if transport == "direct":
        for cap_id, args, mode in calls:
            tools.mode = mode
            try:
                tools.reg.invoke(ENGINE(), cap_id, args, CallContext(Caller(client["id"], "test"), timeout=60))
                seen.append("ok")
            except IntegrationError as err:
                seen.append(err.code)
    elif transport == "memory":
        async def go():
            async with Client(build_server(tools.reg, ENGINE, client["id"])) as c:
                for cap_id, args, mode in calls:
                    tools.mode = mode
                    seen.append(code_of_mcp(await c.call_tool(cap_id, args)))
        anyio.run(go)
    else:
        c = rest_client(api(tools, store))
        for cap_id, args, mode in calls:
            tools.mode = mode
            seen.append(code_of_rest(post(c, cap_id, args, token)))
    return seen


def execution_of(tools, cap_id, args) -> str:
    """The execution id a line should carry: the one a tool that takes one was given, if well formed."""
    cap = tools.reg.get(cap_id)
    value = args.get("execution_id") if cap is not None and isinstance(args, dict) and \
        "execution_id" in cap.input_schema.get("properties", {}) else None
    return value if isinstance(value, str) and _EXECUTION_ID.fullmatch(value) else ""


def check_lines(lines, calls, seen, client_id, channel, tools):
    assert len(lines) == len(calls), (len(lines), len(calls))
    wrong = []
    for line, (cap_id, args, mode), code in zip(lines, calls, seen, strict=True):
        # A result too big to send was refused by the transport after the call ended well.
        outcome = "ok" if code == "too_large" and mode == "big" else code
        want = (cap_id, outcome, client_id, channel, execution_of(tools, cap_id, args))
        got = (line["capability"], line["outcome"], line["client_id"], line["channel"], line["execution_id"])
        if got != want:
            wrong.append(f"{want} -> {got}")
    assert wrong == [], "\n".join(wrong)


def http_client(store, grants=None):
    return store.add_client("Script", "other", transport="http", grants=grants)


# ------------------------------------------------------------------------------ every tool, every outcome

@pytest.mark.parametrize("transport", TRANSPORTS)
def test_every_tool_and_outcome_writes_exactly_one_line(store, transport):
    tools = Tools(store)
    client, token = http_client(store)
    calls = []
    for cap in tools.caps():
        good = valid_args(cap)
        calls += [(cap.id, good, "ok"), (cap.id, {"zz_not_an_argument": JWT}, "ok"), (cap.id, good, "boom"),
                  (cap.id, good, "big")]
    calls += [(name, {}, "ok") for name in UNKNOWN]
    seen = run_calls(transport, tools, store, client, token, calls)
    expected = {"ok": "ok", "boom": "internal", "big": "ok" if transport == "direct" else "too_large"}
    for (cap_id, args, mode), code in zip(calls, seen, strict=True):
        want = "not_found" if cap_id in UNKNOWN else "invalid_input" if "zz_not_an_argument" in args else expected[mode]
        assert code == want, (cap_id, mode, code)
    check_lines(tools.trail.lines, calls, seen, client["id"], CHANNEL[transport], tools)
    # Handlers ran only for valid calls, and every line's details hold.
    assert len(tools.ran) == 3 * len(tools.caps())
    for line, (cap_id, args, mode) in zip(tools.trail.lines, calls, strict=True):
        assert line["permission"] == (tools.reg.get(cap_id).permission if tools.reg.get(cap_id) else "")
        assert line["decision"] == ("invalid" if cap_id in UNKNOWN or "zz_not_an_argument" in args else "allowed")
        assert line["client"] == "Script" and line["seconds"] >= 0
        if mode == "boom":
            assert line["error"].startswith("Boom: broke") and "[removed]" in line["error"]
    assert JWT not in json.dumps(tools.trail.lines)
    assert tools.trail.audit.verify()["ok"]


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_a_call_about_an_execution_carries_its_id_and_no_other_does(store, transport):
    tools = Tools(store)
    client, token = http_client(store)
    calls = [(cap.id, valid_args(cap), "execution") for cap in tools.caps()]
    seen = run_calls(transport, tools, store, client, token, calls)
    assert set(seen) == {"ok"}
    for line in tools.trail.lines:  # the result names one: every tool's line carries it
        closed = tools.reg.get(line["capability"]).output_schema.get("additionalProperties") is False
        assert line["execution_id"] == ("" if closed else EXECUTION), line["capability"]
    mark = tools.trail.mark()
    calls = [(cap.id, valid_args(cap), "ok") for cap in tools.caps()]
    seen = run_calls(transport, tools, store, client, token, calls)
    check_lines(tools.trail.since(mark), calls, seen, client["id"], CHANNEL[transport], tools)
    takes = [line for line in tools.trail.since(mark) if line["execution_id"]]
    assert takes and all(line["execution_id"] == "ex_" + "a" * 20 for line in takes)  # valid_args' id


# ------------------------------------------------------------------------------ permissions and approvals

def expected_permission(cap, client) -> str:
    if cap.public:
        return "ok"
    if cap.permission not in OFFERED or cap.permission not in client["grants"]:
        return "permission_denied"
    if cap.approval == "always" or cap.destructive or (cap.approval == "ask" and cap.permission not in client["auto"]):
        return "approval_required"
    return "ok"


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_every_permission_decision_is_recorded_for_the_client_that_asked(store, transport):
    tools = Tools(store, PermissionAuthorizer(store, wait_seconds=0.05, poll=0.02))
    for grants in ([], list(OFFERED)):
        client, token = http_client(store, grants)
        mark = tools.trail.mark()
        calls = [(cap.id, valid_args(cap), "ok") for cap in tools.caps()]
        seen = run_calls(transport, tools, store, client, token, calls)
        assert seen == [expected_permission(tools.reg.get(c), client) for c, _, _ in calls]
        lines = tools.trail.since(mark)
        check_lines(lines, calls, seen, client["id"], CHANNEL[transport], tools)
        decisions = {"ok": "allowed", "permission_denied": "denied", "approval_required": "approval_pending"}
        assert [line["decision"] for line in lines] == [decisions[code] for code in seen]
        assert all("approval_id" not in line for line in lines)  # nothing was approved
    assert {"ok", "permission_denied", "approval_required"} <= {line["outcome"] for line in tools.trail.lines}
    # Turned off: every call is refused, and recorded, as such.
    store.set_setting("enabled", False)
    mark = tools.trail.mark()
    calls = [(cap.id, valid_args(cap), "ok") for cap in tools.caps()]
    seen = run_calls(transport, tools, store, client, token, calls)
    assert {code for (c, _, _), code in zip(calls, seen, strict=True) if not tools.reg.get(c).public} == {"integrations_disabled"}
    check_lines(tools.trail.since(mark), calls, seen, client["id"], CHANNEL[transport], tools)


def test_a_client_that_isnt_connected_is_recorded_under_the_id_it_gave(store):
    tools = Tools(store, PermissionAuthorizer(store, wait_seconds=0.05, poll=0.02))
    calls = [(cap.id, valid_args(cap), "ok") for cap in tools.caps()]
    seen = run_calls("memory", tools, store, {"id": "cl_stranger"}, None, calls)
    assert {code for (c, _, _), code in zip(calls, seen, strict=True) if not tools.reg.get(c).public} == {"permission_denied"}
    check_lines(tools.trail.lines, calls, seen, "cl_stranger", "mcp-stdio", tools)
    assert tools.ran == [c for c, _, _ in calls if tools.reg.get(c).public]


# ------------------------------------------------------------------------------ timeouts and cancellation

@pytest.mark.parametrize("transport", TRANSPORTS)
def test_every_tool_that_times_out_is_recorded_as_a_timeout(store, transport):
    tools = Tools(store, timeout=SHORT)
    tools.mode = "slow"
    client, token = http_client(store)
    caps = tools.caps()
    if transport == "direct":
        def one(cap):
            try:
                tools.reg.invoke(ENGINE(), cap.id, valid_args(cap), CallContext(Caller(client["id"], "test")))
            except IntegrationError as err:
                return err.code
            return "ok"
        with ThreadPoolExecutor(16) as pool:
            seen = list(pool.map(one, caps))
    elif transport == "memory":
        seen = [""] * len(caps)

        async def go():
            async with Client(build_server(tools.reg, ENGINE, client["id"])) as c:
                async def one(i, cap):
                    seen[i] = code_of_mcp(await c.call_tool(cap.id, valid_args(cap)))
                async with anyio.create_task_group() as tg:
                    for i, cap in enumerate(caps):
                        tg.start_soon(one, i, cap)
        anyio.run(go)
    else:
        with TestClient(api(tools, store), base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000)) as c, \
                ThreadPoolExecutor(8) as pool:
            seen = list(pool.map(lambda cap: code_of_rest(post(c, cap.id, valid_args(cap), token)), caps))
    assert set(seen) == {"timeout"}
    lines = tools.trail.since(0, len(caps))
    assert sorted((line["capability"], line["outcome"], line["client_id"], line["channel"]) for line in lines) == \
        sorted((cap.id, "timeout", client["id"], CHANNEL[transport]) for cap in caps)


def test_every_tool_cancelled_by_its_caller_is_recorded_as_cancelled(store):
    tools = Tools(store)
    tools.mode = "slow"
    caps = tools.caps()
    contexts = [CallContext(Caller("cl_direct", "test")) for _ in caps]

    def one(cap, ctx):
        with pytest.raises(IntegrationError) as err:
            tools.reg.invoke(ENGINE(), cap.id, valid_args(cap), ctx)
        return err.value.code
    with ThreadPoolExecutor(len(caps)) as pool:
        futures = [pool.submit(one, cap, ctx) for cap, ctx in zip(caps, contexts, strict=True)]
        deadline = time.monotonic() + 20
        while len(tools.ran) < len(caps) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(tools.ran) == len(caps), "not every handler started"
        for ctx in contexts:
            ctx.cancel()
        assert {f.result(20) for f in futures} == {"cancelled"}
    assert sorted((line["capability"], line["outcome"], line["client_id"]) for line in tools.trail.lines) == \
        sorted((cap.id, "cancelled", "cl_direct") for cap in caps)


def test_a_call_cancelled_over_mcp_is_recorded_as_cancelled(store):
    """notifications/cancelled: the client gives up, the server stops the handler, and the worker
    thread, which outlives the request, still writes the line."""
    tools = Tools(store)
    tools.mode = "slow"
    sample = [cap for cap in tools.caps() if cap.read_only][:6] + [tools.reg.get("fabric_desktop_execute_python")]

    async def go():
        async with Client(build_server(tools.reg, ENGINE, "cl_mcp"), mode="legacy") as c:
            async with anyio.create_task_group() as tg:
                for cap in sample:
                    tg.start_soon(c.call_tool, cap.id, valid_args(cap))
                for _ in range(1000):
                    if len(tools.ran) == len(sample):
                        break
                    await anyio.sleep(0.02)
                tg.cancel_scope.cancel()
            assert len(tools.ran) == len(sample)
            lines = await anyio.to_thread.run_sync(lambda: tools.trail.since(0, len(sample)))
            assert sorted((line["capability"], line["outcome"], line["channel"], line["client_id"]) for line in lines) == \
                sorted((cap.id, "cancelled", "mcp-stdio", "cl_mcp") for cap in sample)
            tools.mode = "ok"
            assert not (await c.call_tool("fabric_desktop_get_status", {})).is_error  # still connected
    anyio.run(go)


# ------------------------------------------------------------------------------ refused before invoke (REST)

def test_a_rest_call_refused_for_its_body_is_recorded(store):
    tools = Tools(store)
    client, token = http_client(store)
    c = rest_client(api(tools, store))
    cap_id = "fabric_desktop_get_status"
    bodies = [b"not json {", b"[1, 2]", b'{"arguments": 5}', b'{"arguments": {}, "extra": 1}', b"null",
              b'"' + JWT.encode() + b'"', b"[" * 20_000 + b"]" * 20_000]
    for body in bodies:
        res = c.post(f"/api/v1/capabilities/{cap_id}/invoke", content=body, headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_input", (body[:30], res.text[:200])
    res = c.post("/api/v1/capabilities/fabric_desktop_nope/invoke", content=b"{", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 400
    assert tools.ran == []
    lines = tools.trail.lines
    assert [(line["capability"], line["outcome"], line["decision"], line["client_id"], line["channel"], line["execution_id"])
            for line in lines] == [(cap_id, "invalid_input", "invalid", client["id"], "api", "")] * len(bodies) + [
        ("fabric_desktop_nope", "invalid_input", "invalid", client["id"], "api", "")]
    assert lines[0]["permission"] == "READ_METADATA" and lines[-1]["permission"] == ""
    assert JWT not in json.dumps(lines)


def test_a_resource_uri_that_cant_be_read_is_recorded(store):
    tools = Tools(store)
    client, token = http_client(store)
    c = rest_client(api(tools, store))
    auth = {"Authorization": f"Bearer {token}"}
    cases = [("fabric-desktop://nothing/here", 404, "not_found"), ("https://example.com/", 404, "not_found"),
             ("fabric-desktop://notebooks?nope=1", 400, "invalid_input"),
             ("fabric-desktop://notebooks?limit=lots", 400, "invalid_input"),
             (f"fabric-desktop://nothing?token={JWT}", 404, "not_found"), ("x" * 3000, 404, "not_found")]
    for uri, status, code in cases:
        res = c.get("/api/v1/resources/read", params={"uri": uri}, headers=auth)
        assert (res.status_code, res.json()["error"]["code"]) == (status, code), uri
    good = c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://notebooks?limit=5"}, headers=auth)
    assert good.status_code == 200
    lines = tools.trail.lines
    assert [(line["capability"], line["outcome"], line["client_id"], line["channel"]) for line in lines] == [
        ("resources/read", code, client["id"], "api") for _, _, code in cases] + [
        ("fabric_desktop_list_notebooks", "ok", client["id"], "api")]
    assert lines[0]["arguments"] == {"uri": "fabric-desktop://nothing/here"}
    assert lines[-2]["arguments"] == {"uri": "(given)"}  # past the length a line keeps
    assert JWT not in json.dumps(lines) and tools.ran == ["fabric_desktop_list_notebooks"]


def test_a_resource_uri_that_cant_be_read_over_mcp_is_recorded(store):
    tools = Tools(store)

    async def go():
        async with Client(build_server(tools.reg, ENGINE, "cl_mcp")) as c:
            for uri in ("fabric-desktop://nothing/here", "fabric-desktop://notebooks?limit=lots"):
                with pytest.raises(MCPError):
                    await c.read_resource(uri)
            await c.read_resource("fabric-desktop://notebooks")
    anyio.run(go)
    assert [(line["capability"], line["outcome"], line["client_id"], line["channel"]) for line in tools.trail.lines] == [
        ("resources/read", "not_found", "cl_mcp", "mcp-stdio"), ("resources/read", "invalid_input", "cl_mcp", "mcp-stdio"),
        ("fabric_desktop_list_notebooks", "ok", "cl_mcp", "mcp-stdio")]


def test_a_rest_call_refused_because_the_server_is_busy_is_recorded(store, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_CONCURRENT", 1)
    monkeypatch.setattr(http_server, "QUEUE_WAIT_SECONDS", 0.1)
    tools = Tools(store, timeout=1.5)
    client, token = http_client(store)
    with TestClient(api(tools, store), base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000)) as c:
        tools.mode = "slow"
        with ThreadPoolExecutor(1) as pool:
            first = pool.submit(post, c, "fabric_desktop_list_projects", {}, token)
            deadline = time.monotonic() + 10
            while not tools.ran and time.monotonic() < deadline:
                time.sleep(0.01)
            busy = post(c, "fabric_desktop_get_status", {}, token)
            assert busy.status_code == 429 and busy.json()["error"]["code"] == "rate_limited"
            assert first.result(10).json()["error"]["code"] == "timeout"
    assert tools.ran == ["fabric_desktop_list_projects"]
    assert sorted((line["capability"], line["outcome"], line["decision"], line["client_id"]) for line in tools.trail.lines) == [
        ("fabric_desktop_get_status", "rate_limited", "denied", client["id"]),
        ("fabric_desktop_list_projects", "timeout", "allowed", client["id"])]


# ------------------------------------------------------------------------------ refused at the guard

def test_every_guard_refusal_is_recorded_once_and_never_runs_anything(store, monkeypatch):
    monkeypatch.setattr(http_server, "REFUSED_EVERY", 0.0)  # every refusal gets its line here; throttling below
    tools = Tools(store)
    client, token = http_client(store, ["READ_METADATA"])
    app = build_app(tools.reg, store, PORT, ENGINE, limiter=RateLimiter(rate=1e-6, burst=2))
    path = "/api/v1/capabilities/fabric_desktop_get_status/invoke"
    auth = {"Authorization": f"Bearer {token}"}
    body = {"arguments": {}}
    cases = [  # (what, request, status, code, client id on the line, channel)
        ("not this PC", lambda: rest_client(app, "192.168.1.20").post(path, json=body, headers=auth), 403, "permission_denied", "", "api"),
        ("not this PC, MCP", lambda: rest_client(app, "10.0.0.5").post("/mcp", json={}, headers=auth), 403, "permission_denied", "", "mcp-http"),
        ("another Host", lambda: rest_client(app).post(path, json=body, headers={**auth, "Host": "evil.example"}), 403, "permission_denied", "", "api"),
        ("a web page", lambda: rest_client(app).post(path, json=body, headers={**auth, "Origin": "https://evil.example"}), 403, "permission_denied", "", "api"),
        ("too big", lambda: rest_client(app).post(path, content=b"{}", headers={**auth, "Content-Length": str(2 * 1024 * 1024)}), 413, "too_large", "", "api"),
        ("no token", lambda: rest_client(app).post(path, json=body), 401, "permission_denied", "", "api"),
        ("a bad token", lambda: rest_client(app).post(path, json=body, headers={"Authorization": f"Bearer {JWT}"}), 401, "permission_denied", "", "api"),
        ("a bad token, MCP", lambda: rest_client(app).post("/mcp", json={}, headers={"Authorization": "Bearer nope"}), 401, "permission_denied", "", "mcp-http"),
        ("allowed", lambda: rest_client(app).post(path, json=body, headers=auth), 200, "ok", client["id"], "api"),
        ("allowed again", lambda: rest_client(app).post(path, json=body, headers=auth), 200, "ok", client["id"], "api"),
        ("rate limited", lambda: rest_client(app).post(path, json=body, headers=auth), 429, "rate_limited", client["id"], "api"),
    ]
    for what, request, status, code, client_id, channel in cases:
        mark = tools.trail.mark()
        res = request()
        assert res.status_code == status, (what, res.status_code, res.text[:200])
        [line] = tools.trail.since(mark)
        capability = "fabric_desktop_get_status" if code == "ok" else "http_request"
        assert (line["capability"], line["outcome"], line["client_id"], line["channel"], line["execution_id"]) == \
            (capability, code, client_id, channel, ""), what
        if capability == "http_request":
            assert line["arguments"]["method"] == "POST" and line["arguments"]["path"] in (path, "/mcp"), what
            assert line["decision"] == "denied" or code == "too_large", what
    assert tools.ran == ["fabric_desktop_get_status"] * 2
    assert token not in json.dumps(tools.trail.lines) and JWT not in json.dumps(tools.trail.lines)


def test_a_flood_of_refusals_is_recorded_once_per_reason_and_caller_then_counted(store, monkeypatch):
    monkeypatch.setattr(http_server, "REFUSED_EVERY", 0.3)
    tools = Tools(store)
    app = build_app(tools.reg, store, PORT, ENGINE)
    c = rest_client(app)
    for _ in range(4):
        assert c.get("/api/v1/capabilities").status_code == 401
    assert c.get("/api/v1/capabilities", headers={"Origin": "https://evil.example"}).status_code == 403  # another reason
    assert len(tools.trail.lines) == 2
    time.sleep(0.35)
    assert c.get("/api/v1/capabilities").status_code == 401
    first, origin, later = tools.trail.lines
    assert first["arguments"] == {"method": "GET", "path": "/api/v1/capabilities"} and first["outcome"] == "permission_denied"
    assert origin["error"].startswith("Requests from web pages")
    assert later["arguments"]["refused_since_last_line"] == 3


def test_listings_and_open_endpoints_are_not_calls(store):
    tools = Tools(store)
    client, token = http_client(store, ["READ_METADATA"])
    c = rest_client(api(tools, store))
    auth = {"Authorization": f"Bearer {token}"}
    for path in ("/api/v1/health", "/api/v1/openapi.json", "/api/v1/capabilities", "/api/v1/resources"):
        assert c.get(path, headers=auth).status_code == 200, path

    async def go():
        async with Client(build_server(tools.reg, ENGINE, client["id"])) as m:
            await m.list_tools()
            await m.list_resources()
            await m.list_resource_templates()
            await m.list_prompts()
    anyio.run(go)
    assert tools.trail.lines == [] and tools.ran == []


# ------------------------------------------------------------------------------ secrets

def test_no_line_holds_a_secret_code_or_long_text_from_the_arguments(store):
    tools = Tools(store)
    client, token = http_client(store)
    secrets_ = ("SECRET-ONE", "SECRET-TWO", "SECRET-THREE", "SECRET-FOUR", JWT)
    extra = {"token": "SECRET-ONE", "client_secret": "SECRET-TWO", "password": "x", "query": "SELECT 'SECRET-THREE'",
             "code": "print('SECRET-THREE')", "note": f"Authorization: Bearer {JWT}", "long": "y" * 500 + "SECRET-FOUR",
             "nested": {"api_token": "SECRET-ONE", "deeper": {"password": "SECRET-TWO"}}, "cells": ["SECRET-THREE"]}
    calls = []
    for cap in tools.caps():
        good = valid_args(cap)
        calls.append((cap.id, {**good, **extra}, "ok"))  # refused for the fields it doesn't take, but recorded
        for field, prop in cap.input_schema.get("properties", {}).items():  # valid, with a token for a value
            if "string" in ([prop.get("type")] if isinstance(prop.get("type"), str) else prop.get("type") or []):
                try:
                    validate(cap.input_schema, {**good, field: f"Bearer {JWT}"})
                except IntegrationError:
                    continue
                calls.append((cap.id, {**good, field: f"Bearer {JWT}"}, "ok"))
    seen = run_calls("rest", tools, store, client, token, calls)
    check_lines(tools.trail.lines, calls, seen, client["id"], "api", tools)
    assert "ok" in seen and "invalid_input" in seen
    text = json.dumps(tools.trail.lines)
    leaked = [s for s in secrets_ if s in text]
    assert leaked == [], leaked
    files = "".join(p.read_text() for p in tools.trail.audit.folder.glob("audit-*.jsonl"))
    assert not [s for s in secrets_ if s in files]


def test_a_deeply_nested_argument_still_gets_its_line(store):
    tools = Tools(store)
    client, token = http_client(store)
    deep: dict = {}
    inner = deep
    for _ in range(3000):
        inner["a"] = {}
        inner = inner["a"]
    seen = run_calls("direct", tools, store, client, token, [("fabric_desktop_get_status", {"zz": deep}, "ok")])
    assert seen == ["invalid_input"]
    [line] = tools.trail.lines
    assert line["outcome"] == "invalid_input" and line["capability"] == "fabric_desktop_get_status"


# ------------------------------------------------------------------------------ the real processes

def test_a_sample_over_the_real_stdio_process(tmp_path):
    root = tmp_path / "FabricDesktop"
    store = IntegrationStore.at(root)
    store.set_setting("enabled", True)
    client, _ = store.add_client("Stdio tool", grants=["READ_METADATA"])
    proc = Stdio(tmp_path, client["id"])
    try:
        proc.initialize()
        calls = [("fabric_desktop_get_status", {}, "ok"), ("fabric_desktop_nope", {}, "not_found"),
                 ("fabric_desktop_get_project", {"id": "everything", "token": JWT}, "invalid_input"),
                 ("fabric_desktop_execute_python", {"code": "print('SECRET-FIVE')"}, "permission_denied")]
        for name, args, code in calls:
            answer, _ = proc.call("tools/call", {"name": name, "arguments": args})
            result = answer["result"]
            assert (result["structuredContent"]["error"]["code"] if result.get("isError") else "ok") == code, answer
        answer, _ = proc.call("resources/read", {"uri": "fabric-desktop://nothing/here"})
        assert answer["error"]["code"] == -32002
        # Refused by the MCP SDK before KratosForge sees it: no line (nothing names a call it could make).
        for params in ({"name": "fabric_desktop_get_status", "arguments": "x"}, {"name": 5}):
            answer, _ = proc.call("tools/call", params)
            assert answer["error"]["code"] == -32602, answer
        audit = IntegrationAudit(store)
        lines = audit.recent(100)[::-1]
        assert [(line["capability"], line["outcome"], line["client_id"], line["channel"], line["execution_id"])
                for line in lines] == [(name, code, client["id"], "mcp-stdio", "") for name, _, code in calls] + [
            ("resources/read", "not_found", client["id"], "mcp-stdio", "")]
        assert lines[0]["client"] == "Stdio tool" and audit.verify()["ok"]
        assert JWT not in json.dumps(lines) and "SECRET-FIVE" not in json.dumps(lines)
    finally:
        proc.close()


@contextlib.contextmanager
def served(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app(port), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the local API server didn't start"
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def test_a_sample_over_real_mcp_over_http_and_a_rest_client_that_hangs_up(store):
    tools = Tools(store, timeouts={"fabric_desktop_list_notebooks": 0.5})
    client, token = http_client(store)
    with served(lambda port: build_app(tools.reg, store, port, ENGINE, limiter=RateLimiter(rate=1e6, burst=10**6))) as port:
        calls = [("fabric_desktop_get_status", {}, "ok", "ok"), ("fabric_desktop_get_project", {"id": "everything"}, "ok", "invalid_input"),
                 ("fabric_desktop_nope", {}, "ok", "not_found"), ("fabric_desktop_list_projects", {}, "boom", "internal"),
                 ("fabric_desktop_list_notebooks", {}, "slow", "timeout"), ("fabric_desktop_list_proofs", {}, "big", "too_large"),
                 ("fabric_desktop_get_execution_status", {"execution_id": EXECUTION}, "ok", "ok")]

        async def go():
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
            try:
                async with Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as c:
                    for name, args, mode, code in calls:
                        tools.mode = mode
                        assert code_of_mcp(await c.call_tool(name, args)) == code, name
            finally:
                await http.aclose()
        anyio.run(go)
        lines = tools.trail.since(0)
        assert [(line["capability"], line["outcome"], line["client_id"], line["channel"], line["execution_id"]) for line in lines] == [
            (name, "ok" if code == "too_large" else code, client["id"], "mcp-http", EXECUTION if "execution_id" in args else "")
            for name, args, _, code in calls]
        # A refused token on the real socket: one http_request line on the MCP channel.
        mark = tools.trail.mark()
        assert httpx2.post(f"http://127.0.0.1:{port}/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
        [refused] = tools.trail.since(mark)
        assert (refused["capability"], refused["channel"], refused["client_id"]) == ("http_request", "mcp-http", "")
        # A REST client that hangs up while its call runs: the call is cancelled, and recorded so.
        tools.mode = "slow"
        tools.ran.clear()
        mark = tools.trail.mark()
        body = b'{"arguments": {}}'
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall((f"POST /api/v1/capabilities/fabric_desktop_list_receipts/invoke HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                   f"Authorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body)
        deadline = time.monotonic() + 10
        while not tools.ran and time.monotonic() < deadline:
            time.sleep(0.01)
        assert tools.ran == ["fabric_desktop_list_receipts"]
        s.close()
        [line] = tools.trail.since(mark, 1)
        assert (line["capability"], line["outcome"], line["client_id"], line["channel"]) == \
            ("fabric_desktop_list_receipts", "cancelled", client["id"], "api")


def test_a_name_that_names_no_tool_is_redacted_and_capped_on_its_line():
    """An unknown tool's name is the caller's text: on its audit line, secrets are taken out of it
    and it is cut to 200 characters. A real tool's name is kept as it is."""
    reg, _ = spied()
    names = []
    reg.audit = lambda ctx, cap_id, *line, **kw: names.append(cap_id)
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlc2lnbmF0dXJl"
    for name in (f"Bearer {token}", "x" * 5000):
        with pytest.raises(IntegrationError):
            reg.invoke(object(), name, {}, CallContext(Caller("t", "test"), timeout=5))
        reg.refused(CallContext(Caller("t", "test")), name, None, IntegrationError("invalid_input", "no"))
    reg.invoke(object(), "fabric_desktop_get_status", {}, CallContext(Caller("t", "test"), timeout=5))
    assert all(token not in n and len(n) <= 200 for n in names[:4]), names
    assert names[-1] == "fabric_desktop_get_status"
