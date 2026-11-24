"""A NUL character (U+0000) in SQL text never cuts a statement short.

DuckDB 1.x (1.5.6 ships) reads a query's text only up to a NUL, so the statement it runs isn't the one
written. tests/test_sql_literal_nul_fuzz.py covers the
SQL KfDuckDB translates; this covers the rest: duckdb_nul guards DuckDB's own Python entry points, so a
plain DuckDB SQL cell, a local data project query, a saved script or any path calling DuckDB directly gets
the same refusal before anything runs. A NUL inside a plain quoted string is kept, as CHR(0).

Each known entry point is walked with the same statement and must refuse it, or leave the rows. Seeded,
deterministic fuzz (the standard library's random) puts NULs at random in valid statements.
"""

from __future__ import annotations

import random
import subprocess
import sys
import time

import duckdb
import pytest

import fabric_desktop  # noqa: F401  # installs the guard
from fabric_desktop import duckdb_nul, spark_local, sql
from fabric_desktop.academy import learner_pkg, sqlprojlab
from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from fabric_desktop.localdata import LocalData

DELETE = "DELETE FROM t\x00 WHERE k = 2"
MAKE = "CREATE OR REPLACE TABLE t AS SELECT * FROM (VALUES (1, 'x'), (2, 'b')) v(k, s)"


def _refused(exc: BaseException) -> bool:
    return "NUL character" in str(exc)


def _keys(con) -> list[int]:
    return [k for (k,) in con.execute("SELECT k FROM t ORDER BY k").fetchall()]


# ---- the guard itself


@pytest.mark.parametrize("text, want", [
    ("SELECT 'a\x00b' AS c", "a\x00b"),
    ("SELECT '\x00' AS c", "\x00"),
    ("SELECT 'it''s\x00' AS c", "it's\x00"),
    ("SELECT 'é\x00\x00😀' AS c", "é\x00\x00😀"),
    ("SELECT '\x00'' ; DROP TABLE t; --' AS c", "\x00' ; DROP TABLE t; --"),
])
def test_a_nul_in_a_plain_string_comes_back_whole(text, want):
    assert duckdb.connect().execute(text).fetchall() == [(want,)]


@pytest.mark.parametrize("text", [
    DELETE, "\x00", "DELETE FROM t WHERE k\x00 = 2", "SELECT 1; DELETE FROM t\x00 WHERE k = 2",
    "SELECT 1 /* a\x00b */", "SELECT 1 -- a\x00b\n", 'SELECT 1 AS "a\x00b"', "SELECT E'a\x00b'",
    "SELECT $$a\x00b$$", "SELECT 'a'\x00'b'", "SELECT 'a\x00", "SELECT 'a'\n'b\x00'",
])
def test_a_nul_anywhere_else_is_refused_by_name(text):
    with pytest.raises(duckdb.Error) as caught:
        duckdb.connect().execute(text)
    assert _refused(caught.value) or "syntax error" in str(caught.value), caught.value


def test_the_refusal_says_what_why_and_what_next():
    assert "wasn't run, because" in duckdb_nul.REFUSAL and "CHR(0)" in duckdb_nul.REFUSAL


def test_text_without_a_nul_is_passed_on_as_it_is():
    text = "SELECT 'a' || CHR(0) AS c -- x"
    assert duckdb_nul.guard(text) is text


# ---- DuckDB's own entry points, called directly


def _fresh() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(MAKE)
    return con


DIRECT = {
    "connection.execute": lambda con: con.execute(DELETE),
    "connection.executemany": lambda con: con.executemany(DELETE, [[]]),
    "connection.sql": lambda con: con.sql(DELETE),
    "connection.query": lambda con: con.query(DELETE),
    "connection.from_query": lambda con: con.from_query(DELETE),
    "connection.extract_statements": lambda con: [con.execute(s) for s in con.extract_statements(DELETE)],
    "cursor.execute": lambda con: con.cursor().execute(DELETE),
    "duckdb.execute": lambda con: duckdb.execute(DELETE, connection=con),
    "duckdb.sql": lambda con: duckdb.sql(DELETE, connection=con),
    "duckdb.query": lambda con: duckdb.query(DELETE, connection=con),
    "relation.query": lambda con: con.sql("SELECT 1").query("r", DELETE),
}


@pytest.mark.parametrize("call", list(DIRECT), ids=list(DIRECT))
def test_every_direct_duckdb_entry_point_refuses_it(call):
    con = _fresh()
    with pytest.raises(duckdb.Error) as caught:
        DIRECT[call](con)
    assert _refused(caught.value), caught.value
    assert _keys(con) == [1, 2]


