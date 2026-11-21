"""Snowflake SQL on this PC (SF-04): every statement gets an honest label, a cell runs whole or not at
all, and the label comes back as the cell's first output. The 29 rows are the transpile probe of
story #853 (sqlglot 30.23.0, DuckDB 1.5.6); "ran" never means "checked against Snowflake"."""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pytest

from fabric_desktop import notebook_formats
from fabric_desktop.duckdb_rows import duckdb_major
from fabric_desktop import snowflake_sql as sf
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import check_cell
from fabric_desktop.engines.contract import CellRequest, SessionConfig

SETUP = [
    "CREATE TABLE t(id INT, grp VARCHAR, amt DOUBLE, ts TIMESTAMP, v JSON)",
    ("""INSERT INTO t VALUES (1,'a',10,'2026-01-01 10:00','{"k":{"x":1},"arr":[1,2]}'),"""
    """(2,'a',20,'2026-01-02','{"k":{"x":2},"arr":[3]}'),(3,'b',5,'2026-01-03','{"k":{"x":3},"arr":[]}')"""),
]

# (name, Snowflake SQL, verdict, note ids)
PROBE = [
    ("QUALIFY", "select id, grp from t qualify row_number() over (partition by grp order by amt desc) = 1",
     "translated", []),
    ("IFF/NVL/ZEROIFNULL", "select iff(amt>9,'big','small'), nvl(grp,'x'), zeroifnull(amt) from t", "translated", []),
    ("DATEADD/DATEDIFF", "select dateadd(day, 3, ts), datediff(day, ts, current_timestamp()) from t",
     "difference", ["SFD-004", "SFD-006"]),
    ("TO_VARCHAR format", "select to_varchar(ts, 'YYYY-MM-DD') from t", "translated", []),
    ("TO_CHAR format", "select to_char(current_date(), 'YYYY-MM-DD')", "translated", []),
    ("VARIANT path", "select v:k.x::int as x from t", "translated", []),
    ("LATERAL FLATTEN", "select t.id, f.value from t, lateral flatten(input => v:arr) f", "translated", []),
    ("TABLE(FLATTEN)", "select t.id, f.value from t, table(flatten(input => parse_json(t.v):arr)) f",
     "translated", []),
    ("OBJECT_CONSTRUCT with NULL", "select object_construct('a', 1, 'b', null)", "translated", []),
    ("ARRAY_AGG WITHIN GROUP", "select grp, array_agg(id) within group (order by id) from t group by grp",
     "translated", []),
    ("LISTAGG", "select grp, listagg(id, ',') within group (order by id) from t group by grp", "translated", []),
    ("TRY_TO_NUMBER", "select try_to_number('12x'), try_cast('5' as int)", "translated", []),
    ("SPLIT_PART/REGEXP_SUBSTR", "select split_part('a-b-c','-',2), regexp_substr('abc123','[0-9]+')",
     "translated", []),
    ("MERGE", ("merge into t using (select 1 id, 99.0 amt) s on t.id=s.id when matched then update set amt=s.amt "
              "when not matched then insert (id, amt) values (s.id, s.amt)"), "translated", []),
    ("CREATE OR REPLACE TABLE AS", "create or replace table t2 as select * from t", "translated", []),
    ("Time travel AT", "select * from t at(offset => -60)", "not_translatable", ["SFN-006"]),
    ("GENERATOR/SEQ4", "select seq4() from table(generator(rowcount => 3))", "translated", []),
    ("UNPIVOT", "select * from (select id, amt, amt*2 as amt2 from t) unpivot (val for col in (amt, amt2))",
     "translated", []),
    ("CONNECT BY", "select id from t start with id = 1 connect by prior id = id - 1", "translated", []),
    ("EXECUTE IMMEDIATE", "execute immediate 'select 1'", "not_translatable", ["SFN-005"]),
    ("DDL VARIANT/NUMBER/TIMESTAMP_TZ",
     "create table s(a variant, b number(38,0), c timestamp_ltz, d timestamp_tz, e string)",
     "difference", ["SFD-002", "SFD-004"]),
    ("TIMESTAMP_TZ literal with offset", "select '2026-01-01 10:00:00 +05:00'::timestamp_tz",
     "not_translatable", ["SFN-013"]),
    ("Integer division 7/2", "select 7/2, 7 % 2", "difference", ["SFD-003"]),
    ("Quoted alias", 'select 1 as "Mixed", 2 as plain', "translated", []),
    ("Unquoted identifier case", "create table up_test(MyCol int)", "translated", []),
    ("CREATE STREAM", "create stream s1 on table t", "not_translatable", ["SFN-001"]),
    ("CREATE DYNAMIC TABLE", "create dynamic table dt target_lag='1 minute' warehouse=wh as select * from t",
     "not_translatable", ["SFN-003"]),
    ("CREATE TASK", "create task tk warehouse=wh schedule='5 minute' as insert into t select 1, null, null, null, null",
     "not_translatable", ["SFN-002"]),
    ("Snowflake Scripting procedure", "create procedure p() returns int language sql as $$ begin return 1; end $$",
     "not_translatable", ["SFN-004"]),
]


@pytest.fixture
def session():
    s = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    for statement in SETUP:
        assert s.execute(CellRequest(statement, "sql")).status == "ok"
    yield s
    s.close()


def run(s, code):
    return s.execute(CellRequest(code, "sql", "snowflake"))


def rows(result):
    return next(o for o in result.outputs if o["type"] == "table")["rows"]


def test_the_probe_has_29_rows():
    assert len(PROBE) == 29


@pytest.mark.parametrize("name,code,verdict,notes", PROBE, ids=[p[0] for p in PROBE])
def test_each_probe_row_gets_its_label_and_runs_only_when_it_can(session, name, code, verdict, notes):
    t = sf.translate(code)
    (report,) = t.statements
    assert report.verdict == verdict
    assert [n.id for n in report.notes] == notes
    r = run(session, code)
    assert r.outputs[0]["type"] == "translation" and r.outputs[0]["statements"][0]["verdict"] == verdict
    if verdict == "not_translatable":
        assert report.duckdb_sql is None and not t.runnable
        assert r.status == "error" and "runs only in Snowflake" in r.outputs[-1]["evalue"]
        assert "Nothing in the cell ran." in r.outputs[-1]["evalue"]
    else:
        assert r.status == "ok", r.outputs


# ---------------------------------------------------------------- rewrite rules


def test_sfx001_flatten_returns_one_row_per_element_and_index_is_the_position(session):
    r = run(session, "select t.id, f.index, f.value::int as v from t, lateral flatten(input => v:arr) f "
                     "order by t.id, f.index")
    assert r.status == "ok" and rows(r) == [[1, 0, 1], [1, 1, 2], [2, 0, 3]]


def test_sfx001_outer_flatten_keeps_rows_with_an_empty_array(session):
    r = run(session, "select t.id, f.value from t, lateral flatten(input => v:arr, outer => true) f "
                     "where t.id = 3")
    assert r.status == "ok" and rows(r) == [[3, None]]


def test_sfx001_flatten_over_an_object_gives_its_keys(session):
    r = run(session, "select f.key, f.value::int as v from t, lateral flatten(input => v:k) f where t.id = 2")
    assert r.status == "ok" and rows(r) == [["x", 2]]


@pytest.mark.parametrize("code", ["select f.seq from t, lateral flatten(input => v:arr) f",
                                  "select seq from t, lateral flatten(input => v:arr) f"])
def test_sfx001_flatten_seq_is_refused(code):
    t = sf.translate(code)
    assert not t.runnable and t.statements[0].notes[0].id == "SFX-001"


def _flat(code):
    t = sf.translate(code)
    assert t.runnable and not t.statements[0].notes, t.statements[0].notes
    return duckdb.connect().execute(t.duckdb_sql).fetchall()


def test_sfx001_flatten_columns_are_snowflakes():
    rows = _flat("SELECT f.key, f.path, f.index, f.value::int, f.this FROM TABLE(FLATTEN(INPUT => "
                 "PARSE_JSON('{\"a\":1, \"b\":[77,88]}'), PATH => 'b')) f ORDER BY f.index")
    assert rows == [(None, "b[0]", 0, 77, "[77,88]"), (None, "b[1]", 1, 88, "[77,88]")]
    rows = _flat("SELECT key, path, index FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{\"a\":1, \"c\": {\"d\":2}}'), "
                 "PATH => 'c'))")
    assert rows == [("d", "c.d", None)]


def test_sfx001_flatten_outer_gives_one_row_for_an_empty_value_wherever_it_is():
    assert _flat("SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[]'), OUTER => TRUE)) f") == [
        (None, "", None, None, "[]")]
    assert _flat("SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[]'))) f") == []


@pytest.mark.parametrize("value", ["'5'", "'null'", "'\"hi\"'"])
def test_sfx001_flatten_of_a_scalar_gives_no_rows_and_one_with_outer(value):
    assert _flat(f"SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON({value})))") == []
    rows = _flat(f"SELECT key, path, index, value FROM TABLE(FLATTEN(INPUT => PARSE_JSON({value}), OUTER => TRUE))")
    assert rows == [(None, "", None, None)]


def test_sfx001_flatten_of_a_scalar_at_a_path_gives_no_rows_and_one_with_outer():
    assert _flat("SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{\"a\":1}'), PATH => 'a'))") == []
    assert _flat("SELECT key, path, index, value FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{\"a\":1}'), PATH => 'a', "
                 "OUTER => TRUE))") == [(None, "a", None, None)]


def test_sfx001_flatten_over_mixed_rows_counts_only_arrays_and_objects():
    rows = _flat("SELECT p.id, f.index FROM (SELECT column1 AS id, PARSE_JSON(column2) AS v FROM VALUES (1, '[7, 8]'), "
                 "(2, '3'), (3, '{\"k\": 1}')) p, LATERAL FLATTEN(INPUT => p.v) f ORDER BY p.id, f.index")
    assert rows == [(1, 0), (1, 1), (3, None)]


def test_sfx001_flatten_columns_of_json_each_dont_clash_with_the_tables():
    rows = _flat("SELECT id, type, f.value::int FROM (SELECT 1 AS id, 'x' AS type, PARSE_JSON('[5]') AS c) p, "
                 "LATERAL FLATTEN(INPUT => p.c) f")
    assert rows == [(1, "x", 5)]


@pytest.mark.parametrize("path", ["a b", "a-b", "$.a", "a[x]"])
def test_sfx001_flatten_path_that_isnt_plain_is_refused(path):
    t = sf.translate(f"SELECT value FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{{}}'), PATH => '{path}'))")
    assert not t.runnable and t.statements[0].notes[0].id == "SFX-001"


