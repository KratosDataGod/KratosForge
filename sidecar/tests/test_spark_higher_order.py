"""zip_with, map_filter, transform_keys, transform_values and aggregate/reduce with a finish lambda on
this PC (#1371): each gives Databricks' documented answer, or is refused as Databricks refuses it,
through both the DuckDB engine's Databricks SQL and the Spark SQL path."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks

DIALECTS = ["databricks", "spark"]


def run(query: str, dialect: str):
    statements = _databricks(query) if dialect == "databricks" else sql.parse(query, dialect)
    return duckdb.connect().execute(sql.duckdb_sql(statements[-1])).fetchone()


def answer(query: str, dialect: str):
    return run(query, dialect)[0]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("query, want", [
    # Databricks docs examples
    ("SELECT zip_with(array(1, 2), array(3, 4), (x, y) -> x + y)", [4, 6]),
    ("SELECT zip_with(array('a', 'b', 'c'), array('d', 'e', 'f'), (x, y) -> concat(x, y))", ["ad", "be", "cf"]),
    # unequal lengths: the shorter array is padded with NULL, the result as long as the longer
    ("SELECT zip_with(array(1, 2, 3), array(10), (x, y) -> coalesce(y, 0) + x)", [11, 2, 3]),
    ("SELECT zip_with(array(10), array(1, 2, 3), (x, y) -> x IS NULL)", [False, True, True]),
    ("SELECT zip_with(array(1, 2), CAST(array() AS ARRAY<INT>), (x, y) -> y)", [None, None]),
    # NULL and empty arrays
    ("SELECT zip_with(array(1), CAST(NULL AS ARRAY<INT>), (x, y) -> x)", None),
    ("SELECT zip_with(CAST(NULL AS ARRAY<INT>), array(1), (x, y) -> y)", None),
    ("SELECT zip_with(CAST(array() AS ARRAY<INT>), CAST(array() AS ARRAY<INT>), (x, y) -> x + y)", []),
    ("SELECT zip_with(array(1, NULL), array(2, 3), (x, y) -> x + y)", [3, None]),
])
def test_zip_with(dialect, query, want):
    assert answer(query, dialect) == want


@pytest.mark.parametrize("dialect", DIALECTS)
def test_zip_with_docs_struct_example(dialect):
    got = answer("SELECT zip_with(array(1, 2, 3), array('a', 'b', 'c'), (x, y) -> (y, x))", dialect)
    assert [tuple(item.values()) if isinstance(item, dict) else tuple(item) for item in got] == [
        ("a", 1), ("b", 2), ("c", 3)]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_lambda_names_hide_columns_as_in_spark(dialect):
    query = "SELECT x, zip_with(array(x), array(y), (x, y) -> x * y) FROM VALUES (2, 3) AS t(x, y)"
    assert run(query, dialect) == (2, [6])


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("query, want", [
    ("SELECT map_filter(map(1, 0, 2, 2, 3, -1), (k, v) -> k > v)", {1: 0, 3: -1}),   # Databricks docs example
    ("SELECT map_filter(map(1, 0, 2, 2), (k, v) -> v > 5)", {}),
    ("SELECT map_filter(map(1, NULL, 2, 2), (k, v) -> v > 0)", {2: 2}),             # a NULL predicate drops it
    ("SELECT map_filter(CAST(NULL AS MAP<INT, INT>), (k, v) -> k > v)", None),
    ("SELECT map_filter(CAST(map() AS MAP<INT, INT>), (k, v) -> k > v)", {}),
])
def test_map_filter(dialect, query, want):
    assert answer(query, dialect) == want


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("query, want", [
    # Databricks docs examples
    ("SELECT transform_values(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> v + 1)", {1: 2, 2: 3, 3: 4}),
    ("SELECT transform_values(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> k + v)", {1: 2, 2: 4, 3: 6}),
    ("SELECT transform_values(map('a', 1), (k, v) -> CAST(NULL AS INT))", {"a": None}),
    ("SELECT transform_values(CAST(NULL AS MAP<INT, INT>), (k, v) -> v)", None),
    ("SELECT transform_values(CAST(map() AS MAP<INT, INT>), (k, v) -> v + 1)", {}),
    # an index lambda inside still counts from 0
    ("SELECT transform_values(map('a', array(1, 2)), (k, v) -> transform(v, (e, i) -> e * i))", {"a": [0, 2]}),
])
def test_transform_values(dialect, query, want):
    assert answer(query, dialect) == want


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("query, want", [
    # Databricks docs examples
    ("SELECT transform_keys(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> k + 1)", {2: 1, 3: 2, 4: 3}),
    ("SELECT transform_keys(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> k + v)", {2: 1, 4: 2, 6: 3}),
    ("SELECT transform_keys(map('a', 1, 'b', 2), (k, v) -> upper(k))", {"A": 1, "B": 2}),
    ("SELECT transform_keys(CAST(NULL AS MAP<INT, INT>), (k, v) -> k + 1)", None),
    ("SELECT transform_keys(CAST(map() AS MAP<INT, INT>), (k, v) -> k + 1)", {}),
])
def test_transform_keys(dialect, query, want):
    assert answer(query, dialect) == want


@pytest.mark.parametrize("dialect", DIALECTS)
def test_transform_keys_refuses_a_duplicate_key(dialect):
    # Databricks docs: DUPLICATED_MAP_KEY
    with pytest.raises(duckdb.Error, match="DUPLICATED_MAP_KEY"):
        run("SELECT transform_keys(map(1, 'a', 2, 'b'), (k, v) -> 0)", dialect)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_transform_keys_refuses_a_null_key(dialect):
    with pytest.raises(duckdb.Error, match="NULL_MAP_KEY"):
        run("SELECT transform_keys(map(1, 'a', 2, 'b'), (k, v) -> CAST(NULL AS INT))", dialect)


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("query, want", [
    # Databricks docs examples
    ("SELECT aggregate(array(1, 2, 3), 0, (acc, x) -> acc + x, acc -> acc * 10)", 60),
    ("SELECT aggregate(array(1, 2, 3, 4), named_struct('sum', 0, 'cnt', 0), "
     "(acc, x) -> named_struct('sum', acc.sum + x, 'cnt', acc.cnt + 1), acc -> acc.sum / acc.cnt)", 2.5),
    ("SELECT reduce(array(1, 2, 3), 0, (acc, x) -> acc + x, acc -> acc * 10)", 60),
    # without finish, unchanged
    ("SELECT aggregate(array(1, 2, 3), 0, (acc, x) -> acc + x)", 6),
    # an empty array is finish(start); a NULL array is NULL, finish not called
    ("SELECT aggregate(CAST(array() AS ARRAY<INT>), 5, (acc, x) -> acc + x, acc -> acc * 2)", 10),
    ("SELECT aggregate(CAST(NULL AS ARRAY<INT>), 0, (acc, x) -> acc + x, acc -> coalesce(acc, 0) + 1)", None),
    ("SELECT aggregate(array(1, NULL, 3), 0, (acc, x) -> acc + coalesce(x, 100), acc -> acc)", 104),
    ("SELECT aggregate(array(1.5D, 2.5D), 0D, (acc, x) -> acc + x, acc -> acc * 2)", 8.0),
])
def test_aggregate_with_finish(dialect, query, want):
    assert answer(query, dialect) == want


@pytest.mark.parametrize("dialect", DIALECTS)
def test_aggregate_refuses_a_merge_of_another_type_than_start(dialect):
    # Spark refuses a merge whose type isn't start's; casting it to start's type would change the answer.
    with pytest.raises(duckdb.Error, match="another type than its start value"):
        run("SELECT aggregate(array(1.5, 2.5), 0, (acc, x) -> acc + x, acc -> acc)", dialect)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_columns_keep_spark_names(dialect):
    statements = (_databricks if dialect == "databricks" else lambda q: sql.parse(q, dialect))(
        "SELECT map_filter(map(1, 0), (k, v) -> k > v)")
    cursor = duckdb.connect().execute(sql.duckdb_sql(statements[-1]))
    assert cursor.description[0][0] == "map_filter(map(1, 0), (k, v) -> k > v)"


@pytest.mark.parametrize("query", [
    "SELECT transform_values(map(1, 10), (kf_p, v) -> v + kf_p)",       # a lambda argument named like the rewrite's
    "SELECT map_filter(map(1, 10), (k, kf_v) -> kf_v > 0)",
    "SELECT zip_with(array(1), array(2), (kf_i, y) -> kf_i + y)",
    "SELECT aggregate(array(1, 2), 0, (kf_a, x) -> kf_a + x, acc -> acc)",
    "SELECT array_union(kf_v, array(1)) FROM (SELECT array(2) AS kf_v)",  # a column inside a rewritten call
])
def test_a_kf_name_inside_a_rewritten_call_is_refused_plainly(query):
    """#1371 follow-up: the rewrites' own lambda arguments start with kf_, so a person's kf_ name could
    hide one of them (a confusing binder error, or a wrong answer); it is refused with a plain message."""
    with pytest.raises(ValueError, match="names that start with kf_ are reserved"):
        _databricks(query)


def test_a_kf_column_outside_any_rewritten_call_still_runs():
    con = duckdb.connect()
    statements = _databricks("SELECT kf_x + 1 FROM (SELECT 1 AS kf_x)")
    assert con.execute(sql.duckdb_sql(statements[-1])).fetchone()[0] == 2


@pytest.mark.parametrize("query, want", [
    ("SELECT array_distinct(array_except(array(1, 2, 2, 3), array(2)))", [1, 3]),
    ("SELECT aggregate(zip_with(array(1, 2), array(10, 20), (x, y) -> x + y), 0, (acc, v) -> acc + v, acc -> acc * 2)",
     66),
    ("SELECT map_filter(transform_values(map(1, 10, 2, 20), (k, v) -> v + k), (k, v) -> v > 15)", {2: 22}),
    ("SELECT array_union(array_intersect(array(1, 2), array(2, 3)), array_distinct(array(4, 4)))", [2, 4]),
])
def test_rewritten_calls_inside_rewritten_calls_still_run(query, want):
    """The kf_ check reads the statement as written, so a rewrite's own kf_ names inside another
    rewritten call are never taken for the person's (#1400 review)."""
    for dialect in ("databricks", "spark"):
        statements = _databricks(query) if dialect == "databricks" else sql.parse(query, "spark")
        assert duckdb.connect().execute(sql.duckdb_sql(statements[-1])).fetchone()[0] == want, dialect


def test_a_kf_name_in_aggregate_without_finish_is_left_alone():
    statements = _databricks("SELECT aggregate(kf_a, 0, (acc, x) -> acc + x) FROM (SELECT array(1, 2) AS kf_a)")
    assert duckdb.connect().execute(sql.duckdb_sql(statements[-1])).fetchone()[0] == 3
