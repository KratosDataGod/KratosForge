"""Execution engines behind one contract: honest capabilities, isolated sessions, no remote compute
without an authorization, usage that never mixes units (engines/, Phase 1)."""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
import time

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import CAPABILITIES, EngineDescriptor, Support, check_cell
from fabric_desktop.engines.contract import CellRequest, CellResult, SessionClosed, SessionConfig, Unsupported
from fabric_desktop.engines.local_python import PythonSession, _new_outputs
from fabric_desktop.engines.sessions import SessionManager, SwitchNeeded
from fabric_desktop.engines.usage import MemorySink, UsageRecord, totals

T = "unit_tests"
SQL_ENGINES = ["local-duckdb", "local-sqlite", "local-polars"]


def sql(code, dialect=None, rows=1000):
    return CellRequest(code, "sql", dialect, rows)


# ---------------------------------------------------------------- capability rules


def test_a_claim_without_evidence_is_refused():
    with pytest.raises(ValueError, match="needs evidence"):
        Support("native")
    assert not Support().available


def test_an_engine_without_an_adapter_claims_nothing():
    with pytest.raises(ValueError, match="no adapter"):
        EngineDescriptor("x", "X", "local", "local", "none", adapter=False,
                         capabilities={"execute": Support("native", evidence=T)})


def test_an_adapter_must_execute():
    with pytest.raises(ValueError, match="at least execute"):
        EngineDescriptor("x", "X", "local", "local", "none", adapter=True)


def test_an_unverified_remote_engine_must_be_preview_and_say_how_it_is_billed():
    caps = {"execute": Support("native", evidence=T)}
    with pytest.raises(ValueError, match="must be Preview"):
        EngineDescriptor("x", "X", "remote", "databricks", "databricks_dbu", adapter=True, capabilities=caps)
    with pytest.raises(ValueError, match="billed"):
        EngineDescriptor("x", "X", "remote", "databricks", "none", adapter=True, capabilities=caps, preview=True)
    EngineDescriptor("x", "X", "remote", "databricks", "databricks_dbu", adapter=True, capabilities=caps, preview=True)


def test_unknown_capabilities_and_levels_are_refused():
    with pytest.raises(ValueError, match="unknown capabilities"):
        EngineDescriptor("x", "X", "local", "local", "none", adapter=True,
                         capabilities={"execute": Support("native", evidence=T), "teleport": Support()})
    with pytest.raises(ValueError, match="Unknown support level"):
        Support("mostly")


def test_every_registered_engine_lists_every_capability():
    cat = registry.catalog()
    assert cat["version"] == registry.REGISTRY_VERSION
    ids = [e["id"] for e in cat["engines"]]
    assert ids == ["local-python", "local-duckdb", "local-sqlite", "local-polars", "local-spark",
                   "fabric-spark", "databricks-connect", "databricks-sql"]
    for e in cat["engines"]:
        assert set(e["capabilities"]) == set(CAPABILITIES)


def test_planned_engines_claim_nothing_and_say_what_is_planned():
    for eid in ("local-spark", "fabric-spark", "databricks-connect", "databricks-sql"):
        d = registry.get(eid)
        assert not d.adapter and d.planned
        assert all(not d.capability(c).available for c in CAPABILITIES)
        with pytest.raises(ValueError, match="can't run cells"):
            registry.adapter(eid)
        assert check_cell(d, "python")["ok"] is False
    dbc = registry.get("databricks-connect")
    assert dbc.remote and dbc.billing == "databricks_dbu" and "own Python environment" in dbc.planned
    assert registry.get("fabric-spark").billing == "fabric_capacity"


def test_pyspark_on_this_pc_is_simulated_and_spark_sql_on_duckdb_is_translated():
    py = registry.get("local-python")
    assert py.language("pyspark").level == "simulated"
    v = check_cell(py, "pyspark")
    assert v["ok"] and v["level"] == "simulated" and "DuckDB" in v["reason"]
    v = check_cell(registry.get("local-duckdb"), "sql", "spark")
    assert v["ok"] and v["level"] == "translated"
    v = check_cell(registry.get("local-duckdb"), "sql", "warehouse")
    assert not v["ok"] and "doesn't run warehouse" in v["reason"]
    assert not check_cell(py, "scala")["ok"]


