"""Run cells from VS Code: pairing (editor_pairing.py), the editor channel (editor_channel.py) and the
app's side (editor_service.py), against a real engine and a real loopback server."""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest
from test_kernel_sessions import out, svc  # noqa: F401
from test_prove import call

from fabric_desktop.editor_channel import MAX_BODY_BYTES as MAX_BODY
from fabric_desktop.editor_pairing import MAX_WAITING, REQUEST_SECONDS, Pairings


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def test_a_pairing_hands_its_token_over_once_and_keeps_only_a_hash(tmp_path):
    clock = Clock()
    p = Pairings(tmp_path, clock)
    req = p.request("C:\\repos\\sales\x07", "KratosForge for VS Code 0.1.0")
    assert p.collect(req["id"]) == {"state": "waiting"}
    assert [w["window"] for w in p.waiting()] == ["C:\\repos\\sales"]  # control characters dropped
    assert p.decide(req["id"], True, "8h")["paired"]
    got = p.collect(req["id"])
    assert got["state"] == "paired" and got["token"].startswith("kfp_")
    assert p.collect(req["id"]) == {"state": "expired"}  # handed over once
    assert got["token"] not in (tmp_path / "pairings.json").read_text(encoding="utf-8")
    assert p.check(got["token"])["window"] == "C:\\repos\\sales"
    assert Pairings(tmp_path, clock).check(got["token"]) is not None  # 8 hours survives a restart
    clock.now += 8 * 3600
    assert p.check(got["token"]) is None and p.list() == []
    assert p.check("kfp_not-a-token") is None and p.check(None) is None


def test_a_session_pairing_is_never_written_down_and_ends_when_asked(tmp_path):
    p = Pairings(tmp_path)
    req = p.request("C:\\repos\\a", "x")
    p.decide(req["id"], True, "session")
    token = p.collect(req["id"])["token"]
    assert not (tmp_path / "pairings.json").exists()
    assert Pairings(tmp_path).check(token) is None  # gone with the app
    [row] = p.list()
    assert p.end(row["id"]) and p.check(token) is None


def test_requests_are_few_and_short_lived(tmp_path):
    clock = Clock()
    p = Pairings(tmp_path, clock)
    for _ in range(MAX_WAITING):
        p.request("w", "c")
    with pytest.raises(PermissionError, match="Too many"):
        p.request("w", "c")
    denied = p.waiting()[0]["id"]
    p.decide(denied, False)
    assert p.collect(denied) == {"state": "denied"}
    clock.now += REQUEST_SECONDS + 1
    assert p.waiting() == []
    with pytest.raises(LookupError, match="expired"):
        p.decide(denied, True)
    with pytest.raises(ValueError, match="keep"):
        p.decide(p.request("w", "c")["id"], True, "forever")


class Window:
    """A VS Code window, as the extension talks to the channel."""

    def __init__(self, svc):
        port, self.hello = (svc.root / "editor-port").read_text(encoding="utf-8").split()
        self.port = int(port)
        self.token = ""

    def call(self, method, path, body=None, headers=None):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body)
            h["Content-Type"] = "application/json"
        con.request(method, path, body=data, headers=h)
        res = con.getresponse()
        payload = json.loads(res.read())
        con.close()
        return res.status, payload

    def pair(self, svc, keep="session", window=None):
        status, req = self.call("POST", "/v1/pair", {"window": window or str(svc.root), "client": "test"},
                                {"X-KratosForge-Hello": self.hello})
        assert status == 200, req
        [waiting] = call(svc, "editor_pending")["pairing"]
        assert waiting["code"] == req["result"]["code"]  # the app shows the code the window shows
        call(svc, "editor_decide_pairing", request_id=waiting["id"], approve=True, keep=keep)
        status, got = self.call("GET", f"/v1/pair/{req['result']['id']}", headers={"X-KratosForge-Hello": self.hello})
        self.token = got["result"]["token"]

    def run(self, path, code, timeout=30):
        status, started = self.call("POST", "/v1/run", {"path": path, "code": code})
        assert status == 200, started
        deadline = time.monotonic() + timeout
        snap = started["result"]
        while snap["status"] == "running" and time.monotonic() < deadline:
            snap = self.call("GET", f"/v1/run?path={_q(path)}&wait=0.5")[1]["result"]
        return snap


def _q(text):
    from urllib.parse import quote
    return quote(text, safe="")