def test_sfx001_flatten_arguments_without_an_equivalent_are_refused():
    t = sf.translate("select f.value from t, lateral flatten(input => v, recursive => true) f")
    assert not t.runnable and t.statements[0].notes[0].id == "SFX-001"


def test_sfx002_date_formats_become_strftime(session):
    r = run(session, "select to_varchar(ts, 'YYYY-MM-DD HH24:MI') from t where id = 1")
    assert r.status == "ok" and rows(r) == [["2026-01-01 10:00"]]
    r = run(session, "select to_char(ts, 'DD \"of\" MON') from t where id = 1")
    assert r.status == "ok" and rows(r) == [["01 of Jan"]]


@pytest.mark.parametrize("fmt", ["YYYY-Q", "WW"])
def test_sfx002_format_elements_without_an_equivalent_are_refused(fmt):
    t = sf.translate(f"select to_varchar(ts, '{fmt}') from t")
    assert not t.runnable and t.statements[0].notes[0].id == "SFX-002"


def test_sfx003_number_formats_are_refused():
    t = sf.translate("select to_varchar(amt, '999,999.00') from t")
    assert not t.runnable and t.statements[0].notes[0].id == "SFX-003"


def test_sfx004_object_construct_drops_sql_nulls_and_keeps_json_nulls(session):
    r = run(session, "select object_construct('a', 1, 'b', null, 'c', parse_json('null')) as o")
    assert r.status == "ok" and json.loads(rows(r)[0][0]) == {"a": 1, "c": None}
    r = run(session, """select object_construct('n', parse_json('{"x": null}')) as o""")
    assert r.status == "ok" and json.loads(rows(r)[0][0]) == {"n": {"x": None}}
    r = run(session, "select object_construct() as o")
    assert r.status == "ok" and json.loads(rows(r)[0][0]) == {}


def test_sfx004_object_construct_star_is_refused():
    t = sf.translate("select object_construct(*) from t")
    assert not t.runnable and t.statements[0].notes[0].id == "SFX-004"


# ---------------------------------------------------------------- known differences


@pytest.mark.parametrize("code,note", [
    ("create view x as select MyCol * 2 from t", "SFD-001"),
    ("create table x (c timestamp_tz)", "SFD-002"),
    ("select amt / 2 from t", "SFD-003"),
    ("select current_timestamp()", "SFD-004"),
    ("select date_trunc('month', ts) from t", "SFD-006"),
    ("select dateadd(week, 1, ts) from t", "SFD-006"),
    ("select typeof(v) from t", "SFD-007"),
    ("select l[i] from t", "SFD-009"),
    ("select approx_percentile(x, 0.5) from t", "SFD-010"),
    ("select hll(x) from t", "SFD-010"),
    ("select v['a']::string from t", "SFD-009"),
])
def test_each_known_difference_is_named(code, note):
    (report,) = sf.translate(code).statements
    assert report.verdict == "difference" and note in [n.id for n in report.notes]
    assert all(n.reason and n.action for n in report.notes)


def test_division_by_a_decimal_literal_isnt_flagged():
    assert sf.translate("select amt / 2.0 from t").statements[0].verdict == "translated"


def test_quoted_names_keep_their_case_without_a_note():
    assert sf.translate('create table "Up"("MyCol" int)').statements[0].verdict == "translated"


# ---------------------------------------------------------------- whole cells


class _Spy:
    """The session's connection, with every execute recorded."""

    def __init__(self, con):
        self.con, self.calls = con, []

    def execute(self, query, *args):
        self.calls.append(query)
        return self.con.execute(query, *args)

    def __getattr__(self, name):
        return getattr(self.con, name)


def test_a_cell_runs_whole_or_not_at_all(session, monkeypatch):
    spy = _Spy(session._con)
    monkeypatch.setattr(session, "_con", spy)
    r = run(session, "insert into t (id) values (9); create stream s1 on table t")
    assert r.status == "error" and "Statement 2 runs only in Snowflake" in r.outputs[-1]["evalue"]
    assert [o["verdict"] for o in r.outputs[0]["statements"]] == ["translated", "not_translatable"]
    assert spy.calls == []
    assert rows(session.execute(CellRequest("select count(*) from t where id = 9", "sql"))) == [[0]]


@pytest.mark.parametrize("code", [
    "copy into t from @my_stage",
    "select * from @my_stage/file.csv",
    "put file:///tmp/x.csv @my_stage",
    "list @my_stage",
    "use warehouse wh",
    "show tables",
    "undrop table t",
    "create table t3 clone t",
    "grant select on table t to role r",
    "revoke select on table t from role r",
    "call p()",
    "select snowflake.cortex.complete('m', 'hi')",
])
def test_snowflake_only_commands_are_refused_before_anything_runs(session, code):
    r = run(session, code)
    assert r.status == "error" and r.outputs[0]["type"] == "translation" and not r.outputs[0]["runnable"]


def test_copy_attach_and_install_are_refused_first(session):
    for code in ("attach 'x.duckdb'", "install httpfs"):
        assert run(session, code).status == "error"


def test_the_translation_comes_first_and_the_result_after(session):
    r = run(session, "select iff(amt > 9, 'big', 'small') as size from t where id = 1")
    assert [o["type"] for o in r.outputs] == ["translation", "table"]
    first = r.outputs[0]
    assert first["dialect"] == "snowflake" and first["rules_version"] == sf.RULES_VERSION and first["runnable"]
    assert "Not checked against a Snowflake account." in first["text"]
    assert rows(r) == [["big"]]


def test_other_dialects_get_no_translation_output(session):
    r = session.execute(CellRequest("select 1", "sql", "databricks"))
    assert [o["type"] for o in r.outputs] == ["table"]


def test_the_plan_says_it_is_duckdbs(session):
    plan = session.plan("select iff(amt > 9, 'big', 'small') from t", "snowflake")
    assert plan.startswith("Plan of the DuckDB translation on this PC, not of Snowflake's plan.")


@pytest.mark.parametrize("code,message", [("select from where (", "Could not read the query"),
                                          ("  ;  ", "The query is empty.")])
def test_unreadable_and_empty_input_say_so(code, message):
    with pytest.raises(ValueError, match=message):
        sf.translate(code)


def test_describe_shows_snowflake_as_translated():
    d = registry.get("local-duckdb")
    v = check_cell(d, "sql", "snowflake")
    assert v["ok"] and v["level"] == "translated" and "not checked against a Snowflake account" in v["reason"]
    assert d.as_dict()["dialects"]["snowflake"]["level"] == "translated"


# ---------------------------------------------------------------- exports keep it as text


EVIL = "<img src=x onerror=alert(1)><script>alert(1)</script>"


def test_exports_keep_the_translation_as_escaped_text():
    out = {"type": "translation", "dialect": "snowflake", "text": "Statement 1: " + EVIL}
    cells = [{"type": "code", "source": "select 1", "outputs": [out]}]
    page = notebook_formats.to_html("nb", cells)
    assert EVIL not in page and "&lt;script&gt;" in page
    nb = json.loads(notebook_formats.to_ipynb_text(cells, outputs=True))
    (o,) = nb["cells"][0]["outputs"]
    assert o["output_type"] == "stream" and "".join(o["text"]) == out["text"]


# ---------------------------------------------------------------- docs


def test_the_docs_table_is_the_codes():
    page = (Path(__file__).resolve().parents[2] / "docs" / "engines.md").read_text(encoding="utf-8")
    assert sf.docs_table() in page


def test_every_rule_the_code_uses_is_in_the_docs_table():
    source = Path(sf.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'"(SF[XDN]-\d{3})"', source))
    listed = [id for id, _, _ in sf.CATALOG]
    assert len(listed) == len(set(listed)) and used == set(listed)


@pytest.mark.parametrize("code", [
    "select dateadd(day, 1, ts::timestamp) from t",
    "select date_trunc('day', current_timestamp())",
    "select dateadd(month, 1, to_timestamp('2026-01-01 10:00'))",
    "select dateadd(day, 1, to_timestamp(s)) from t",
    "select date_trunc('day', to_timestamp_ntz(s)) from t",
    "select dateadd(week, 1, try_to_timestamp(s)) from t",
    "select date_trunc('month', to_timestamp_tz(s)) from t",
    "select date_trunc('week', convert_timezone('UTC', ts)) from t",
])
def test_date_arithmetic_on_a_plain_timestamp_is_not_a_date_difference(code):
    (report,) = sf.translate(code).statements
    assert "SFD-006" not in [n.id for n in report.notes]


@pytest.mark.parametrize("code", [
    "select dateadd(day, 1, ts) from t",
    "select date_trunc('month', ts) from t",
    "select dateadd(year, 1, d) from t",
])
def test_date_arithmetic_on_what_may_be_a_date_is_a_date_difference(code):
    (report,) = sf.translate(code).statements
    assert "SFD-006" in [n.id for n in report.notes]


def test_date_trunc_of_a_date_stays_a_date_on_every_sqlglot():
    """#1252: cast back to DATE here, as Snowflake (and sqlglot 30.25 itself) does, so no SFD-006."""
    (report,) = sf.translate("select date_trunc('month', ts::date) from t").statements
    assert "SFD-006" not in [n.id for n in report.notes]
    assert report.duckdb_sql.upper().startswith("SELECT CAST(DATE_TRUNC('MONTH', CAST(TS AS DATE)) AS DATE)")


def test_a_time_unit_on_date_arithmetic_is_not_a_date_difference():
    (report,) = sf.translate("select dateadd(hour, 2, ts), date_trunc('minute', ts) from t").statements
    assert "SFD-006" not in [n.id for n in report.notes]


def test_values_in_from_get_snowflakes_column_names_and_an_aliased_list_keeps_its_own():
    con = duckdb.connect()
    sql = sf.translate("select column1, column2 from values (1, 'a'), (2, 'b') order by column1").duckdb_sql
    assert con.execute(sql).fetchall() == [(1, "a"), (2, "b")]
    sql = sf.translate("select v.column2 from values (1, 'a') as v").duckdb_sql
    assert con.execute(sql).fetchall() == [("a",)]
    sql = sf.translate("select a from values (7, 'x') as v(a, b)").duckdb_sql
    assert con.execute(sql).fetchall() == [(7,)]
    con.execute("create table iv(a int, b varchar)")
    con.execute(sf.translate("insert into iv values (1, 'z')").duckdb_sql)  # INSERT ... VALUES is left alone
    assert con.execute("select * from iv").fetchall() == [(1, "z")]


