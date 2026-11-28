"""Readers of SQL a person typed, each read through sql_dialects.parse: a key and value function given an odd
number of arguments (Spark's map('a'), VAR_MAP('a'), Snowflake's OBJECT_CONSTRUCT('a')) is refused with a
message naming the function, not an IndexError from inside sqlglot. Follow-up to the readers #2510 covered."""

from __future__ import annotations

import pytest
from sqlglot.errors import ParseError

from fabric_desktop import model_ossie, spark_compat, warehouse
from fabric_desktop import sql as spark_sql
from fabric_desktop.localdata import LocalData, delta, tsql

PAIRS = "needs key and value pairs"


def test_a_local_data_project_refuses_spark_map_with_one_argument(tmp_path):
    local = LocalData(tmp_path / "local-data")
    engine = local.engine(local.create("Pairs")["id"])
    with pytest.raises(ValueError, match="Could not read the query as Spark SQL: MAP " + PAIRS):
        engine.execute("SELECT map('a') AS m", dialect="spark")


@pytest.mark.parametrize("text", ["map('a')", "map('a', 1, 'b') AS m"])
def test_f_expr_and_select_expr_refuse_map_with_odd_arguments(text):
    with pytest.raises(ParseError, match="MAP " + PAIRS):
        spark_compat.spark_expr(text)
    with pytest.raises(ParseError, match="MAP " + PAIRS):
        spark_compat.expr(text)


def test_local_t_sql_refuses_var_map_with_one_argument():
    with pytest.raises(ValueError, match="Could not read the query as T-SQL: VAR_MAP " + PAIRS):
        tsql.translate("SELECT VAR_MAP('a') AS m")


def test_a_warehouse_read_check_refuses_var_map_with_one_argument():
    with pytest.raises(ValueError, match="can't be shown to only read: VAR_MAP " + PAIRS):
        warehouse.verdicts("SELECT VAR_MAP('a') AS m")


def test_a_warehouse_change_check_marks_var_map_with_one_argument_unreadable():
    [verdict] = warehouse.verdicts("SELECT VAR_MAP('a') AS m", writes=True)
    assert verdict.kind == "fabric_mutation"
    assert "couldn't be read as T-SQL" in verdict.reason


def test_a_time_travel_read_refuses_var_map_with_one_argument():
    with pytest.raises(ValueError, match=r"AT \(VERSION/TIMESTAMP => \.\.\.\) clause: VAR_MAP " + PAIRS):
        delta.rewrite_as_of("SELECT VAR_MAP('a') AS m FROM t AT (VERSION => 1)")


def test_an_ossie_metric_with_object_construct_odd_arguments_is_left_as_written():
    with pytest.raises(ParseError, match="OBJECT_CONSTRUCT " + PAIRS):
        model_ossie._qualified("OBJECT_CONSTRUCT('a')", "sales", "snowflake")


def test_the_empty_struct_check_counts_a_spark_map_with_one_argument_as_another_mistake():
    # struct<> next to map('a'): once the struct has a field the query still can't be read, so the empty
    # struct isn't named; the query's first reading error is reported (it used to be an IndexError)
    with pytest.raises(ValueError, match="Could not read the query") as raised:
        spark_sql.parse("SELECT CAST(NULL AS struct<>) AS s, map('a') AS m")
    assert spark_sql._EMPTY_STRUCT not in str(raised.value)
