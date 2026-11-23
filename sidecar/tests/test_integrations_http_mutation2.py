"""Mutation testing, second pass: the local HTTP API's guard, bodies and refusals are exact.

Pins the strict JSON a REST body must be (no NaN or Infinity, no lone surrogate), the error body
(redacted, with its headers), the rate limiter's bucket per tool, each guard check and the audit
line a refused request gets (at most one per reason and caller every REFUSED_EVERY seconds, the
next one counting the rest), and the line for a REST call refused before ``Registry.invoke``: a
body that isn't JSON or has the wrong shape, a busy server. Nothing listens on a socket.
"""

from __future__ import annotations

import json

import anyio
import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from fabric_desktop.integrations import http_server, peer
from fabric_desktop.integrations.core import IntegrationError
from fabric_desktop.integrations.registry import Capability, Registry, schema
from fabric_desktop.integrations.store import IntegrationStore
from fabric_desktop.redact import REMOVED

PORT = 47615
HOST = {"host": f"127.0.0.1:{PORT}"}
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"  # pragma: allowlist secret (a made-up token)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setattr(peer, "checked", lambda: False)


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


class Lines(list):
    def __call__(self, *args, **kwargs):
        self.append(args)


# ---- _strict_json

@pytest.mark.parametrize("raw", [b'{"a": NaN}', b'{"a": Infinity}', b'{"a": -Infinity}', b"NaN"])
def test_nan_and_infinity_arent_json(raw):
    with pytest.raises(ValueError, match="isn't JSON"):
        http_server._strict_json(raw)


def test_a_lone_surrogate_isnt_json_either():
    with pytest.raises(ValueError):
        http_server._strict_json(b'{"a": "\\ud800"}')


def test_strict_json_returns_what_json_is():
    assert http_server._strict_json(b'{"a": [1, 2.5, "\\u00e9", "\\ud83d\\ude00", null, true]}') == \
        {"a": [1, 2.5, "é", "\U0001f600", None, True]}


# ---- error_response

def test_an_error_response_is_redacted_and_carries_its_headers():
    r = http_server.error_response(IntegrationError("not_found", f"no {JWT}", hint="h"), {"X-A": "1"})
    assert r.status_code == 404 and r.headers["x-a"] == "1"
    assert json.loads(r.body) == {"ok": False, "error": {"code": "not_found", "message": f"no {REMOVED}",
                                                         "retryable": False, "hint": "h"}}


# ---- RateLimiter

def test_each_tool_has_its_own_bucket_and_a_refusal_spends_nothing():
    now = [0.0]
    lim = http_server.RateLimiter(rate=1.0, burst=2, clock=lambda: now[0])
    assert [lim.allow("a"), lim.allow("a"), lim.allow("a")] == [True, True, False]
    assert lim.allow("b") is True
    now[0] = 0.5
    assert lim.allow("a") is False  # half a token
    now[0] = 1.0
    assert lim.allow("a") is True and lim.allow("a") is False


def test_a_bucket_refills_to_its_burst_and_no_more():
    now = [0.0]
    lim = http_server.RateLimiter(rate=1.0, burst=2, clock=lambda: now[0])
    lim.allow("a")
    now[0] = 1000.0
    assert [lim.allow("a") for _ in range(3)] == [True, True, False]


# ---- Guard

class Inner:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await JSONResponse({"ok": True})(scope, receive, send)


def _registry(audit=None):
    reg = Registry()
    reg.audit = audit
    return reg


def _through(guard, headers, client=("127.0.0.1", 50000), path="/api/v1/health", method="GET", body=b""):
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "client": client,
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    anyio.run(guard, scope, receive, send)
    status = sent[0]["status"]
    payload = json.loads(b"".join(m.get("body", b"") for m in sent[1:]) or b"{}")
    return status, payload


@pytest.mark.parametrize("client", [("127.0.0.1", 1), ("::1", 1), ("localhost", 1), ("testclient", 1)])
def test_loopback_callers_pass(store, client):
    guard = http_server.Guard(Inner(), store, PORT, same_user=lambda a, b: True)
    assert _through(guard, HOST, client=client)[0] == 200


@pytest.mark.parametrize("client", [("10.0.0.2", 1), ("127.0.0.2", 1), None])
def test_any_other_caller_is_refused(store, client):
    inner = Inner()
    guard = http_server.Guard(inner, store, PORT, same_user=lambda a, b: True)
    status, body = _through(guard, HOST, client=client)
    assert status == 403 and body["error"]["message"] == "Only this PC can use the local API."
    assert inner.calls == 0


