"""Malformed input on the integration transports: the real ``fabric-desktop mcp`` process over stdio,
and the real local API (integrations/http_server.py, with its production uvicorn settings) over a
socket. Bad framing must never crash the server, wedge it, or make it answer for the wrong request.

Stdio is read with a thread per pipe and a queue, not select() on the pipe, so this runs on Windows.

What the MCP SDK does with a bad stdio line, pinned here: a line that isn't one JSON-RPC message
(not JSON, not UTF-8 JSON, a batch array, a scalar, jsonrpc "1.0", no method, an id that is an object
or a float, params that aren't an object, nesting past the parser's depth) is dropped without an
answer, since it has no id the server could answer to. A well-formed request it can't serve gets a
JSON-RPC error with that request's id. Either way the next request is answered with its own id.
"""

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

import fabric_desktop
from fabric_desktop.integrations import http_server
from fabric_desktop.integrations.http_server import build_app, server_config
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_conformance import env  # noqa: F401 - a fixture

# The engine's own folder, for a child process. Not all of sys.path: other tests add project folders
# to it, and on Windows a variable longer than 32,767 characters stops the child starting.
ENGINE = str(Path(fabric_desktop.__file__).resolve().parent.parent)

READ_SECONDS = 20  # generous: a first answer waits for the process to import everything
INIT = {"jsonrpc": "2.0", "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fuzz", "version": "1"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def frame(**message):
    return json.dumps({"jsonrpc": "2.0", **message}).encode()


# ---------------------------------------------------------------------------- the stdio process

class Stdio:
    """``fabric-desktop mcp`` as an AI tool starts it, fed raw bytes; stdout and stderr read by threads."""

    def __init__(self, tmp, client_id):
        process_env = dict(os.environ, LOCALAPPDATA=str(tmp), HOME=str(tmp), ProgramData=str(tmp / "programdata"),
                           PYTHONPATH=ENGINE)
        self.proc = subprocess.Popen([sys.executable, "-m", "fabric_desktop.cli", "mcp", "--client", client_id],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=process_env)
        self.lines: queue.Queue = queue.Queue()
        self.stderr: list[bytes] = []
        self._next = 0
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines.put), daemon=True).start()
        self._err = threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr.append), daemon=True)
        self._err.start()

    @staticmethod
    def _pump(pipe, put):
        for line in iter(pipe.readline, b""):
            put(line)
        pipe.close()

    def write(self, data: bytes):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def call(self, method, params=None, pre: bytes = b""):
        """Send ``pre`` (raw bytes, if any) and then a valid request; wait for that request's answer.
        Returns (its answer, every other message that came first)."""
        self._next += 1
        rid = f"good-{self._next}"
        message = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self.write(pre + json.dumps(message).encode() + b"\n")
        others = []
        deadline = time.monotonic() + READ_SECONDS
        while True:
            try:
                line = self.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f"no answer to {rid}; alive={self.alive()}; stderr={self.err_text()[-2000:]}") from None
            got = json.loads(line)
            if got.get("id") == rid:
                return got, others
            others.append(got)

    def initialize(self):
        answer, others = self.call("initialize", INIT["params"])
        assert others == [] and answer["result"]["serverInfo"]["name"] == "fabric-desktop", answer
        self.write(json.dumps(INITIALIZED).encode() + b"\n")
        return answer

    def tools(self, pre: bytes = b""):
        answer, others = self.call("tools/list", pre=pre)
        assert "fabric_desktop_get_status" in {t["name"] for t in answer["result"]["tools"]}, answer
        return others

    def alive(self):
        return self.proc.poll() is None

    def err_text(self):
        return b"".join(self.stderr).decode("utf-8", "replace")

    def close(self, timeout=10):
        if self.alive():
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self._err.join(5)
        return self.proc.returncode


def _client(root):
    store = IntegrationStore.at(root / "FabricDesktop")
    store.set_setting("enabled", True)
    client, _ = store.add_client("Fuzz", grants=["READ_METADATA"])
    return client["id"]


@pytest.fixture
def stdio(tmp_path):
    started = Stdio(tmp_path, _client(tmp_path))
    yield started
    started.close()


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    """One initialized process for every bad-frame case, as a long-lived AI tool session would be."""
    tmp = tmp_path_factory.mktemp("fuzz")
    started = Stdio(tmp, _client(tmp))
    started.initialize()
    yield started
    started.close()


DEEP = b"[" * 10_000 + b"]" * 10_000
DEEP_OBJECT = b'{"a":' * 10_000 + b"1" + b"}" * 10_000
HUGE = b"a" * 3_000_000

