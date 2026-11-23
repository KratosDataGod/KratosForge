"""Generative fuzzing of the integration request parsers (#2363 hardening).

test_integrations_framing_fuzz.py sends a fixed list of bad frames; this file generates them, from a
few fixed seeds (``random.Random(seed)``: the same cases on every run, a few hundred per seed), at:

- the REST invoke body (/api/v1/capabilities/{id}/invoke): random bytes and text, random JSON,
  truncated JSON, deep nesting, NaN / Infinity / 1e309 / -0 / long numbers, duplicate keys, byte
  order marks and UTF-16, lone surrogates, and ``arguments`` of every JSON type;
- the resource reader (/api/v1/resources/read?uri=...) and ``Registry.resolve`` itself: scheme
  variants, percent-encodings single and double, ``..``, overlong URIs, ``?`` ``&`` ``=`` storms;
- JSON-RPC over /mcp on an initialized session: random methods, params and ids of every type;
- the stdio line reader: random-bytes lines through one long-lived ``fabric-desktop mcp`` process.

The local API runs on a real socket with its production settings (server_config), so a request
that hangs fails on the client's timeout. Every tool's handler is a spy. What must hold:

- never a 5xx, except 502/503 from the error table; every REST error is the API's JSON shape with
  the status the table gives its code; every MCP answer is JSON-RPC; never a Python traceback;
- a handler runs only for a request that validated, and only with arguments that are strict JSON
  (no NaN or Infinity) its schema accepts; each REST call that passes the guard writes one audit line;
- the server keeps serving: a good request after each batch succeeds.
"""

from __future__ import annotations

import contextlib
import json
import random
import re
import socket
import threading
import time
from urllib.parse import quote

import httpx2
import pytest
import uvicorn

from fabric_desktop.integrations import peer
from fabric_desktop.integrations.core import CODES, IntegrationError, redact_secrets, validate
from fabric_desktop.integrations.http_server import STATUS, RateLimiter, build_app, server_config
from fabric_desktop.integrations.registry import MAX_AUDITED_NAME, MAX_URI
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_audit_every_call import Tools
from test_integrations_framing_fuzz import Stdio
from test_integrations_limits import valid_args

SEEDS = (1, 2363, 90210)
CASES = 250            # per seed, for each HTTP parser
BATCH = 50             # a good request after every this many
REQUEST_SECONDS = 10.0  # no request may take longer
NUMBER_LITERALS = ("NaN", "-NaN", "Infinity", "-Infinity", "1e309", "-1e309", "1E400", "-0", "-0.0", "0e0", "1e-400",
                   "4.9e-324", "123456789012345678901234567890", "9" * 5000, "0.1", "-1", "2147483648", "1.0")
# Statuses that aren't their code's in the error table, on purpose: no or a bad token (the guard),
# a method the path doesn't take, a body that didn't arrive in time.
GUARD_STATUS = {401: "permission_denied", 405: "invalid_input", 408: "timeout"}
SURROGATES = ("\\ud800", "\\udfff", "\\udbff\\udbff", "a\\ud834b")


def ENGINE():  # noqa: N802 - a stand-in for the engine's Service; the spied handlers never use it
    return object()


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setattr(peer, "checked", lambda: False)


# ---------------------------------------------------------------------------------- generators

def text(rng: random.Random, most: int = 12) -> str:
    """Random text: ASCII, JSON's escapes, controls, the BMP (no surrogates), astral, BOM, bidi."""
    out = []
    for _ in range(rng.randint(0, most)):
        pick = rng.random()
        if pick < 0.5:
            out.append(chr(rng.randint(0x20, 0x7e)))
        elif pick < 0.6:
            out.append(rng.choice('"\\/\b\f\n\r\t\x00\x7f'))
        elif pick < 0.85:
            c = rng.randint(0x80, 0xfffd)
            out.append(chr(c) if not 0xd800 <= c <= 0xdfff else "?")
        elif pick < 0.95:
            out.append(chr(rng.randint(0x10000, 0x10ffff)))
        else:
            out.append(rng.choice("\ufeff\u2028\u2029\u202e\u200b"))
    return "".join(out)


