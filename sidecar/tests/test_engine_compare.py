"""One query on two engines, answers compared (KUDP-06): whole results only when asked for, never cut,
refused up front for engines that can't return them, and no remote result onto this PC without a confirm."""

from __future__ import annotations

import subprocess
import sys

import pytest

from fabric_desktop.engines import compare as cmp
from fabric_desktop.engines import local_sql, registry
from fabric_desktop.engines.capabilities import EngineDescriptor, Support
from fabric_desktop.engines.contract import CellRequest, CellResult, SessionConfig, Unsupported

DATA = "CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a', 1.5), (2, 'b', 2.5), (3, 'c', 3.5)) AS v(id, name, amount)"
SQLITE_DATA = ("CREATE TABLE t (id INTEGER, name TEXT, amount REAL);"
               "INSERT INTO t VALUES (1, 'a', 1.5), (2, 'b', 2.5), (3, 'c', 3.5)")
QUERY = "SELECT id, name, amount FROM t ORDER BY id"


def sql(code, rows=0):
    return CellRequest(code, "sql", result_rows=rows)


@pytest.fixture
def duck():
    s = registry.adapter("local-duckdb").open_session(SessionConfig("left"))
    assert s.execute(sql(DATA)).status == "ok"
    yield s
    s.close()


@pytest.fixture
def lite():
    s = registry.adapter("local-sqlite").open_session(SessionConfig("right"))
    assert s.execute(sql(SQLITE_DATA)).status == "ok"
    yield s
    s.close()


class Counting:
    """A session that records what it was asked to run."""

    def __init__(self, engine):
        self.engine, self.notebook, self.ran = engine, "nb", []

    def execute(self, request, on_output=None):
        self.ran.append(request)
        return CellResult("ok", [], 0.0, 0, self.engine)


# ---------------------------------------------------------------- the contract


def test_no_table_unless_asked_and_never_sent_to_the_app(duck):
    res = duck.execute(sql(QUERY))
    assert res.table is None
    asked = duck.execute(sql(QUERY, rows=10))
    assert asked.table is not None and asked.table.num_rows == 3 and asked.rows == 3
    assert "table" not in asked.as_dict()


def test_the_whole_result_comes_back_while_the_cell_shows_a_preview(duck):
    res = duck.execute(CellRequest("SELECT * FROM range(2500) AS r(i)", "sql", preview_rows=10, result_rows=5000))
    assert res.table.num_rows == 2500 and res.rows == 2500
    assert res.outputs[-1]["total_rows"] == 2500 and len(res.outputs[-1]["rows"]) == 10


@pytest.mark.parametrize("engine", ["local-duckdb", "local-sqlite"])
def test_more_rows_than_asked_for_is_an_error_never_a_cut_table(engine):
    s = registry.adapter(engine).open_session(SessionConfig("nb"))
    try:
        code = ("SELECT * FROM range(50) AS r(i)" if engine == "local-duckdb"
                else "WITH RECURSIVE r(i) AS (SELECT 0 UNION ALL SELECT i + 1 FROM r WHERE i < 49) SELECT i FROM r")
        res = s.execute(sql(code, rows=20))
        assert res.status == "error" and res.table is None
        assert "more than the 20 rows" in res.outputs[-1]["evalue"]
        assert s.execute(sql(code, rows=50)).table.num_rows == 50
    finally:
        s.close()


def test_a_result_larger_than_the_byte_cap_is_an_error(duck, monkeypatch):
    monkeypatch.setattr(local_sql, "RESULT_BYTES", 64)
    res = duck.execute(sql("SELECT * FROM range(1000) AS r(i)", rows=1000))
    assert res.status == "error" and res.table is None and "larger than" in res.outputs[-1]["evalue"]


def test_only_duckdb_and_sqlite_claim_result_and_polars_says_why_not():
    claims = {e.id for e in registry.ENGINES.values() if e.capability("result").available}
    assert claims == {"local-duckdb", "local-sqlite"}
    assert registry.get("local-polars").capability("result").note