@pytest.fixture
def paired(svc):  # noqa: F811
    svc.save_notebook("Daily", [{"type": "code", "source": "x = 1"}])
    call(svc, "editor_set_enabled", enabled=True)
    window = Window(svc)
    yield svc, window, svc.notebook_file_path("Daily")["path"]
    call(svc, "editor_set_enabled", enabled=False)


def test_the_channel_is_off_until_switched_on_and_ends_pairings_when_switched_off(svc, tmp_path):  # noqa: F811
    assert call(svc, "editor_status")["listening"] is False
    assert not (svc.root / "editor-port").exists()
    call(svc, "editor_set_enabled", enabled=True)
    window = Window(svc)
    window.pair(svc)
    assert window.call("GET", "/v1/status")[0] == 200
    call(svc, "editor_set_enabled", enabled=False)
    assert not (svc.root / "editor-port").exists() and call(svc, "editor_status")["pairings"] == []
    call(svc, "editor_set_enabled", enabled=True)
    assert Window(svc).hello != window.hello  # a new hello each time it listens
    window.port = Window(svc).port
    assert window.call("GET", "/v1/status")[0] == 401  # the old pairing ended


def test_only_this_pc_and_no_web_page_can_call(paired):
    svc, window, _ = paired
    assert window.call("POST", "/v1/pair", {"window": "w"})[0] == 401  # no hello
    assert window.call("POST", "/v1/pair", {"window": "w"}, {"X-KratosForge-Hello": "guess"})[0] == 401
    assert window.call("GET", "/v1/status")[0] == 401  # not paired
    hello = {"X-KratosForge-Hello": window.hello}
    assert window.call("POST", "/v1/pair", {"window": "w"}, {**hello, "Origin": "https://evil.example"})[0] == 403
    assert window.call("POST", "/v1/pair", {"window": "w"}, {**hello, "Host": "evil.example"})[0] == 403
    assert window.call("POST", "/v1/pair", None, {**hello, "Transfer-Encoding": "chunked"})[0] == 411
    assert call(svc, "editor_pending")["pairing"] == []  # none of those reached the person