def value(rng: random.Random, depth: int = 3):
    """A random JSON value."""
    kinds = ["null", "bool", "int", "float", "str"] + (["list", "dict"] if depth > 0 else [])
    kind = rng.choice(kinds)
    if kind == "null":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "int":
        return rng.choice([0, -1, 1, 2**31, -(2**63), 2**64, 10**30, rng.randint(-1000, 1000)])
    if kind == "float":
        return rng.choice([0.5, -0.0, 1e300, -1e-300, rng.uniform(-1e6, 1e6)])
    if kind == "str":
        return text(rng)
    if kind == "list":
        return [value(rng, depth - 1) for _ in range(rng.randint(0, 4))]
    return {text(rng, 6): value(rng, depth - 1) for _ in range(rng.randint(0, 4))}


def nested(n: int, rng: random.Random) -> bytes:
    if rng.random() < 0.5:
        return b"[" * n + b"]" * n
    return b'{"a":' * n + b"1" + b"}" * n


def invoke_body(rng: random.Random, cap) -> bytes:
    """One generated REST invoke body for ``cap`` (None: a tool that doesn't exist)."""
    good = valid_args(cap) if cap is not None else {}
    props = cap.input_schema.get("properties", {}) if cap is not None else {}
    numeric = [k for k, p in props.items() if p.get("type") in ("integer", "number")]
    kind = rng.randrange(12)
    if kind == 0:
        return bytes(rng.randrange(256) for _ in range(rng.randint(0, 200)))
    if kind == 1:
        return text(rng, 60).encode("utf-8")
    if kind == 2:
        return json.dumps(value(rng)).encode()
    if kind == 3:
        whole = json.dumps({"arguments": {**good, **({text(rng, 5): value(rng)} if rng.random() < 0.5 else {})}}).encode()
        return whole[:rng.randint(0, len(whole) - 1)]
    if kind == 4:
        depth = rng.choice([50, 900, 1100, 5000, 60_000])
        inner = nested(depth, rng)
        return b'{"arguments": {"zz": ' + inner + b"}}" if rng.random() < 0.5 else inner
    if kind == 5:
        field = rng.choice(numeric) if numeric else rng.choice(list(props) or ["zz"])
        rest = json.dumps({k: v for k, v in good.items() if k != field})[1:-1]
        literal = rng.choice(NUMBER_LITERALS)
        return ('{"arguments": {' + (rest + ", " if rest else "") + f'"{field}": {literal}' + "}}").encode()
    if kind == 6:  # duplicate keys, outer or inner; the last one counts
        inner = json.dumps(good)
        if rng.random() < 0.5:
            return f'{{"arguments": {json.dumps(value(rng))}, "arguments": {inner}}}'.encode()
        field = rng.choice(list(props) or ["zz"])
        return f'{{"arguments": {{"{field}": {json.dumps(value(rng))}, "{field}": {json.dumps(good.get(field, 1))}}}}}'.encode()
    if kind == 7:
        body = json.dumps({"arguments": good})
        return rng.choice([b"\xef\xbb\xbf" + body.encode(), body.encode("utf-16"), body.encode("utf-16-le"),
                           body.encode("utf-32"), b"\xef\xbb\xbf\xef\xbb\xbf" + body.encode(), b" \n\t" + body.encode() + b"\n\n"])
    if kind == 8:
        return json.dumps({"arguments": rng.choice([None, True, 0, 1.5, "s", [], {}, [good], value(rng)])}).encode()
    if kind == 9:  # valid arguments with one field changed to anything
        field = rng.choice(list(props) or ["zz"])
        return json.dumps({"arguments": {**good, field: value(rng, 2)}}).encode()
    if kind == 10:  # a lone surrogate in a key or a value
        s = rng.choice(SURROGATES)
        field = rng.choice(list(props) or ["zz"])
        return (f'{{"arguments": {{"{s}": 1}}}}' if rng.random() < 0.5 else f'{{"arguments": {{"{field}": "{s}"}}}}').encode()
    return json.dumps({"arguments": good}).encode()  # some that work