def test_the_contract_doesnt_load_pyarrow():
    code = "import sys, fabric_desktop.engines.contract; print('pyarrow' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# ---------------------------------------------------------------- comparing


def test_duckdb_and_sqlite_agree(duck, lite):
    report = cmp.compare(duck, lite, sql(QUERY), rules={"keys": ["id"]})
    assert report["schema"] == "engine-compare@1"
    assert report["result"] == "match" and report["level"] == "complete"
    assert report["left"]["engine"] == "local-duckdb" and report["right"]["engine"] == "local-sqlite"
    assert report["left"]["rows"] == report["right"]["rows"] == 3


def test_a_difference_is_found(duck, lite):
    lite.execute(sql("UPDATE t SET amount = 9.5 WHERE id = 2"))
    report = cmp.compare(duck, lite, sql(QUERY), rules={"keys": ["id"]})
    assert report["result"] == "differs"
    assert report["comparison"]["counts"]["changed"] == 1


def test_each_side_can_have_its_own_sql(duck, lite):
    report = cmp.compare(duck, lite, sql("SELECT id FROM t WHERE id <= 2"),
                         right_request=sql("SELECT id FROM t WHERE id < 3"))
    assert report["result"] == "match"


def test_an_engine_without_result_is_refused_before_anything_runs(duck):
    polars = Counting("local-polars")
    left = Counting("local-duckdb")
    with pytest.raises(Unsupported, match="can't return a whole result"):
        cmp.compare(left, polars, sql(QUERY))
    assert left.ran == [] and polars.ran == []


def test_a_remote_result_needs_the_callers_confirm(monkeypatch):
    remote = EngineDescriptor("fake-remote", "Fake warehouse (remote)", "remote", "databricks", "databricks_dbu",
                              adapter=True, preview=True,
                              capabilities={"execute": Support("native", evidence="unit_tests"),
                                            "result": Support("native", evidence="unit_tests")})
    monkeypatch.setitem(registry.ENGINES, remote.id, remote)
    left, right = Counting("local-duckdb"), Counting("fake-remote")
    with pytest.raises(cmp.CopyNotConfirmed, match="Confirm that copy first"):
        cmp.compare(left, right, sql(QUERY))
    assert left.ran == [] and right.ran == []
    report = cmp.compare(left, right, sql(QUERY), copy_confirmed=True)
    assert report["result"] == "not_compared"          # the fakes return no table
    assert left.ran[0].result_rows == cmp.DEFAULT_ROWS


def test_a_failed_left_side_stops_before_the_right_runs(duck):
    right = Counting("local-sqlite")
    report = cmp.compare(duck, right, sql("SELECT * FROM missing"))
    assert report["result"] == "not_compared" and report["right"]["status"] == "not_run"
    assert "didn't give an answer" in report["problem"] and right.ran == []


def test_a_side_with_too_many_rows_is_not_compared(duck, lite):
    report = cmp.compare(duck, lite, sql(QUERY), rows=2)
    assert report["result"] == "not_compared" and "more than the 2 rows" in report["problem"]


def test_bad_input_is_refused_before_anything_runs(duck):
    right = Counting("local-sqlite")
    with pytest.raises(ValueError, match="Unknown comparison rule"):
        cmp.compare(duck, right, sql(QUERY), rules={"nope": 1})
    with pytest.raises(ValueError, match="two different sessions"):
        cmp.compare(duck, duck, sql(QUERY))
    with pytest.raises(ValueError, match="above 0"):
        cmp.compare(duck, right, sql(QUERY), rows=0)
    assert right.ran == []


def test_a_key_only_one_side_has_is_reported(duck, lite):
    report = cmp.compare(duck, lite, sql(QUERY), right_request=sql("SELECT id AS ident, name, amount FROM t"),
                         rules={"keys": ["id"]})
    assert report["result"] == "not_compared" and "Key column" in report["problem"]