def test_a_paired_window_runs_cells_in_the_notebooks_own_session(paired):
    svc, window, path = paired
    window.pair(svc)
    assert window.call("POST", "/v1/notebook", {"path": path})[1]["result"] == {"name": "Daily"}
    assert out(window.run(path, "y = 41\nprint(y + 1)")) == "42\n"
    # The same session as the app's: the app sees the variable, and the window sees the app's.
    assert any(v["name"] == "y" for v in call(svc, "kernel_variables", notebook="Daily"))
    call(svc, "start_cell", code="z = 'from the app'", notebook="Daily")
    time.sleep(0.5)
    assert out(window.run(path, "print(z)")) == "from the app\n"
    status, started = window.call("POST", "/v1/run", {"path": path, "code": "import time\nfor _ in range(300):\n    time.sleep(0.1)"})
    assert status == 200
    assert window.call("POST", "/v1/interrupt", {"path": path})[0] == 200
    deadline = time.monotonic() + 15
    while call(svc, "poll_cell", notebook="Daily")["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.2)
    assert call(svc, "poll_cell", notebook="Daily")["status"] != "running"
    lines = (svc.root / "audit").glob("audit-*.jsonl")
    actions = [json.loads(line)["action"] for f in lines for line in f.read_text(encoding="utf-8").splitlines()]
    assert {"editor_pair_request", "editor_run", "editor_interrupt"} <= set(actions)
    assert window.call("DELETE", "/v1/pair")[0] == 200
    assert window.call("POST", "/v1/run", {"path": path, "code": "1"})[0] == 401


def test_a_file_the_app_doesnt_know_isnt_run(paired, tmp_path):
    svc, window, _ = paired
    window.pair(svc)
    stray = svc.root / "elsewhere.ipynb"
    stray.write_text('{"cells": []}', encoding="utf-8")
    status, res = window.call("POST", "/v1/run", {"path": str(stray), "code": "print('no')"})
    assert status == 404 and "isn't in KratosForge's notebooks folder" in res["error"]["message"]
    assert window.call("POST", "/v1/run", {"path": r"\\host\share\x.ipynb", "code": "1"})[0] == 403


def test_a_window_reaches_only_notebooks_in_the_folder_it_paired_from(paired, tmp_path):
    svc, window, path = paired
    other = tmp_path / "other-repo"
    other.mkdir()
    window.pair(svc, window=str(other))
    for method, url, body in (("POST", "/v1/notebook", {"path": path}), ("POST", "/v1/run", {"path": path, "code": "1"}),
                              ("GET", f"/v1/run?path={_q(path)}", None), ("POST", "/v1/interrupt", {"path": path}),
                              ("GET", f"/v1/variables?path={_q(path)}", None)):
        status, res = window.call(method, url, body)
        assert status == 403 and "the folder this VS Code window was paired from" in res["error"]["message"], (url, res)
    # A sibling whose name starts the same isn't inside.
    window.pair(svc, window=str(svc.root)[:-1])
    assert window.call("POST", "/v1/notebook", {"path": path})[0] == 403


def test_a_negative_length_is_refused_and_an_unpaired_body_is_never_read(paired):
    svc, window, path = paired
    hello = {"X-KratosForge-Hello": window.hello, "Content-Type": "application/json"}
    con = http.client.HTTPConnection("127.0.0.1", window.port, timeout=10)
    con.putrequest("POST", "/v1/pair", skip_host=True)
    for k, v in {"Host": f"127.0.0.1:{window.port}", **hello, "Content-Length": "-1"}.items():
        con.putheader(k, v)
    con.endheaders()
    res = con.getresponse()
    assert res.status == 400 and b"negative" in res.read()
    con.close()
    # Not paired: refused on the headers, before a (claimed) 1 MiB body is read.
    con = http.client.HTTPConnection("127.0.0.1", window.port, timeout=10)
    con.putrequest("POST", "/v1/run", skip_host=True)
    for k, v in {"Host": f"127.0.0.1:{window.port}", "Content-Type": "application/json",
                 "Content-Length": str(1024 * 1024)}.items():
        con.putheader(k, v)
    con.endheaders()
    res = con.getresponse()
    assert res.status == 401
    con.close()


WRITE = "from fabric_desktop import lakehouse\nprint(lakehouse.write_refusal('LH_Gold', 'dbo', 'daily'))"


def _answer(svc, approve, seen):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        writes = call(svc, "editor_pending")["writes"]
        if writes:
            seen.append(writes[0])
            call(svc, "editor_decide_write", ask_id=writes[0]["id"], approve=approve)
            return
        time.sleep(0.05)


@pytest.mark.parametrize("process", [False, True])
def test_a_write_from_vs_code_asks_in_the_app_and_the_apps_own_doesnt(paired, process):
    svc, window, path = paired
    if process:
        call(svc, "save_app_settings", changes={"kernel_process": True})
    window.pair(svc)
    seen: list = []
    answer = threading.Thread(target=_answer, args=(svc, False, seen))
    answer.start()
    assert "was denied in KratosForge" in out(window.run(path, WRITE))
    answer.join()
    assert seen and seen[0]["target"] == "LH_Gold.dbo.daily" and seen[0]["notebook"] == "Daily"
    answer = threading.Thread(target=_answer, args=(svc, True, seen))
    answer.start()
    two = WRITE + "\nprint(lakehouse.write_refusal('LH_Gold', 'dbo', 'daily'))"
    assert out(window.run(path, two)) == "None\nNone\n"  # one approval covers the table for the cell
    answer.join()
    assert len(seen) == 2
    call(svc, "start_cell", code=WRITE, notebook="Daily")  # the app's own cell: nothing asks
    deadline = time.monotonic() + 20
    snap = call(svc, "poll_cell", notebook="Daily", wait=0.5)
    while snap["status"] == "running" and time.monotonic() < deadline:
        snap = call(svc, "poll_cell", notebook="Daily", wait=0.5)
    assert out(snap) == "None\n" and call(svc, "editor_pending")["writes"] == []


@pytest.mark.parametrize("route", ["status", "variables", "poll", "notebook"])
def test_turning_it_off_by_policy_stops_paired_windows_at_once(paired, tmp_path, monkeypatch, route):
    svc, window, path = paired
    window.pair(svc, keep="8h")
    assert window.call("POST", "/v1/run", {"path": path, "code": "secret = 'abc'"})[0] == 200
    time.sleep(0.5)
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(json.dumps({"integrations_editors": False}), encoding="utf-8")
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "policy_path", lambda: policy)
    method, url, body = {"status": ("GET", "/v1/status", None),
                         "variables": ("GET", f"/v1/variables?path={_q(path)}", None),
                         "poll": ("GET", f"/v1/run?path={_q(path)}", None),
                         "notebook": ("POST", "/v1/notebook", {"path": path})}[route]
    status, res = window.call(method, url, body)
    assert status == 403 and res["error"]["code"] == "off" and "abc" not in json.dumps(res)
    deadline = time.monotonic() + 10
    while (svc.root / "editor-port").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not (svc.root / "editor-port").exists()  # the channel stopped listening
    assert svc.editor_pairings.list() == [] and call(svc, "editor_status")["listening"] is False