def unknown_name(rng: random.Random) -> str:
    name = rng.choice(["fabric_desktop_nope", "x", "FABRIC_DESKTOP_GET_STATUS", "fabric_desktop_get_status~", "a" * 300,
                       text(rng, 20), "%2e", "%00", "_", "-"])
    name = name.replace("/", "_").replace("\\", "_") or "z"
    return name if name.strip(".") else "z"


# ---------------------------------------------------------------------------------- the server

@contextlib.contextmanager
def served(store, tools):
    """The local API as ``serve`` runs it (server_config), on a socket bound here; no rate limit."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # accepted connections inherit it (Nagle again)
    port = sock.getsockname()[1]
    app = build_app(tools.reg, store, port, ENGINE, limiter=RateLimiter(rate=1e6, burst=10**6))
    server = uvicorn.Server(server_config(app, port))
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


@pytest.fixture
def api(tmp_path):
    store = IntegrationStore(tmp_path / "integrations" / "integrations.sqlite")
    store.set_setting("enabled", True)
    client, token = store.add_client("Fuzz", "other", transport="http")
    tools = Tools(store)
    # TCP_NODELAY: a body sent after its headers otherwise waits on Nagle and a delayed ACK (40 ms a request).
    transport = httpx2.HTTPTransport(socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)])
    with served(store, tools) as port, httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=REQUEST_SECONDS,
                                                     headers={"Authorization": f"Bearer {token}"}, transport=transport) as http:
        yield {"http": http, "tools": tools, "client": client, "port": port, "token": token}


def strict(arguments) -> None:
    """Arguments a handler got are strict JSON: no NaN or Infinity, no lone surrogate."""
    json.dumps(arguments, allow_nan=False, ensure_ascii=False).encode("utf-8")


def check_rest(res, what) -> dict:
    """The API's own shape and status for whatever came back."""
    assert res.status_code < 500 or res.status_code in (502, 503), (what, res.status_code, res.text[:300])
    assert "Traceback" not in res.text, (what, res.text[:300])
    assert res.headers["content-type"].startswith("application/json"), (what, res.status_code, res.text[:200])
    data = res.json()
    if res.status_code == 200:
        assert data["ok"] is True, (what, data)
    else:
        err = data["error"]
        assert data["ok"] is False and err["code"] in CODES, (what, data)
        assert isinstance(err["message"], str) and isinstance(err["retryable"], bool), (what, data)
        assert STATUS[err["code"]] == res.status_code or GUARD_STATUS.get(res.status_code) == err["code"], \
            (what, res.status_code, err)
    return data


def check_ran(tools, before_calls, before_lines, ok_cap, line_cap, data, what):
    """A handler ran for exactly the call that succeeded, with arguments its schema takes; one line."""
    calls = tools.calls[before_calls:]
    if ok_cap is None:
        assert calls == [], (what, calls)
    else:
        assert [c for c, _ in calls] == [ok_cap], (what, calls)
        cap = tools.reg.get(ok_cap)
        validate(cap.input_schema, calls[0][1])
        strict(calls[0][1])
    if line_cap is not None:
        [line] = tools.trail.lines[before_lines:]
        # A name that names no tool is the caller's text: redacted and cut to 200 on its line.
        expect = line_cap if tools.reg.get(line_cap) is not None else redact_secrets(line_cap)[:MAX_AUDITED_NAME]
        assert line["capability"] == expect, (what, line["capability"], expect)
        assert line["outcome"] == ("ok" if data["ok"] else data["error"]["code"]), (what, line, data)


def still_serves(api):
    before = len(api["tools"].calls)
    res = api["http"].post("/api/v1/capabilities/fabric_desktop_get_status/invoke", json={"arguments": {}})
    assert res.status_code == 200 and res.json()["ok"], res.text
    assert [c for c, _ in api["tools"].calls[before:]] == ["fabric_desktop_get_status"]


# ---------------------------------------------------------------------------------- (a) REST invoke body