# name -> (raw line without its newline, what the server answers: None for nothing, else (id, error code or "result"))
BAD_FRAMES = {
    "not_json": (b"this is not json {", None),
    "empty_line": (b"", None),
    "whitespace_only": (b" \t  \r", None),
    "batch_array": (b"[" + frame(id="in-batch", method="tools/list") + b"]", None),
    "empty_array": (b"[]", None),
    "number": (b"42", None),
    "null": (b"null", None),
    "jsonrpc_1_0": (json.dumps({"jsonrpc": "1.0", "id": "v1", "method": "tools/list"}).encode(), None),
    "missing_method": (frame(id="no-method"), None),
    "object_id": (frame(id={"good-1": 1}, method="tools/list"), None),
    "float_id": (frame(id=1.5, method="tools/list"), None),
    "unknown_method": (frame(id="unknown", method="no/such/method"), ("unknown", -32601)),
    "params_a_list": (frame(id="params-list", method="tools/call", params=[1, 2]), None),
    "params_wrong_types": (frame(id="params-types", method="tools/call", params={"name": 5}), ("params-types", -32602)),
    "arguments_a_string": (frame(id="args-string", method="tools/call",
                                 params={"name": "fabric_desktop_get_status", "arguments": "x"}), ("args-string", -32602)),
    "very_long_garbage": (HUGE, None),
    "very_long_valid": (frame(id="long", method="tools/list", params={"_meta": {"pad": HUGE.decode()}}), ("long", "result")),
    "deeply_nested_array": (DEEP, None),
    "deeply_nested_in_a_request": (b'{"jsonrpc":"2.0","id":"deep","method":"tools/list","params":{"_meta":{"x":' + DEEP + b"}}}",
                                   None),
    "deeply_nested_object": (DEEP_OBJECT, None),
    "invalid_utf8_alone": (b"\xff\xfe\xfd\x80", None),
    # Bad bytes inside a string are replaced (U+FFFD), so the request is still well formed and answered.
    "invalid_utf8_in_a_string": (b'{"jsonrpc":"2.0","id":"utf8","method":"tools/list","params":{"_meta":{"x":"\xff\xc3"}}}',
                                 ("utf8", "result")),
    # JSON-RPC: a message with an id is a request, whatever its name; there is no such request method.
    "notification_with_an_id": (frame(id="notif", method="notifications/initialized"), ("notif", -32601)),
    "a_response_nobody_asked_for": (frame(id="never-sent", result={}), None),
}


@pytest.mark.parametrize("name", BAD_FRAMES)
def test_a_bad_stdio_frame_between_good_ones(session, name):
    bad, expected = BAD_FRAMES[name]
    others = session.tools(pre=bad + b"\n")  # the good request right behind it is answered with its own id
    if expected is None:
        assert others == [], others
    else:
        rid, what = expected
        assert len(others) == 1 and others[0]["id"] == rid, others
        if what == "result":
            assert "result" in others[0], others[0]
        else:
            assert others[0]["error"]["code"] == what and "result" not in others[0], others[0]
    assert session.alive()
    assert session.tools() == []  # and the one after it, with nothing left over


def test_a_request_split_across_two_writes_is_answered_once(session):
    message = frame(id="split", method="tools/list") + b"\n"
    half = len(message) // 2
    session.write(message[:half])
    time.sleep(0.2)  # the reader sees half a line first
    session.write(message[half:])
    others = session.tools()
    assert [m["id"] for m in others] == ["split"] and "result" in others[0]


def test_every_bad_frame_before_initialize_then_initialize_and_tools_list_work(stdio):
    for bad, _ in BAD_FRAMES.values():
        stdio.write(bad + b"\n")
    answer, others = stdio.call("initialize", INIT["params"])
    assert answer["result"]["protocolVersion"] == "2025-11-25"
    # Whatever came back before it answers only requests that were sent, and none of them ran.
    sent = {exp[0] for _, exp in BAD_FRAMES.values() if exp is not None}
    assert {m["id"] for m in others} <= sent and all("error" in m for m in others), others
    stdio.write(json.dumps(INITIALIZED).encode() + b"\n")
    assert stdio.tools() == []
    assert stdio.alive()


def test_tools_call_before_initialize_is_refused_and_doesnt_run(stdio):
    answer, others = stdio.call("tools/call", {"name": "fabric_desktop_get_status", "arguments": {}})
    assert others == [] and "result" not in answer and answer["error"]["code"] == -32602, answer
    stdio.initialize()
    status, _ = stdio.call("tools/call", {"name": "fabric_desktop_get_status", "arguments": {}})
    assert status["result"]["structuredContent"]["caller"]["channel"] == "mcp-stdio"