def test_policy_can_keep_it_off(svc, tmp_path, monkeypatch):  # noqa: F811
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"integrations_editors": False}), encoding="utf-8")
    from fabric_desktop import app_settings
    monkeypatch.setattr(app_settings, "policy_path", lambda: policy)
    assert "doesn't let editors" in call(svc, "editor_status")["blocked"]
    with pytest.raises(AssertionError, match="doesn't let editors"):
        call(svc, "editor_set_enabled", enabled=True)
    policy.write_text(json.dumps({"integrations_editors": "yes"}), encoding="utf-8")  # unreadable: off
    assert call(svc, "editor_status")["blocked"]


@pytest.mark.parametrize("folder", ["", "   ", ".", "notebooks", "/", "C:\\" if __import__("os").name == "nt" else "//"])
def test_a_window_cant_pair_a_whole_drive_or_no_folder(paired, folder):
    svc, window, _ = paired
    status, res = window.call("POST", "/v1/pair", {"window": folder, "client": "t"}, {"X-KratosForge-Hello": window.hello})
    assert status == 403 and "whole drive" in res["error"]["message"]
    assert call(svc, "editor_pending")["pairing"] == []


def test_refused_calls_while_off_turn_it_off_once(svc):  # noqa: F811
    started = []
    svc._editor_turned_off = lambda: started.append(1)  # never finishes: the flag stays set
    svc._editor_state()["stopping"] = False
    assert [svc.editor_channel_open() for _ in range(5)] == [False] * 5
    time.sleep(0.2)
    assert started == [1]


def test_the_channel_proves_it_knows_the_hello_without_giving_it_away(paired):
    import hashlib
    import hmac
    svc, window, _ = paired
    nonce = "n" * 32
    status, res = window.call("GET", f"/v1/hello?nonce={nonce}")
    assert status == 200
    assert res["result"]["proof"] == hmac.new(window.hello.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    assert window.hello not in json.dumps(res)
    assert window.call("GET", "/v1/hello?nonce=short")[0] == 400


def test_only_programs_running_as_this_windows_account_can_call(paired, monkeypatch):
    from fabric_desktop.integrations import peer
    svc, window, path = paired
    window.pair(svc)
    asked = []
    monkeypatch.setattr(peer, "checked", lambda: True)
    monkeypatch.setattr(peer, "same_user", lambda client, server: asked.append((client, server)) or False)
    hello = {"X-KratosForge-Hello": window.hello}
    for method, url, body, headers in (("POST", "/v1/run", {"path": path, "code": "print(1)"}, None),
                                       ("POST", "/v1/pair", {"window": str(svc.root), "client": "t"}, hello),
                                       ("GET", "/v1/hello?nonce=" + "n" * 32, None, None)):
        status, res = window.call(method, url, body, headers)
        assert status == 403 and "Windows account" in res["error"]["message"], (url, res)
    assert asked and all(server == window.port for _, server in asked)
    assert call(svc, "editor_pending")["pairing"] == []
    monkeypatch.setattr(peer, "same_user", lambda client, server: True)
    assert window.call("GET", "/v1/status")[0] == 200


def test_idle_connections_cant_hold_more_than_a_few_engine_threads(tmp_path, monkeypatch):
    import socket
    from fabric_desktop import editor_channel
    from fabric_desktop.editor_channel import MAX_CONNECTIONS, Channel
    # Idle connections hold their slots for the whole test, whatever the machine's load.
    monkeypatch.setattr(editor_channel._Handler, "timeout", 120)
    channel = Channel(None, tmp_path)
    port = channel.start()
    slots = channel._server._slots

    def handlers():
        return sum("process_request_thread" in t.name for t in threading.enumerate())

    def until(check, seconds=30):
        deadline = time.monotonic() + seconds
        while not check() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert check()

    before = handlers()
    held, over = [], []
    try:
        for _ in range(MAX_CONNECTIONS):  # one at a time, each accepted before the next
            taken = MAX_CONNECTIONS - slots._value
            held.append(socket.create_connection(("127.0.0.1", port), timeout=30))
            until(lambda t=taken: MAX_CONNECTIONS - slots._value == t + 1)
        assert slots._value == 0
        for _ in range(MAX_CONNECTIONS * 2):
            s = socket.create_connection(("127.0.0.1", port), timeout=30)
            over.append(s)
            reply = s.recv(4096)
            assert reply.startswith(b"HTTP/1.0 503") and b'"busy"' in reply, reply
        assert handlers() - before <= MAX_CONNECTIONS
    finally:
        for s in held + over:
            s.close()
        until(lambda: slots._value == MAX_CONNECTIONS)  # slots come back once they close
        channel.stop()


def test_a_connection_that_sends_nothing_is_closed_and_frees_its_slot(tmp_path, monkeypatch):
    import socket
    from fabric_desktop import editor_channel
    from fabric_desktop.editor_channel import MAX_CONNECTIONS, Channel
    assert editor_channel._Handler.timeout == 5
    monkeypatch.setattr(editor_channel._Handler, "timeout", 0.3)
    channel = Channel(None, tmp_path)
    port = channel.start()
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=30)
        assert s.recv(4096) == b""  # closed by the engine, without an answer
        s.close()
        deadline = time.monotonic() + 30
        while channel._server._slots._value != MAX_CONNECTIONS and time.monotonic() < deadline:
            time.sleep(0.02)
        assert channel._server._slots._value == MAX_CONNECTIONS
    finally:
        channel.stop()


