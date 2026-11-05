"""Every engine adapter meets the engine contract and its own descriptor (KUDP-01).

engine_conformance.py holds the checks; this file gives each registered adapter a harness and runs
every check against it. Remote engines run against the fakes their own tests use, so nothing here
reaches a workspace. A new adapter fails ``test_every_adapter_has_a_harness`` until it adds one.
"""

from __future__ import annotations

import sys

import pytest

import test_databricks_sql as dbsql_t
import test_dbconnect as dbc_t
import test_fabric_spark as fs_t
from engine_conformance import CHECKS, Harness
from fabric_desktop import app_settings, connprofiles
from fabric_desktop.engines import dbconnect as dbc
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import EngineDescriptor, Support
from fabric_desktop.engines.contract import CellRequest, CellResult, SessionClosed, SessionConfig, Unsupported


def sql(code, dialect=None):
    return CellRequest(code, "sql", dialect)


def _sql_engine(engine: str, make: str) -> Harness:
    def tables(s):
        return {t["name"] for t in s.catalog()}

    def leave(s):
        if engine == "local-polars":
            import polars as pl
            s._ctx.register("kudp_probe", pl.DataFrame({"id": [1]}))
        else:
            assert s.execute(sql(make)).status == "ok"

    return Harness(
        engine, lambda: registry.adapter(engine), lambda a: a.open_session(SessionConfig("nb")),
        ok_cell=sql("SELECT 1 AS id, 'a' AS name"), error_cell=sql("SELECT * FROM no_such_table"),
        refused_cells=(CellRequest("print(1)", "python"), sql("SELECT 1", "tsql")),
        result_cell=sql("SELECT 1 AS id UNION ALL SELECT 2 UNION ALL SELECT 3"),
        leave_state=leave, sees_state=lambda s: "kudp_probe" in tables(s))


def duckdb(tmp_path, monkeypatch):
    return _sql_engine("local-duckdb", "CREATE TABLE kudp_probe AS SELECT 1 AS id")


def sqlite(tmp_path, monkeypatch):
    return _sql_engine("local-sqlite", "CREATE TABLE kudp_probe (id INTEGER)")


def polars(tmp_path, monkeypatch):
    return _sql_engine("local-polars", "")


def python(tmp_path, monkeypatch):
    def leave(s):
        assert s.execute(CellRequest("kudp_probe = 1")).status == "ok"

    return Harness(
        "local-python", lambda: registry.adapter("local-python"), lambda a: a.open_session(SessionConfig("nb")),
        ok_cell=CellRequest("x = 41\nprint('hi')\nx + 1"), error_cell=CellRequest("1/0"),
        refused_cells=(sql("SELECT 1"),),
        leave_state=leave, sees_state=lambda s: any(v["name"] == "kudp_probe" for v in s.variables()))


# An access token as Entra hands one out (header, claims, signature), built up so no scanner takes it for real.
_JWT = "eyJ" + "hbGciOiJSUzI1NiJ9." + "eyJhdWQiOiIyZmY4MTRhNiIsImV4cCI6MX0." + "c2lnbmF0dXJl" * 3


def databricks_sql(tmp_path, monkeypatch):
    ws = dbsql_t.FakeWorkspace()
    failing = dbsql_t.FakeWorkspace(fail="[TABLE_OR_VIEW_NOT_FOUND] no_such_table; " + dbsql_t.PAT + " " + _JWT)

    def opener(workspace):
        return lambda _a: dbsql_t.open_session(workspace)

    return Harness(
        "databricks-sql", lambda: dbsql_t.adapter(ws), opener(ws),
        ok_cell=sql("SELECT id, name FROM main.sales.orders"), error_cell=sql("SELECT * FROM no_such_table"),
        refused_cells=(CellRequest("print(1)", "python"), sql("SELECT 1", "tsql")),
        open_failing=opener(failing),
        open_unauthorized=lambda a: a.open_session(SessionConfig("nb", options={"connection": dbsql_t.CONN})),
        contacted=lambda: len(ws.calls), secrets=(dbsql_t.PAT, _JWT))


