"""Snowflake SQL on this PC, rules 2026.11.21: a result's column names (SFX-031), writes to a TIMESTAMP_TZ
column (SFX-032), and TYPEOF of a timestamp with a time zone (SFX-009, SFN-035). Every expected value is one
docs.snowflake.com shows; the page is named at each test."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf
from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig


def _run(code: str, con: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyRelation:
    con = con or duckdb.connect()
    translation = sf.translate(code)
    assert translation.runnable, translation.first_refusal()
    *before, last = translation.statements
    for statement in before:
        con.execute(statement.duckdb_sql)
    return con.sql(last.duckdb_sql)


def _columns(code: str) -> list[str]:
    return list(_run(code).columns)


# ---------------------------------------------------------------- SFX-031: result column names

# sql/select: the page's employee_table and department_table (its rows don't change a header)
_EMPLOYEES = ("CREATE TABLE employee_table (employee_ID INTEGER, last_name VARCHAR, first_name VARCHAR, "
              "department_ID INTEGER); CREATE TABLE department_table (department_ID INTEGER, "
              "department_name VARCHAR); ")


@pytest.mark.parametrize("query, header", [
    ("SELECT pi() * 2.0 * 2.0 AS area_of_circle", ["AREA_OF_CIRCLE"]),
    (_EMPLOYEES + "SELECT last_name FROM employee_table WHERE employee_ID = 101", ["LAST_NAME"]),
    (_EMPLOYEES + "SELECT department_name, last_name, first_name FROM employee_table INNER JOIN department_table "
     "ON employee_table.department_ID = department_table.department_ID ORDER BY department_name, last_name, "
     "first_name", ["DEPARTMENT_NAME", "LAST_NAME", "FIRST_NAME"]),
    (_EMPLOYEES + "SELECT * FROM employee_table", ["EMPLOYEE_ID", "LAST_NAME", "FIRST_NAME", "DEPARTMENT_ID"]),
    (_EMPLOYEES + "SELECT * EXCLUDE department_id FROM employee_table", ["EMPLOYEE_ID", "LAST_NAME", "FIRST_NAME"]),
    (_EMPLOYEES + "SELECT * RENAME department_id AS department FROM employee_table",
     ["EMPLOYEE_ID", "LAST_NAME", "FIRST_NAME", "DEPARTMENT"]),
    (_EMPLOYEES + "SELECT * RENAME (department_id AS department, employee_id AS id) FROM employee_table",
     ["ID", "LAST_NAME", "FIRST_NAME", "DEPARTMENT"]),
    (_EMPLOYEES + "SELECT * EXCLUDE first_name RENAME (department_id AS department, employee_id AS id) "
     "FROM employee_table", ["ID", "LAST_NAME", "DEPARTMENT"]),
    (_EMPLOYEES + "SELECT * REPLACE ('DEPT-' || department_id AS department_id) FROM employee_table",
     ["EMPLOYEE_ID", "LAST_NAME", "FIRST_NAME", "DEPARTMENT_ID"]),
])
def test_select_names_its_columns_as_snowflakes_select_page_shows(query, header):
    assert _columns(query) == header


def test_values_names_its_columns_column1_and_so_on_as_the_values_page_shows():
    assert _columns("SELECT * FROM (VALUES (1, 'one'), (2, 'two'), (3, 'three'))") == ["COLUMN1", "COLUMN2"]
    assert _columns("SELECT column1 FROM (VALUES (1, 'one'), (2, 'two'), (3, 'three'))") == ["COLUMN1"]


def test_flatten_names_its_columns_as_its_output_section_lists_them():
    # SEQ isn't rewritten (SFX-001); the others are named as Snowflake names them
    assert _columns("SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[1, 77]'))) f") \
        == ["KEY", "PATH", "INDEX", "VALUE", "THIS"]


def test_a_column_and_an_alias_are_upper_case_as_the_is_timestamp_and_datetime_pages_show():
    # is_timestamp: SELECT v, TYPEOF(v) AS type FROM vardttm has the columns V and TYPE
    assert _columns("CREATE TABLE vardttm (v VARIANT); SELECT v, TYPEOF(v) AS type FROM vardttm") == ["V", "TYPE"]
    # data-types-datetime: SELECT ts, HOUR(ts) FROM ts_test names its first column TS
    assert _columns("CREATE TABLE ts_test (ts TIMESTAMP_NTZ); SELECT ts, HOUR(ts) FROM ts_test")[0] == "TS"


def test_a_quoted_alias_keeps_its_case_and_names_still_resolve_by_any_case():
    # identifiers-syntax: a double-quoted identifier "is stored and resolved exactly as entered, including case"
    assert _columns('SELECT 1 AS "x", 2 AS "MixedCase"') == ["x", "MixedCase"]
    rel = _run("WITH c AS (SELECT 1 AS id) SELECT id FROM (SELECT * FROM c) AS s(id) WHERE ID = 1 ORDER BY Id")
    assert rel.columns == ["ID"] and rel.fetchall() == [(1,)]


def test_a_snowflake_cell_shows_upper_case_headers():
    session = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    try:
        result = session.execute(CellRequest("SELECT pi() * 2.0 * 2.0 AS area_of_circle", "sql", "snowflake"))
        assert result.status == "ok" and result.outputs[-1]["columns"] == ["AREA_OF_CIRCLE"]
    finally:
        session.close()


# ---------------------------------------------------------------- SFX-032: writes to a TIMESTAMP_TZ column

# data-types-datetime's TIMESTAMP_TZ example, in its America/Los_Angeles session: '2024-01-01 16:00:00' is
# 2024-01-01 16:00:00.000 -0800, HOUR 16
_TZ_TABLE = "CREATE OR REPLACE TABLE ts_test(ts TIMESTAMP_TZ); ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; "


@pytest.mark.parametrize("write", [
    "INSERT INTO ts_test VALUES('2024-01-01 16:00:00')",
    "INSERT INTO ts_test (ts) SELECT '2024-01-01 16:00:00'",
    "INSERT INTO ts_test SELECT * FROM (SELECT '2024-01-01 16:00:00')",
    "INSERT INTO ts_test VALUES('2000-01-01 00:00:00'); UPDATE ts_test SET ts = '2024-01-01 16:00:00'",
    ("MERGE INTO ts_test USING (SELECT 1 AS k) ON FALSE WHEN NOT MATCHED THEN INSERT (ts) VALUES "
    "('2024-01-01 16:00:00')"),
])
def test_text_with_no_offset_is_stored_in_the_session_zone(write):
    assert _run(_TZ_TABLE + write + "; SELECT HOUR(ts), date_part(epoch_second, ts) FROM ts_test").fetchall() \
        == [(16, 1704153600)]


@pytest.mark.parametrize("write", [
    "INSERT INTO ts_test VALUES('2024-01-02T16:00:00+00:00')",
    "INSERT INTO ts_test SELECT '2024-01-02T16:00:00+00:00'",
    "INSERT INTO ts_test SELECT * FROM (SELECT '2024-01-02T16:00:00+00:00') UNION ALL SELECT '2024-01-01 16:00:00'",
    "INSERT INTO ts_test VALUES('2000-01-01 00:00:00'); UPDATE ts_test SET ts = '2024-01-02T16:00:00+00:00'",
    ("MERGE INTO ts_test USING (SELECT 1 AS k) ON FALSE WHEN NOT MATCHED THEN INSERT VALUES "
    "('2024-01-02T16:00:00+00:00')"),
])
def test_text_with_another_offset_stops_by_name_where_it_was_stored_without_it(write):
    # Snowflake keeps +0000 with the value (data-types-datetime); DuckDB would keep only the instant
    with pytest.raises(duckdb.Error) as raised:
        _run(_TZ_TABLE + write + "; SELECT ts FROM ts_test").fetchall()
    assert sf.stopped_by_name(raised.value) and "SFN-033" in str(raised.value)


def test_the_pages_own_text_with_a_spaced_offset_is_refused_by_name():
    translation = sf.translate(_TZ_TABLE + "INSERT INTO ts_test VALUES('2024-01-02 16:00:00 +00:00')")
    assert [n.id for n in translation.statements[-1].notes] == ["SFN-013"] and not translation.runnable


def test_a_value_that_isnt_text_still_carries_the_offset_label():
    translation = sf.translate(_TZ_TABLE + "CREATE TABLE src (s TIMESTAMP_TZ); INSERT INTO ts_test SELECT s FROM src")
    write = translation.statements[-1]
    assert write.verdict == "difference" and [n.id for n in write.notes] == ["SFD-002"]
    assert sf.translate("INSERT INTO ts_test VALUES (NULL)", dict(translation.tables)).statements[0].verdict \
        == "translated"


def test_an_ltz_column_reads_an_offset_as_the_datetime_page_shows():
    # TIMESTAMP_LTZ example: HOUR 16 and 8 in America/Los_Angeles, then 19 and 11 in America/New_York
    ltz = ("CREATE OR REPLACE TABLE ts_test(ts TIMESTAMP_LTZ); ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; "
           "INSERT INTO ts_test VALUES('2024-01-01 16:00:00'); INSERT INTO ts_test VALUES('2024-01-02 16:00:00 +00:00'); ")
    con = duckdb.connect()
    assert _run(ltz + "SELECT HOUR(ts) FROM ts_test ORDER BY ts", con).fetchall() == [(16,), (8,)]
    assert _run("ALTER SESSION SET TIMEZONE = 'America/New_York'; SELECT HOUR(ts) FROM ts_test ORDER BY ts",
                con).fetchall() == [(19,), (11,)]


def test_a_session_remembers_the_tables_snowflake_cells_made():
    session = registry.adapter("local-duckdb").open_session(SessionConfig("nb"))
    try:
        made = session.execute(CellRequest(_TZ_TABLE + "CREATE TABLE other (ts TIMESTAMP)", "sql", "snowflake"))
        assert made.status == "ok"
        stored = session.execute(CellRequest("INSERT INTO ts_test VALUES('2024-01-01 16:00:00'); "
                                             "SELECT HOUR(ts) FROM ts_test", "sql", "snowflake"))
        assert stored.status == "ok" and stored.outputs[-1]["rows"] == [[16]]
        stopped = session.execute(CellRequest("INSERT INTO ts_test VALUES('2024-01-02T16:00:00+00:00')", "sql",
                                              "snowflake"))
        assert stopped.status == "error" and "SFN-033" in stopped.outputs[-1]["evalue"]
        # a column made in DuckDB SQL: TIMESTAMP_TZ or TIMESTAMP_LTZ isn't known, so the write is labelled
        session.execute(CellRequest("CREATE TABLE plain (ts TIMESTAMPTZ)", "sql", "duckdb"))
        labelled = session.execute(CellRequest("INSERT INTO plain VALUES ('2024-01-01 16:00:00')", "sql", "snowflake"))
        label = labelled.outputs[0]
        assert labelled.status == "ok" and [n["id"] for n in label["statements"][0]["notes"]] == ["SFD-002"]
    finally:
        session.close()


# ---------------------------------------------------------------- SFX-009 and SFN-035: TYPEOF of a timestamp

@pytest.mark.parametrize("value, name", [
    # is_timestamp's example table: TYPEOF of each TO_VARIANT value
    ("TO_TIMESTAMP_TZ('2020-02-24 15:00:00.123 +01:00')", "TIMESTAMP_TZ"),
    ("TO_TIMESTAMP_LTZ('2022-02-24 13:00:00.123 +01:00')", "TIMESTAMP_LTZ"),
    ("TO_TIMESTAMP('2023-02-24 12:00:00.456')", "TIMESTAMP_NTZ"),
    ("TO_DATE('2024-02-24')", "DATE"),
])
def test_typeof_names_each_timestamp_as_the_is_timestamp_page_shows(value, name):
    statement = sf.translate(f"SELECT TYPEOF(TO_VARIANT({value}))").statements[0]
    assert "SFD-007" not in [n.id for n in statement.notes]
    assert duckdb.sql(statement.duckdb_sql).fetchall() == [(name,)]


def test_typeof_of_a_cast_to_either_zoned_type_is_that_type():
    # the type a cast gives is the one TO_TIMESTAMP_TZ and TO_TIMESTAMP_LTZ give (is_timestamp)
    for kind in ("TIMESTAMP_TZ", "TIMESTAMP_LTZ"):
        statement = sf.translate(f"SELECT TYPEOF('2026-01-01 10:00'::{kind})").statements[0]
        assert "SFD-007" not in [n.id for n in statement.notes]
        assert duckdb.sql(statement.duckdb_sql).fetchall() == [(kind,)]


def test_typeof_of_current_timestamp_is_ltz_as_its_page_says():
    # current_timestamp: "The data type of the returned value is TIMESTAMP_LTZ."
    assert _run("SELECT TYPEOF(TO_VARIANT(CURRENT_TIMESTAMP()))").fetchall() == [("TIMESTAMP_LTZ",)]


@pytest.mark.parametrize("query", [
    "SELECT TYPEOF(TO_VARIANT(c)) FROM (SELECT TO_TIMESTAMP_TZ('2020-02-24 15:00:00') AS c)",
    "SELECT TYPEOF(v) FROM (SELECT TO_VARIANT(TO_TIMESTAMP_LTZ('2020-02-24 15:00:00')) AS v)",
])
def test_typeof_of_a_zoned_timestamp_whose_kind_isnt_known_stops_by_name(query):
    statement = sf.translate(query).statements[0]
    assert "SFD-007" in [n.id for n in statement.notes]
    with pytest.raises(duckdb.Error) as raised:
        duckdb.sql(statement.duckdb_sql).fetchall()
    assert sf.stopped_by_name(raised.value) and "SFN-035" in str(raised.value)


def test_rules_version_and_the_new_rules_are_listed():
    table = sf.docs_table()
    assert sf.RULES_VERSION == "2026.11.21"
    assert all(f"| {rule} |" in table for rule in ("SFX-031", "SFX-032", "SFN-035"))