@pytest.mark.parametrize("seed", SEEDS)
def test_rest_invoke_body(api, seed):
    rng = random.Random(seed)
    tools, http = api["tools"], api["http"]
    caps = tools.reg.all()
    kinds_seen = set()
    for i in range(CASES):
        cap = rng.choice(caps) if rng.random() < 0.9 else None
        cap_id = cap.id if cap is not None else unknown_name(rng)
        body = invoke_body(rng, cap)
        what = (seed, i, cap_id, body[:120])
        before_calls, before_lines = len(tools.calls), len(tools.trail.lines)
        started = time.monotonic()
        res = http.post(f"/api/v1/capabilities/{quote(cap_id, safe='')}/invoke", content=body,
                        headers={"Content-Type": "application/json"})
        assert time.monotonic() - started < REQUEST_SECONDS, what
        data = check_rest(res, what)
        kinds_seen.add(res.status_code)
        ok_cap = cap_id if res.status_code == 200 else None
        if cap is None:
            assert res.status_code in (400, 404), what  # unknown: a bad body or not_found, never run
        check_ran(tools, before_calls, before_lines, ok_cap, cap_id, data, what)
        if i % BATCH == BATCH - 1:
            still_serves(api)
    assert {200, 400, 404} <= kinds_seen, kinds_seen  # the generator reaches every branch


@pytest.mark.parametrize("literal, accepted", [
    ("NaN", False), ("-NaN", False), ("Infinity", False), ("-Infinity", False), ("1e309", False), ("-1e309", False),
    ("1E400", False), ("-0", True), ("-0.0", True), ("0e0", True), ("1e-400", True), ("0.5", True)])
def test_a_number_field_takes_only_finite_json_numbers(api, literal, accepted):
    """wait_seconds is {"type": "number", "minimum": 0, "maximum": 120}: NaN passed both bounds,
    and Python's parser reads NaN and Infinity, which aren't JSON."""
    tools, http = api["tools"], api["http"]
    body = '{"arguments": {"code": "print(1)", "wait_seconds": ' + literal + "}}"
    before = len(tools.calls)
    res = http.post("/api/v1/capabilities/fabric_desktop_execute_python/invoke", content=body.encode())
    data = check_rest(res, literal)
    assert (res.status_code == 200) is accepted, (literal, data)
    assert len(tools.calls) - before == int(accepted)
    for _, args in tools.calls[before:]:
        strict(args)


def test_a_lone_surrogate_in_the_body_is_a_400_not_a_500(api):
    for body in ('{"arguments": {"\\ud800": 1}}', '{"arguments": {"code": "a\\udfffb"}}', '{"\\ud800": 1}'):
        res = api["http"].post("/api/v1/capabilities/fabric_desktop_execute_python/invoke", content=body.encode())
        assert check_rest(res, body)["error"]["code"] == "invalid_input"
    assert api["tools"].calls == []
    still_serves(api)


def test_an_unknown_path_or_method_answers_in_the_apis_shape(api):
    http = api["http"]
    for method, path, status in [("GET", "/api/v1/nothing", 404), ("POST", "/api/v1/capabilities/a%2Fb/invoke", 404),
                                 ("GET", "/api/v1/capabilities/fabric_desktop_get_status/invoke", 405),
                                 ("DELETE", "/api/v1/capabilities", 405), ("PUT", "/api/v1/resources/read", 405)]:
        res = http.request(method, path)
        assert res.status_code == status, (method, path, res.status_code)
        check_rest(res, (method, path))
    assert api["tools"].calls == []


# ---------------------------------------------------------------------------------- (b) resource URIs

SCHEMES = ("fabric-desktop://", "FABRIC-DESKTOP://", "fabric-desktop:/", "fabric-desktop:", "fabric-desktop:///", "",
           "http://", " fabric-desktop://", "fabric-desktop://\x00", "fabric%2Ddesktop://", "fabric-desktop%3A%2F%2F")
SEGMENTS = ("..", ".", "%2e%2e", "%252e%252e", "a%2Fb", "%2F", "%00", "%", "%zz", "%E2%82", "%ED%A0%80", "%FF", "a?b",
            "a#b", "a&b=c", "ab", "x" * 2100, "%25", "%2525", "+", "a b", "é", "%C3%A9", "~", "{name}")