def test_a_relation_filter_is_never_cut_short():
    # DuckDB 1.5.6 read filter("k = 2<NUL> OR true") as k = 2
    with pytest.raises(duckdb.Error) as caught:
        _fresh().table("t").filter("k = 2\x00 OR true")
    assert _refused(caught.value)


def test_a_table_name_holding_a_nul_isnt_read_as_a_shorter_one():
    # DuckDB 1.5.6 read table("t<NUL>x") as t
    with pytest.raises(duckdb.Error) as caught:
        _fresh().table("t\x00x")
    assert _refused(caught.value)


def test_values_bound_as_parameters_keep_their_nuls():
    con = _fresh()
    con.execute("INSERT INTO t VALUES (?, ?)", [3, "a\x00b"])
    assert con.execute("SELECT s FROM t WHERE k = $k", {"k": 3}).fetchall() == [("a\x00b",)]


# ---- the app's entry points: SQL cells, local data, Spark, SQL page, project scripts


@pytest.fixture(scope="module")
def session():
    return registry.adapter("local-duckdb").open_session(SessionConfig("nul-guard"))


def _cell(session, code: str, dialect: str | None = None):
    result = session.execute(CellRequest(code=code, language="sql", dialect=dialect))
    out = result.outputs[-1]
    return ("error", out["ename"], out["evalue"]) if result.status != "ok" else out.get("rows")


@pytest.mark.parametrize("dialect", [None, "duckdb", "spark", "databricks", "snowflake"])
def test_a_sql_cell_refuses_it(session, dialect):
    _cell(session, MAKE)
    got = _cell(session, DELETE, dialect)
    assert isinstance(got, tuple) and "NUL character" in got[2], got
    assert _cell(session, "SELECT k FROM t ORDER BY k") == [[1], [2]]


def test_a_duckdb_sql_cell_keeps_a_nul_in_a_string(session):
    assert _cell(session, "SELECT 'a\x00b' AS c, length('a\x00b') AS n") == [["a\x00b", 3]]


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("nul") / "local-data")
    return local.engine(local.create("NUL")["id"])


@pytest.mark.parametrize("dialect", ["duckdb", "tsql", "spark"])
@pytest.mark.parametrize("how", ["execute", "execute_reader"])
def test_a_local_data_project_query_refuses_it(project, dialect, how):
    project.execute(MAKE)
    with pytest.raises(Exception) as caught:  # noqa: PT011 - each dialect names it in its own words
        getattr(project, how)(DELETE, dialect=dialect).close() if how == "execute_reader" else \
            project.execute(DELETE, dialect=dialect)
    assert "NUL character" in str(caught.value), caught.value
    assert [r[0] for r in project.execute("SELECT k FROM t ORDER BY k")["rows"]] == [1, 2]


@pytest.mark.parametrize("dialect", ["duckdb", "spark"])
def test_the_sql_page_translation_refuses_it(dialect):
    # sql_worker (SQL page runs, saved queries, templates, pipeline scripts) and the MCP execute_sql tool
    # run sql.parse and sql.duckdb_sql; the connection's own guard is behind them
    con = _fresh()
    with pytest.raises(Exception) as caught:  # noqa: PT011
        for statement in sql.parse(DELETE, dialect):
            con.execute(sql.duckdb_sql(statement))
    assert "NUL character" in str(caught.value)
    assert _keys(con) == [1, 2]


def test_spark_sql_on_this_pc_refuses_it():
    # spark.sql and %%sql translate with KfDuckDB (98c36ba); a table name holding a NUL is looked up whole
    spark = spark_local.session()
    spark.createDataFrame([(1,), (2,)], ["k"]).createOrReplaceTempView("nul_guard_v")
    with pytest.raises(ValueError, match="NUL character"):
        spark.sql("SELECT k FROM nul_guard_v WHERE k\x00 = 2 OR true").collect()


def test_a_warehouse_project_script_refuses_it():
    con = sqlprojlab._connect()
    con.execute(MAKE)
    with pytest.raises(ValueError, match="NUL character"):
        sqlprojlab._execute(con, DELETE)
    assert _keys(con) == [1, 2]


