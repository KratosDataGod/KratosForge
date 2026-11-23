"""Small Snowflake gaps closed (2026.11.23): $n positional columns (SFX-082, SFN-080), FLATTEN's SEQ (SFX-083,
SFN-081), one column in both * REPLACE and RENAME (SFX-084, SFN-082), TO_JSON of an array or object holding a DOUBLE
(SFN-083).

Sources, each value below is the page's own:
- constructs/values: SELECT column1, $2 FROM (VALUES (1, 'one'), (2, 'two'), (3, 'three')) gives the columns COLUMN1
  and $2, rows (1, one), (2, two), (3, three). Its join example (v1.$2, v2.$2) shows no output.
- sql/select: "$col_position: Specifies the position of the column (1-based) as defined in the FROM clause";
  SELECT $2 FROM employee_table ORDER BY $2 gives one column $2: Comstock, Levine, Montgomery. "You can specify the
  same column name in REPLACE and RENAME": SELECT * REPLACE ('DEPT-' || department_id AS department_id) RENAME
  department_id AS department FROM employee_table gives EMPLOYEE_ID, LAST_NAME, FIRST_NAME, DEPARTMENT with
  (101, Montgomery, Pat, DEPT-1), (102, Levine, Terry, DEPT-2), (103, Comstock, Dana, DEPT-2).
- functions/flatten: SEQ is "A unique sequence number associated with the input record; the sequence is not
  guaranteed to be gap-free or ordered in any particular way." Every example (one input record) shows SEQ 1.
- functions/array_construct and functions/array_construct_compact: 3::DOUBLE in an array is shown as
  3.000000000000000e+00. functions/to_json shows no DOUBLE, so how TO_JSON writes one isn't documented.

Header case: this branch doesn't upper-case a result column's name (only CREATE's, SFX-072), so the docs' DEPARTMENT
is checked as the column's place and value, under the name as written."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf
from fabric_desktop import snowflake_sql_parity as parity

_EMPLOYEES = """CREATE TABLE employee_table (
    employee_ID INTEGER,
    last_name VARCHAR,
    first_name VARCHAR,
    department_ID INTEGER
    );