def test_closing_stdin_mid_frame_ends_the_process_cleanly(stdio):
    stdio.initialize()
    stdio.write(b'{"jsonrpc":"2.0","id":"cut","method":"tools/li')
    started = time.monotonic()
    assert stdio.close(timeout=10) == 0
    assert time.monotonic() - started < 5
    err = stdio.err_text()
    assert "Traceback" not in err and "Error" not in err, err
    assert err.strip().startswith("KratosForge MCP server")  # its start line, and nothing else
    assert stdio.lines.empty()  # no answer to the half request


# ---------------------------------------------------------------------------- the local API

@pytest.fixture
def api(env, monkeypatch):  # noqa: F811 - the conformance fixture
    """The local API as ``serve`` runs it (server_config: its h11 protocol and limits), on a real socket."""
    monkeypatch.setattr(http_server, "BODY_SECONDS", 0.5)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = build_app(env["registry"], env["store"], port, lambda: env["service"])
    server = uvicorn.Server(server_config(app, port))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the local API server didn't start"
    yield {"port": port, "token": env["token"], "client": env["client"]}
    server.should_exit = True
    thread.join(10)
    sock.close()


INVOKE = "/api/v1/capabilities/fabric_desktop_get_status/invoke"
MCP_ACCEPT = "Accept: application/json, text/event-stream\r\n"


def head(api, method, path, length=None, content_type="application/json", extra="", keep_alive=False):
    lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{api['port']}", f"Authorization: Bearer {api['token']}"]
    if not keep_alive:
        lines.append("Connection: close")
    if content_type:
        lines.append(f"Content-Type: {content_type}")
    if length is not None:
        lines.append(f"Content-Length: {length}")
    return ("\r\n".join(lines) + "\r\n" + extra + "\r\n").encode()


def exchange(api, data, chunk=None, timeout=10.0):
    """Send raw bytes and read one response (status, headers, body), and whether the server then closed.
    A reader thread runs from the start, so a response sent before all of ``data`` was taken is still read."""
    s = socket.create_connection(("127.0.0.1", api["port"]), timeout=timeout)
    got, done = bytearray(), threading.Event()

    def read():
        try:
            while chunk_ := s.recv(65536):
                got.extend(chunk_)
        except OSError:  # a reset once the server has answered and closed
            pass
        done.set()
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        step = chunk or len(data)
        for i in range(0, len(data), step):
            s.sendall(data[i:i + step])
            if chunk:
                time.sleep(0.01)
    except OSError:  # refused part way: what some cases want
        pass
    deadline = time.monotonic() + timeout
    while b"\r\n\r\n" not in got and not done.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    header, _, _ = bytes(got).partition(b"\r\n\r\n")
    assert header, f"no response within {timeout}s"
    fields = {k.strip().lower(): v.strip() for k, _, v in (line.decode("latin-1").partition(":") for line in header.split(b"\r\n")[1:])}
    length = int(fields.get("content-length", "0"))
    while len(got) - len(header) - 4 < length and not done.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    closed = done.wait(5)
    s.close()
    status = int(header.split(b" ", 2)[1])
    return status, fields, bytes(got)[len(header) + 4:len(header) + 4 + length], closed


def api_error(status, fields, body, code):
    assert 400 <= status < 500, (status, body)
    assert fields["content-type"].startswith("application/json"), fields
    data = json.loads(body)
    assert data["ok"] is False and data["error"]["code"] == code, data
    assert isinstance(data["error"]["message"], str) and isinstance(data["error"]["retryable"], bool)
    return data["error"]


def still_serves(api):
    body = b'{"arguments": {}}'
    status, _, out, _ = exchange(api, head(api, "POST", INVOKE, len(body)) + body)
    assert status == 200, out
    assert json.loads(out)["result"]["caller"]["client_id"] == api["client"]["id"]


def no_server_errors(caplog):
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")], caplog.text


@pytest.mark.parametrize("body, content_type", [
    (b"not json {", "application/json"),
    (b"\xff\xfe\x00 not utf-8", "application/json"),
    (b'[{"arguments": {}}]', "application/json"),  # REST isn't JSON-RPC: a batch is just a wrong body
    (b"not json {", "text/plain"),
])
def test_rest_bad_body_is_a_400_and_the_next_request_works(api, caplog, body, content_type):
    status, fields, out, _ = exchange(api, head(api, "POST", INVOKE, len(body), content_type) + body)
    api_error(status, fields, out, "invalid_input")
    still_serves(api)
    no_server_errors(caplog)