def test_ratio_to_report_is_the_value_over_its_partitions_sum_and_null_when_that_sum_is_0():
    con = duckdb.connect()
    for statement in SETUP:
        con.execute(statement)
    sql = sf.translate("select id, ratio_to_report(amt) over (partition by grp order by id) r from t "
                       "order by id").duckdb_sql
    assert "ORDER BY id)" not in sql  # the window's ORDER BY doesn't change the result, so it's dropped
    assert [round(r, 9) for _, r in con.execute(sql).fetchall()] == [round(10 / 30, 9), round(20 / 30, 9), 1.0]
    sql = sf.translate("select ratio_to_report(x) over () from values (1), (-1), (null) as v(x)").duckdb_sql
    assert con.execute(sql).fetchall() == [(None,)] * 3


@pytest.mark.parametrize("code", [
    "select ratio_to_report(amt) over (order by id rows between unbounded preceding and current row) from t",
    "select ratio_to_report(amt, id) over () from t",
    "select haversine(1, 2, 3) from t",
])
def test_ratio_to_report_and_haversine_refuse_shapes_snowflake_does_not_allow(code):
    translation = sf.translate(code)
    assert not translation.runnable
    assert translation.first_refusal()


def test_haversine_is_kilometres_on_a_6371_km_sphere_and_reads_columns():
    con = duckdb.connect()
    sql = sf.translate("select haversine(40.7127, -74.0059, 34.0500, -118.2500)").duckdb_sql
    assert round(con.execute(sql).fetchall()[0][0], 9) == 3936.385096389  # Snowflake's docs example
    con.execute("create table p(a_lat int, a_lon int, b_lat int, b_lon int)")
    con.execute("insert into p values (0, 0, 0, 180), (0, 0, 0, 0), (null, 0, 0, 0)")
    sql = sf.translate("select haversine(a_lat, a_lon, b_lat, b_lon) from p").duckdb_sql
    got = [r[0] for r in con.execute(sql).fetchall()]
    assert round(got[0], 6) == round(3.141592653589793 * 6371, 6) and got[1] == 0 and got[2] is None


@pytest.mark.parametrize("code, want", [
    ("select dateadd(month, 1, '2026-01-31'::date)", "2026-02-28"),
    ("select dateadd(year, 1, to_date('2026-01-01'))", "2027-01-01"),
    ("select timestampadd(week, 1, try_to_date('2026-01-01'))", "2026-01-08"),
    ("select dateadd(day, 1, current_date) - interval 1 day = current_date", None),
])
def test_dateadd_of_a_plain_date_by_a_date_unit_gives_a_date_and_no_note(code, want):
    """#1319: Snowflake keeps a DATE a DATE; DuckDB's DATE + INTERVAL is a TIMESTAMP, so it's cast back."""
    (report,) = sf.translate(code).statements
    assert "SFD-006" not in [n.id for n in report.notes]
    con = duckdb.connect()
    if want is None:
        assert con.execute(report.duckdb_sql).fetchone()[0] is True
        assert con.execute(sf.translate("select dateadd(day, 1, current_date)").duckdb_sql).description[0][1] \
            == "DATE"
        return
    (got,) = con.execute(report.duckdb_sql).fetchone()
    assert type(got).__name__ == "date" and got.isoformat() == want


def test_dateadd_of_a_date_by_an_hour_stays_a_timestamp():
    (report,) = sf.translate("select dateadd(hour, 2, '2026-01-01'::date)").statements
    assert "SFD-006" not in [n.id for n in report.notes]
    (got,) = duckdb.connect().execute(report.duckdb_sql).fetchone()
    assert got.isoformat() == "2026-01-01T02:00:00"


# ---------------------------------------------------------------- TYPEOF (SFX-009, #1372)


@pytest.mark.parametrize("code, want", [
    # The rows of Snowflake's TYPEOF page.
    ("select typeof(parse_json('null'))", "NULL_VALUE"),
    ("select typeof(null::variant)", None),
    ("select typeof(parse_json('true'))", "BOOLEAN"),
    ("select typeof(parse_json('-17'))", "INTEGER"),
    ("select typeof(parse_json('123.12'))", "DECIMAL"),
    ("select typeof(parse_json('1.912e2'))", "DOUBLE"),
    ("""select typeof(parse_json('"Om ara pa ca na dhih"  '))""", "VARCHAR"),
    ("select typeof(parse_json('[-1, 12, 289, 2188, false,]'))", "ARRAY"),
    ("""select typeof(parse_json('{ "x" : "abc", "y" : false, "z": 10} '))""", "OBJECT"),
    ("select typeof('check in'::variant)", "VARCHAR"),
    ("select typeof(to_variant('2024-01-17 19:00:00.000'::timestamp))", "TIMESTAMP_NTZ"),
    # PARSE_JSON's own rules for numbers: an exponent is DOUBLE; past NUMBER(38, 37) is DOUBLE.
    ("select typeof(parse_json('1E2'))", "DOUBLE"),
    ("select typeof(parse_json('18446744073709551615'))", "INTEGER"),
    ("select typeof(parse_json('9' || repeat('0', 37)))", "INTEGER"),
    ("select typeof(parse_json('9' || repeat('0', 38)))", "DOUBLE"),
    ("select typeof(parse_json('0.' || repeat('1', 37)))", "DECIMAL"),
    ("select typeof(parse_json('0.' || repeat('1', 38)))", "DOUBLE"),
    ("select typeof(try_parse_json('not json'))", None),
    # Typed values.
    ("select typeof(1)", "INTEGER"),
    ("select typeof(1.5)", "DECIMAL"),
    ("select typeof(7::number(10, 0))", "INTEGER"),
    ("select typeof(1.5::float)", "DOUBLE"),
    ("select typeof(true)", "BOOLEAN"),
    ("select typeof('2026-01-01'::date)", "DATE"),
    ("select typeof('10:00'::time)", "TIME"),
])
def test_typeof_gives_snowflakes_names_and_no_note_where_they_are_exact(code, want):
    (report,) = sf.translate(code).statements
    assert report.verdict == "translated", report.notes
    assert duckdb.connect().execute(report.duckdb_sql).fetchone()[0] == want


def test_typeof_of_a_column_names_json_values_and_flags_what_it_cant_know():
    con = duckdb.connect()
    con.execute("create table j(v json)")
    con.execute("""insert into j values ('null'), ('true'), ('-17'), ('"s"'), ('[1]'), ('{"a": 1}'), (null),"""
                """ ('99999999999999999999'), ('123.12'), ('1.912e2')""")
    (report,) = sf.translate("select typeof(v) from j").statements
    assert report.verdict == "difference" and [n.id for n in report.notes] == ["SFD-007"]
    got = [r[0] for r in con.execute(report.duckdb_sql + " LIMIT 8").fetchall()]
    assert got == ["NULL_VALUE", "BOOLEAN", "INTEGER", "VARCHAR", "ARRAY", "OBJECT", None, "INTEGER"]
    # A fraction or an exponent in a column: DuckDB may have written it again, so the query stops at it (SFN-034)
    # rather than name it
    for number in ("123.12", "1.912e2"):
        con.execute("delete from j")
        con.execute("insert into j values (?)", [number])
        with pytest.raises(duckdb.Error, match="SFN-034 The type of a JSON number with a fraction"):
            con.execute(report.duckdb_sql).fetchall()


@pytest.mark.parametrize("code, want", [
    ("""select typeof(v:a) from (select parse_json('{"a": 1.912e2}') as v)""", "SFN-034"),  # 191.2 here: it stops
    ("""select typeof(v:a) from (select parse_json('{"a": 10}') as v)""", "INTEGER"),
    ("select typeof('2026-01-01 10:00'::timestamp_ltz)", "TIMESTAMP WITH TIME ZONE"),  # LTZ or TZ: DuckDB's name
])
def test_typeof_never_gives_a_confident_wrong_name(code, want):
    (report,) = sf.translate(code).statements
    assert "SFD-007" in [n.id for n in report.notes]
    if want.startswith("SFN-"):
        with pytest.raises(duckdb.Error, match=want):
            duckdb.connect().execute(report.duckdb_sql).fetchone()
        return
    assert duckdb.connect().execute(report.duckdb_sql).fetchone()[0] == want


def test_typeof_reads_its_argument_as_a_tree_and_the_rule_is_listed():
    sql = sf.translate("select typeof(v:k.x) from t").duckdb_sql
    assert "LAMBDA" not in sql.upper() and "->" in sql  # a path stays a path (#1252)
    # the ::variant is read past: the value isn't cast to VARIANT, except where the VARIANT branch (a DuckDB
    # VARIANT value, SFX-024) asks variant_typeof what it holds
    sql = sf.translate("select typeof('a'::variant)").duckdb_sql
    assert sql.count("AS VARIANT") == sql.upper().count("VARIANT_TYPEOF(CAST(")
    assert _first("select typeof('a'::variant)") == "VARCHAR"
    assert "SFX-009" in [row[0] for row in sf.CATALOG] and "SFX-009" in sf.docs_table()


@pytest.mark.parametrize("code, want", [
    ("""select typeof(parse_json('{"a": 1.912e2}'):a)""", "DOUBLE"),
    ("""select typeof(parse_json('{"a": [1, {"b": 191.2}]}'):a[1].b)""", "DECIMAL"),
    ("""select typeof(parse_json('{"a": 10}'):a)""", "INTEGER"),
    ("""select typeof(parse_json('{"a": null}'):a)""", "NULL_VALUE"),
    ("""select typeof(parse_json('{"a": "x"}'):a)""", "VARCHAR"),
    ("""select typeof(parse_json('{"a": 1}'):A)""", None),  # path keys are case-sensitive: no such key
    ("""select typeof(get_path(parse_json('{"a": [true]}'), 'a[0]'))""", "BOOLEAN"),
])
def test_typeof_of_a_path_into_json_written_in_the_query_is_exact(code, want):
    (report,) = sf.translate(code).statements
    assert "SFD-007" not in [n.id for n in report.notes]
    assert duckdb.connect().execute(report.duckdb_sql).fetchone()[0] == want


def test_typeof_of_json_snowflake_wont_parse_falls_back_instead_of_crashing():
    for code in ("""select typeof(parse_json('{"a": NaN}'):a)""", """select typeof(parse_json('[Infinity]')[0])""",
                 """select typeof(parse_json('{"a": 1, "a": 2}'):a)"""):
        (report,) = sf.translate(code).statements
        assert report.verdict != "not_translatable" and "SFD-007" in [n.id for n in report.notes]


@pytest.mark.parametrize("code", ["select 1/0", "select 1/0.0", "select 1/0e5",
                                  "select iff(b = 0 and c, 0, a / b) from (select 1 as a, 0 as b, false as c)", "select a / b from (select 1 as a, 0 as b)",
                                  "select 1 / (a - 1) from (select 1 as a)"])
def test_dividing_by_zero_stops_as_in_snowflake(code):
    with pytest.raises(duckdb.Error, match="Division by zero"):
        duckdb.connect().execute(sf.translate(code).duckdb_sql).fetchall()