def databricks_connect(tmp_path, monkeypatch):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[dev]\nhost = {dbc_t.HOST}\nauth_type = databricks-cli\n")
    monkeypatch.setattr(connprofiles, "databricks_config_path", lambda: cfg)
    monkeypatch.setattr(dbc, "find_cli", lambda environ=None: "/opt/databricks/databricks")
    monkeypatch.setattr(dbc, "RUNTIME_PYTHON", {16: "%d.%d" % sys.version_info[:2]})
    runtimes = _runtimes(tmp_path)
    looked_up = []

    def cluster_of(conn, cid):
        looked_up.append(cid)
        return {"spark_version": "16.4.x-scala2.12", "state": "RUNNING"}

    def adapter():
        return dbc.DatabricksConnectAdapter(runtimes, cluster_of=cluster_of, cell_seconds=60)

    config = SessionConfig("nb", options={"connection": dbc_t.CONN})
    return Harness(
        "databricks-connect", adapter, lambda a: a.open_session(config, dbc_t.ok()),
        ok_cell=CellRequest("print('hi')\nspark.range(3)", "pyspark"), error_cell=CellRequest("1/0", "python"),
        refused_cells=(sql("SELECT 1"),),
        open_unauthorized=lambda a: a.open_session(config), contacted=lambda: len(looked_up))


def fabric_spark(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: True if key == "allow_fabric_runs" else None)
    livy = fs_t.FakeLivy()
    failing = fs_t.FakeLivy()
    failing.error_text = "[TABLE_OR_VIEW_NOT_FOUND] no_such_table; Authorization: Bearer " + fs_t.TOKEN + " " + _JWT
    config = SessionConfig("nb", options={"workspace_id": fs_t.WS, "lakehouse_id": fs_t.LH})
    return Harness(
        "fabric-spark", lambda: fs_t.adapter(livy), lambda a: a.open_session(config, fs_t.ok()),
        ok_cell=CellRequest("print('hi')", "pyspark"), error_cell=CellRequest("boom", "python"),
        refused_cells=(CellRequest("x", "r"), sql("SELECT 1", "tsql")),
        result_cell=sql("SELECT id, name, amount FROM sales"),
        open_failing=lambda _a: fs_t.adapter(failing).open_session(config, fs_t.ok()),
        open_unauthorized=lambda a: a.open_session(config), contacted=lambda: len(livy.calls),
        secrets=(fs_t.TOKEN, _JWT))


def _runtimes(tmp_path):
    r = dbc.Runtimes(tmp_path)
    folder = r.folder("16.4")
    (folder / "databricks" / "connect").mkdir(parents=True)
    (folder / "databricks" / "__init__.py").write_text("")
    (folder / "databricks" / "connect" / "__init__.py").write_text(dbc_t.FAKE)
    meta = folder / "databricks_connect-16.4.2.dist-info"
    meta.mkdir()
    (meta / "METADATA").write_text("Metadata-Version: 2.1\nName: databricks-connect\nVersion: 16.4.2\n")
    return r


HARNESSES = {
    "local-python": python, "local-duckdb": duckdb, "local-sqlite": sqlite, "local-polars": polars,
    "databricks-sql": databricks_sql, "databricks-connect": databricks_connect, "fabric-spark": fabric_spark,
}


@pytest.fixture(params=sorted(HARNESSES))
def harness(request, tmp_path, monkeypatch):
    h = HARNESSES[request.param](tmp_path, monkeypatch)
    yield h
    h.close_all()


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_every_adapter_meets_the_contract(harness, check):
    CHECKS[check](harness)


def test_every_adapter_has_a_harness():
    with_adapter = sorted(eid for eid, d in registry.ENGINES.items() if d.adapter)
    assert with_adapter == sorted(HARNESSES), (
        "An engine with an adapter joins the conformance run: add a Harness in test_engine_conformance.py.")