def test_rest_reads_the_body_as_json_whatever_its_content_type(api):
    """Deliberate: the documented ``curl -X POST`` sends no Content-Type (and ``curl -d`` sends a form
    one), and with no browser Origin allowed a lax Content-Type opens nothing. Pinned so a change is seen."""
    body = b'{"arguments": {}}'
    for content_type in ("text/plain", "application/x-www-form-urlencoded", None):
        status, _, out, _ = exchange(api, head(api, "POST", INVOKE, len(body), content_type) + body)
        assert status == 200, (content_type, out)


@pytest.mark.parametrize("body, code", [
    (b"not json {", -32700),  # parse error
    (b"[" + frame(id=1, method="initialize", params=INIT["params"]) + b"]", None),  # a batch: not served
    (b"42", None),
])
def test_mcp_over_http_bad_json_rpc_is_a_400_with_a_json_rpc_error(api, caplog, body, code):
    status, fields, out, _ = exchange(api, head(api, "POST", "/mcp", len(body), extra=MCP_ACCEPT) + body)
    assert status == 400 and fields["content-type"].startswith("application/json"), (status, out)
    data = json.loads(out)
    assert data["jsonrpc"] == "2.0" and data["id"] is None and "result" not in data, data
    assert data["error"]["code"] == (code if code is not None else data["error"]["code"]) and data["error"]["code"] < 0
    still_serves(api)
    no_server_errors(caplog)


def test_mcp_over_http_with_a_wrong_content_type_is_a_400(api, caplog):
    # The MCP SDK answers this one in plain text ("Invalid Content-Type header"), before KratosForge sees it.
    body = frame(id=1, method="initialize", params=INIT["params"])
    status, _, out, _ = exchange(api, head(api, "POST", "/mcp", len(body), "text/plain", extra=MCP_ACCEPT) + body)
    assert status == 400 and b"Content-Type" in out, out
    still_serves(api)
    no_server_errors(caplog)


@pytest.mark.parametrize("path", [INVOKE, "/mcp"])
def test_a_body_shorter_than_its_content_length_times_out_with_a_408(api, caplog, path):
    started = time.monotonic()
    status, fields, out, closed = exchange(api, head(api, "POST", path, 500, extra=MCP_ACCEPT, keep_alive=True) + b'{"argu')
    assert time.monotonic() - started < 3  # BODY_SECONDS is 0.5 here, and the server closes at once
    err = api_error(status, fields, out, "timeout")
    assert status == 408 and err["retryable"] is True
    assert closed  # the connection isn't reused: what follows can't be read as the rest of this body
    still_serves(api)
    no_server_errors(caplog)


def test_a_client_that_hangs_up_mid_body_is_dropped_quietly(api, caplog):
    s = socket.create_connection(("127.0.0.1", api["port"]), timeout=5)
    s.sendall(head(api, "POST", INVOKE, 500) + b'{"argu')
    s.shutdown(socket.SHUT_WR)
    s.settimeout(5)
    try:
        assert s.recv(65536) == b""  # nobody to answer: closed, no response
    except ConnectionResetError:
        pass
    s.close()
    still_serves(api)
    no_server_errors(caplog)  # no "Exception in ASGI application"


@pytest.mark.parametrize("data", [
    pytest.param(lambda api: head(api, "POST", INVOKE, 17, extra="X-Big: " + "a" * 1_000_000 + "\r\n") + b'{"arguments": {}}',
                 id="a 1 MB header"),
    pytest.param(lambda api: head(api, "GET", "/api/v1/capabilities", extra="".join(f"X-{i}: {'v' * 20}\r\n" for i in range(20_000))),
                 id="20000 headers"),
    pytest.param(lambda api: b"NOT A REQUEST LINE\r\n\r\n", id="a garbage request line"),
])
def test_a_huge_or_malformed_head_is_a_400_in_the_apis_shape(api, caplog, data):
    status, fields, out, closed = exchange(api, data(api), chunk=16384)
    api_error(status, fields, out, "invalid_input")
    assert status == 400 and closed
    still_serves(api)


def test_the_uvicorn_internals_the_local_api_overrides_still_exist():
    """_protocol() overrides uvicorn's send_400_response and wraps connection handling. uvicorn is
    pinned in constraints.txt; if an upgrade renames these, this fails instead of the API silently
    answering bad heads in plain text again."""
    from uvicorn.protocols.http.h11_impl import H11Protocol
    for name in ("send_400_response", "connection_made", "connection_lost", "data_received"):
        assert callable(getattr(H11Protocol, name, None)), name
    assert issubclass(http_server._protocol(), H11Protocol)
    assert "send_400_response" in vars(http_server._protocol())