def test_a_program_running_as_someone_else_is_refused_and_the_test_client_isnt_asked(store):
    asked = []

    def other(client_port, port):
        asked.append((client_port, port))
        return False
    guard = http_server.Guard(Inner(), store, PORT, same_user=other)
    status, body = _through(guard, HOST, client=("127.0.0.1", 50001))
    assert status == 403 and body["error"]["message"].startswith("Only programs running as the Windows account")
    assert asked == [(50001, PORT)]
    assert _through(guard, HOST, client=("testclient", 1))[0] == 200
    assert asked == [(50001, PORT)]


def test_no_user_check_when_it_cant_be_told(store):
    guard = http_server.Guard(Inner(), store, PORT)
    assert guard.same_user is None
    assert _through(guard, HOST, client=("127.0.0.1", 5))[0] == 200


@pytest.mark.parametrize("host", [f"localhost:{PORT}", f"[::1]:{PORT}"])
def test_every_loopback_host_with_this_port_passes(store, host):
    guard = http_server.Guard(Inner(), store, PORT, same_user=lambda a, b: True)
    assert _through(guard, {"host": host})[0] == 200


@pytest.mark.parametrize("headers, message", [
    ({"host": f"127.0.0.1:{PORT + 1}"}, "The Host header must be this PC's loopback address and port."),
    ({}, "The Host header must be this PC's loopback address and port."),
    (HOST | {"origin": "null"}, "Requests from web pages aren't accepted."),
    (HOST | {"content-length": "-1"}, "Requests are limited to 1024 KB."),
    (HOST | {"content-length": "1e3"}, "Requests are limited to 1024 KB."),
])
def test_each_guard_check_refuses_with_its_reason(store, headers, message):
    inner = Inner()
    guard = http_server.Guard(inner, store, PORT, same_user=lambda a, b: True)
    status, body = _through(guard, headers)
    assert body["error"]["message"] == message and status in (403, 413)
    assert inner.calls == 0


def test_a_bearer_token_is_read_whatever_the_case_of_its_scheme_and_with_spaces(store):
    _, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    guard = http_server.Guard(Inner(), store, PORT, same_user=lambda a, b: True)
    path = "/api/v1/capabilities"
    assert _through(guard, HOST | {"authorization": f"bearer  {token} "}, path=path)[0] == 200
    assert _through(guard, HOST | {"authorization": f"BEARER {token}"}, path=path)[0] == 200
    status, body = _through(guard, HOST | {"authorization": token}, path=path)
    assert status == 401 and body["error"]["message"] == "Send Authorization: Bearer <token>."
    status, body = _through(guard, HOST | {"authorization": "Bearer "}, path=path)
    assert status == 401 and body["error"]["message"] == "Send Authorization: Bearer <token>."


def test_the_open_paths_need_no_token(store):
    guard = http_server.Guard(Inner(), store, PORT, same_user=lambda a, b: True)
    assert _through(guard, HOST, path="/api/v1/openapi.json")[0] == 200
    assert _through(guard, HOST, path="/api/v1/health")[0] == 200
    assert _through(guard, HOST, path="/api/v1/healthz")[0] == 401


# ---- the audit line for a refused request

def test_a_refused_request_gets_one_line_naming_the_request_and_the_reason(store, monkeypatch):
    lines = Lines()
    guard = http_server.Guard(Inner(), store, PORT, registry=_registry(lines), same_user=lambda a, b: True)
    _through(guard, HOST | {"origin": "https://x"}, path="/api/v1/" + "p" * 300, method="POST")
    (ctx, name, permission, request, arrived, seconds, err, execution), = lines
    assert name == "http_request" and permission == "" and seconds == 0.0 and execution == ""
    assert request == {"method": "POST", "path": ("/api/v1/" + "p" * 300)[:200]}
    assert err.message == "Requests from web pages aren't accepted."
    assert ctx.caller.channel == "api" and ctx.caller.client_id == "" and ctx.caller.name == ""
    assert arrived > 1_000_000_000


def test_a_refused_mcp_request_is_on_the_mcp_http_channel(store):
    lines = Lines()
    guard = http_server.Guard(Inner(), store, PORT, registry=_registry(lines), same_user=lambda a, b: True)
    _through(guard, HOST | {"origin": "https://x"}, path="/mcp")
    assert lines[0][0].caller.channel == "mcp-http"