@pytest.mark.parametrize("code, want", [
    ("select 7/2", 3.5), ("select div0(1, 0)", 0), ("select div0null(1, null)", 0), ("select div0(null, 0)", None),
    ("select a / b from (select 1 as a, null as b)", None), ("select a / b from (select 1 as a, 4 as b)", 0.25),
])
def test_division_that_snowflake_answers_still_answers(code, want):
    sql = sf.translate(code).duckdb_sql
    assert duckdb.connect().execute(sql).fetchone()[0] == want
    if code == "select 7/2":
        assert "ERROR" not in sql.upper()  # a divisor written as a non-zero number needs no check


@pytest.mark.parametrize("name", sorted(sf._ACCOUNT_FUNCTIONS))
def test_account_and_session_functions_are_refused_by_name(name):
    args = "-1" if name == "LAST_QUERY_ID" else ""
    t = sf.translate(f"SELECT 1 AS a, {name.lower()}({args}) AS b")
    assert not t.runnable
    [note] = t.statements[0].notes
    assert note.id == "SFN-014" and note.title.startswith(name)


def test_functions_that_run_here_are_not_taken_for_account_functions():
    assert sf.translate("SELECT current_date(), current_timestamp(), last_value(a) OVER (ORDER BY a) FROM t").runnable


def _one(sql):
    import duckdb
    t = sf.translate(sql)
    assert t.runnable, t.first_refusal()
    return duckdb.connect().execute(t.duckdb_sql).fetchone(), t.statements[0]


def test_is_and_as_read_the_type_a_variant_was_made_from():
    row, st = _one("SELECT is_varchar(to_variant('2024-01-17'::date)), is_date(to_variant('2024-01-17'::date)), "
                   "is_integer(to_variant(15::number(38, 0))), is_decimal(to_variant(3.21::double)), "
                   "as_integer(to_variant(1.23::decimal(6, 3))), as_varchar(parse_json('\"x\"'))")
    assert row == (False, True, True, False, None, "x")


def test_is_and_as_of_sql_null_are_null_and_json_null_is_its_own_type():
    row, _ = _one("SELECT is_varchar(NULL), as_varchar(NULL), is_null_value(parse_json('null')), "
                  "is_varchar(parse_json('null')), as_integer(parse_json('null'))")
    assert row == (None, None, True, False, None)


def test_the_decimal_family_goes_by_how_the_number_was_written_or_stops():
    # SFN-034 (SFD-008 retired): PARSE_JSON's own text says DECIMAL or DOUBLE, as does a path into PARSE_JSON of
    # text in the query; a stored value doesn't, so the query stops at it instead of calling 1.912e2 a decimal
    assert _one("SELECT is_decimal(parse_json('1.5'))")[1].notes == ()
    assert _one("SELECT is_integer(parse_json('1')), as_double(parse_json('1.5'))")[1].notes == ()
    assert _first("SELECT is_decimal(parse_json('1.5'))") is True
    assert _first("SELECT is_decimal(parse_json(' 1.912e2 '))") is False
    assert _first("SELECT as_number(parse_json('1.912E2'))") is None
    assert float(_first("SELECT as_number(parse_json('1.5'), 4, 1)")) == 1.5
    assert _first("SELECT is_decimal(parse_json('{\"a\": 1.912e2}'):a)") is False
    assert _first("SELECT is_decimal(parse_json('{\"a\": [191.2]}'):a[0])") is True
    assert _first("SELECT is_decimal(parse_json(column1)) FROM VALUES ('1.912e2')") is False
    assert _first("SELECT is_decimal(v) FROM (SELECT parse_json('-17') AS v)") is True
    for code in ("SELECT is_decimal(v) FROM (SELECT parse_json('1.912e2') AS v)",
                 "SELECT as_decimal(v:a) FROM (SELECT parse_json('{\"a\": 123.12}') AS v)",
                 "SELECT as_number(v) FROM (SELECT parse_json('0.5') AS v)"):
        with pytest.raises(duckdb.Error, match="SFN-034 .*Runs only in Snowflake"):
            _first(code)


@pytest.mark.parametrize("sql, rule", [
    ("SELECT is_timestamp_tz(v) FROM t", "SFN-015"), ("SELECT as_timestamp_ltz(v) FROM t", "SFN-015"),
    ("SELECT as_decimal(v, p) FROM t", "SFX-011"), ("SELECT as_decimal(v, 40, 2) FROM t", "SFX-011"),
    ("SELECT as_decimal(v, 4, 6) FROM t", "SFX-011"), ("SELECT is_integer(v, 1) FROM t", "SFX-011"),
    ("SELECT as_varchar() FROM t", "SFX-011")])
def test_variant_types_that_cannot_be_rewritten_are_refused_by_name(sql, rule):
    t = sf.translate(sql)
    assert not t.runnable and t.statements[0].notes[0].id == rule


def test_nested_is_and_as_calls_are_all_rewritten_and_the_value_is_read_once():
    row, st = _one("SELECT is_integer(as_integer(parse_json('5'))), as_integer(as_object(parse_json('{\"a\": 5}')):a)")
    assert row == (True, 5)
    sql = sf.translate("SELECT is_integer(parse_json(col)) FROM t").duckdb_sql
    assert sql.upper().count("JSON(COL)") == 1 and "LAMBDA" in sql.upper()
def test_a_json_string_cast_to_text_loses_its_quotes_and_json_null_is_sql_null():
    import duckdb
    t = sf.translate("SELECT v:a::string, v:n::string, v:o::varchar, v:z::string, to_varchar(v:a), v:a, "
                     "'x'::string FROM (SELECT parse_json('{\"a\": \"Pine\", \"n\": 1.5, \"o\": {\"k\": 1}, "
                     "\"z\": null}') AS v)")
    assert t.statements[0].verdict == "translated"
    row = duckdb.connect().execute(t.duckdb_sql).fetchone()
    assert row[:6] == ("Pine", "1.5", '{"k":1}', None, "Pine", '"Pine"') and row[6] == "x"


def test_brackets_index_json_from_zero_and_lists_from_an_integer_column():
    import duckdb
    t = sf.translate("SELECT v[0], v['k'][1]::string, l[0], l[i], x[0] FROM (SELECT parse_json('[\"a\"]') AS v, "
                     "parse_json('{\"k\": [1, \"b\"]}') AS w, array_construct(10, 20) AS l, 1 AS i, "
                     "array_construct(7) AS x)")
    sql = t.duckdb_sql
    assert "v[0]" in sql and "l[1]" in sql and "l[i + 1]" in sql and "x[1]" in sql
    t = sf.translate("SELECT w['k'][1]::string, l[i] FROM (SELECT parse_json('{\"k\": [1, \"b\"]}') AS w, "
                     "array_construct(10, 20) AS l, 1 AS i)")
    assert duckdb.connect().execute(t.duckdb_sql).fetchone() == ("b", 20)


def test_a_column_whose_type_the_statement_does_not_show_is_left_as_written():
    sql = sf.translate("SELECT v['a']::string, x[0] FROM t").duckdb_sql
    assert sql == "SELECT CAST(v['a'] AS TEXT), x[1] FROM t"
    (report,) = sf.translate("SELECT v['a']::string, x[0] FROM t").statements
    assert report.verdict == "difference" and [n.id for n in report.notes] == ["SFD-009"]


@pytest.mark.parametrize("code", [
    "SELECT m['k'] FROM t",                                   # a string key alone reads the same on JSON and MAP
    "SELECT id::string FROM t",                               # no brackets
    "SELECT v:a::string FROM t",                              # a path is JSON whatever the column is
    "SELECT parse_json('[5,6]')[0]",
    "SELECT l[1] FROM (SELECT array_construct(10, 20) AS l)",
    "WITH s AS (SELECT parse_json('{\"a\": \"P\"}') AS v) SELECT v['a']::string FROM s",
])
def test_brackets_whose_value_type_is_known_raise_no_note(code):
    assert "SFD-009" not in [n.id for n in sf.translate(code).statements[0].notes]


@pytest.mark.parametrize("code", ["SELECT l[0] FROM t", "SELECT (v['a'])::varchar FROM t",
                                  "SELECT to_varchar(v['a']) FROM t", "SELECT v['a'][0] FROM t"])
def test_brackets_on_a_column_of_unknown_type_are_labelled(code):
    assert "SFD-009" in [n.id for n in sf.translate(code).statements[0].notes]


_SKETCH_ARGS = {"APPROX_TOP_K": "x, 2", "APPROX_TOP_K_ACCUMULATE": "x, 2", "APPROX_TOP_K_ESTIMATE": "x, 2",
                "APPROX_PERCENTILE_ESTIMATE": "x, 0.5", "MINHASH": "5, x"}


@pytest.mark.parametrize("name", sorted(sf._SKETCH_FUNCTIONS))
def test_sketch_and_bitmap_states_are_refused_by_name(name):
    t = sf.translate(f"SELECT {name.lower()}({_SKETCH_ARGS.get(name, 'x')}) FROM t")
    (note,) = t.statements[0].notes
    assert not t.runnable and note.id == "SFN-016" and "sketch and bitmap" in note.title


def test_hll_and_datasketches_hll_count_distinct_values_as_snowflake_types_them():
    import duckdb
    t = sf.translate("SELECT hll(x), hll(DISTINCT x), datasketches_hll(x), datasketches_hll(DISTINCT x, 12), "
                     "typeof(approx_percentile(x, 0.5)) FROM (SELECT column1 AS x FROM VALUES (1), (1), (2))")
    assert "SFD-010" in [n.id for n in t.statements[0].notes]
    assert duckdb.connect().execute(t.duckdb_sql).fetchone() == (2, 2, 2.0, 2.0, "DOUBLE")


def test_approximate_functions_in_a_window_are_cast_outside_it():
    import duckdb
    t = sf.translate("SELECT approx_percentile(x, 0.5) OVER (PARTITION BY g), datasketches_hll(x) OVER () FROM "
                     "(SELECT column1 AS x, column2 AS g FROM VALUES (1, 1), (3, 1)) LIMIT 1")
    assert "OVER (PARTITION BY g) AS DOUBLE)" in t.duckdb_sql and "OVER () AS DOUBLE)" in t.duckdb_sql
    median, distinct = duckdb.connect().execute(t.duckdb_sql).fetchone()
    assert isinstance(median, float) and distinct == 2.0


@pytest.mark.parametrize("code", ["SELECT hll(*) FROM t", "SELECT hll(a, b) FROM t", "SELECT datasketches_hll() FROM t"])
def test_hll_forms_that_are_not_rewritten_say_so(code):
    (note,) = sf.translate(code).statements[0].notes
    assert note.id == "SFX-014"


