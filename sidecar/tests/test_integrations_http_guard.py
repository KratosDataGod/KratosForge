"""The local HTTP API's edges (integrations/http_server.py) that test_integrations_http.py leaves out: the
same-account check, refused requests audited without flooding the disk, a chunked body past the limit, a
server busy with other calls, and ``serve`` starting, writing http.json and stopping with its parent.
No real socket: Starlette's test client and a stand-in uvicorn server."""

import json
import os
import subprocess
import sys
import threading

import pytest
from starlette.testclient import TestClient

from fabric_desktop.integrations import http_server, peer
from fabric_desktop.integrations.http_server import Guard, build_app
from fabric_desktop.integrations.store import IntegrationStore

from test_integrations_http import PORT, auth, http_client, registry

BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def store(tmp_path):
    s = IntegrationStore(tmp_path / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


class Reached:
    """The app behind the guard: records that a request got through."""

    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":  # the test client's start and stop, passed through by the guard
            while (await receive())["type"] != "lifespan.shutdown":
                await send({"type": "lifespan.startup.complete"})
            await send({"type": "lifespan.shutdown.complete"})
            return
        self.calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class Audit:
    def __init__(self, fail=False):
        self.lines, self.fail = [], fail

    def __call__(self, ctx, capability, arguments_hash, request, started, seconds, err, result):
        if self.fail:
            raise OSError("disk full")
        self.lines.append({"channel": ctx.caller.channel, "request": request, "code": err.code})


# --------------------------------------------------------------------------- who may call

def test_a_program_of_another_windows_account_is_refused_before_its_token_is_looked_at(store):
    asked, inner = [], Reached()
    lookups = []
    store.client_by_token = lambda token: lookups.append(token)  # must not be reached

    def same_user(peer_port, server_port):
        asked.append((peer_port, server_port))
        return False
    guard = Guard(inner, store, PORT, same_user=same_user)
    with TestClient(guard, base_url=BASE, client=("127.0.0.1", 51000)) as c:
        r = c.get("/api/v1/health")
    assert r.status_code == 403 and "same person as KratosForge" in r.json()["error"]["hint"]
    assert asked == [(51000, PORT)] and inner.calls == [] and lookups == []


def test_a_program_of_this_account_goes_on_to_the_other_checks(store):
    inner = Reached()
    guard = Guard(inner, store, PORT, same_user=lambda p, s: True)
    with TestClient(guard, base_url=BASE, client=("127.0.0.1", 51000)) as c:
        assert c.get("/api/v1/health").status_code == 200
        assert c.get("/api/v1/health", headers={"Host": "evil.example"}).status_code == 403
    assert inner.calls == ["/api/v1/health"]


def test_the_check_is_on_by_default_only_where_windows_can_answer(store, monkeypatch):
    monkeypatch.setattr(peer, "checked", lambda: False)
    assert Guard(Reached(), store, PORT).same_user is None
    monkeypatch.setattr(peer, "checked", lambda: True)
    assert Guard(Reached(), store, PORT).same_user is peer.same_user


def test_a_websocket_is_not_served(store):
    inner, sent = Reached(), []

    async def send(message):
        sent.append(message)
    import anyio
    anyio.run(Guard(inner, store, PORT), {"type": "websocket", "path": "/mcp", "headers": []}, None, send)
    assert inner.calls == [] and sent == []


# --------------------------------------------------------------------------- refused requests in the audit

def test_a_flood_of_refusals_writes_one_line_then_counts_the_rest(store, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(http_server.time, "monotonic", lambda: now[0])
    audit = Audit()
    guard = Guard(Reached(), store, PORT, registry=type("R", (), {"audit": audit})())
    with TestClient(guard, base_url=BASE) as c:
        for _ in range(5):
            assert c.get("/api/v1/capabilities", headers={"Host": "evil.example"}).status_code == 403
        assert len(audit.lines) == 1 and "refused_since_last_line" not in audit.lines[0]["request"]
        c.get("/mcp", headers={"Host": "evil.example"})  # the same reason and caller: still counted, not written
        now[0] += http_server.REFUSED_EVERY + 1
        c.get("/mcp", headers={"Host": "evil.example"})
    assert len(audit.lines) == 2
    assert audit.lines[1]["request"] == {"method": "GET", "path": "/mcp", "refused_since_last_line": 5}
    assert audit.lines[1]["channel"] == "mcp-http" and audit.lines[0]["channel"] == "api"


def test_each_reason_is_audited_on_its_own(store):
    audit = Audit()
    guard = Guard(Reached(), store, PORT, registry=type("R", (), {"audit": audit})())
    with TestClient(guard, base_url=BASE) as c:
        c.get("/api/v1/capabilities", headers={"Host": "evil.example"})
        c.get("/api/v1/capabilities", headers={"Origin": "https://evil.example"})
        c.get("/api/v1/capabilities")
    assert len(audit.lines) == 3 and {line["code"] for line in audit.lines} == {"permission_denied"}


def test_an_audit_that_fails_doesnt_turn_a_refusal_into_a_pass(store, capsys):
    inner = Reached()
    guard = Guard(inner, store, PORT, registry=type("R", (), {"audit": Audit(fail=True)})())
    with TestClient(guard, base_url=BASE) as c:
        assert c.get("/api/v1/capabilities", headers={"Origin": "null"}).status_code == 403
    assert inner.calls == [] and "could not record a refused request: OSError: disk full" in capsys.readouterr().err


def test_a_path_in_the_audit_is_cut_to_200_characters(store):
    audit = Audit()
    guard = Guard(Reached(), store, PORT, registry=type("R", (), {"audit": audit})())
    with TestClient(guard, base_url=BASE) as c:
        c.get("/api/v1/" + "x" * 500)
    assert len(audit.lines[0]["request"]["path"]) == 200


# --------------------------------------------------------------------------- bodies and load

def test_a_chunked_body_past_the_limit_is_refused_while_it_is_read(store):
    _, token = http_client(store)

    def chunks():
        for _ in range(http_server.MAX_BODY_BYTES // 65536 + 2):
            yield b"a" * 65536
    with TestClient(build_app(registry(store), store, PORT), base_url=BASE) as c:
        r = c.post("/api/v1/capabilities/ext_slow/invoke", headers=auth(token), content=chunks())
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"


def test_a_content_length_that_isnt_a_number_is_refused(store):
    _, token = http_client(store)
    inner = Reached()
    with TestClient(Guard(inner, store, PORT), base_url=BASE) as c:
        r = c.post("/api/v1/capabilities/x/invoke", headers=auth(token) | {"Content-Length": "-1"}, content=b"")
    assert r.status_code == 413 and inner.calls == []


def test_a_call_that_waits_too_long_for_a_slot_is_told_to_try_again(store, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_CONCURRENT", 0)  # every slot taken
    monkeypatch.setattr(http_server, "QUEUE_WAIT_SECONDS", 0.05)
    _, token = http_client(store)
    with TestClient(build_app(registry(store), store, PORT), base_url=BASE) as c:
        r = c.post("/api/v1/capabilities/ext_slow/invoke", headers=auth(token), json={"arguments": {}})
    assert r.status_code == 429 and r.headers["Retry-After"] == "5" and r.json()["error"]["retryable"]


# --------------------------------------------------------------------------- the parent engine

def test_the_parent_is_alive_while_its_process_runs():
    assert http_server._parent_alive(0) and http_server._parent_alive(-1)  # no parent to follow
    assert http_server._parent_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not http_server._parent_alive(child.pid)


# --------------------------------------------------------------------------- serve

class Server:
    """uvicorn.Server: 'listens' at once, and runs until told to exit."""
    seen = {}

    def __init__(self, config):
        self.config, self.started, self.should_exit = config, False, False
        Server.seen = {"host": config.host, "port": config.port, "server_header": config.server_header}

    def run(self):
        self.started = True
        state = Server.root / "integrations" / "http.json"
        done = threading.Event()
        while not self.should_exit and not done.wait(0.02):
            if state.exists():
                Server.seen["state"] = json.loads(state.read_text())
        assert self.should_exit


@pytest.fixture
def serving(tmp_path, monkeypatch):
    import uvicorn
    root = tmp_path / "data"
    s = IntegrationStore.at(root)
    s.set_setting("enabled", True)
    s.set_setting("http_enabled", True)
    Server.root = root
    monkeypatch.setattr("fabric_desktop.service.data_dir", lambda: root)
    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(peer, "checked", lambda: False)

    # serve() sets its own authorizer and audit on the registry; give it a fresh one, not the process-wide
    # default other tests use.
    def fresh():
        from fabric_desktop.integrations.capabilities import register_core
        from fabric_desktop.integrations.registry import Registry
        reg = Registry()
        register_core(reg)
        return reg
    monkeypatch.setattr("fabric_desktop.integrations.registry.default_registry", fresh)
    return root


def test_serve_listens_on_loopback_only_says_so_and_stops_with_its_parent(serving, monkeypatch):
    alive = iter([True, False])
    monkeypatch.setattr(http_server, "_parent_alive", lambda pid: next(alive, False))
    monkeypatch.setattr(http_server.time, "sleep", lambda s: None)
    assert http_server.serve(47999, parent_pid=12345) == 0
    assert Server.seen["host"] == "127.0.0.1" and Server.seen["port"] == 47999 and Server.seen["server_header"] is False
    assert Server.seen["state"]["port"] == 47999 and Server.seen["state"]["pid"] == os.getpid()
    assert not (serving / "integrations" / "http.json").exists()  # gone once it stopped


def test_serve_wont_start_where_another_account_listens_on_the_port(serving, monkeypatch, capsys):
    monkeypatch.setattr(peer, "checked", lambda: True)
    monkeypatch.setattr(peer, "listener_is_other_user", lambda port: port == 47999)
    assert http_server.serve(47999) == 2
    assert "Another Windows account's program is listening on port 47999" in capsys.readouterr().err
    assert not (serving / "integrations" / "http.json").exists()


# --------------------------------------------------------------------------- resources over REST

def test_resources_are_listed_and_read_with_the_same_token_checks(store):
    _, token = http_client(store)
    with TestClient(build_app(registry(store), store, PORT), base_url=BASE) as c:
        assert c.get("/api/v1/resources").status_code == 401
        listed = c.get("/api/v1/resources", headers=auth(token)).json()
        uris = {r["uri"]: r for r in listed["resources"]}
        assert listed["ok"] and "fabric-desktop://capabilities" in uris
        assert uris["fabric-desktop://projects/{id}"]["template"] is True
        read = c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://capabilities"}, headers=auth(token))
        assert read.status_code == 200 and read.json()["capability"] == uris["fabric-desktop://capabilities"]["capability"]
        missing = c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://nothing"}, headers=auth(token))
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
        bad = c.get("/api/v1/resources/read", params={"uri": "fabric-desktop://capabilities?bogus=1"}, headers=auth(token))
        assert bad.status_code == 400


# ---- connections: a cap, and a deadline for each request's headers (http_server.server_config) ----

def _serving(monkeypatch, **limits):
    import socket
    import threading
    import time

    import uvicorn

    from fabric_desktop.integrations import http_server

    for name, value in limits.items():
        monkeypatch.setattr(http_server, name, value)

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
        await send({"type": "http.response.body", "body": b"ok"})

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = http_server.server_config(app, port)
    assert (config.host, config.limit_concurrency, config.timeout_keep_alive) == ("127.0.0.1", http_server.MAX_CONNECTIONS, http_server.IDLE_SECONDS)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started

    def stop():
        server.should_exit = True
        thread.join(10)
        sock.close()
    return port, stop


def _get(port, timeout=5.0):
    import socket

    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while chunk := s.recv(4096):
            data += chunk
    return int(data.split(b" ", 2)[1]) if data else None


def _closed_within(s, seconds):
    import socket
    import time

    s.settimeout(seconds)
    start = time.monotonic()
    try:
        while s.recv(1024):
            pass
    except (socket.timeout, TimeoutError):
        return None
    except ConnectionResetError:
        pass
    return time.monotonic() - start


def test_a_connection_that_sends_nothing_or_a_byte_at_a_time_is_closed(monkeypatch):
    import socket
    import time

    port, stop = _serving(monkeypatch, HEADER_SECONDS=0.5)
    try:
        silent = socket.create_connection(("127.0.0.1", port), timeout=5)
        trickle = socket.create_connection(("127.0.0.1", port), timeout=5)
        start, cut_off = time.monotonic(), False
        for b in b"GET / HTTP/1.1\r\nHost: x" * 4:
            try:
                trickle.sendall(bytes([b]))  # a byte at a time, past the deadline
            except OSError:  # closed under it: what this test wants
                cut_off = True
                break
            time.sleep(0.05)
        assert cut_off or _closed_within(trickle, 5) is not None
        assert time.monotonic() - start < 5
        assert _closed_within(silent, 5) is not None
        silent.close()
        trickle.close()
        assert _get(port) == 200  # and the server carries on
    finally:
        stop()


def test_past_the_connection_cap_a_request_gets_busy_and_the_slots_come_back(monkeypatch):
    import socket
    import time

    port, stop = _serving(monkeypatch, HEADER_SECONDS=2.0, MAX_CONNECTIONS=4)
    try:
        held = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(4)]
        time.sleep(0.2)
        assert _get(port) == 503
        for s in held:
            s.close()
        time.sleep(0.3)
        assert _get(port) == 200
    finally:
        stop()