QUERY_VALUES = ("1", "0", "-1", "500", "501", "nan", "inf", "1e309", "-0", "9" * 5000, "1_0", "１", " 1", "true",
                "%2525", "", "=", "&", "?", "1.5")


def segment(rng: random.Random) -> str:
    if rng.random() < 0.7:
        return rng.choice(SEGMENTS)
    raw = text(rng, 10)
    return "".join(quote(ch, safe="") if rng.random() < 0.5 else ch for ch in raw) or "z"


def resource_uri(rng: random.Random, caps) -> str:
    cap = rng.choice(caps)
    path = re.sub(r"\{[a-z_]+\}", lambda _m: segment(rng), cap.resource)
    path = rng.choice(SCHEMES) + path.removeprefix("fabric-desktop://") if rng.random() < 0.3 else path
    if rng.random() < 0.15:
        path = path.replace("/", rng.choice(["//", "/./", "/../", "%2F"]), 1)
    if rng.random() < 0.6:
        keys = list(cap.input_schema.get("properties", {})) + ["zz", "", "uri", "limit", "offset", text(rng, 4)]
        parts = []
        for _ in range(rng.randint(0, 6)):
            storm = rng.random()
            if storm < 0.15:
                parts.append(rng.choice(["", "=", "==", "&", "?", "=&="]))
            else:
                parts.append(f"{rng.choice(keys)}{rng.choice(['=', '==', ''])}{rng.choice(QUERY_VALUES)}")
        path += "?" * rng.randint(1, 2) + "&".join(parts)
    if rng.random() < 0.05:
        path += "a" * (MAX_URI + rng.randint(-5, 5) - len(path))
    return path


@pytest.mark.parametrize("seed", SEEDS)
def test_resource_uris(api, seed):
    rng = random.Random(seed)
    tools, http = api["tools"], api["http"]
    caps = tools.reg.resources()
    statuses = set()
    for i in range(CASES):
        uri = resource_uri(rng, caps)
        before_calls, before_lines = len(tools.calls), len(tools.trail.lines)
        if rng.random() < 0.5:
            res = http.get("/api/v1/resources/read", params={"uri": uri})
            sent = uri
        else:  # the query written by hand, some characters encoded and some not: & and = split it
            raw = "".join(quote(ch, safe="") if rng.random() < 0.5 or not " " < ch != "\x7f" else ch for ch in uri)
            res = http.get("/api/v1/resources/read?uri=" + raw.replace("#", "%23").replace(" ", "%20"))
            sent = None
        what = (seed, i, uri[:150])
        data = check_rest(res, what)
        statuses.add(res.status_code)
        ok_cap = data.get("capability") if res.status_code == 200 else None
        # Read or refused, it is one line: the tool it named, or resources/read for a URI naming none.
        line = tools.trail.lines[before_lines]["capability"] if len(tools.trail.lines) > before_lines else None
        assert line is not None and (line == "resources/read" or tools.reg.get(line) is not None), what
        check_ran(tools, before_calls, before_lines, ok_cap, line, data, what)
        if sent is not None:  # the same answer as the registry gives directly
            try:
                cap, args = tools.reg.resolve(sent)
            except IntegrationError as err:
                assert (line, data["error"]["code"]) == ("resources/read", err.code), what
            else:
                assert line == cap.id, what
        if i % BATCH == BATCH - 1:
            still_serves(api)
    assert {200, 400, 404} <= statuses, statuses


@pytest.mark.parametrize("seed", SEEDS)
def test_registry_resolve_directly(tmp_path, seed):
    """Thousands of URIs straight into ``Registry.resolve``: it answers with a capability and its
    arguments or refuses with not_found / invalid_input, never anything else; and every URI a
    resource template makes resolves back to that template and those values."""
    rng = random.Random(seed)
    reg = Tools(IntegrationStore(tmp_path / "integrations.sqlite")).reg
    caps = reg.resources()
    for i in range(2000):
        uri = resource_uri(rng, caps) if rng.random() < 0.8 else text(rng, 40)
        try:
            cap, args = reg.resolve(uri)
        except IntegrationError as err:
            assert err.code in ("not_found", "invalid_input"), (seed, i, uri, err.code)
            assert len(err.message) < MAX_URI + 400, (seed, i)
            continue
        assert uri.startswith("fabric-desktop://") and len(uri) <= MAX_URI, (seed, i, uri)
        assert set(args) <= set(cap.input_schema.get("properties", {})), (seed, i, uri, args)
        for name in cap.resource_params:
            assert isinstance(args[name], str) and args[name], (seed, i, uri, args)
    # Round trip: the URI a template makes from any values resolves to them.
    for i in range(500):
        cap = rng.choice(caps)
        params = {p: text(rng, 10) or "z" for p in cap.resource_params}
        uri = cap.resource_uri(**params)
        if len(uri) > MAX_URI:
            continue
        got, args = reg.resolve(uri)
        assert (got.id, args) == (cap.id, params), (seed, i, uri)