# ---------------------------------------------------------------- the checks catch overclaims


class _Overclaiming:
    """Claims a plan, a catalog and whole results it doesn't have, shows variables it doesn't claim, runs a
    language it marks unsupported, never fails a cell, and its harness doesn't show that sessions share nothing."""

    desc = EngineDescriptor(
        "local-duckdb", "Overclaiming", "local", "local", "none", adapter=True,
        languages={"sql": Support("native", evidence="unit_tests")},
        capabilities={"execute": Support("native", evidence="unit_tests"),
                      "plan": Support("native", evidence="unit_tests"),
                      "catalog": Support("native", evidence="unit_tests"),
                      "result": Support("native", evidence="unit_tests")})

    def __init__(self):
        self.engine, self.closed = "local-duckdb", False

    def describe(self):
        return self.desc

    def open_session(self, config, authorization=None):
        return self

    def execute(self, request, on_output=None):
        if self.closed:
            raise SessionClosed("closed")
        return CellResult("ok", [{"type": "text", "text": "1"}], 0.0, None, self.engine)

    def cancel(self):
        return False

    def variables(self):
        return []

    def plan(self, code, dialect=None):
        raise Unsupported("no plan after all")

    def catalog(self):
        return [{"name": "t"}]

    def close(self):
        self.closed = True


def _overclaiming_harness():
    return Harness("local-duckdb", _Overclaiming, lambda a: a.open_session(SessionConfig("nb")),
                   ok_cell=sql("SELECT 1"), error_cell=sql("SELECT nope"),
                   refused_cells=(CellRequest("print(1)", "python"),))


@pytest.mark.parametrize("check, message", [
    ("descriptor", "differs from the registry"),
    ("capabilities", r"claims plan but raised Unsupported .*catalog entry .* lacks \['catalog', 'schema', 'type'\]"),
    ("refused", "ran a python"),
    ("errors", "came back 'ok'"),
    ("isolation", "a local harness shows"),
    ("result", "claims result but returned no table"),
])
def test_an_adapter_that_overclaims_fails_the_checks(check, message):
    with pytest.raises(AssertionError, match=message):
        CHECKS[check](_overclaiming_harness())


class _Cutting:
    """Returns the first rows asked for when the result has more, instead of an error."""

    def __init__(self):
        self.engine, self.closed = "local-duckdb", False

    def open_session(self, config, authorization=None):
        return self

    def execute(self, request, on_output=None):
        import pyarrow as pa
        rows = min(request.result_rows, 3)
        return CellResult("ok", [], 0.0, rows, self.engine, table=pa.table({"i": list(range(rows))}))

    def close(self):
        self.closed = True


def test_an_engine_that_cuts_a_result_fails_the_checks():
    h = Harness("local-duckdb", _Cutting, lambda a: a.open_session(SessionConfig("nb")),
                ok_cell=sql("SELECT 1"), error_cell=sql("SELECT nope"))
    with pytest.raises(AssertionError, match="came back 'ok'"):
        CHECKS["result_never_cut"](h)


def test_a_remote_engine_that_sends_a_refused_cell_to_the_platform_fails_the_checks():
    sent = []

    class Chatty:
        engine, closed = "databricks-sql", False

        def execute(self, request, on_output=None):
            sent.append(request)    # reached the warehouse before saying no
            return CellResult("error", [{"type": "error", "ename": "Unsupported", "evalue": "no tsql"}],
                              0.0, None, self.engine)

        def close(self):
            self.closed = True

    h = Harness("databricks-sql", lambda: None, lambda _a: Chatty(), ok_cell=sql("SELECT 1"),
                error_cell=sql("SELECT nope"), refused_cells=(sql("SELECT 1", "tsql"),),
                contacted=lambda: len(sent))
    with pytest.raises(AssertionError, match="sent a sql/tsql cell it can't run to the platform"):
        CHECKS["refused"](h)
