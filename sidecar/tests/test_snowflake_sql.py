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
from fabric_desktop import snowflake_sql as sf
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import check_cell
from fabric_desktop.engines.contract import CellRequest, SessionConfig

SETUP = [
    "CREATE TABLE t(id INT, grp VARCHAR, amt DOUBLE, ts TIMESTAMP, v JSON)",
    """INSERT INTO t VALUES (1,'a',10,'2026-01-01 10:00','{"k":{"x":1},"arr":[1,2]}'),"""
    """(2,'a',20,'2026-01-02','{"k":{"x":2},"arr":[3]}'),(3,'b',5,'2026-01-03','{"k":{"x":3},"arr":[]}')""",
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
    ("LATERAL FLATTEN", "select t.id, f.value from t, lateral flatten(input => v:arr) f", "difference", ["SFD-005"]),
    ("TABLE(FLATTEN)", "select t.id, f.value from t, table(flatten(input => parse_json(t.v):arr)) f",
     "difference", ["SFD-005"]),
    ("OBJECT_CONSTRUCT with NULL", "select object_construct('a', 1, 'b', null)", "translated", []),
    ("ARRAY_AGG WITHIN GROUP", "select grp, array_agg(id) within group (order by id) from t group by grp",
     "translated", []),
    ("LISTAGG", "select grp, listagg(id, ',') within group (order by id) from t group by grp", "translated", []),
    ("TRY_TO_NUMBER", "select try_to_number('12x'), try_cast('5' as int)", "translated", []),
    ("SPLIT_PART/REGEXP_SUBSTR", "select split_part('a-b-c','-',2), regexp_substr('abc123','[0-9]+')",
     "translated", []),
    ("MERGE", "merge into t using (select 1 id, 99.0 amt) s on t.id=s.id when matched then update set amt=s.amt "
              "when not matched then insert (id, amt) values (s.id, s.amt)", "translated", []),
    ("CREATE OR REPLACE TABLE AS", "create or replace table t2 as select * from t", "difference", ["SFD-001"]),
    ("Time travel AT", "select * from t at(offset => -60)", "not_translatable", ["SFN-006"]),
    ("GENERATOR/SEQ4", "select seq4() from table(generator(rowcount => 3))", "translated", []),
    ("UNPIVOT", "select * from (select id, amt, amt*2 as amt2 from t) unpivot (val for col in (amt, amt2))",
     "translated", []),
    ("CONNECT BY", "select id from t start with id = 1 connect by prior id = id - 1", "translated", []),
    ("EXECUTE IMMEDIATE", "execute immediate 'select 1'", "not_translatable", ["SFN-005"]),
    ("DDL VARIANT/NUMBER/TIMESTAMP_TZ",
     "create table s(a variant, b number(38,0), c timestamp_ltz, d timestamp_tz, e string)",
     "difference", ["SFD-001", "SFD-002", "SFD-004"]),
    ("TIMESTAMP_TZ literal with offset", "select '2026-01-01 10:00:00 +05:00'::timestamp_tz",
     "not_translatable", ["SFN-013"]),
    ("Integer division 7/2", "select 7/2, 7 % 2", "difference", ["SFD-003"]),
    ("Quoted alias", 'select 1 as "Mixed", 2 as plain', "translated", []),
    ("Unquoted identifier case", "create table up_test(MyCol int)", "difference", ["SFD-001"]),
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


@pytest.mark.parametrize("column", ["seq", "this"])
def test_sfx001_flatten_columns_without_an_equivalent_are_refused(column):
    t = sf.translate(f"select f.{column} from t, lateral flatten(input => v:arr) f")
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
    ("create table x(MyCol int)", "SFD-001"),
    ("select '2026-01-01'::timestamp_tz", "SFD-002"),
    ("select amt / 2 from t", "SFD-003"),
    ("select current_timestamp()", "SFD-004"),
    ("select f.value from t, lateral flatten(input => v:arr) f", "SFD-005"),
    ("select date_trunc('month', ts) from t", "SFD-006"),
    ("select dateadd(week, 1, ts) from t", "SFD-006"),
    ("select typeof(v) from t", "SFD-007"),
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
    assert round(con.execute(sql).fetchone()[0], 9) == 3936.385096389  # Snowflake's docs example
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
