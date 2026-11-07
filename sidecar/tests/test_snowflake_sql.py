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
    got = [r[0] for r in con.execute(report.duckdb_sql).fetchall()]
    # A fraction or an exponent in a column: DuckDB may have written it again, so it isn't named.
    assert got == ["NULL_VALUE", "BOOLEAN", "INTEGER", "VARCHAR", "ARRAY", "OBJECT", None, "INTEGER",
                   "DECIMAL or DOUBLE", "DECIMAL or DOUBLE"]


@pytest.mark.parametrize("code, want", [
    ("""select typeof(v:a) from (select parse_json('{"a": 1.912e2}') as v)""", "DECIMAL or DOUBLE"),  # 191.2 here
    ("""select typeof(v:a) from (select parse_json('{"a": 10}') as v)""", "INTEGER"),
    ("select typeof('2026-01-01 10:00'::timestamp_ltz)", "TIMESTAMP WITH TIME ZONE"),  # LTZ or TZ: DuckDB's name
])
def test_typeof_never_gives_a_confident_wrong_name(code, want):
    (report,) = sf.translate(code).statements
    assert "SFD-007" in [n.id for n in report.notes]
    assert duckdb.connect().execute(report.duckdb_sql).fetchone()[0] == want


def test_typeof_reads_its_argument_as_a_tree_and_the_rule_is_listed():
    sql = sf.translate("select typeof(v:k.x) from t").duckdb_sql
    assert "LAMBDA" not in sql.upper() and "->" in sql  # a path stays a path (#1252)
    assert "VARIANT" not in sf.translate("select typeof('a'::variant)").duckdb_sql
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


def test_the_decimal_family_is_labelled_and_the_rest_is_not():
    assert [n.id for n in _one("SELECT is_decimal(parse_json('1.5'))")[1].notes] == ["SFD-008"]
    assert [n.id for n in _one("SELECT as_number(parse_json('1.5'), 4, 1)")[1].notes] == ["SFD-008"]
    assert _one("SELECT is_integer(parse_json('1')), as_double(parse_json('1.5'))")[1].notes == ()


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
