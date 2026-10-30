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
        # What a result statement prints (fabric_spark._result_code), as Spark would for three rows.
        self.result_text = json.dumps({
            "mark": fs.RESULT_MARK, "fields": [["id", "bigint"], ["name", "string"], ["amount", "decimal(10,2)"]],
            "rows": [["1", "a", "1.50"], ["2", None, "2.50"], ["3", "c", "3.50"]]})

    def _result(self, n):
        code, kind = self.statements[n]["code"], self.statements[n]["kind"]
        if n in self.cancelled:
            return {"id": n, "state": "cancelled"}
        if "boom" in code:
            out = {"status": "error", "ename": "NameError", "evalue": "name 'boom' is not defined",
                   "traceback": ["Traceback..."]}
        elif kind == "pyspark" and fs.RESULT_MARK in code:
            out = {"status": "ok", "data": {"text/plain": self.result_text}}
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


def test_a_poll_that_fails_while_starting_ends_the_session_it_created():
    livy = FakeLivy(start_polls=3)
    real = livy.request

    def refuse_polls(method, url, **kw):
        if method == "GET":
            return Resp(429, {"message": "too many requests"})
        return real(method, url, **kw)
    livy.request = refuse_polls
    with pytest.raises(fs.FabricSparkError, match="HTTP 429"):
        open_session(livy)
    assert livy.deleted == ["7"]
    # A session that died while starting is ended too (deleting a dead session is harmless).
    dead = FakeLivy(start_state="dead")
    with pytest.raises(fs.FabricSparkError, match="dead"):
        open_session(dead)
    assert dead.deleted == ["7"]


def test_a_session_fabric_doesnt_name_says_so_and_deletes_nothing():
    livy = FakeLivy()
    real = livy.request
    livy.request = lambda method, url, **kw: Resp(200, {"state": "starting"}) if method == "POST" else real(method, url, **kw)
    with pytest.raises(fs.FabricSparkError, match="didn't say which one"):
        open_session(livy)
    assert livy.deleted == []


def test_a_cancel_that_fails_or_comes_too_late_keeps_the_real_result():
    livy = FakeLivy(polls=2)
    s = open_session(livy)
    real = livy.request

    def refuse_cancel(method, url, **kw):
        if url.endswith("/cancel"):
            return Resp(500, {"message": "busy"})
        return real(method, url, **kw)
    livy.request = refuse_cancel
    livy.on_poll = lambda: livy.on_poll and (setattr(livy, "on_poll", None) or seen.append(s.cancel()))
    seen = []
    r = s.execute(CellRequest("print('hello')", "pyspark"))
    assert seen == [False] and r.status == "ok" and r.outputs == [{"type": "stream", "text": "hello"}]


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


# ---------------------------------------------------------------- whole results (the result capability)


def test_a_sql_cell_hands_back_its_whole_result_typed_from_sparks_schema():
    livy = FakeLivy()
    s = open_session(livy)
    r = s.execute(CellRequest("SELECT id, name, amount FROM sales", "sql", preview_rows=2, result_rows=10))
    assert r.status == "ok" and r.rows == 3 and r.table.num_rows == 3
    assert str(r.table.schema.field("id").type) == "int64" and str(r.table.schema.field("amount").type) == "decimal128(10, 2)"
    assert r.table.column("name").to_pylist() == ["a", None, "c"]
    assert r.outputs[0]["total_rows"] == 3 and len(r.outputs[0]["rows"]) == 2
    sent = livy.statements["0"]
    assert sent["kind"] == "pyspark" and "spark.sql('SELECT id, name, amount FROM sales')" in sent["code"]
    assert ".limit(11)" in sent["code"] and "table" not in r.as_dict()


def _run_result_code(code, fields, rows):
    """Run a generated result statement against fakes of spark and pyspark.sql.functions."""
    import sys
    import types
    seen = {"selected": []}
    F = types.ModuleType("pyspark.sql.functions")
    F.col = lambda c: ("col", c)
    # Fresh fakes for all three modules, put back afterwards: a real pyspark (or the PySpark API
    # stand-in other tests load) must never see these.
    sql_mod = types.ModuleType("pyspark.sql")
    sql_mod.functions = F
    fakes = {"pyspark": types.ModuleType("pyspark"), "pyspark.sql": sql_mod, "pyspark.sql.functions": F}

    class Field:
        def __init__(self, name, t):
            self.name, self.dataType = name, type("T", (), {"simpleString": lambda _self, t=t: t})()

    class Df:
        schema = type("S", (), {"fields": [Field(n, t) for n, t in fields]})()
        columns = [n for n, _ in fields]

        def toDF(self, *names):
            seen["renamed"] = list(names)
            return self

        def select(self, cols):
            seen["selected"] = [c.alias_name if hasattr(c, "alias_name") else c for c in cols]
            return self

        def limit(self, n):
            seen["limit"] = n
            return self

        def collect(self):
            return rows

    class Aliased(tuple):
        def alias(self, name):
            out = Aliased(self)
            out.alias_name = (*self, name)
            return out

    F.date_format = lambda c, fmt: Aliased(("date_format", c, fmt))

    class Spark:
        def sql(self, text):
            seen["sql"] = text
            return Df()

    printed = []
    scope = {"spark": Spark(), "print": printed.append}
    saved = {name: sys.modules.get(name) for name in fakes}
    sys.modules.update(fakes)
    try:
        exec(code, scope)   # noqa: S102 - the generated statement, on fakes
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return seen, json.loads(printed[0]), scope