def test_the_hello_names_the_channel_api_and_refuses_an_extension_too_old_for_it(paired, monkeypatch):
    from fabric_desktop import editor_channel
    svc, window, _ = paired
    nonce = "n" * 32
    status, res = window.call("GET", f"/v1/hello?nonce={nonce}&api={editor_channel.API_VERSION}")
    assert status == 200 and res["result"]["api"] == editor_channel.API_VERSION
    assert window.call("GET", f"/v1/hello?nonce={nonce}")[0] == 200  # a window that doesn't say speaks API 1
    for garbled in ("abc", "1.5", "-1", "0x1", "%C2%B2", "1" * 7):
        status, res = window.call("GET", f"/v1/hello?nonce={nonce}&api={garbled}")
        assert status == 400 and res["error"]["code"] == "invalid_input" and "proof" not in json.dumps(res), garbled
    monkeypatch.setattr(editor_channel, "OLDEST_CLIENT_API", editor_channel.API_VERSION + 1)
    status, res = window.call("GET", f"/v1/hello?nonce={nonce}&api={editor_channel.API_VERSION}")
    assert status == 426 and res["error"]["code"] == "extension_too_old" and "proof" not in json.dumps(res)
    # An extension from before the version was sent speaks API 1, so it's told to update too, not let through.
    status, res = window.call("GET", f"/v1/hello?nonce={nonce}")
    assert status == 426 and res["error"]["code"] == "extension_too_old"


def _raw(port, data, read_timeout=15, half_close=False):
    """Sends ``data`` as is and returns what comes back before the engine closes the connection.
    ``half_close`` ends the sending side, for data that isn't a whole request (on Windows the caller
    check may then no longer find the connection and refuse it 403, which is a clean answer too)."""
    import socket
    s = socket.create_connection(("127.0.0.1", port), timeout=read_timeout)
    try:
        s.sendall(data)
        if half_close:
            s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            try:
                got = s.recv(65536)
            except ConnectionResetError:  # closed with bytes it didn't read: what it answered is above
                got = b""
            if not got:
                return b"".join(chunks)
            chunks.append(got)
    finally:
        s.close()


def _status(reply):
    line = reply.split(b"\r\n", 1)[0].split()
    if len(line) > 1 and line[1].isdigit():
        return int(line[1])
    # Some Python releases take a malformed request line as HTTP/0.9 and send only the error page.
    if reply.startswith(b"<!DOCTYPE HTML") and b"Error code: 400" in reply:
        return 400
    return None


