"""Spark and Databricks SQL lambdas, map_contains_key, forall and get_json_object paths on this PC
(#1325): each gives Spark's answer or is refused, never a quietly different value. Found by the value
checks of #1320."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks


def answer(query: str, dialect: str = "databricks"):
    statements = _databricks(query) if dialect == "databricks" else sql.parse(query, dialect)
    return duckdb.connect().execute(sql.duckdb_sql(statements[-1])).fetchone()[0]


@pytest.mark.parametrize("dialect", ["databricks", "spark"])
@pytest.mark.parametrize("query, want", [
    ("SELECT transform(array(1, 2, 3), (x, i) -> x + i)", [1, 3, 5]),            # Databricks docs example
    ("SELECT filter(array(0, 2, 3), (x, i) -> x > i)", [2, 3]),                  # Databricks docs example
    ("SELECT transform(array('a', 'b'), (x, i) -> concat(x, i))", ["a0", "b1"]),  # the index inside a call
    ("SELECT transform(array(1, 2), (x, I) -> x * 10 + i)", [10, 21]),            # names ignore case
    ("SELECT transform(array(array(1, 2)), (x, i) -> transform(x, y -> y + i))", [[1, 2]]),
    ("SELECT transform(array(7), (x, i) -> named_struct('i', i))", [{"i": 0}]),   # the key stays 'i'
    ("SELECT transform(array(1, 2), x -> x + 1)", [2, 3]),                        # no index: unchanged
    ("SELECT transform(array(5, 6), (x, i) -> i)", [0, 1]),                       # the index alone
    ("SELECT transform(array(named_struct('i', 5)), (x, i) -> x.i + i)", [5]),    # a field named i stays
    ("SELECT transform(array(named_struct('a', named_struct('i', 7))), (x, i) -> x.a.i * 10 + i)", [70]),
    ("SELECT transform(array(map('i', 3)), (x, i) -> x['i'] + i)", [3]),
])
def test_an_index_lambda_counts_from_0_as_in_spark(dialect, query, want):
    assert answer(query, dialect) == want


def test_an_inner_lambda_reusing_the_index_name_is_refused():
    with pytest.raises(ValueError, match="reuses the index name"):
        _databricks("SELECT transform(array(array(1)), (x, i) -> transform(x, (y, i) -> y + i))")


@pytest.mark.parametrize("query, want", [
    ("SELECT forall(array(1, 2, 3), x -> x % 2 == 0)", False),
    ("SELECT forall(array(2, 4, 8), x -> x % 2 == 0)", True),
    ("SELECT forall(array(1, NULL, 3), x -> x % 2 == 0)", False),
    ("SELECT forall(array(2, NULL, 8), x -> x % 2 == 0)", None),
    ("SELECT forall(CAST(NULL AS ARRAY<INT>), x -> x > 0)", None),
    ("SELECT forall(array(), x -> x > 0)", True),
])
def test_forall_answers_as_spark(query, want):
    assert answer(query) is want


@pytest.mark.parametrize("query, want", [
    ("SELECT map_contains_key(map(1, 'a', 2, 'b'), 2)", True),
    ("SELECT map_contains_key(map(1, 'a', 2, 'b'), 3)", False),
    ("SELECT map_contains_key(map(1, 'a'), NULL)", None),
])
def test_map_contains_key_takes_the_map_first(query, want):
    assert answer(query) is want


@pytest.mark.parametrize("path", ["$.items[*].n", "$.items[*]", "$.*"])
def test_get_json_object_with_a_wildcard_is_refused_never_a_list(path):
    with pytest.raises(ValueError, match="returns JSON text there"):
        _databricks(f"SELECT get_json_object('{{\"items\":[{{\"n\":1}}]}}', '{path}')")


def test_get_json_object_with_a_path_to_one_value_still_runs():
    assert answer("""SELECT get_json_object('{"items":[{"n":1},{"n":2}]}', '$.items[1].n')""") == "2"