@pytest.mark.parametrize("name", sorted(sf._TEXT_FEATURES))
def test_text_search_collation_hash_agg_and_xml_are_refused_by_name(name):
    code = {"COLLATE": "SELECT a FROM t ORDER BY a COLLATE 'en-ci'", "SEARCH": "SELECT search(a, 'x') FROM t",
            "SEARCH_IP": "SELECT search_ip(a, '10.0.0.1') FROM t", "XMLGET": "SELECT xmlget(a, 'b') FROM t"}
    t = sf.translate(code.get(name, f"SELECT {name.lower()}(a) FROM t"))
    (note,) = t.statements[0].notes
    assert not t.runnable and note.id == "SFN-017" and note.title.startswith(name)


def test_octet_length_of_a_column_is_taken_for_text():
    assert sf.translate("SELECT octet_length(a) FROM t").duckdb_sql == "SELECT STRLEN(CAST(a AS TEXT)) FROM t"


def test_try_decoders_give_null_for_an_odd_number_of_hex_digits_and_bad_utf8():
    import duckdb
    t = sf.translate("SELECT try_hex_decode_binary('414'), try_hex_decode_string('FF'), "
                     "try_base64_decode_string('/w==')")
    assert duckdb.connect().execute(t.duckdb_sql).fetchone() == (None, None, None)


@pytest.mark.parametrize("code", ["SELECT try_base64_decode_binary(a, '$') FROM t",
                                  "SELECT try_base64_decode_string(a, '$') FROM t"])
def test_decoders_with_an_alphabet_say_so(code):
    (note,) = sf.translate(code).statements[0].notes
    assert note.id == "SFX-015" and "alphabet" in note.title


@pytest.mark.parametrize("args", ["5, 10, 0, 5", "5, 3, 3, 2"])
def test_width_bucket_with_min_not_below_max_stops_instead_of_answering(args):
    import duckdb
    t = sf.translate(f"SELECT width_bucket({args})")
    with pytest.raises(duckdb.Error, match="min_value below max_value"):
        duckdb.connect().execute(t.duckdb_sql).fetchall()


def test_an_object_constant_reads_its_keys_as_text_and_keeps_them_in_order():
    import duckdb
    t = sf.translate("SELECT {'b': 2, 'a': 1}::string, a FROM (SELECT 'column a' AS a)")
    assert duckdb.connect().execute(t.duckdb_sql).fetchone() == ('{"a":1,"b":2}', "column a")


def test_nested_object_calls_are_rewritten_inner_first_and_bind_each_argument_once():
    import duckdb
    calls = "object_construct('a', 0)"
    for i in range(1, 16):
        calls = f"object_insert({calls}, 'k{i}', {i})"
    t = sf.translate(f"SELECT object_delete({calls}, 'a')::string")
    assert len(t.duckdb_sql) < 40_000  # each level names its object once
    want = "{" + ",".join(f'"k{i}":{i}' for i in sorted(range(1, 16), key=str)) + "}"
    assert duckdb.connect().execute(t.duckdb_sql).fetchone() == (want,)


def test_object_insert_with_an_update_flag_column_errors_only_when_it_is_false():
    import duckdb
    t = sf.translate("SELECT object_insert(object_construct('a', 1), 'a', 2, u)::string FROM "
                     "(SELECT column1 AS u FROM VALUES (TRUE))")
    assert duckdb.connect().execute(t.duckdb_sql).fetchone() == ('{"a":2}',)
    t = sf.translate("SELECT object_insert(object_construct('a', 1), 'a', 2, u) FROM (SELECT FALSE AS u)")
    with pytest.raises(duckdb.Error, match="Duplicate field key a"):
        duckdb.connect().execute(t.duckdb_sql).fetchall()


@pytest.mark.parametrize("code,error", [
    ("SELECT to_object(parse_json('[1]'))", "TO_OBJECT needs an OBJECT"),
    ("SELECT arrays_to_object(['a'], [1, 2])", "unequal lengths"),
    ("SELECT object_agg(k, v) FROM (SELECT column1 AS k, column2 AS v FROM VALUES ('a', 1), ('a', 2))", "unique"),
])
def test_object_functions_stop_where_snowflake_does(code, error):
    import duckdb
    with pytest.raises(duckdb.Error, match=error):
        duckdb.connect().execute(sf.translate(code).duckdb_sql).fetchall()


@pytest.mark.parametrize("code", ["SELECT object_agg(DISTINCT k, v) FROM t", "SELECT objectagg(k, v) FROM t",
                                  "SELECT object_pick(o) FROM t", "SELECT arrays_to_object(a) FROM t"])
def test_object_forms_that_are_not_rewritten_say_so(code):
    (note,) = sf.translate(code).statements[0].notes
    assert note.id == "SFX-016"


def test_object_pick_with_one_key_column_reads_it_as_a_key():
    import duckdb
    t = sf.translate("SELECT object_pick(parse_json('{\"a\": 1, \"b\": 2}'), k)::string, "
                     "object_pick(parse_json('{\"a\": 1, \"b\": 2}'), parse_json('[\"b\"]'))::string "
                     "FROM (SELECT 'a' AS k)")
    assert duckdb.connect().execute(t.duckdb_sql).fetchone() == ('{"a":1}', '{"b":2}')


@pytest.mark.parametrize("code,rule", [
    ("SELECT to_decfloat('1.5')", "SFN-018"), ("SELECT try_to_decfloat(a) FROM t", "SFN-018"),
    ("SELECT CAST(a AS DECFLOAT) FROM t", "SFN-018"),
    (("SELECT * FROM t MATCH_RECOGNIZE (ORDER BY a MEASURES FIRST(a) AS f ONE ROW PER MATCH PATTERN (x+) "
     "DEFINE x AS a > 0)"), "SFN-019"),
    ("INSERT OVERWRITE INTO t SELECT * FROM s", "SFN-020"),
])
def test_decfloat_match_recognize_and_insert_overwrite_are_refused_by_name(code, rule):
    t = sf.translate(code)
    assert not t.runnable and t.statements[0].notes[0].id == rule


def test_a_plain_insert_still_runs():
    assert sf.translate("INSERT INTO t SELECT * FROM s").duckdb_sql == "INSERT INTO t SELECT * FROM s"


def test_time_of_a_timestamp_is_its_time_of_day():
    import duckdb
    t = sf.translate("SELECT time(ts) FROM (SELECT '2024-01-01 10:20:30'::timestamp AS ts)")
    assert str(duckdb.connect().execute(t.duckdb_sql).fetchone()[0]) == "10:20:30"


@pytest.mark.parametrize("code", [
    "SELECT TIME(CURRENT_TIMESTAMP)",
    "SELECT TIME(CURRENT_TIMESTAMP())",
    "SELECT TIME(SYSDATE())",
    "SELECT TIME('2024-01-01 10:20:30+00:00'::TIMESTAMP_TZ)",
    "SELECT TIME('2024-01-01 10:20:30'::TIMESTAMP_LTZ)",
    "SELECT TIME(CONVERT_TIMEZONE('UTC', '2024-01-01 10:20:30+00:00'::TIMESTAMP_TZ))",
])
def test_time_of_a_zoned_timestamp_runs(code):
    import duckdb
    t = sf.translate(code)
    assert t.runnable
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    value = con.execute(t.duckdb_sql).fetchone()[0]
    assert type(value).__name__ == "time"


def test_time_of_a_timestamp_tz_is_the_local_time():
    import duckdb
    con = duckdb.connect()
    con.execute("SET TimeZone = 'Etc/GMT-2'")  # UTC+02:00, the text's own offset, as Snowflake keeps it
    t = sf.translate("SELECT TIME('2024-01-01 10:20:30+02:00'::TIMESTAMP_TZ)")
    assert str(con.execute(t.duckdb_sql).fetchone()[0]) == "10:20:30"
    # in another zone Snowflake still gives 10:20:30 (the value keeps +02:00); DuckDB would give that zone's
    # time, so it stops by name (SFN-033)
    con.execute("SET TimeZone = 'UTC'")
    with pytest.raises(duckdb.Error, match="SFN-033 TIMESTAMP_TZ with another UTC offset"):
        con.execute(t.duckdb_sql).fetchone()
_EVENTS = ("SELECT * FROM VALUES ('a', 1, 5), ('a', 2, NULL), ('a', 3, 5), ('a', 4, 7), ('b', 1, NULL), ('b', 2, 9) "
           "AS t(p, o, x)")


def _rows(code):
    t = sf.translate(code)
    assert t.runnable, t.first_refusal()
    return duckdb.connect().execute(t.duckdb_sql).fetchall()


def test_sfx018_change_event_keeps_the_select_star_and_where():
    rows = _rows(f"SELECT *, CONDITIONAL_CHANGE_EVENT(x) OVER (PARTITION BY p ORDER BY o) AS e "
                 f"FROM ({_EVENTS}) AS s WHERE o > 1 ORDER BY p, o")
    # the WHERE drops o = 1 before the events are counted, and SELECT * shows only the table's columns
    assert rows == [("a", 2, None, 0), ("a", 3, 5, 0), ("a", 4, 7, 1), ("b", 2, 9, 0)]


def test_sfx018_a_null_is_not_a_change_on_either_side():
    rows = _rows(f"SELECT p, o, CONDITIONAL_CHANGE_EVENT(x) OVER (PARTITION BY p ORDER BY o) FROM ({_EVENTS}) "
                 "ORDER BY p, o")
    assert [r[2] for r in rows] == [0, 0, 0, 1, 0, 0]


def test_sfx018_true_event_counts_true_rows_and_skips_null():
    rows = _rows(f"SELECT CONDITIONAL_TRUE_EVENT(x > 4) OVER (PARTITION BY p ORDER BY o) FROM ({_EVENTS}) "
                 "ORDER BY p, o")
    assert [r[0] for r in rows] == [1, 1, 2, 3, 0, 1]


def test_sfx018_fill_functions():
    rows = _rows(f"SELECT INTERPOLATE_FFILL(x) OVER (PARTITION BY p ORDER BY o), "
                 f"INTERPOLATE_BFILL(x) OVER (PARTITION BY p ORDER BY o) FROM ({_EVENTS}) ORDER BY p, o")
    assert rows == [(5, 5), (5, 5), (5, 5), (7, 7), (None, 9), (9, 9)]


@pytest.mark.parametrize("order, expected", [
    ("o", 6.0),
    ("o * 1.5", 6.0),
    ("'2025-01-0' || o::varchar::date", 6.0),
    ("('2025-01-01 10:0' || o::varchar || ':00')::timestamp", 6.0),
])
def test_sfx018_linear_by_a_number_date_or_timestamp(order, expected):
    code = (f"SELECT INTERPOLATE_LINEAR(x) OVER (ORDER BY {order}) FROM VALUES (1, 4), (2, NULL), (3, 8), (4, NULL) "
            "AS t(o, x) ORDER BY o")
    code = code.replace("'2025-01-0' || o::varchar::date", "('2025-01-0' || o::varchar)::date")
    t = sf.translate(code)
    assert [n.id for s in t.statements for n in s.notes] == ["SFD-011"]
    assert [r[0] for r in duckdb.connect().execute(t.duckdb_sql).fetchall()] == [4.0, expected, 8.0, None]