def test_every_process_that_imports_the_package_is_guarded(tmp_path):
    # sql_worker, kernel hosts and the MCP server import fabric_desktop; a learner's process imports kf_learner
    learner_pkg.build(tmp_path)
    check = ("import duckdb, {pkg}; c = duckdb.connect(); c.execute(\"CREATE TABLE t AS SELECT 1 k UNION SELECT 2\")\n"
             "try: c.execute('DELETE FROM t\\x00 WHERE k = 2')\nexcept duckdb.Error as e: print('NUL character' in str(e))\n"
             "print(c.execute('SELECT count(*) FROM t').fetchall())")
    for pkg, path in (("fabric_desktop", str(sql.__file__).rsplit("fabric_desktop", 1)[0]), ("kf_learner", str(tmp_path))):
        out = subprocess.run([sys.executable, "-c", check.format(pkg=pkg)], capture_output=True, text=True, timeout=60,
                             env={"PYTHONPATH": path, "PATH": ""}, check=True).stdout.split()
        assert out == ["True", "[(2,)]"], (pkg, out)


# ---- seeded fuzz: NULs at random in valid statements


BASES = [  # (statement, the keys of t it may leave, read whole)
    ("DELETE FROM t WHERE k = 2 AND s <> 'x y' /* c */", ([1, 2], [1])),
    ("DELETE FROM t WHERE k IN (SELECT 2) AND s = 'b'", ([1, 2], [1])),
    ("UPDATE t SET k = k + 10 WHERE k = 2 AND s <> 'x\ty'", ([1, 2], [1, 12])),
    ("INSERT INTO t SELECT 3, 'n' WHERE 'a' <> 'b'", ([1, 2], [1, 2, 3])),
]


def _run_direct(text: str) -> tuple[bool, list[int]]:
    con = _fresh()
    try:
        con.execute(text)
    except duckdb.Error:
        return False, _keys(con)
    return True, _keys(con)


@pytest.mark.parametrize("seed", [20261123, 156, 2])
def test_random_nuls_never_change_what_runs(seed):
    r = random.Random(seed)
    start = time.monotonic()
    for _ in range(120):
        base, allowed = r.choice(BASES)
        text = base
        for _ in range(r.randint(1, 3)):
            at = r.randrange(len(text) + 1)
            text = text[:at] + "\x00" + text[at:]
        ran, keys = _run_direct(text)
        assert keys in allowed, (text, ran, keys)
        if not ran:
            assert keys == [1, 2], (text, keys)
    assert time.monotonic() - start < 10


@pytest.mark.parametrize("seed", [7, 11])
def test_random_nuls_in_a_cell_never_change_what_runs(session, seed):
    r = random.Random(seed)
    for _ in range(30):
        base, allowed = r.choice(BASES)
        at = r.randrange(len(base) + 1)
        text = base[:at] + "\x00" + base[at:]
        _cell(session, MAKE)
        got = _cell(session, text)
        keys = [k for (k,) in _cell(session, "SELECT k FROM t ORDER BY k")]
        assert keys in allowed, (text, got, keys)
        if isinstance(got, tuple):
            assert keys == [1, 2], (text, got, keys)


@pytest.mark.parametrize("seed", [3, 5])
def test_random_strings_with_nuls_round_trip(seed):
    r = random.Random(seed)
    alphabet = ["\x00", "\x00", "''", "\\", "a", "é", "😀", " ", ";", "--", "/*", "*/", "\n", '"', "$$", "%"]
    con = duckdb.connect()
    for _ in range(80):
        pieces = ["".join(r.choice(alphabet) for _ in range(r.randint(0, 10))) for _ in range(r.randint(1, 3))]
        got = con.execute("SELECT " + ", ".join(f"'{p}'" for p in pieces)).fetchall()
        assert got == [tuple(p.replace("''", "'") for p in pieces)], pieces


@pytest.mark.parametrize("call", [
    lambda c, r: r.join(c.sql("SELECT 2 AS k"), "k = k\0 OR true"),
    lambda c, r: r.cross(c.sql("SELECT 2 AS j")).filter("j = 2\0 OR true"),
    lambda c, r: r.sum("k\0"),
    lambda c, r: r.min("k\0"),
    lambda c, r: r.value_counts("k\0"),
    lambda c, r: c.get_table_names("SELECT * FROM t\0x"),
    lambda c, r: r.filter(duckdb.SQLExpression("k = 2\0 OR true")),
], ids=["join", "cross-filter", "sum", "min", "value_counts", "get_table_names", "sql-expression"])
def test_relation_and_connection_helpers_that_take_expression_text_are_guarded(call):
    con = duckdb.connect()
    rel = con.sql("SELECT 1 AS k")
    with pytest.raises(duckdb.ParserException, match="NUL character"):
        call(con, rel)