def test_local_engines_bill_nothing_but_say_onelake_still_uses_capacity():
    for eid in ("local-python", *SQL_ENGINES):
        d = registry.get(eid)
        assert d.billing == "none" and not d.remote
        assert "still uses Fabric capacity" in d.capability("cost").note


def test_local_spark_status_only_reports():
    s = registry.local_spark_status()
    assert set(s) == {"pyspark", "java", "ready", "detail"}


# ---------------------------------------------------------------- SQL engines (shared conformance)


@pytest.fixture(params=SQL_ENGINES)
def session(request):
    s = registry.adapter(request.param).open_session(SessionConfig("nb"))
    yield s
    s.close()


def _make_table(s):
    if s.engine == "local-polars":
        import polars as pl
        s._ctx.register("t", pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", None]}))
        return
    if s.engine == "local-sqlite":
        r = s.execute(sql("CREATE TABLE t (id INTEGER, name TEXT); INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, NULL);"))
    else:
        r = s.execute(sql("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, NULL)) AS v(id, name)"))
    assert r.status == "ok", r.outputs


def test_sql_engines_run_a_query_and_show_a_table_with_the_exact_total(session):
    _make_table(session)
    seen = []
    r = session.execute(sql("SELECT id, name FROM t ORDER BY id", rows=2), seen.append)
    assert r.status == "ok" and r.rows == 3 and r.engine == session.engine
    out = r.outputs[0]
    assert out["type"] == "table" and out["columns"] == ["id", "name"]
    assert out["rows"] == [[1, "a"], [2, "b"]] and out["total_rows"] == 3
    assert seen == r.outputs and r.seconds >= 0


def test_sql_engines_report_errors_on_the_cell(session):
    r = session.execute(sql("SELECT * FROM no_such_table"))
    assert r.status == "error" and r.outputs[0]["type"] == "error" and r.outputs[0]["evalue"]


def test_sql_engines_show_a_plan_and_their_catalog(session):
    _make_table(session)
    assert session.plan("SELECT * FROM t WHERE id > 1").strip()
    assert any(t["name"] == "t" for t in session.catalog())


def test_sql_engines_have_no_variables_and_refuse_python(session):
    with pytest.raises(Unsupported):
        session.variables()
    with pytest.raises(Unsupported):
        session.execute(CellRequest("print(1)", "python"))


def test_closing_a_sql_session_frees_it_and_a_new_session_starts_empty(session):
    _make_table(session)
    session.close()
    assert session.closed
    with pytest.raises(SessionClosed):
        session.execute(sql("SELECT 1"))
    session.close()  # twice is fine
    fresh = registry.adapter(session.engine).open_session(SessionConfig("nb"))
    try:
        assert not any(t["name"] == "t" for t in fresh.catalog())
    finally:
        fresh.close()


def test_two_sessions_of_one_engine_share_nothing():
    a = registry.adapter("local-duckdb").open_session(SessionConfig("a"))
    b = registry.adapter("local-duckdb").open_session(SessionConfig("b"))
    try:
        a.execute(sql("CREATE TABLE only_a AS SELECT 1 AS x"))
        assert b.execute(sql("SELECT * FROM only_a")).status == "error"
    finally:
        a.close(), b.close()


def test_duckdb_runs_spark_sql_translated():
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    try:
        r = s.execute(sql("SELECT date_format(DATE '2026-10-20', 'yyyy-MM') AS m", "spark"))
        assert r.status == "ok" and r.outputs[0]["rows"] == [["2026-10"]]
        with pytest.raises(Unsupported):
            s._sql("SELECT 1", "warehouse")
        assert "warehouse" in s.execute(sql("SELECT 1", "warehouse")).outputs[0]["evalue"]
    finally:
        s.close()


@pytest.mark.parametrize("engine", ["local-duckdb", "local-sqlite"])
def test_cancel_stops_a_long_statement(engine):
    s = registry.adapter(engine).open_session(SessionConfig("nb"))
    long = ("SELECT count(*) FROM range(10000000000) a" if engine == "local-duckdb" else
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c")
    result: list[CellResult] = []
    t = threading.Thread(target=lambda: result.append(s.execute(sql(long))))
    t.start()
    deadline = time.monotonic() + 10
    while not s._running and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    assert s.cancel() is True
    t.join(20)
    try:
        assert not t.is_alive() and result[0].status == "cancelled"
        assert s.execute(sql("SELECT 1 AS one")).status == "ok"  # the session still works
    finally:
        s.close()


def test_polars_cancel_is_unsupported_and_says_so():
    s = registry.adapter("local-polars").open_session(SessionConfig("nb"))
    try:
        assert s.cancel() is False  # nothing running
        s._running = True
        with pytest.raises(Unsupported, match="can't stop"):
            s.cancel()
        s._running = False
    finally:
        s.close()


def test_database_files_open_read_only(tmp_path):
    path = tmp_path / "data.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    s = registry.adapter("local-sqlite").open_session(SessionConfig("nb", database=str(path)))
    try:
        assert s.execute(sql("SELECT x FROM t")).outputs[0]["rows"] == [[1]]
        r = s.execute(sql("INSERT INTO t VALUES (2)"))
        assert r.status == "error" and "readonly" in r.outputs[0]["evalue"].replace("-", "").replace(" ", "").lower()
    finally:
        s.close()
    import duckdb
    dpath = tmp_path / "data.duckdb"
    c = duckdb.connect(str(dpath))
    c.execute("CREATE TABLE t AS SELECT 1 AS x")
    c.close()
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb", database=str(dpath)))
    try:
        assert s.execute(sql("SELECT x FROM t")).status == "ok"
        assert s.execute(sql("INSERT INTO t VALUES (2)")).status == "error"
    finally:
        s.close()
    c = duckdb.connect(str(dpath), read_only=True)
    assert c.execute("SELECT count(*) FROM t").fetchone() == (1,)
    c.close()


def test_sqlite_splits_statements_like_sqlite_does():
    from fabric_desktop.engines.local_sql import _statements
    assert _statements("SELECT 1;\nSELECT ';' AS s;\n\n") == ["SELECT 1;", "SELECT ';' AS s;"]
    assert _statements("SELECT 1") == ["SELECT 1"]
    assert _statements(" ; \n") == []


# ---------------------------------------------------------------- Python engine


class FakeKernel:
    """The ProcessKernel surface the Python session uses, with a stream that grows over two polls."""

    def __init__(self):
        self.pid = 4242
        self.notebook_name = ""
        self.closed = False
        self.interrupted = False
        self.polls = 0

    def start(self, code):
        return {"status": "running", "version": 1, "outputs": [{"type": "stream", "text": "a"}]}

    def poll(self, version, wait):
        self.polls += 1
        if self.polls == 1:
            return {"status": "running", "version": 2, "outputs": [{"type": "stream", "text": "ab"}]}
        return {"status": "ok", "version": 3,
                "outputs": [{"type": "stream", "text": "ab"}, {"type": "text", "text": "42"}]}

    def interrupt(self):
        self.interrupted = True
        return {"interrupted": True}

    def variables(self):
        return [{"name": "x"}]

    def close(self):
        self.closed = True


def test_python_session_streams_only_what_is_new():
    k = FakeKernel()
    s = PythonSession(SessionConfig("nb"), k)
    seen = []
    r = s.execute(CellRequest("print('ab'); 42"), seen.append)
    assert seen == [{"type": "stream", "text": "a"}, {"type": "stream", "text": "b"}, {"type": "text", "text": "42"}]
    assert r.status == "ok" and r.outputs[-1] == {"type": "text", "text": "42"} and k.notebook_name == "nb"
    assert s.variables() == [{"name": "x"}] and s.cancel() is True and k.interrupted
    with pytest.raises(Unsupported):
        s.plan("x")
    with pytest.raises(Unsupported):
        s.execute(CellRequest("SELECT 1", "sql"))
    s.close()
    assert k.closed and s.cancel() is False
    with pytest.raises(SessionClosed):
        s.execute(CellRequest("1"))


def test_new_outputs_handles_replaced_outputs():
    assert _new_outputs([{"type": "text", "text": "1"}], [{"type": "table", "rows": []}]) == [{"type": "table", "rows": []}]


def test_python_sessions_are_separate_processes_and_close_ends_them():
    a = registry.adapter("local-python").open_session(SessionConfig("a"))
    b = registry.adapter("local-python").open_session(SessionConfig("b"))
    try:
        assert a.execute(CellRequest("x = 1\nimport os\nprint(os.getpid())")).status == "ok"
        r = b.execute(CellRequest("print('x' in globals())"))
        assert r.outputs[0]["text"].strip() == "False"
        assert a.pid != b.pid
        assert any(v["name"] == "x" for v in a.variables())
    finally:
        a.close(), b.close()
    assert a.closed and b.closed


# ---------------------------------------------------------------- sessions: switching and authorization


class FakeRemote:
    """A remote engine for the rules only; nothing is contacted."""

    desc = EngineDescriptor("fake-remote", "Fake remote", "remote", "databricks", "databricks_dbu", adapter=True,
                            languages={"sql": Support("native", evidence=T)},
                            capabilities={"execute": Support("native", evidence=T)}, preview=True)

    def __init__(self):
        self.opened = 0

    def describe(self):
        return self.desc

    def open_session(self, config, authorization=None):
        self.opened += 1
        return registry.adapter("local-duckdb").open_session(config)


@pytest.fixture
def manager():
    remote = FakeRemote()
    sink = MemorySink()
    m = SessionManager(lambda eid: remote if eid == "fake-remote" else registry.adapter(eid), sink.write)
    m.remote, m.sink = remote, sink
    yield m
    m.close_all()


def test_switching_engines_needs_a_decision_and_never_carries_state(manager):
    s1 = manager.switch("nb", "local-duckdb")
    manager.execute("nb", sql("CREATE TABLE t AS SELECT 1 AS x"))
    with pytest.raises(SwitchNeeded) as e:
        manager.switch("nb", "local-sqlite")
    t = e.value.transition
    assert t["needed"] and t["keeps"] == ["cells", "outputs"] and t["decisions"] == ["close", "keep"]
    assert manager.engine_of("nb") == "local-duckdb" and not s1.closed
    s2 = manager.switch("nb", "local-sqlite", decision="close")
    assert s1.closed and manager.engine_of("nb") == "local-sqlite"
    assert manager.execute("nb", sql("SELECT * FROM t")).status == "error"
    assert manager.switch("nb", "local-sqlite") is s2  # same engine: same session, nothing asked


def test_kept_sessions_come_back_and_close_with_the_notebook(manager):
    s1 = manager.switch("nb", "local-duckdb")
    manager.execute("nb", sql("CREATE TABLE t AS SELECT 7 AS x"))
    manager.switch("nb", "local-sqlite", decision="keep")
    assert not s1.closed and len(manager.open_sessions()) == 2
    assert manager.switch("nb", "local-duckdb", decision="keep") is s1
    assert manager.execute("nb", sql("SELECT x FROM t")).outputs[0]["rows"] == [[7]]
    manager.close("nb")
    assert s1.closed and manager.open_sessions() == []


def test_a_cell_the_engine_cant_run_stops_and_never_moves_elsewhere(manager):
    with pytest.raises(Unsupported, match="Pick an engine"):
        manager.execute("nb", sql("SELECT 1"))
    manager.switch("nb", "local-sqlite")
    with pytest.raises(Unsupported, match="doesn't run python"):
        manager.execute("nb", CellRequest("print(1)", "python"))
    assert manager.engine_of("nb") == "local-sqlite" and manager.remote.opened == 0


def test_remote_engines_need_an_authorization_for_that_engine_and_target(manager):
    with pytest.raises(auth.RemoteNotAuthorized, match="may be billed"):
        manager.switch("nb", "fake-remote", target="wh-1")
    wrong = auth.confirmed("fake-remote", "wh-2", "chris")
    with pytest.raises(auth.RemoteNotAuthorized, match="not fake-remote on wh-1"):
        manager.switch("nb", "fake-remote", target="wh-1", authorization=wrong)
    old = auth.confirmed("fake-remote", "wh-1", "chris", now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
    with pytest.raises(auth.RemoteNotAuthorized, match="expired"):
        manager.switch("nb", "fake-remote", target="wh-1", authorization=old)
    assert manager.remote.opened == 0
    ok = auth.confirmed("fake-remote", "wh-1", "chris")
    manager.switch("nb", "fake-remote", target="wh-1", authorization=ok)
    assert manager.remote.opened == 1
    t = manager.transition("nb", "local-duckdb")
    assert t["decisions"] == ["close"]  # a remote session is never kept running in the background
    with pytest.raises(ValueError, match="isn't possible"):
        manager.switch("nb", "local-duckdb", decision="keep")


def test_policy_authorizes_only_listed_engines_and_never_forbids_asking_by_default():
    policy = {"remote_compute": "policy", "allowed_remote_engines": ["databricks-sql"]}
    a = auth.from_policy(policy, "Sales", "databricks-sql", "wh-1")
    assert a and a.reason == "policy" and a.by == "policy:Sales" and a.as_dict()["target"] == "wh-1"
    assert auth.from_policy(policy, "Sales", "databricks-connect", "c-1") is None
    assert auth.from_policy({}, "Sales", "databricks-sql", "wh-1") is None
    with pytest.raises(auth.RemoteNotAuthorized, match="doesn't allow"):
        auth.from_policy({"remote_compute": "never"}, "Sales", "databricks-sql", "wh-1")
    with pytest.raises(ValueError):
        auth.from_policy({"remote_compute": "sometimes"}, "Sales", "databricks-sql", "wh-1")
    with pytest.raises(ValueError):
        auth.confirmed("databricks-sql", "", "chris")
    assert auth.require(registry.get("local-duckdb"), "", None) is None


def test_runs_are_recorded_as_measured_seconds(manager):
    manager.switch("nb", "local-duckdb")
    manager.execute("nb", sql("SELECT 1"))
    [rec] = manager.sink.records
    assert rec.kind == "measured" and rec.unit == "seconds" and rec.engine == "local-duckdb"


# ---------------------------------------------------------------- usage


def test_usage_never_mixes_units_or_kinds():
    a = UsageRecord("local-duckdb", "seconds", 2.0, "measured", "measure.py")
    b = UsageRecord("local-polars", "seconds", 3.0, "measured", "measure.py")
    assert a.add(b).amount == 5.0 and a.add(b).engine == "*"
    dbu = UsageRecord("databricks-sql", "databricks_dbu", 0.4, "billed", "system.billing.usage")
    cu = UsageRecord("fabric-spark", "fabric_cu_seconds", 120, "billed", "Capacity Metrics")
    with pytest.raises(ValueError, match="never combined"):
        dbu.add(cu)
    est = UsageRecord("databricks-sql", "databricks_dbu", 0.5, "estimated", assumptions=("2X-Small, 3 minutes",))
    with pytest.raises(ValueError, match="never combined"):
        dbu.add(est)
    rows = totals([a, b, dbu, cu, est])
    assert {(r["kind"], r["unit"]): r["amount"] for r in rows} == {
        ("billed", "databricks_dbu"): 0.4, ("billed", "fabric_cu_seconds"): 120,
        ("estimated", "databricks_dbu"): 0.5, ("measured", "seconds"): 5.0}


def test_estimates_say_what_they_assume_and_bills_say_where_they_came_from():
    with pytest.raises(ValueError, match="assumes"):
        UsageRecord("x", "databricks_dbu", 1, "estimated")
    with pytest.raises(ValueError, match="where the bill"):
        UsageRecord("x", "databricks_dbu", 1, "billed")
    with pytest.raises(ValueError, match="Unknown unit"):
        UsageRecord("x", "dollars", 1, "measured")
    with pytest.raises(ValueError, match="negative"):
        UsageRecord("x", "seconds", -1, "measured")


# ---------------------------------------------------------------- nothing outside the session


@pytest.mark.parametrize("statement", ["COPY (SELECT 1) TO '{d}/out.csv'", "ATTACH '{d}/other.duckdb'",
                                       "INSTALL httpfs", "EXPORT DATABASE '{d}/dump'"])
def test_duckdb_sessions_refuse_statements_that_reach_outside(tmp_path, statement):
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    try:
        r = s.execute(sql(statement.format(d=tmp_path.as_posix())))
        assert r.status == "error", r.outputs
        assert r.outputs[0]["ename"] in ("OutsideSession", "PermissionException"), r.outputs
        assert list(tmp_path.iterdir()) == []
        assert s.execute(sql("SELECT 1 AS still_works")).status == "ok"
    finally:
        s.close()


def test_duckdb_refusal_names_the_statement_and_plan_refuses_too(tmp_path):
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    try:
        r = s.execute(sql(f"CREATE TABLE t AS SELECT 1 AS x; COPY t TO '{tmp_path.as_posix()}/o.csv'"))
        assert r.outputs[0]["ename"] == "OutsideSession" and "COPY" in r.outputs[0]["evalue"]
        assert "statement 2" in r.outputs[0]["evalue"]
        assert not any(t["name"] == "t" for t in s.catalog())  # refused before anything ran
        from fabric_desktop.engines.local_sql import OutsideSession
        with pytest.raises(OutsideSession):
            s.plan("INSTALL httpfs")
    finally:
        s.close()


def test_duckdb_sessions_cant_read_local_files_even_when_the_classifier_allows_it(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x\n1\n")
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    try:
        r = s.execute(sql(f"SELECT * FROM read_csv('{f.as_posix()}')"))
        assert r.status == "error" and "Permission" in r.outputs[0]["evalue"]
        assert s.execute(sql("CREATE TABLE mine AS SELECT 1 AS x; INSERT INTO mine VALUES (2); "
                             "SELECT count(*) AS n FROM mine")).outputs[0]["rows"] == [[2]]
    finally:
        s.close()


def test_sqlite_sessions_refuse_attach(tmp_path):
    s = registry.adapter("local-sqlite").open_session(SessionConfig("nb"))
    try:
        r = s.execute(sql(f"ATTACH DATABASE '{(tmp_path / 'other.db').as_posix()}' AS other"))
        assert r.status == "error" and "not authorized" in r.outputs[0]["evalue"]
        assert list(tmp_path.iterdir()) == []
        assert s.execute(sql("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1); SELECT x FROM t")).status == "ok"
    finally:
        s.close()


# ---------------------------------------------------------------- Python engine on the notebook's sessions


class StoppableKernel(FakeKernel):
    def __init__(self):
        super().__init__()
        self.stopped = 0

    def stop(self):
        self.stopped += 1
        return {"stopped": True}


def _sessions(process=True):
    from fabric_desktop.kernel_sessions import KernelSessions
    made = []

    def make(_in_process):
        made.append(StoppableKernel())
        return made[-1]
    return KernelSessions(make, lambda: process), made


def test_python_engine_uses_the_notebooks_own_kernel_and_stop_on_close():
    sessions, made = _sessions()
    adapter = registry.adapter("local-python", notebook_sessions=sessions)
    assert adapter.describe().id == "local-python"
    s = adapter.open_session(SessionConfig("sales"))
    assert s._kernel is sessions.kernel("sales") is made[0]  # one kernel, not a second process
    assert s.execute(CellRequest("print('ab'); 42")).status == "ok"
    s.close()
    assert made[0].stopped == 1 and s.closed and not made[0].closed


def test_python_engine_on_shared_or_in_engine_kernels_says_what_to_change():
    sessions, _ = _sessions(process=False)
    with pytest.raises(Unsupported, match="own process"):
        registry.adapter("local-python", notebook_sessions=sessions).open_session(SessionConfig("a"))
    sessions, _ = _sessions()
    sessions.share("b", "a")
    with pytest.raises(Unsupported, match="shares a's session"):
        registry.adapter("local-python", notebook_sessions=sessions).open_session(SessionConfig("b"))
    assert registry.adapter("local-python", notebook_sessions=sessions).open_session(SessionConfig("a"))
