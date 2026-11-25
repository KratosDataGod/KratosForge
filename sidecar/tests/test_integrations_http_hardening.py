"""The local HTTP API (integrations/http_server.py) against a real server on 127.0.0.1, set up as
``serve`` sets it up (``server_config``): the caller's address comes from the socket and request
headers never decide who is calling; the account check runs for every caller; an unexpected failure is answered without its text or a traceback; and a
call that runs past the header deadline is not cut off. Raw sockets, so nothing smooths the requests."""

import json
import socket
import threading
import time

import pytest
import uvicorn
from starlette.testclient import TestClient

from fabric_desktop.integrations import http_server, peer
from fabric_desktop.integrations.http_server import build_app

from test_integrations_http import IN_PROCESS, PORT, auth, http_client, registry, this_account


@pytest.fixture(autouse=True)
def no_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


@pytest.fixture
def store(tmp_path):
    from fabric_desktop.integrations.store import IntegrationStore

    s = IntegrationStore(tmp_path / "integrations.sqlite")
    s.set_setting("enabled", True)
    return s


def _start(app_for_port):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(http_server.server_config(app_for_port(port), port))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the local API server didn't start"

    def stop():
        server.should_exit = True
        thread.join(10)
        sock.close()
    return port, stop


def _request(port, method, path, headers, body=b"", timeout=30.0):
    """One request on its own connection: (the caller's own port, status, parsed JSON body)."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        lines = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{port}", "Connection: close",
                 f"Content-Length: {len(body)}", *(f"{k}: {v}" for k, v in headers.items())]
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        own_port = s.getsockname()[1]
    head, _, payload = data.partition(b"\r\n\r\n")
    return own_port, int(head.split(b" ", 2)[1]), json.loads(payload) if payload else None


# --------------------------------------------------------------------------- the caller is the socket

@pytest.fixture
def checked_server(store, monkeypatch):
    """A real server whose account check (peer.same_user, Windows' answer) records which caller port
    it was asked about and answers ``answer[0]``."""
    asked, answer = [], [True]

    def same_user(peer_port, server_port):
        asked.append((peer_port, server_port))
        return answer[0]
    monkeypatch.setattr(peer, "checked", lambda: True)
    monkeypatch.setattr(peer, "same_user", same_user)
    port, stop = _start(lambda port: build_app(registry(store), store, port))
    yield port, asked, answer
    stop()


CLAIMED = ["192.0.2.1:1", "192.0.2.1", "127.0.0.1:1", "[::1]:1", "127.0.0.1, 192.0.2.1:1"]


@pytest.mark.parametrize("claimed", CLAIMED)
def test_the_callers_address_comes_from_the_socket(store, checked_server, claimed):
    port, asked, _ = checked_server
    _, token = http_client(store)
    own_port, status, body = _request(port, "GET", "/api/v1/capabilities", auth(token) | {"X-Forwarded-For": claimed})
    assert status == 200 and body["ok"] is True
    assert asked == [(own_port, port)]


@pytest.mark.parametrize("claimed", CLAIMED)
def test_the_account_check_uses_the_real_connection(store, checked_server, claimed):
    port, asked, answer = checked_server
    answer[0] = False
    _, token = http_client(store)
    own_port, status, body = _request(port, "GET", "/api/v1/capabilities", auth(token) | {"X-Forwarded-For": claimed})
    assert status == 403 and "Windows account" in body["error"]["message"]
    assert asked == [(own_port, port)]


def test_the_server_config_takes_the_callers_address_from_the_socket_only():
    cfg = http_server.server_config(lambda scope, receive, send: None, PORT)
    assert cfg.proxy_headers is False


def test_the_account_check_runs_for_every_caller(store):
    asked = []

    def same_user(peer_port, server_port):
        asked.append(peer_port)
        return True
    _, token = http_client(store)
    app = build_app(registry(store), store, PORT, same_user=same_user)
    for n, host in enumerate(["127.0.0.1", "::1", "localhost"]):
        # Not entered as a context manager: the app's lifespan can only run once, and these routes don't need it.
        c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=(host, 50000 + n))
        assert c.get("/api/v1/health").status_code == 200
        assert c.get("/api/v1/capabilities", headers=auth(token)).status_code == 200
    assert asked == [50000, 50000, 50001, 50001, 50002, 50002]


def test_a_caller_without_a_loopback_address_is_refused_before_the_account_check(store):
    asked = []
    app = build_app(registry(store), store, PORT, same_user=lambda p, s: asked.append(p) or True)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:  # the in-process client's own name
        r = c.get("/api/v1/health")
    assert r.status_code == 403 and r.json()["error"]["message"] == "Only this PC can use the local API."
    assert asked == []


# --------------------------------------------------------------------------- failures don't leak

SECRET = "fdt_" + "s3cr3t" * 6  # pragma: allowlist secret (made up)


def test_an_unexpected_failure_is_a_plain_500_without_its_text_or_a_traceback(store):
    reg = registry(store)

    def broken(ctx):
        raise RuntimeError(f"boom at C:\\Users\\someone\\AppData token={SECRET}")
    reg.describe = broken  # type: ignore[method-assign]
    _, token = http_client(store)
    with TestClient(build_app(reg, store, PORT, same_user=this_account), base_url=f"http://127.0.0.1:{PORT}",
                    client=IN_PROCESS, raise_server_exceptions=False) as c:
        r = c.get("/api/v1/capabilities", headers=auth(token))
    assert r.status_code == 500 and r.headers["content-type"] == "application/json"
    assert r.json() == {"ok": False, "error": {"code": "internal", "message": "KratosForge hit an unexpected problem.",
                                               "retryable": False}}
    for leaked in (SECRET, "Traceback", "RuntimeError", "AppData", "someone"):
        assert leaked not in r.text


def test_a_refusal_never_echoes_the_token_it_was_sent(store, checked_server):
    port, _, _ = checked_server
    for headers in ({"Authorization": f"Bearer {SECRET}"}, {"Authorization": f"Bearer {SECRET}", "Origin": "null"},
                    {"Authorization": f"Bearer {SECRET}", "X-Forwarded-For": "192.0.2.1:1"}):
        _, status, body = _request(port, "GET", "/api/v1/capabilities", headers)
        assert status in (401, 403) and SECRET not in json.dumps(body)


# --------------------------------------------------------------------------- a running call isn't cut off

def test_a_call_that_runs_past_the_header_deadline_still_gets_its_answer(store, monkeypatch):
    monkeypatch.setattr(http_server, "HEADER_SECONDS", 0.3)
    _, token = http_client(store)
    port, stop = _start(lambda port: build_app(registry(store), store, port))
    try:
        body = json.dumps({"arguments": {"seconds": 1.0}}).encode()
        _, status, result = _request(port, "POST", "/api/v1/capabilities/ext_slow/invoke",
                                     auth(token) | {"Content-Type": "application/json"}, body)
    finally:
        stop()
    assert status == 200 and result["ok"] is True and result["result"] == {"slept": 1.0}