@pytest.mark.parametrize("code", [
    "SELECT INTERPOLATE_BFILL(x) OVER (ORDER BY o ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) FROM t",
    "SELECT CONDITIONAL_TRUE_EVENT(x) OVER (PARTITION BY p) FROM t",
    "SELECT INTERPOLATE_LINEAR(x) OVER (ORDER BY a, b) FROM t",
    "SELECT INTERPOLATE_FFILL(x, y) OVER (ORDER BY o) FROM t",
    "SELECT CONDITIONAL_CHANGE_EVENT(x) OVER (ORDER BY o) FROM t JOIN u ON t.id = u.id",
    "SELECT p, CONDITIONAL_CHANGE_EVENT(x) OVER (ORDER BY p) FROM t GROUP BY p, x",
])
def test_sfx018_refuses_what_it_cant_do_faithfully(code):
    t = sf.translate(code)
    assert not t.runnable and t.statements[0].notes[0].id == "SFX-018"


def test_sfx018_true_event_without_a_window_inside_runs_after_a_join():
    rows = _rows("SELECT CONDITIONAL_TRUE_EVENT(a.x > 1) OVER (ORDER BY a.o) FROM VALUES (1, 2), (2, 0) AS a(o, x) "
                 "JOIN VALUES (1), (2) AS b(o) ON a.o = b.o ORDER BY a.o")
    assert rows == [(1,), (1,)]


def test_sfx018_qualified_columns_still_resolve():
    rows = _rows("SELECT t.o, CONDITIONAL_CHANGE_EVENT(t.x) OVER (ORDER BY t.o) FROM VALUES (1, 2), (2, 3) AS t(o, x) "
                 "ORDER BY t.o")
    assert rows == [(1, 0), (2, 1)]


def test_sfx018_lag_and_running_count_read_tied_rows_in_one_order():
    sql = sf.translate("SELECT CONDITIONAL_CHANGE_EVENT(x) OVER (PARTITION BY p ORDER BY o), CONDITIONAL_TRUE_EVENT("
                       "LAG(x) OVER (PARTITION BY p ORDER BY o) > 1) OVER (PARTITION BY p ORDER BY o) FROM s").duckdb_sql
    assert "LAG((x)) OVER (PARTITION BY p ORDER BY __sf_place0)" in sql
    assert "LAG(x) OVER (PARTITION BY p ORDER BY __sf_place1)" in sql
    rows = _rows("SELECT CONDITIONAL_CHANGE_EVENT(x) OVER (ORDER BY o) FROM VALUES (1, 'a'), (1, 'b'), (1, 'a'), "
                 "(2, 'a') AS t(o, x)")
    assert max(r[0] for r in rows) in (1, 2, 3)  # tied rows come in some order, but one order for LAG and count


@pytest.mark.parametrize(("sql", "why"), [
    ("SELECT hash(10)", "Snowflake's own hash"), ("SELECT hash(a, b) FROM t", "Snowflake's own hash"),
    ("SELECT uniform(1, 10, 42)", "UNIFORM with a generator other than RANDOM()"),
    ("SELECT uniform(1, 10, random(12))", "UNIFORM with a generator other than RANDOM()"),
    ("SELECT uniform(1, 10, a) FROM t", "UNIFORM with a generator other than RANDOM()"),
])
def test_snowflake_own_hash_and_a_uniform_series_are_refused(sql, why):
    translation = sf.translate(sql)
    assert not translation.runnable and why in translation.first_refusal()


def test_uniform_from_random_still_runs():
    assert sf.translate("SELECT uniform(1, 10, random())").runnable


def _first(sql):
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    return duckdb.connect().sql(translation.duckdb_sql).fetchall()[0][0]


def test_alter_session_sets_the_time_zone_and_refuses_other_parameters():
    translation = sf.translate("ALTER SESSION SET TIMEZONE = 'America/New_York'")
    assert translation.runnable and translation.duckdb_sql == "SET TimeZone = 'America/New_York'"
    for sql in ("ALTER SESSION UNSET TIMESTAMP_OUTPUT_FORMAT", "ALTER SESSION SET TIMESTAMP_OUTPUT_FORMAT = 'x'"):
        translation = sf.translate(sql)  # no longer an AttributeError
        assert not translation.runnable and "Session parameters" in translation.first_refusal()


def test_array_distinct_keeps_first_places_and_flatten_of_a_null_is_null():
    assert _first("SELECT array_distinct([3, 1, 3, NULL, 2, NULL, 1])") == [3, 1, None, 2]
    # the array is worked out once: random values aren't drawn again for each element
    assert _first("SELECT array_size(array_distinct(array_construct(random(), random(), random())))") == 3
    assert _first("SELECT array_flatten([[1], NULL])") is None
    assert _first("SELECT array_flatten([[1], [NULL]])") == [1, None]


def test_array_insert_past_the_end_fills_the_gap_and_keeps_its_places_before():
    assert _first("SELECT array_insert(array_construct(0, 1), 4, 9)") == [0, 1, None, None, 9]
    assert _first("SELECT array_insert(array_construct(0, 1), 1, 9)") == [0, 9, 1]
    assert _first("SELECT array_insert(array_construct(0, 1), 2, 9)") == [0, 1, 9]
    assert _first("SELECT array_insert(NULL::ARRAY, 2, 9)") is None


def test_sample_row_and_block_are_bernoulli_and_system():
    sql = sf.translate("SELECT * FROM t SAMPLE ROW (0); SELECT * FROM t SAMPLE BLOCK (3) SEED (82)").duckdb_sql
    assert "BERNOULLI (0 PERCENT)" in sql and "SYSTEM (3 PERCENT) REPEATABLE (82)" in sql
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT * FROM range(5) r(i)")
    assert con.execute(sf.translate("SELECT count(*) FROM t SAMPLE ROW (0)").duckdb_sql).fetchone() == (0,)
    assert con.execute(sf.translate("SELECT count(*) FROM t SAMPLE BLOCK (100)").duckdb_sql).fetchone() == (5,)


def test_get_of_an_object_key_cast_to_text_has_no_json_quotes():
    assert _first("SELECT get(object_construct('a', 'x'), 'a')::VARCHAR") == "x"
    assert _first("SELECT strip_null_value(parse_json('{\"c\": \"3\"}'):c)::VARCHAR") == "3"
    assert _first("SELECT get([2.5, 1], 0)") == 2.5  # an array's element is left as it is


def test_the_e_parameter_gives_the_first_group_unless_one_is_named():
    assert _first("SELECT regexp_substr('the best', 'the\\\\W+(\\\\w+)', 1, 1, 'e')") == "best"
    assert _first("SELECT regexp_substr('ab', '(a)(b)', 1, 1, 'e', 2)") == "b"
    assert _first("SELECT regexp_substr('ab', '(a)(b)')") == "ab"  # no 'e': the whole match
    assert _first("SELECT regexp_substr('ab', 'a.', 1, 1, 'e')") == "ab"  # no group in the pattern: kept as before


def test_a_whole_number_in_a_string_is_read_by_its_size():
    import datetime as dt
    for text in ("31536000", "31536000000", "31536000000000", "31536000000000000"):
        assert _first(f"SELECT to_timestamp('{text}')") == dt.datetime(1971, 1, 1), text
    assert _first("SELECT to_timestamp(31536000)") == dt.datetime(1971, 1, 1)  # a number is seconds


def test_timestamp_tz_from_parts_is_in_the_session_zone():
    con = duckdb.connect()
    for statement in sf.translate("ALTER SESSION SET TIMEZONE = 'America/New_York'; "
                                  "SELECT date_part(epoch_second, timestamp_tz_from_parts(2013, 4, 5, 12, 0, 0))"
                                  ).statements:
        last = con.execute(statement.duckdb_sql)
    assert last.fetchall()[0][0] == 1365177600  # 12:00 -0400


@pytest.mark.parametrize("pattern", ["(" * 1000 + "a" + ")" * 1000, "a{99999999999999999999}"])
def test_a_pattern_python_cannot_read_still_translates(pattern):
    translation = sf.translate(f"SELECT regexp_substr('a', '{pattern}', 1, 1, 'e')")
    assert translation.runnable, translation.first_refusal()


def test_get_reads_a_to_variant_value_as_json():
    assert _first("SELECT get(to_variant(object_construct('t', 31.5)), 't')::FLOAT") == 31.5
    assert _first("SELECT to_variant(object_construct('a', 'x')):a::VARCHAR") == "x"
    assert _first("SELECT get(object_construct('a', 2)::VARIANT, 'a')::INT") == 2
    assert _first("SELECT get(to_variant(array_construct(1, 2)), 1)::INT") == 2


def test_an_array_column_holds_arrays_and_an_object_column_holds_json():
    rows = _rows("CREATE TABLE t (a ARRAY, o OBJECT); "
                 "INSERT INTO t SELECT ARRAY_CONSTRUCT(1, 2), OBJECT_CONSTRUCT('k', 1); "
                 "UPDATE t SET a = ARRAY_APPEND(a, 'x'); "
                 "SELECT a, ARRAY_SIZE(a), TYPEOF(a[0]), TYPEOF(a[2]), o:k::INT FROM t")
    assert rows == [([1, 2, "x"], 3, "INTEGER", "VARCHAR", 1)]


def test_a_cast_to_array_reads_a_list_or_a_json_array_and_object_is_json():
    assert _first("SELECT PARSE_JSON('[1, \"a\"]')::ARRAY") == [1, "a"]
    assert _first("SELECT ARRAY_CONSTRUCT(1, 2)::ARRAY") == [1, 2]
    assert _first("SELECT CAST(NULL AS ARRAY)") is None
    assert _first("SELECT TYPEOF(CAST(NULL AS OBJECT))") is None
    # a structured ARRAY(INT) keeps its element type
    assert "INT[]" in sf.translate("SELECT CAST(NULL AS ARRAY(INT))").duckdb_sql


