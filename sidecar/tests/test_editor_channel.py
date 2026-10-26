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