def test_the_query_goes_in_as_a_string_literal_only():
    query = "SELECT '\\'); import os; os.system(\"x\") #' AS q"
    seen, printed, scope = _run_result_code(fs._result_code(query, 5), [], [])
    assert seen["sql"] == query and seen["limit"] == 6
    assert printed == {"mark": fs.RESULT_MARK, "fields": [], "rows": []}


def test_timestamps_are_formatted_by_spark_and_the_session_namespace_is_left_clean():
    fields = [["id", "bigint"], ["at", "timestamp"], ["id", "string"]]
    seen, printed, scope = _run_result_code(fs._result_code("SELECT 1", 5), fields, [[1, "2024-01-01 10:00:00.000000", "x"]])
    assert seen["renamed"] == ["c0", "c1", "c2"]   # duplicate names can't clash
    assert seen["selected"] == [("col", "c0"), ("date_format", "c1", "yyyy-MM-dd HH:mm:ss.SSSSSS", "c1"), ("col", "c2")]
    assert printed["fields"] == fields and printed["rows"] == [["1", "2024-01-01 10:00:00.000000", "x"]]
    assert not [k for k in scope if k.startswith("_kf_")]


def test_columns_kept_as_text_are_named():
    livy = FakeLivy()
    livy.result_text = json.dumps({"mark": fs.RESULT_MARK, "fields": [["d", "date"], ["n", "bigint"]],
                                   "rows": [["not a date", "1"]]})
    r = open_session(livy).execute(CellRequest("SELECT d, n FROM t", "sql", result_rows=10))
    assert r.status == "ok" and r.outputs[0] == {"type": "stream", "text": "Kept as text: d (date).\n"}


def test_more_rows_than_asked_for_is_an_error():
    livy = FakeLivy()
    r = open_session(livy).execute(CellRequest("SELECT * FROM sales", "sql", result_rows=2))
    assert r.status == "error" and r.table is None and "more than the 2 rows" in r.outputs[0]["evalue"]


def test_a_result_over_the_byte_cap_is_an_error(monkeypatch):
    monkeypatch.setattr(fs, "RESULT_BYTES", 32)
    r = open_session(FakeLivy()).execute(CellRequest("SELECT * FROM sales", "sql", result_rows=10))
    assert r.status == "error" and r.table is None and "larger than" in r.outputs[0]["evalue"]


def test_an_unexpected_reply_is_an_error_never_a_guess():
    livy = FakeLivy()
    livy.result_text = "something else " + fs.RESULT_MARK
    r = open_session(livy).execute(CellRequest("SELECT 1", "sql", result_rows=10))
    assert r.status == "error" and "expected form" in r.outputs[0]["evalue"]


def test_whole_results_are_for_sql_cells_only():
    livy = FakeLivy()
    s = open_session(livy)
    with pytest.raises(Unsupported, match="SQL cells only"):
        s.execute(CellRequest("df.show()", "pyspark", result_rows=10))
    assert livy.statements == {}


def test_a_value_that_doesnt_read_as_its_type_stays_text():
    livy = FakeLivy()
    livy.result_text = json.dumps({"mark": fs.RESULT_MARK, "fields": [["d", "date"], ["tags", "array<string>"]],
                                   "rows": [["not a date", "['a', 'b']"]]})
    r = open_session(livy).execute(CellRequest("SELECT d, tags FROM t", "sql", result_rows=10))
    assert r.status == "ok" and r.table.column("d").to_pylist() == ["not a date"]
    assert str(r.table.schema.field("tags").type) == "string"


def test_the_result_note_says_the_cap():
    assert "256 MB" in fs.FABRIC_SPARK.capability("result").note


def test_duplicate_column_names_are_refused_not_dropped():
    livy = FakeLivy()
    livy.result_text = json.dumps({"mark": fs.RESULT_MARK, "fields": [["id", "bigint"], ["id", "bigint"]],
                                   "rows": [["1", "2"]]})
    r = open_session(livy).execute(CellRequest("SELECT a.id, b.id FROM a JOIN b", "sql", result_rows=10))
    assert r.status == "error" and r.table is None and "more than one column named id" in r.outputs[0]["evalue"]


@pytest.mark.parametrize("fields, rows", [
    ([["a"]], [["1"]]),                       # a field that isn't [name, type]
    ([["a", "bigint"], ["b", "bigint"]], [["1"]]),   # a row shorter than the fields
    ([["a", "bigint"]], [[1]]),               # a value that isn't text or null
    ("not a list", []),
])
def test_a_reply_with_the_mark_but_the_wrong_shape_is_an_error(fields, rows):
    livy = FakeLivy()
    livy.result_text = json.dumps({"mark": fs.RESULT_MARK, "fields": fields, "rows": rows})
    r = open_session(livy).execute(CellRequest("SELECT 1", "sql", result_rows=10))
    assert r.status == "error" and "expected form" in r.outputs[0]["evalue"]