INSERT INTO employee_table (employee_ID, last_name, first_name, department_ID) VALUES
    (101, 'Montgomery', 'Pat', 1),
    (102, 'Levine', 'Terry', 2),
    (103, 'Comstock', 'Dana', 2);"""


def _con(setup: str = "") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if setup:
        translation = sf.translate(setup)
        assert translation.runnable, translation.first_refusal()
        con.execute(translation.duckdb_sql)
    return con


def _run(sql: str, setup: str = "") -> tuple[list[tuple], list[str]]:
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    assert all(s.verdict == "translated" for s in translation.statements), translation.statements
    con = _con(setup)
    rows = con.execute(translation.duckdb_sql).fetchall()
    return rows, [d[0] for d in con.description]


def _refused(sql: str) -> str:
    translation = sf.translate(sql)
    assert not translation.runnable
    return translation.statements[-1].notes[0].id


def _stops(sql: str, rule: str, setup: str = "") -> None:
    translation = sf.translate(sql)
    assert translation.runnable, translation.first_refusal()
    with pytest.raises(duckdb.Error) as caught:
        _con(setup).execute(translation.duckdb_sql).fetchall()
    assert sf.stopped_by_name(caught.value) and rule in str(caught.value)


# ---------------------------------------------------------------- SFX-082 and SFN-080: $1, $2...

def test_the_values_pages_positional_example():
    rows, names = _run("SELECT column1, $2 FROM (VALUES (1, 'one'), (2, 'two'), (3, 'three'))")
    assert rows == [(1, "one"), (2, "two"), (3, "three")]
    assert names == ["column1", "$2"]


def test_positions_over_values_with_no_parentheses():
    rows, names = _run("SELECT column1, $2 FROM VALUES (1, 'one'), (2, 'two'), (3, 'three')")
    assert rows == [(1, "one"), (2, "two"), (3, "three")] and names == ["column1", "$2"]


def test_the_select_pages_positional_example():
    rows, names = _run("SELECT $2 FROM employee_table ORDER BY $2", _EMPLOYEES)
    assert rows == [("Comstock",), ("Levine",), ("Montgomery",)]
    assert names == ["$2"]


def test_positions_in_where_and_beside_a_star_keep_the_sources_own_columns():
    rows, names = _run("SELECT *, e.$1 FROM employee_table e WHERE $4 = 2 ORDER BY $1", _EMPLOYEES)
    assert names == ["EMPLOYEE_ID", "LAST_NAME", "FIRST_NAME", "DEPARTMENT_ID", "$1"]
    assert rows == [(102, "Levine", "Terry", 2, 102), (103, "Comstock", "Dana", 2, 103)]


def test_a_position_with_an_alias_and_a_variable_still_read_as_before():
    rows, names = _run("SELECT $1 AS a FROM VALUES (7), (8)")
    assert rows == [(7,), (8,)] and names == ["a"]
    rows, _ = _run("SET x = 'v'; SELECT $x, $1 FROM VALUES (1)")
    assert rows == [("v", 1)]


@pytest.mark.parametrize("sql", [
    ("SELECT v1.$2, v2.$2 FROM (VALUES (1, 'one'), (2, 'two')) AS v1 "
     "INNER JOIN (VALUES (1, 'One'), (3, 'three')) AS v2 WHERE v2.$1 = v1.$1"),  # the values page's join example
    "SELECT $1 FROM a, b",
    "SELECT $1",
    "SELECT x.$1 FROM employee_table",
    "SELECT $1 FROM VALUES (1) UNION SELECT 2 ORDER BY $1",
])
def test_a_position_over_more_than_one_source_stops_by_name(sql):
    assert _refused(sql) == "SFN-080"


# ---------------------------------------------------------------- SFX-083 and SFN-081: FLATTEN's SEQ

_FLAT = "SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{\"a\":1, \"b\":[77,88]}'), "


def test_the_flatten_pages_examples_have_seq_1_first():
    rows, names = _run(_FLAT + "PATH => 'b')) f")
    assert names == ["seq", "key", "path", "index", "value", "this"]
    assert rows == [(1, None, "b[0]", 0, "77", "[77,88]"), (1, None, "b[1]", 1, "88", "[77,88]")]
    rows, _ = _run(_FLAT + "OUTER => TRUE)) f")
    assert [r[:4] for r in rows] == [(1, "a", "a", None), (1, "b", "b", None)]
    rows, _ = _run("SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{\"a\":1, \"b\":[77,88], \"c\": {\"d\":\"X\"}}'))) f")
    assert [r[:4] for r in rows] == [(1, "a", "a", None), (1, "b", "b", None), (1, "c", "c", None)]
    assert _run("SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[]'))) f")[0] == []
    rows, _ = _run("SELECT * FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[]'), OUTER => TRUE)) f")
    assert rows == [(1, None, "", None, None, "[]")]


def test_seq_by_name_when_flatten_has_one_input_record():
    rows, _ = _run("SELECT f.seq, seq, f.index FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[5, 6]'))) f ORDER BY f.index")
    assert rows == [(1, 1, 0), (1, 1, 1)]


@pytest.mark.parametrize("sql", [
    "SELECT f.seq FROM (SELECT PARSE_JSON('[1]') AS v) t, LATERAL FLATTEN(INPUT => t.v) f",
    "SELECT seq FROM (SELECT PARSE_JSON('[1]') AS v) t, LATERAL FLATTEN(INPUT => t.v) f",
    "SELECT f.* FROM (SELECT PARSE_JSON('[1]') AS v) t, LATERAL FLATTEN(INPUT => t.v) f",
    "SELECT * FROM (SELECT PARSE_JSON('[1]') AS v) t, LATERAL FLATTEN(INPUT => t.v) f",
    "SELECT * FROM (SELECT 1 AS x) t, LATERAL FLATTEN(INPUT => PARSE_JSON('[1]')) f",
    "SELECT * FROM TABLE(FLATTEN(INPUT => x))",
])
def test_seq_over_many_input_records_stops_by_name(sql):
    assert _refused(sql) == "SFN-081"


def test_other_columns_of_a_lateral_flatten_still_run():
    rows, _ = _run("SELECT t.id, f.index, f.value::INT FROM (SELECT 1 AS id, PARSE_JSON('[7, 8]') AS v) t, "
                   "LATERAL FLATTEN(INPUT => t.v) f ORDER BY f.index")
    assert rows == [(1, 0, 7), (1, 1, 8)]


# ---------------------------------------------------------------- SFX-084 and SFN-082: * REPLACE and RENAME

def test_the_select_pages_replace_and_rename_example():
    rows, names = _run("SELECT * REPLACE ('DEPT-' || department_id AS department_id) RENAME department_id AS department "
                       "FROM employee_table", _EMPLOYEES)
    assert rows == [(101, "Montgomery", "Pat", "DEPT-1"), (102, "Levine", "Terry", "DEPT-2"),
                    (103, "Comstock", "Dana", "DEPT-2")]
    assert names[:3] == ["EMPLOYEE_ID", "LAST_NAME", "FIRST_NAME"] and names[3].upper() == "DEPARTMENT"


def test_replace_alone_keeps_each_columns_own_name():
    # a || in REPLACE no longer names every column of the * after the REPLACE's text
    _, names = _run("SELECT * REPLACE ('DEPT-' || department_id AS department_id) FROM employee_table", _EMPLOYEES)
    assert names == ["EMPLOYEE_ID", "LAST_NAME", "FIRST_NAME", "department_id"]


def test_replace_and_rename_keep_order_limit_and_with():
    rows, names = _run("WITH x AS (SELECT * FROM employee_table) SELECT * REPLACE (UPPER(last_name) AS last_name) "
                       "RENAME (last_name AS ln, first_name AS fn) FROM x ORDER BY employee_id DESC LIMIT 2",
                       _EMPLOYEES)
    assert names == ["EMPLOYEE_ID", "ln", "fn", "DEPARTMENT_ID"]
    assert rows == [(103, "COMSTOCK", "Dana", 2), (102, "LEVINE", "Terry", 2)]


def test_replace_and_rename_inside_a_subquery():
    rows, _ = _run("SELECT department FROM (SELECT * REPLACE ('DEPT-' || department_id AS department_id) "
                   "RENAME department_id AS department FROM employee_table) WHERE employee_id = 101", _EMPLOYEES)
    assert rows == [("DEPT-1",)]


@pytest.mark.parametrize("sql", [
    "SELECT * REPLACE (1 AS last_name) RENAME last_name AS x, last_name FROM employee_table",
    "SELECT * REPLACE (1 AS last_name) RENAME last_name AS x, 2 AS x FROM employee_table",
    "SELECT e.* REPLACE (1 AS last_name) RENAME last_name AS x, d.* FROM employee_table e, employee_table d",
])
def test_replace_and_rename_beside_a_name_that_may_clash_stops_by_name(sql):
    assert _refused(sql) == "SFN-082"


# ---------------------------------------------------------------- SFN-083: TO_JSON of a DOUBLE in an array

@pytest.mark.parametrize("sql", [
    "SELECT TO_JSON(ARRAY_CONSTRUCT_COMPACT(NULL, 'hello', 3::double, 4, 5))",  # the page's array, as JSON text
    "SELECT TO_JSON(ARRAY_CONSTRUCT(1, 3::DOUBLE))",
    "SELECT TO_JSON(OBJECT_CONSTRUCT('a', 3::DOUBLE))",
    "SELECT TO_JSON(ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('a', 1.5::FLOAT)))",
    "SELECT TO_JSON(TO_VARIANT(ARRAY_CONSTRUCT(1.5::DOUBLE)))",
    "SELECT TO_JSON(OBJECT_CONSTRUCT('a', 3::DOUBLE))::VARCHAR",
])
def test_to_json_of_a_double_in_an_array_or_object_stops_by_name(sql):
    _stops(sql, "SFN-083")


def test_to_json_without_a_double_still_runs():
    rows, _ = _run("SELECT TO_JSON(ARRAY_CONSTRUCT(1, 'a')), TO_JSON(OBJECT_CONSTRUCT('b', 1, 'a', 'x')), "
                   "TO_JSON(ARRAY_CONSTRUCT('a\"1.5', 2)), TO_JSON(NULL)")
    assert rows == [('[1,"a"]', '{"a":"x","b":1}', '["a\\"1.5",2]', None)]


def test_to_json_of_parse_json_keeps_its_number():
    # to_json: varchar1 '{"PI":3.14}', and TO_JSON(PARSE_JSON(varchar1)) = varchar1 is true
    rows, _ = _run("SELECT TO_JSON(PARSE_JSON('{\"PI\":3.14}')) = '{\"PI\":3.14}'")
    assert rows == [(True,)]


def test_the_compact_pages_array_is_its_documented_numbers():
    # 3.000000000000000e+00 is 3.0 as a number: the parity case compares values, not their text
    [case] = [c for c in parity.CASES if c.function == "ARRAY_CONSTRUCT_COMPACT (docs example)"]
    assert case.expected == ["hello", 3.000000000000000e+00, 4, 5]
    assert parity.run(case) == ("same", case.expected)


def test_the_new_rules_are_in_the_catalog():
    ids = {row[0] for row in sf.CATALOG}
    assert {"SFX-082", "SFX-083", "SFX-084", "SFN-080", "SFN-081", "SFN-082", "SFN-083"} <= ids
    assert sf.RULES_VERSION == "2026.11.23"