def test_to_json_of_an_array_column_keeps_each_element_type():
    rows = _rows("CREATE TABLE t (a ARRAY, n INT); INSERT INTO t SELECT ARRAY_CONSTRUCT(1, 2), 1; "
                 "INSERT INTO t SELECT NULL, 2; UPDATE t SET a = ARRAY_APPEND(a, 'five') WHERE n = 1; "
                 "SELECT TO_JSON(a), TO_JSON(ARRAY_APPEND(a, 3)), TO_JSON(OBJECT_CONSTRUCT('k', a)), TO_JSON(n) "
                 "FROM t ORDER BY n")
    assert rows == [('[1,2,"five"]', '[1,2,"five",3]', '{"k":[1,2,"five"]}', "1"), (None, None, "{}", "2")]
    assert _first("SELECT TO_JSON(ARRAY_CONSTRUCT(1, 2)::ARRAY)") == "[1,2]"
    # a list inside an object inside an object too, and the outer object isn't copied for the check
    nested = "SELECT TO_JSON(OBJECT_CONSTRUCT('k', OBJECT_CONSTRUCT('j', a))) FROM t"
    assert sf.translate(nested).duckdb_sql.upper().count("MAP_FROM_ENTRIES") == 1
    assert _rows("CREATE TABLE t (a ARRAY); INSERT INTO t SELECT ARRAY_CONSTRUCT(1, 'x'::VARIANT); "
                 + nested) == [('{"k":{"j":[1,"x"]}}',)]


_VARIANT_TABLE = ("CREATE TABLE t (id INT, g VARCHAR, v INT, j VARIANT); "
                  "INSERT INTO t SELECT 1, 'a', 10, PARSE_JSON('{\"arr\": [1, 2], \"o\": {\"k\": 1}}'); "
                  "INSERT INTO t SELECT 2, 'a', 20, PARSE_JSON('[5]'); "
                  "INSERT INTO t SELECT 3, 'b', 30, TO_VARIANT('plain'); ")


def test_a_variant_column_reads_its_paths_flattens_and_prints_as_json():
    assert _rows(_VARIANT_TABLE + "SELECT j:arr[1]::INT, j:o.k::INT, GET_PATH(j, 'o.k')::INT FROM t WHERE id = 1") \
        == [(2, 1, 1)]
    assert _rows(_VARIANT_TABLE + "SELECT t.id, f.value::INT FROM t, LATERAL FLATTEN(INPUT => t.j:arr) f "
                 "ORDER BY 1, 2") == [(1, 1), (1, 2)]
    assert _rows(_VARIANT_TABLE + "SELECT f.key FROM t, LATERAL FLATTEN(INPUT => t.j) f WHERE t.id = 1 "
                 "ORDER BY 1") == [("arr",), ("o",)]
    assert _rows(_VARIANT_TABLE + "SELECT j::VARCHAR, TO_VARCHAR(j) FROM t ORDER BY id") == [
        ('{"arr":[1,2],"o":{"k":1}}', '{"arr":[1,2],"o":{"k":1}}'), ("[5]", "[5]"), ("plain", "plain")]
    # ARRAY_SIZE is NULL for a value that isn't an array, as Snowflake's page says
    assert _rows(_VARIANT_TABLE + "SELECT ARRAY_SIZE(j), ARRAY_SIZE(j:arr), ARRAY_SIZE(j:o) FROM t ORDER BY id") \
        == [(None, 2, None), (1, None, None), (None, None, None)]


def test_text_casts_keep_their_column_names_and_text_columns_that_arent_json_still_read():
    translation = sf.translate(
        "CREATE TABLE t (n INT, s VARCHAR, j VARIANT); INSERT INTO t SELECT 1, 'x', PARSE_JSON('{\"a\":[1]}'); "
        "SELECT n::VARCHAR, TRY_CAST(s AS VARCHAR), j::VARCHAR, TO_VARCHAR(s), UPPER(s::VARCHAR), ARRAY_SIZE(j:a), "
        "n::VARCHAR AS named, (SELECT MAX(n::VARCHAR) FROM t) FROM t")
    con = duckdb.connect()
    for statement in translation.statements[:-1]:
        con.execute(statement.duckdb_sql)
    result = con.sql(translation.statements[-1].duckdb_sql)
    assert result.columns == ["CAST(n AS TEXT)", "TRY_CAST(s AS TEXT)", "CAST(j AS TEXT)", "CAST(s AS TEXT)",
                              "UPPER(CAST(s AS TEXT))", "ARRAY_LENGTH(CAST(j AS JSON) -> '$.a')", "named",
                              '(SELECT MAX(CAST(n AS TEXT)) AS "MAX(CAST(n AS TEXT))" FROM t)']
    # 'x' isn't JSON: the VARIANT branch is never taken for it, even when DuckDB works it out for every row
    assert result.fetchall() == [("1", "x", '{"a":[1]}', "x", "X", 1, "1", "1")]


def test_merge_stops_when_a_target_row_matches_several_source_rows_for_an_update():
    setup = _VARIANT_TABLE + "CREATE TABLE s (id INT, v INT); INSERT INTO s VALUES (1, 100), (1, 101), (4, 400); "
    with pytest.raises(duckdb.Error, match="more than one source row"):
        _rows(setup + "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v")
    with pytest.raises(duckdb.Error, match="more than one source row"):
        _rows(setup + "MERGE INTO t USING (SELECT id, v FROM s) AS src ON t.id = src.id "
              "WHEN MATCHED THEN UPDATE SET v = src.v")
    # several deletes don't conflict, as on Snowflake's page, on DuckDB 1.x (the pinned one). DuckDB 2 refuses
    # them itself, stricter than Snowflake: still a stop with nothing changed, never another answer
    delete = setup + "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE; SELECT list(id ORDER BY id) FROM t"
    if duckdb_major() < 2:
        assert _rows(delete) == [([2, 3],)]
    else:
        with pytest.raises(duckdb.Error, match="more than once"):
            _rows(delete)
    assert _rows(setup + "MERGE INTO t AS tt USING (SELECT 1 AS id, 7 AS v) AS x ON tt.id = x.id "
                 "WHEN MATCHED THEN UPDATE SET v = x.v; SELECT v FROM t WHERE id = 1") == [(7,)]


def test_pivot_names_its_columns_with_the_quotes_and_any_is_refused_by_name():
    translation = sf.translate(_VARIANT_TABLE + "SELECT * FROM (SELECT g, v FROM t) PIVOT (SUM(v) FOR g IN ('a', 'b'))")
    con = duckdb.connect()
    for statement in translation.statements[:-1]:
        con.execute(statement.duckdb_sql)
    result = con.sql(translation.statements[-1].duckdb_sql)
    assert result.columns == ["'a'", "'b'"] and result.fetchall() == [(30, 30)]
    for code in ("SELECT * FROM t PIVOT (SUM(v) FOR g IN (ANY ORDER BY g))",
                 "SELECT * FROM t PIVOT (SUM(v) FOR g IN (SELECT g FROM t))"):
        assert sf.translate(code).first_refusal().startswith("Statement 1 runs only in Snowflake: PIVOT on ANY")


def test_session_variables_and_identifier_of_a_variable_the_cell_sets():
    assert _rows("SET x = 5; SELECT $x + 1, $X") == [(6, 5)]
    assert _rows(_VARIANT_TABLE + "SET tn = 't'; SET c = 'v'; SELECT SUM(IDENTIFIER($c)) FROM IDENTIFIER($tn)") \
        == [(60,)]
    assert "IDENTIFIER() of a variable this cell doesn't set" in sf.translate(
        "SELECT * FROM IDENTIFIER($nope)").first_refusal()
    # a value that isn't a plain name stays refused
    assert not sf.translate("SET tn = 't; DROP TABLE x'; SELECT * FROM IDENTIFIER($tn)").runnable


@pytest.mark.parametrize(("sql", "want"), [
    ("SELECT array_construct(1, 'a', NULL)", [1, "a", None]),
    ("SELECT array_construct_compact(NULL, 'a', -1)", ["a", -1]),
    ("SELECT array_insert(array_construct(0, 1), 2, 'x')", [0, 1, "x"]),
    ("SELECT array_prepend(array_construct('a'), 2.5::double)", [2.5, "a"]),
    ("SELECT array_append(array_construct(1, 2), 'a')", [1, 2, "a"])])
def test_an_array_of_text_and_numbers_is_a_list_of_variants(sql, want):
    """SFX-040 (SFN-025 retired): each element keeps its own type, as in Snowflake's array."""
    translation = sf.translate(sql)
    assert translation.runnable and "VARIANT" in translation.duckdb_sql
    assert duckdb.connect().execute(translation.duckdb_sql).fetchone()[0] == want


def test_an_array_of_one_kind_or_from_a_column_still_runs():
    assert sf.translate("SELECT array_construct(1, 2), array_construct('a', NULL), array_append(array_construct(1), x) "
                        "FROM t").runnable


@pytest.mark.parametrize("query", ["SELECT IFF(b = 0 OR c, 0, a / b) FROM t", "SELECT IFF(b = 0 AND NOT c, 0, a / b) FROM t"])
def test_an_iff_that_only_looks_like_div0_keeps_its_division_guard(query):
    # Telling DIV0's form apart used to move the divisor (OR) or the numerator (AND) out of the query.
    sql = sf.translate(query).duckdb_sql
    assert "ELSE a / CASE WHEN b = 0 THEN ERROR('Division by zero') ELSE b END END" in sql


def test_div0_and_div0null_still_divide_without_the_guard():
    sql = sf.translate("SELECT DIV0(a, b), DIV0NULL(a, b) FROM t").duckdb_sql
    assert "ERROR" not in sql and sql.count("ELSE a / b END") == 2