def test_refusals_are_recorded_once_per_reason_and_caller_every_ten_seconds(store, monkeypatch):
    assert http_server.REFUSED_EVERY == 10.0
    now = [1000.0]
    monkeypatch.setattr(http_server.time, "monotonic", lambda: now[0])
    lines = Lines()
    guard = http_server.Guard(Inner(), store, PORT, registry=_registry(lines), same_user=lambda a, b: True)
    web = HOST | {"origin": "https://x"}
    _through(guard, web)
    _through(guard, web)
    _through(guard, web)
    _through(guard, {"host": "evil"})  # another reason: its own line
    assert len(lines) == 2
    now[0] += 9.99
    _through(guard, web)
    assert len(lines) == 2
    now[0] += 0.01
    _through(guard, web)
    assert len(lines) == 3
    assert lines[2][3]["refused_since_last_line"] == 3
    now[0] += 10
    _through(guard, web)
    assert "refused_since_last_line" not in lines[3][3]


def test_a_rate_limited_client_is_named_on_its_line(store):
    found, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    lines = Lines()
    guard = http_server.Guard(Inner(), store, PORT, registry=_registry(lines), same_user=lambda a, b: True,
                              limiter=http_server.RateLimiter(rate=0.0001, burst=1))
    auth = HOST | {"authorization": f"Bearer {token}"}
    assert _through(guard, auth, path="/api/v1/capabilities")[0] == 200
    assert _through(guard, auth, path="/api/v1/capabilities")[0] == 429
    ctx = lines[0][0]
    assert ctx.caller.client_id == found["id"] and ctx.caller.name == "Script"


def test_no_registry_or_no_sink_means_no_line_and_a_broken_sink_doesnt_stop_the_refusal(store, capsys):
    for guard in (http_server.Guard(Inner(), store, PORT, same_user=lambda a, b: True),
                  http_server.Guard(Inner(), store, PORT, registry=_registry(None), same_user=lambda a, b: True)):
        assert _through(guard, {"host": "evil"})[0] == 403

    def broken(*a):
        raise OSError("disk full")
    guard = http_server.Guard(Inner(), store, PORT, registry=_registry(broken), same_user=lambda a, b: True)
    assert _through(guard, {"host": "evil"})[0] == 403
    assert "audit: could not record a refused request: OSError: disk full" in capsys.readouterr().err


# ---- REST bodies and the line for a call refused before invoke

def _app(store, run=None):
    reg = Registry()
    reg.register(Capability("ext_echo", "Echo", "Echoes.", schema({"n": {"type": "number"}}), schema(extra=True),
                            "READ_METADATA", run or (lambda s, c, **kw: kw), needs_service=False))
    lines = Lines()
    reg.audit = lines
    _, token = store.add_client("Script", "http", transport="http", grants=["READ_METADATA"])
    app = http_server.build_app(reg, store, PORT)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}"), {"Authorization": f"Bearer {token}"}, lines


URL = "/api/v1/capabilities/ext_echo/invoke"