def test_malformed_requests_are_refused_cleanly_and_the_channel_keeps_serving(paired):
    import random
    svc, window, path = paired
    window.pair(svc)
    port, host = window.port, f"127.0.0.1:{window.port}".encode()
    auth = f"Authorization: Bearer {window.token}\r\n".encode()

    def post(body, extra=b"", length=None):
        n = len(body) if length is None else length
        return (b"POST /v1/run HTTP/1.1\r\nHost: " + host + b"\r\n" + auth + extra
                + b"Content-Type: application/json\r\nContent-Length: " + str(n).encode() + b"\r\n\r\n" + body)

    cases = {
        "garbage request line": (b"\x00\xff\xfe nonsense HTTP/1.1\r\n\r\n", {501}),
        "unknown method": (b"PUT /v1/run HTTP/1.1\r\nHost: " + host + b"\r\n\r\n", {501}),
        "header line too long": (b"GET /v1/status HTTP/1.1\r\nHost: " + host + b"\r\nX: " + b"a" * 70000 + b"\r\n\r\n", {431, 400}),
        "too many headers": (b"GET /v1/status HTTP/1.1\r\nHost: " + host + b"\r\n"
                             + b"".join(b"X-%d: y\r\n" % i for i in range(150)) + b"\r\n", {431}),
        "request line too long": (b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n", {414}),
        "body over the limit": (post(b"{}", length=MAX_BODY + 1), {413}),
        "body not json": (post(b"{not json"), {400}),
        "body not utf-8": (post(b'{"path": "\xff\xfe"}'), {400}),
        "body deeply nested": (post(b"[" * 200_000 + b"]" * 200_000), {400}),
        "body a list": (post(b"[1, 2]"), {400}),
        "body a number": (post(b"7"), {400}),
        "path not text": (post(json.dumps({"path": 7, "code": "x"}).encode()), {400}),
        "code too long": (post(json.dumps({"path": path, "code": None}).encode()), {400}),
        "negative length": (post(b"{}", length=-5), {400}),
        "length not a number": (post(b"{}", extra=b"", length="ten"), {400}),
        "chunked body": (post(b"{}", extra=b"Transfer-Encoding: chunked\r\n"), {411}),
        "poll wait not a number": (b"GET /v1/run?path=" + _q(path).encode() + b"&wait=nan&version=1e999 HTTP/1.1\r\nHost: "
                                   + host + b"\r\n" + auth + b"\r\n", {200}),
        "query with control characters": (b"GET /v1/variables?path=%00%0d%0a HTTP/1.1\r\nHost: " + host + b"\r\n" + auth + b"\r\n", {400, 403, 404}),
    }
    # Two words is an HTTP/0.9 request to http.server: refused 400, though some Python releases answer
    # it as HTTP/0.9 does, with the error page and no status line.
    reply = _raw(port, b"\x00\xff\xfe nonsense\r\n\r\n")
    assert _status(reply) == 400, reply[:200]
    for name, (data, want) in cases.items():
        started = time.monotonic()
        reply = _raw(port, data)
        assert _status(reply) in want, (name, reply[:200])
        assert time.monotonic() - started < 10, name
        if b"application/json" in reply:
            json.loads(reply.split(b"\r\n\r\n", 1)[1])  # a refusal the extension can read
    # Truncated: a body shorter than its Content-Length is closed after the idle timeout, not held.
    started = time.monotonic()
    reply = _raw(port, post(b'{"path": "x"', length=500), half_close=True)
    assert time.monotonic() - started < 10 and (reply == b"" or _status(reply) in {400, 403, 408, 500}), reply[:200]
    # Random bytes after a valid start: never a crash, always an answer or a close.
    rng = random.Random(7)
    for _ in range(40):
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 400)))
        reply = _raw(port, rng.choice([b"", b"GET /v1/status HTTP/1.1\r\nHost: " + host + b"\r\n", post(b"")]) + junk,
                     half_close=True)
        assert reply == b"" or _status(reply) is not None, reply[:200]
    # Two requests in one connection: the second is never run, because each connection serves one.
    first = b"GET /v1/status HTTP/1.1\r\nHost: " + host + b"\r\n" + auth + b"\r\n"
    reply = _raw(port, first + post(json.dumps({"path": path, "code": "pipelined = 1"}).encode()))
    assert reply.count(b"HTTP/1.") == 1 and _status(reply) == 200
    # And it still serves a well-formed window.
    assert window.call("GET", "/v1/status")[0] == 200


def test_numbers_in_a_query_that_arent_finite_fall_back_to_the_default():
    from fabric_desktop.editor_channel import _float, _int
    for text in ("nan", "inf", "-inf", "1e999", "x", None):
        assert _float(text, 0.5) == 0.5, text
    assert _float("2.5", 0) == 2.5
    assert _int("9" * 5000, -1) == -1 and _int("4", -1) == 4