def test_every_public_relation_and_connection_method_is_guarded_or_listed_as_taking_no_text():
    """Fails closed: a method a later DuckDB adds is guarded unless it's listed as taking no text."""
    for owner, no_text in ((duckdb.DuckDBPyRelation, duckdb_nul._RELATION_NO_TEXT),
                           (duckdb.DuckDBPyConnection, duckdb_nul._CONNECTION_NO_TEXT)):
        for name in dir(owner):
            member = getattr(owner, name)
            if name.startswith("_") or name in no_text or not callable(member):
                continue
            assert getattr(member, "__kf_nul_guard__", False), f"{owner.__name__}.{name} isn't guarded"


def test_a_sql_expression_built_from_text_is_guarded():
    con = duckdb.connect()
    con.execute("CREATE TABLE t (k INT)")
    con.execute("INSERT INTO t VALUES (1), (2)")
    for name in ("SQLExpression", "sql_expr"):
        if hasattr(duckdb, name):
            assert getattr(getattr(duckdb, name), "__kf_nul_guard__", False), f"duckdb.{name} isn't guarded"
    assert con.table("t").filter(duckdb.SQLExpression("k = 1")).fetchall() == [(1,)]
    with pytest.raises(duckdb.ParserException, match="NUL character"):
        con.table("t").filter(duckdb.SQLExpression("k = 1\0 OR TRUE")).fetchall()


DATAFRAME_SCANS = '''
import duckdb
import pandas as pd
frame = pd.DataFrame({"a": [1, 2, 3]})
out = [duckdb.sql("SELECT count(*) FROM frame").fetchall()[0][0],
       duckdb.connect().execute("SELECT count(*) FROM frame").fetchone()[0]]

def inside():
    local_frame = pd.DataFrame({"a": [1]})
    con = duckdb.connect()
    return [con.sql("SELECT count(*) FROM local_frame").fetchall()[0][0],
            con.sql("SELECT a FROM frame").filter("a > 1").aggregate("count(*)").fetchall()[0][0]]

def outer():
    closed = pd.DataFrame({"a": [7, 8]})
    def inner():
        closed  # noqa: B018 - a closure, as a cell's helper function has
        return duckdb.query("SELECT sum(a) FROM closed").fetchall()[0][0]
    return inner()

out += inside() + [outer()]
'''


def test_a_dataframe_named_in_the_sql_is_still_found_where_the_caller_holds_it():
    """DuckDB finds a DataFrame by name in the calling frame; the guard must not stand in that frame's place."""
    pytest.importorskip("pandas")
    scope: dict = {}
    exec(compile(DATAFRAME_SCANS, "<cell>", "exec"), scope)  # noqa: S102 - the test's own cell text
    assert scope["out"] == [3, 3, 1, 2, 15]


def test_a_callers_variable_named_like_the_guards_own_cant_take_the_call_over():
    pytest.importorskip("pandas")
    import pandas as pd
    frame = pd.DataFrame({"a": [1, 2]})  # noqa: F841 - found by name by DuckDB
    __kf_nul_call = lambda *a, **k: "hijacked"  # noqa: E731,N806 - the guard's own name, on purpose
    __kf_nul_args = ("SELECT 0",)  # noqa: N806
    assert duckdb.sql("SELECT count(*) FROM frame").fetchall() == [(2,)]
    assert (__kf_nul_call, __kf_nul_args) != (None, None)
    with pytest.raises(duckdb.ParserException, match="NUL character"):
        duckdb.sql("SELECT count(*) FROM frame\0")


def test_a_dataframe_two_frames_up_is_found_or_not_just_as_without_the_guard():
    """DuckDB looks only in the frame that calls it (unless python_scan_all_frames is set). A DataFrame held
    by an outer function is not found from an inner one that doesn't name it; that is DuckDB's own rule,
    the same without the guard, so it isn't a guard bug to fix."""
    pytest.importorskip("pandas")
    import pandas as pd

    def outer(sql_call):
        held_outside = pd.DataFrame({"a": [1]})  # noqa: F841 - two frames up from DuckDB's call

        def inner():
            try:
                return sql_call("SELECT count(*) FROM held_outside").fetchall()
            except duckdb.CatalogException:
                return "not found"
        return inner()

    assert outer(duckdb.sql) == outer(duckdb.sql.__wrapped__)