@pytest.mark.parametrize("body, message", [
    (b'{"arguments": {"n": NaN}}', "The body must be JSON: {\"arguments\": {...}}."),
    (b'{"arguments": {"n": "\\udc00"}}', "The body must be JSON: {\"arguments\": {...}}."),
    (b"{nope", "The body must be JSON: {\"arguments\": {...}}."),
    (b"[]", "The body must be {\"arguments\": {...}}."),
    (b'{"arguments": null}', "The body must be {\"arguments\": {...}}."),
    (b'{"arguments": []}', "The body must be {\"arguments\": {...}}."),
    (b'{"arguments": {}, "extra": 1}', "The body must be {\"arguments\": {...}}."),
    (b'{"other": 1}', "The body must be {\"arguments\": {...}}."),
    (b"[" * 100_000 + b"]" * 100_000, "The body is nested too deeply."),
])
def test_a_body_that_isnt_the_right_json_is_a_400_with_its_own_line(store, body, message):
    client, auth, lines = _app(store)
    with client as c:
        r = c.post(URL, content=body, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["message"] == message
    (ctx, name, permission, arguments, _at, seconds, err, execution), = lines
    assert name == "ext_echo" and permission == "READ_METADATA" and arguments is None
    assert err.message == message and ctx.caller.channel == "api" and ctx.caller.name == "Script"


@pytest.mark.parametrize("body", [b"", b"  \n", b"{}", b'{"arguments": {}}'])
def test_an_empty_body_or_no_arguments_is_a_call_with_none(store, body):
    client, auth, lines = _app(store)
    with client as c:
        r = c.post(URL, content=body, headers=auth)
    assert r.status_code == 200 and r.json()["result"] == {}
    assert lines[0][3] == {} and lines[0][6] is None


def test_a_good_body_reaches_the_tool(store):
    client, auth, _ = _app(store)
    with client as c:
        r = c.post(URL, content=b'{"arguments": {"n": 1.5}}', headers=auth)
    assert r.json() == {"ok": True, "capability": "ext_echo", "seconds": r.json()["seconds"], "result": {"n": 1.5}}


def test_a_failed_call_answers_with_its_code(store):
    def fails(s, c, **kw):
        raise LookupError(f"no notebook {JWT}")
    client, auth, _ = _app(store, fails)
    with client as c:
        r = c.post(URL, content=b"{}", headers=auth)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found" and JWT not in r.text


def test_a_resource_uri_that_names_nothing_gets_a_line_with_its_uri(store):
    client, auth, lines = _app(store)
    with client as c:
        r = c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://nothing"}, headers=auth)
    assert r.status_code == 404
    assert lines[0][1] == "resources/read" and lines[0][3] == {"uri": "fabric-desktop://nothing"}


def test_a_busy_server_refuses_after_its_wait_with_a_line(store, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_CONCURRENT", 0)
    monkeypatch.setattr(http_server, "QUEUE_WAIT_SECONDS", 0.05)
    client, auth, lines = _app(store)
    with client as c:
        r = c.post(URL, content=b'{"arguments": {"n": 2}}', headers=auth)
    assert r.status_code == 429 and r.headers["retry-after"] == "5"
    assert r.json()["error"] == {"code": "rate_limited", "message": "KratosForge is busy with other calls.",
                                 "retryable": True, "hint": "Try again shortly."}
    assert lines[0][1] == "ext_echo" and lines[0][3] == {"n": 2} and lines[0][6].code == "rate_limited"


@pytest.mark.parametrize("method, path, status, code", [
    ("GET", "/api/v1/nowhere", 404, "not_found"),
    ("DELETE", "/api/v1/health", 405, "invalid_input"),
])
def test_no_such_path_or_method_answers_in_the_apis_shape(store, method, path, status, code):
    client, auth, _ = _app(store)
    with client as c:
        r = c.request(method, path, headers=auth)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert r.json()["error"]["hint"] == "See /api/v1/openapi.json."


def test_any_other_http_error_is_internal():
    class Odd(Exception):
        status_code = 418
    r = anyio.run(http_server._http_error, type("Req", (), {"method": "GET"})(), Odd())
    assert r.status_code == 500 and json.loads(r.body)["error"]["code"] == "internal"


def test_listing_capabilities_is_ok(store):
    client, auth, lines = _app(store)
    with client as c:
        r = c.get("/api/v1/capabilities", headers=auth)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert [cap["id"] for cap in r.json()["capabilities"]] == ["ext_echo"]
    assert lines == []  # a listing isn't a call


# ---- bodies the guard reads

def _streamed(guard, messages, headers=HOST):
    """Through the guard with ``messages`` as what the connection sends, then nothing more."""
    sent, it = [], iter(messages)

    async def receive():
        try:
            return next(it)
        except StopIteration:
            await anyio.sleep(3600)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/v1/health", "client": ("127.0.0.1", 1),
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    anyio.run(guard, scope, receive, send)
    return sent


class Body:
    def __init__(self):
        self.body = b""

    async def __call__(self, scope, receive, send):
        while True:
            message = await receive()
            self.body += message.get("body", b"")
            if not message.get("more_body"):
                break
        await JSONResponse({"ok": True})(scope, receive, send)


def test_a_body_in_several_parts_is_read_to_its_last_part(store):
    inner = Body()
    guard = http_server.Guard(inner, store, PORT, same_user=lambda a, b: True)
    sent = _streamed(guard, [{"type": "http.request", "body": b"ab", "more_body": True},
                             {"type": "http.request", "body": b"cd", "more_body": True},
                             {"type": "http.request", "body": b"e", "more_body": False}])
    assert sent[0]["status"] == 200 and inner.body == b"abcde"


def test_a_body_that_stops_arriving_is_a_retryable_408_that_closes_the_connection(store, monkeypatch):
    monkeypatch.setattr(http_server, "BODY_SECONDS", 0.05)
    lines = Lines()
    inner = Body()
    guard = http_server.Guard(inner, store, PORT, registry=_registry(lines), same_user=lambda a, b: True)
    sent = _streamed(guard, [{"type": "http.request", "body": b"ab", "more_body": True}])
    assert sent[0]["status"] == 408 and (b"connection", b"close") in sent[0]["headers"]
    error = json.loads(sent[1]["body"])["error"]
    assert error["code"] == "timeout" and error["retryable"] is True
    assert error["message"] == "The request body didn't all arrive within 0.05 seconds."
    assert inner.body == b"" and lines[0][6].code == "timeout"


def test_a_client_that_goes_away_mid_body_gets_no_answer(store):
    inner = Body()
    guard = http_server.Guard(inner, store, PORT, same_user=lambda a, b: True)
    sent = _streamed(guard, [{"type": "http.request", "body": b"ab", "more_body": True}, {"type": "http.disconnect"}])
    assert sent == [] and inner.body == b""


def test_a_caller_with_no_port_is_asked_about_as_port_0(store):
    asked = []
    guard = http_server.Guard(Inner(), store, PORT, same_user=lambda a, b: asked.append((a, b)) or True)
    _through(guard, HOST, client=("127.0.0.1", None))
    assert asked == [(0, PORT)]


# ---- the OpenAPI document and the MCP endpoint's settings

def test_the_openapi_document_lists_each_error_status_and_a_successful_calls_shape(store):
    reg = Registry()
    reg.register(Capability("ext_echo", "Echo", "Echoes.", schema(), schema(extra=True), "READ_METADATA",
                            lambda s, c: {}, needs_service=False))
    doc = http_server.openapi(reg, PORT)
    listed = doc["paths"]["/api/v1/capabilities"]["get"]["responses"]
    assert sorted(listed) == ["200", "400", "401", "403", "404", "428", "429", "503"]
    ok = doc["paths"]["/api/v1/capabilities/ext_echo/invoke"]["post"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"]["properties"]["ok"] == {"const": True}
    assert doc["components"]["schemas"]["Error"]["required"] == ["code", "message", "retryable"]


def test_the_mcp_endpoint_keeps_at_most_20_sessions_and_only_loopback_hosts(store, monkeypatch):
    from mcp.server import streamable_http_manager
    seen = {}
    real = streamable_http_manager.StreamableHTTPSessionManager

    class Capturing(real):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(streamable_http_manager, "StreamableHTTPSessionManager", Capturing)
    http_server.build_app(Registry(), store, PORT)
    assert seen["max_sessions"] == 20 and seen["max_request_body_size"] == http_server.MAX_BODY_BYTES
    security = seen["security_settings"]
    assert security.enable_dns_rebinding_protection is True
    assert sorted(security.allowed_hosts) == sorted({f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"})
    assert security.allowed_origins == []


# ---- the protocol's header deadline and its 400

class Transport:
    def __init__(self, closing=False):
        self.data, self.closed, self.closing = b"", False, closing

    def write(self, data):
        self.data += data

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closing


def _bare_protocol():
    cls = http_server._protocol()
    return cls.__new__(cls)


def test_a_connection_whose_headers_are_late_is_closed_unless_a_request_is_running():
    p = _bare_protocol()
    p.cycle, p.transport = None, Transport()
    p._headers_late()
    assert p.transport.closed
    p.cycle, p.transport = type("Cycle", (), {"response_complete": True})(), Transport()
    p._headers_late()
    assert p.transport.closed
    p.cycle, p.transport = type("Cycle", (), {"response_complete": False})(), Transport()
    p._headers_late()
    assert not p.transport.closed
    p.cycle, p.transport = None, Transport(closing=True)
    p._headers_late()
    assert not p.transport.closed


def test_a_lost_connection_cancels_its_header_deadline(monkeypatch):
    from uvicorn.protocols.http.h11_impl import H11Protocol
    monkeypatch.setattr(H11Protocol, "connection_lost", lambda self, exc: None)
    cancelled = []
    p = _bare_protocol()
    p._deadline = type("Timer", (), {"cancel": lambda self: cancelled.append(1)})()
    p.connection_lost(None)
    assert cancelled == [1]
    p._deadline = None
    p.connection_lost(None)  # no deadline: nothing to cancel


def test_a_malformed_request_line_is_a_400_in_the_apis_shape():
    import h11
    p = _bare_protocol()
    p.conn, p.transport = h11.Connection(h11.SERVER), Transport()
    p.conn.receive_data(b"GARBAGE\r\n\r\n")
    with pytest.raises(h11.RemoteProtocolError):
        p.conn.next_event()
    p.send_400_response("illegal request line")
    head, _, body = p.transport.data.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 400 Bad Request\r\n") and b"connection: close" in head
    assert json.loads(body) == {"ok": False, "error": {"code": "invalid_input", "retryable": False,
                                                       "message": "The request line or headers are malformed or too big."}}
    assert p.transport.closed
