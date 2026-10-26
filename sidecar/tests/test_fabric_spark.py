"""Fabric Spark sessions against a fake of Fabric's Livy API: nothing reaches a tenant. Sessions
need an authorization for that lakehouse and the organization's allow_fabric_runs; statements are
sent once; closing ends the session in Fabric."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import app_settings
from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import fabric_spark as fs
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import CAPABILITIES, check_cell
from fabric_desktop.engines.contract import CellRequest, SessionClosed, SessionConfig, Unsupported
from fabric_desktop.fabric_api import FabricError

WS = "11111111-2222-3333-4444-555555555555"
LH = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TOKEN = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".c2lnbmF0dXJlLXNpZ25hdHVyZQ"
BASE = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/lakehouses/{LH}/livyapi/versions/2023-12-01/sessions"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()

    def json(self):
        return self._body


class FakeLivy:
    def __init__(self, start_polls=1, polls=0, start_state="idle", refuse=None):
        self.calls = []
        self.start_polls = start_polls
        self.polls = polls
        self.start_state = start_state
        self.refuse = refuse
        self.statements = {}
        self.cancelled = []
        self.deleted = []
        self.on_poll = None

    def _result(self, n):
        code, kind = self.statements[n]["code"], self.statements[n]["kind"]
        if n in self.cancelled:
            return {"id": n, "state": "cancelled"}
        if "boom" in code:
            out = {"status": "error", "ename": "NameError", "evalue": "name 'boom' is not defined",
                   "traceback": ["Traceback..."]}
        elif kind == "sql":
            out = {"status": "ok", "data": {"application/json": {
                "schema": {"fields": [{"name": "id", "type": "long"}, {"name": "name", "type": "string"}]},
                "data": [[1, "a"], [2, None], [3, "c"]]}}}
        else:
            out = {"status": "ok", "data": {"text/plain": "hello"}}
        return {"id": n, "state": "available", "output": out}

    def request(self, method, url, headers=None, timeout=None, json=None):
        assert url.startswith(BASE) and timeout and headers == {"Authorization": f"Bearer {TOKEN}"}
        path = url[len(BASE):]
        self.calls.append((method, path, json))
        if self.refuse:
            return Resp(self.refuse, {"errorCode": "Forbidden", "message": "no access " + TOKEN})
        if method == "POST" and path == "":
            return Resp(200, {"id": "7", "state": "starting" if self.start_polls else self.start_state})
        if method == "GET" and path == "/7":
            self.start_polls -= 1
            return Resp(200, {"id": "7", "state": self.start_state if self.start_polls <= 0 else "starting"})
        if method == "POST" and path == "/7/statements":
            n = str(len(self.statements))
            self.statements[n] = {"code": json["code"], "kind": json["kind"], "left": self.polls}
            return Resp(200, {"id": n, "state": "waiting"} if self.polls else self._result(n))
        if method == "POST" and path.endswith("/cancel"):
            self.cancelled.append(path.split("/")[-2])
            return Resp(200, {"msg": "canceled"})
        if method == "GET" and path.startswith("/7/statements/"):
            if self.on_poll:
                self.on_poll()
            n = path.split("/")[-1]
            self.statements[n]["left"] -= 1
            if self.statements[n]["left"] > 0 and n not in self.cancelled:
                return Resp(200, {"id": n, "state": "running"})
            return Resp(200, self._result(n))
        if method == "DELETE" and path == "/7":
            self.deleted.append("7")
            return Resp(200, {})
        return Resp(404, {"message": "no such path"})


@pytest.fixture(autouse=True)
def fabric_runs_allowed(monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: True if key == "allow_fabric_runs" else None)


def adapter(livy):
    return fs.FabricSparkAdapter(client_for=lambda ws, lh: fs.LivyClient(lambda: TOKEN, ws, lh, livy),
                                 sleep=lambda s: None)


def ok():
    return auth.confirmed("fabric-spark", f"{WS}/{LH}", "chris")


def open_session(livy, **options):
    return adapter(livy).open_session(SessionConfig("Sales", options={"workspace_id": WS, "lakehouse_id": LH,
                                                                     **options}), ok())


def test_registered_as_preview_remote_on_fabric_capacity():
    d = registry.get("fabric-spark")
    assert d.adapter and d.remote and d.preview and d.billing == "fabric_capacity"
    assert set(d.as_dict()["capabilities"]) == set(CAPABILITIES)
    assert check_cell(d, "pyspark")["level"] == "native" and check_cell(d, "sql", "spark")["ok"]
    assert not check_cell(d, "sql", "warehouse")["ok"]
    assert any("including while idle" in x for x in d.limitations)


def test_nothing_starts_without_an_authorization_for_that_lakehouse():
    livy = FakeLivy()
    a = adapter(livy)
    cfg = SessionConfig("nb", options={"workspace_id": WS, "lakehouse_id": LH})
    with pytest.raises(auth.RemoteNotAuthorized):
        a.open_session(cfg)
    with pytest.raises(auth.RemoteNotAuthorized):
        a.open_session(cfg, auth.confirmed("fabric-spark", f"{WS}/other", "chris"))
    assert livy.calls == []


def test_the_organization_can_forbid_fabric_runs(monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: False if key == "allow_fabric_runs" else None)
    livy = FakeLivy()
    with pytest.raises(FabricError, match="doesn't allow interactive Spark sessions"):
        open_session(livy)
    assert livy.calls == []


@pytest.mark.parametrize("ids", [{"workspace_id": "../x", "lakehouse_id": LH}, {"workspace_id": WS},
                                 {"workspace_id": WS, "lakehouse_id": LH + "/../y"}])
def test_bad_or_missing_ids_never_reach_fabric(ids):
    livy = FakeLivy()
    with pytest.raises(Unsupported, match="Pick the workspace and lakehouse"):
        adapter(livy).open_session(SessionConfig("nb", options=ids), ok())
    assert livy.calls == []


def test_without_a_sign_in_it_says_so():
    with pytest.raises(Unsupported, match="Sign in first"):
        fs.FabricSparkAdapter().open_session(SessionConfig("nb", options={"workspace_id": WS, "lakehouse_id": LH}), ok())


def test_opening_creates_a_session_with_its_conf_and_waits_until_idle():
    livy = FakeLivy(start_polls=3)
    s = open_session(livy, conf={"spark.executor.memory": "4g"})
    assert s.id == "7" and livy.calls[0] == ("POST", "", {"name": "KratosForge Sales",
                                                          "conf": {"spark.executor.memory": "4g"}})
    assert [c[:2] for c in livy.calls[1:]] == [("GET", "/7")] * 3


def test_a_session_that_dies_while_starting_says_so():
    with pytest.raises(fs.FabricSparkError, match="couldn't start .*dead"):
        open_session(FakeLivy(start_state="dead"))


def test_a_session_that_never_starts_is_ended():
    livy = FakeLivy(start_polls=10**6)
    now = [0.0]
    client = fs.LivyClient(lambda: TOKEN, WS, LH, livy)
    with pytest.raises(fs.FabricSparkError, match="didn't start within 1 minutes"):
        fs.FabricSparkSession(SessionConfig("nb"), client, sleep=lambda s: now.__setitem__(0, now[0] + 30),
                              clock=lambda: now[0], start_seconds=60)
    assert livy.deleted == ["7"]


def test_cells_run_once_per_statement_in_their_language():
    livy = FakeLivy(polls=2)
    s = open_session(livy)
    seen = []
    r = s.execute(CellRequest("print('hello')", "pyspark"), seen.append)
    assert r.status == "ok" and r.outputs == [{"type": "stream", "text": "hello"}] and seen == r.outputs
    r = s.execute(CellRequest("SELECT * FROM t", "sql", "spark", preview_rows=2))
    assert r.rows == 3 and r.outputs[0]["total_rows"] == 3 and len(r.outputs[0]["rows"]) == 2
    s.execute(CellRequest('println("x")', "scala"))
    assert [st["kind"] for st in livy.statements.values()] == ["pyspark", "sql", "spark"]
    assert sum(1 for m, p, _ in livy.calls if m == "POST" and p == "/7/statements") == 3


def test_errors_come_back_as_the_cells_error_without_the_token():
    r = open_session(FakeLivy()).execute(CellRequest("boom", "python"))
    assert r.status == "error" and r.outputs[0]["ename"] == "NameError"
    with pytest.raises(fs.FabricSparkError, match="HTTP 403") as e:
        open_session(FakeLivy(refuse=403))
    assert TOKEN not in str(e.value) and "Contributor" in str(e.value)


def test_other_languages_and_dialects_are_refused():
    s = open_session(FakeLivy())
    with pytest.raises(Unsupported):
        s.execute(CellRequest("x", "r"))
    with pytest.raises(Unsupported, match="not warehouse"):
        s.execute(CellRequest("SELECT 1", "sql", "warehouse"))


def test_cancel_cancels_the_statement_in_fabric():
    livy = FakeLivy(polls=50)
    s = open_session(livy)
    livy.on_poll = lambda: livy.on_poll and (setattr(livy, "on_poll", None) or s.cancel())
    r = s.execute(CellRequest("spark.range(10**9).count()", "pyspark"))
    assert r.status == "cancelled" and livy.cancelled == ["0"]
    assert s.cancel() is False


def test_a_statement_running_too_long_is_cancelled():
    livy = FakeLivy(polls=10**6)
    now = [0.0]
    client = fs.LivyClient(lambda: TOKEN, WS, LH, livy)
    s = fs.FabricSparkSession(SessionConfig("nb"), client, sleep=lambda x: now.__setitem__(0, now[0] + 60),
                              clock=lambda: now[0], max_seconds=300)
    r = s.execute(CellRequest("spark.range(1).count()", "pyspark"))
    assert r.status == "error" and "after 5 minutes" in r.outputs[0]["evalue"] and livy.cancelled == ["0"]


def test_close_ends_the_session_in_fabric_once():
    livy = FakeLivy()
    s = open_session(livy)
    s.close()
    s.close()
    assert livy.deleted == ["7"]
    with pytest.raises(SessionClosed):
        s.execute(CellRequest("1", "python"))