# ---------------------------------------------------------------------------------- (c) JSON-RPC over /mcp

METHODS = ("tools/call", "tools/list", "resources/read", "resources/list", "resources/templates/list", "prompts/get",
           "prompts/list", "initialize", "ping", "completion/complete", "logging/setLevel", "resources/subscribe",
           "notifications/initialized", "notifications/cancelled", "notifications/progress", "server/discover",
           "tools/call/", "TOOLS/CALL", "", "rpc.discover", "$/cancelRequest")
LISTEN = "subscriptions/listen"  # opens a stream that stays open: not a request that answers


def rpc_id(rng: random.Random):
    return rng.choice([rng.randint(-10, 10**6), 2**70, -(2**70), 0, 1.5, -0.0, "fz-" + text(rng, 6), "", None, True,
                       [1], {"a": 1}, "é"])


def rpc_message(rng: random.Random, caps):
    method = rng.choice(METHODS) if rng.random() < 0.8 else text(rng, 12)
    if method == LISTEN:
        method = "tools/list"
    message: dict = {"jsonrpc": rng.choice(["2.0"] * 8 + ["1.0", 2.0, None])}
    if rng.random() < 0.85:
        message["id"] = rpc_id(rng)
    if method != "" or rng.random() < 0.5:
        message["method"] = method
    shape = rng.random()
    if method == "tools/call" and caps and shape < 0.7:
        cap = rng.choice(caps)
        args = valid_args(cap)
        pick = rng.random()
        if pick < 0.3:
            args = {**args, rng.choice(list(cap.input_schema["properties"]) or ["zz"]): value(rng, 2)}
        elif pick < 0.45:
            args = value(rng)
        message["params"] = {"name": cap.id if rng.random() < 0.9 else text(rng, 10), "arguments": args}
    elif method == "resources/read" and caps and shape < 0.7:
        message["params"] = {"uri": resource_uri(rng, [c for c in caps if c.resource])}
    elif shape < 0.9:
        message["params"] = value(rng, 2) if rng.random() < 0.3 else {text(rng, 5): value(rng, 2) for _ in range(rng.randint(0, 3))}
    if rng.random() < 0.1:
        message[text(rng, 4) or "x"] = value(rng, 1)
    if rng.random() < 0.05:
        return [message, rpc_message(rng, caps)]
    return message


def mcp_messages(res) -> list:
    """The JSON-RPC messages in an answer: JSON, or a server-sent event stream of them."""
    kind = res.headers.get("content-type", "")
    if res.status_code == 202 and not res.content:  # accepted: a notification or a response, nothing to answer
        return []
    if kind.startswith("application/json"):
        try:
            data = res.json()
        except ValueError:
            raise AssertionError(f"not JSON: {res.status_code} {kind} {res.content[:300]!r}") from None
        return data if isinstance(data, list) else [data]
    if kind.startswith("text/event-stream"):
        # split("\n"), not splitlines(): a JSON string may hold U+2028 and other line breaks unescaped.
        lines = (line.rstrip("\r") for line in res.text.split("\n"))
        return [json.loads(line[5:]) for line in lines if line.startswith("data:") and line[5:].strip()]
    assert not res.content, (res.status_code, kind, res.text[:200])
    return []