@pytest.mark.parametrize("fn", ["TO_TIMESTAMP_NTZ", "TO_TIMESTAMP"])
def test_to_timestamp_ntz_of_a_column_reads_it_by_its_type(fn):
    # DuckDB has no TO_TIMESTAMP_NTZ, so a column stopped with its catalog error (a literal was rewritten already).
    import datetime as dt
    con = duckdb.connect()
    numbers = con.execute(sf.translate(f"SELECT {fn}(x) FROM (VALUES (1700000000), (-3600), (NULL)) AS t(x)")
                          .duckdb_sql).fetchall()
    assert numbers == [(dt.datetime(2023, 11, 14, 22, 13, 20),), (dt.datetime(1969, 12, 31, 23, 0),), (None,)]
    fraction = con.execute(sf.translate(f"SELECT {fn}(x) FROM (VALUES (1700000000.123456789)) AS t(x)")
                           .duckdb_sql).fetchall()
    assert fraction == [(dt.datetime(2023, 11, 14, 22, 13, 20, 123456),)]
    texts = con.execute(sf.translate(
        f"SELECT {fn}(x) FROM (VALUES ('2024-01-01 10:00:00'), ('1700000000'), ('1700000000123'), "
        "('1700000000123456'), ('1700000000123456789')) AS t(x)").duckdb_sql).fetchall()
    second = dt.datetime(2023, 11, 14, 22, 13, 20)
    assert texts == [(dt.datetime(2024, 1, 1, 10, 0),), (second,), (second.replace(microsecond=123000),),
                     (second.replace(microsecond=123456),), (second.replace(microsecond=123456),)]
    dates = con.execute(sf.translate(f"SELECT {fn}(x) FROM (VALUES (DATE '2024-01-01')) AS t(x)").duckdb_sql).fetchall()
    assert dates == [(dt.datetime(2024, 1, 1),)]
    with pytest.raises(duckdb.Error, match="(?i)timestamp"):
        con.execute(sf.translate(f"SELECT {fn}(x) FROM (VALUES ('nope')) AS t(x)").duckdb_sql).fetchall()
    # Text with a UTC offset: the offset is dropped, as Snowflake's NTZ reads it (DuckDB 1.5.6 stopped, 2.0 dropped it).
    offsets = con.execute(sf.translate(
        f"SELECT {fn}(x) FROM (VALUES ('2024-01-02 03:04:05 +02:00'), ('2024-01-02 03:04:05.5-0700'), "
        "('2024-01-02T03:04:05Z'), ('2024-01-02')) AS t(x)").duckdb_sql).fetchall()
    assert offsets == [(dt.datetime(2024, 1, 2, 3, 4, 5),), (dt.datetime(2024, 1, 2, 3, 4, 5, 500000),),
                       (dt.datetime(2024, 1, 2, 3, 4, 5),), (dt.datetime(2024, 1, 2),)]
    # A TIMESTAMPTZ column: the session time zone's wall clock (DuckDB keeps no offset of its own per value),
    # which is the value's own when its offset is the session zone's (SFN-033 stops any other)
    con.execute("SET TimeZone = 'Etc/GMT-2'")
    zoned = con.execute(sf.translate(
        f"SELECT {fn}(x) FROM (SELECT CAST('2024-01-02 03:04:05+02' AS TIMESTAMPTZ) AS x) AS t").duckdb_sql).fetchall()
    assert zoned == [(dt.datetime(2024, 1, 2, 3, 4, 5),)]


# ---------------------------------------------------------------- 2026.11.20: no answer that differs silently


def test_jarowinkler_similarity_is_refused_by_name():
    # https://docs.snowflake.com/en/sql-reference/functions/jarowinkler_similarity: 'Gute nacht' and
    # 'Ich weis nicht' score 56 there, 'Ich weiß nicht' and 'Ich weiss nicht' 97; DuckDB's gives 57 and 94
    t = sf.translate("SELECT jarowinkler_similarity(s, t) FROM ed")
    assert not t.runnable and t.statements[0].notes[0].id == "SFN-031"
    assert "EDITDISTANCE runs here" in t.first_refusal()
    assert sf.RULES_VERSION == "2026.11.21" and "SFN-031" in sf.docs_table()


@pytest.mark.parametrize("code, want", [
    # https://docs.snowflake.com/en/sql-reference/functions/to_json: TO_JSON(PARSE_JSON('{"b":1,"a":2}')) is {"a":2,"b":1}
    ("""SELECT to_json(parse_json('{"b":1,"a":2}'))""", '{"a":2,"b":1}'),
    ("""SELECT to_json(parse_json('{"food":"bard"}'))""", '{"food":"bard"}'),
    # every level, inside arrays too; an array keeps its order
    ("""SELECT to_json(parse_json('{"b":[3,{"z":1,"y":[]}],"a":{"d":null,"c":"x"}}'))""",
     '{"a":{"c":"x","d":null},"b":[3,{"y":[],"z":1}]}'),
    ("""SELECT to_json(parse_json('[{"b":1,"a":2},{}]'))""", '[{"a":2,"b":1},{}]'),
    # keys that need escaping, and the empty key, sort by the key itself
    ("""SELECT to_json(parse_json('{"k\\\\"q":1,"a b":2,"":3}'))""", '{"":3,"a b":2,"k\\"q":1}'),
    ("SELECT to_json(parse_json('\"s\"'))", '"s"'),
    ("SELECT to_json(NULL)", None),
    ("SELECT to_json(v) FROM (SELECT parse_json('{\"b\":1,\"a\":2}') AS v)", '{"a":2,"b":1}'),
    ("SELECT to_json(object_construct('b', 1, 'a', object_construct('d', 1, 'c', 2)))", '{"a":{"c":2,"d":1},"b":1}'),
])
def test_to_json_sorts_an_objects_keys_at_every_level(code, want):
    t = sf.translate(code)
    assert t.statements[0].verdict == "translated", t.summary()
    assert _first(code) == want


def test_to_json_of_a_column_sorts_each_rows_keys():
    con = duckdb.connect()
    con.execute(sf.translate("CREATE TABLE j (v VARIANT)").duckdb_sql)
    con.execute(sf.translate("""INSERT INTO j SELECT parse_json(s) FROM (SELECT column1 AS s FROM VALUES """
                             """('{"b":1,"a":2}'), ('{"y":{"q":1,"p":2}}'), (NULL))""").duckdb_sql)
    rows = con.execute(sf.translate("SELECT to_json(v) FROM j").duckdb_sql).fetchall()
    assert sorted(rows, key=str) == [('{"a":2,"b":1}',), ('{"y":{"p":2,"q":1}}',), (None,)]


def test_to_timestamp_tz_answers_as_snowflake_or_stops_by_name():
    # https://docs.snowflake.com/en/sql-reference/functions/to_timestamp: in America/Los_Angeles,
    # TO_TIMESTAMP_TZ('2024-04-05 01:02:03') is 2024-04-05 01:02:03.000 -0700
    import datetime as dt
    con = duckdb.connect()
    la = "ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; "
    value = con.execute(sf.translate(la + "SELECT to_timestamp_tz('2024-04-05 01:02:03')").duckdb_sql).fetchone()[0]
    assert value.replace(tzinfo=None) == dt.datetime(2024, 4, 5, 1, 2, 3)
    assert value.utcoffset() == dt.timedelta(hours=-7)
    # of a column (sqlglot left TO_TIMESTAMP_TZ, which DuckDB doesn't have), and its own offset when it is the zone's
    code = ("SELECT to_timestamp_tz(x), try_to_timestamp_tz(x) FROM "
            "(SELECT column1 AS x FROM VALUES ('2024-04-05 01:02:03'), ('2024-04-05T01:02:03-07:00'))")
    t = sf.translate(code)
    assert t.statements[0].verdict == "translated", t.summary()
    rows = con.execute(t.duckdb_sql).fetchall()
    assert {r[0] for r in rows} == {r[1] for r in rows} == {value}
    assert con.execute(sf.translate("SELECT try_to_timestamp_tz('Invalid')").duckdb_sql).fetchone() == (None,)
    # another offset: Snowflake keeps +05:00 with the value (hour 10), DuckDB would show 21:00 the day before
    for stop in ("SELECT hour(to_timestamp_tz('2024-01-01T10:00:00+05:00'))",
                 "SELECT try_to_timestamp_tz(x) FROM (SELECT '2024-01-01 10:00:00Z' AS x)",
                 "SELECT x::timestamp_tz FROM (SELECT '2024-01-01T10:00:00+05:00' AS x)"):
        t = sf.translate(stop)
        assert t.runnable, stop
        with pytest.raises(duckdb.Error, match="SFN-033 TIMESTAMP_TZ with another UTC offset"):
            con.execute(t.duckdb_sql).fetchall()
    # a format with TZH or TZM reads an offset, which DuckDB doesn't keep
    t = sf.translate("SELECT to_timestamp_tz(x, 'YYYY-MM-DD HH24:MI:SS TZH:TZM') FROM t")
    assert not t.runnable and t.statements[0].notes[0].id == "SFN-033"
    # a TIMESTAMP_TZ column keeps its label: what was written into it isn't seen here
    assert [n.id for n in sf.translate("CREATE TABLE z (c TIMESTAMP_TZ)").statements[0].notes] == ["SFD-002"]


def test_a_refusal_written_into_the_query_reads_as_one():
    from fabric_desktop import snowflake_sql_parity as parity
    case = parity.Case("TO_TIMESTAMP_TZ", "Conversion", "SELECT to_timestamp_tz('2024-01-01T10:00:00+05:00')",
                       None, "untranslated", "", "")
    outcome, value = parity.run(case)
    assert outcome == "untranslated" and value.startswith("SFN-033 ")
    assert not sf.stopped_by_name(duckdb.Error("Conversion Error: SFN-033 written by someone"))


def test_unquoted_names_are_stored_in_upper_case():
    # https://docs.snowflake.com/en/sql-reference/identifiers-syntax: unquoted identifiers "are stored and resolved
    # as uppercase characters"; a double-quoted one "is stored and resolved exactly as entered, including case"
    con = duckdb.connect()
    for code in ("CREATE TABLE mytable (c1 INT, \"c2\" INT, MixedCase INT)", "INSERT INTO MyTable VALUES (1, 2, 3)",
                 "CREATE VIEW v AS SELECT c1, c1 AS alias1, \"c2\" AS \"lower\", mytable.mixedcase FROM mytable",
                 "CREATE TABLE copy AS SELECT * FROM mytable", "ALTER TABLE copy ADD COLUMN added INT",
                 "ALTER TABLE copy RENAME COLUMN added TO renamed", "ALTER TABLE copy RENAME TO copy2",
                 "CREATE SCHEMA mine", "CREATE TABLE mine.tmp (x INT)", "CREATE TEMPORARY TABLE tmp2 (y INT)"):
        t = sf.translate(code)
        assert t.statements[0].verdict == "translated", (code, t.summary())
        con.execute(t.duckdb_sql)
    names = con.execute("SELECT table_schema, table_name, string_agg(column_name, ',' ORDER BY ordinal_position) "
                        "FROM information_schema.columns WHERE table_name IN ('MYTABLE', 'V', 'COPY2', 'TMP', 'TMP2') "
                        "GROUP BY ALL ORDER BY 2").fetchall()
    assert names == [("main", "COPY2", "C1,c2,MIXEDCASE,RENAMED"), ("main", "MYTABLE", "C1,c2,MIXEDCASE"),
                     ("MINE", "TMP", "X"), ("main", "TMP2", "Y"), ("main", "V", "C1,ALIAS1,lower,MIXEDCASE")]
    # a query finds them by any case, as in Snowflake, and SELECT * gives the stored names
    result = con.execute(sf.translate("SELECT * FROM mytable WHERE c1 = 1").duckdb_sql)
    assert [d[0] for d in result.description] == ["C1", "c2", "MIXEDCASE"]
    # a column made by an expression with no alias keeps DuckDB's name, and says so
    t = sf.translate("CREATE TABLE sums AS SELECT c1 + 1 FROM mytable")
    assert [n.id for n in t.statements[0].notes] == ["SFD-001"]