def initialize(http) -> dict:
    accept = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    res = http.post("/mcp", headers=accept, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fuzz", "version": "1"}}})
    assert res.status_code == 200, res.text
    headers = {**accept, "Mcp-Session-Id": res.headers["mcp-session-id"], "Mcp-Protocol-Version": "2025-11-25"}
    assert http.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    return headers


@pytest.mark.parametrize("seed", SEEDS)
def test_json_rpc_over_http(api, seed):
    rng = random.Random(seed)
    tools, http = api["tools"], api["http"]
    caps = tools.reg.all()
    headers = initialize(http)
    succeeded = 0  # tools/call answers that ran a tool
    for i in range(CASES):
        message = rpc_message(rng, caps)
        body = json.dumps(message).encode()
        what = (seed, i, body[:200])
        before = len(tools.calls)
        started = time.monotonic()
        res = http.post("/mcp", content=body, headers=headers)
        assert time.monotonic() - started < REQUEST_SECONDS, what
        assert res.status_code < 500, (what, res.status_code, res.text[:300])
        assert "Traceback" not in res.text, (what, res.text[:300])
        for answer in mcp_messages(res):
            assert answer.get("jsonrpc") == "2.0", (what, answer)
            if "error" in answer:
                assert isinstance(answer["error"]["code"], int) and isinstance(answer["error"]["message"], str), (what, answer)
                assert "result" not in answer, (what, answer)
            elif "result" in answer:
                result = answer["result"]
                if isinstance(result, dict) and ("content" in result and not result.get("isError") or "contents" in result):
                    succeeded += 1  # a tool call or a resource read that ran its tool
            else:
                assert "method" in answer, (what, answer)  # a notification from the server
        for cap_id, args in tools.calls[before:]:  # whatever ran was valid
            validate(tools.reg.get(cap_id).input_schema, args)
            strict(args)
        assert len(tools.calls) - before <= 2, what  # at most one tool per message (two in a batch)
        if i % BATCH == BATCH - 1:
            res = http.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": f"good-{i}", "method": "tools/call",
                                                          "params": {"name": "fabric_desktop_get_status", "arguments": {}}})
            [answer] = mcp_messages(res)
            assert answer["id"] == f"good-{i}" and not answer["result"].get("isError"), answer
            succeeded += 1
    assert len(tools.calls) == succeeded, (len(tools.calls), succeeded)
    still_serves(api)


# ---------------------------------------------------------------------------------- (d) the stdio line reader

def test_stdio_line_reader(tmp_path):
    store = IntegrationStore.at(tmp_path / "FabricDesktop")
    store.set_setting("enabled", True)
    client, _ = store.add_client("Fuzz", grants=["READ_METADATA"])
    proc = Stdio(tmp_path, client["id"])
    try:
        proc.initialize()
        for seed in SEEDS:
            rng = random.Random(seed)
            for batch in range(3):
                lines = []
                for _ in range(15):
                    kind = rng.random()
                    if kind < 0.35:
                        line = bytes(rng.randrange(256) for _ in range(rng.randint(0, 400)))
                    elif kind < 0.55:
                        line = text(rng, 80).encode("utf-8")
                    elif kind < 0.7:
                        line = json.dumps(value(rng)).encode()
                    else:
                        message = rpc_message(rng, [])  # no tool names: nothing real is called
                        if isinstance(message, dict) and isinstance(message.get("id"), str):
                            message["id"] = "fz-" + message["id"]
                        line = json.dumps(message).encode()
                    lines.append(line.replace(b"\n", b" ").replace(b"\r", b" "))
                others = proc.tools(pre=b"\n".join(lines) + b"\n")  # then a good tools/list, answered
                for answer in others:
                    assert answer.get("jsonrpc") == "2.0" and ("error" in answer or "result" in answer or "method" in answer), \
                        (seed, batch, answer)
                    assert not str(answer.get("id", "")).startswith("good-"), answer
                assert proc.alive(), (seed, batch, proc.err_text()[-2000:])
        status, _ = proc.call("tools/call", {"name": "fabric_desktop_get_status", "arguments": {}})
        assert not status["result"].get("isError"), status
    finally:
        proc.close()
    assert "Traceback" not in proc.err_text(), proc.err_text()[-3000:]
