"""DuckDB 2.0 output shapes the engine reads (#1341): its JSON profile, and Column text with quoted names.

These run on the pinned DuckDB too, with the 2.0 shapes written out, so the engine reads both."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop import spark_local
from fabric_desktop.localdata import engine


def test_a_duckdb_2_profile_reads_as_the_1x_profile():
    raw = {"query": {"total_time": 0.5, "cpu_time": 0.4, "total_rows_scanned": 30},
           "system": {"peak_buffer_memory": 1024, "peak_temp_dir_size": 0},
           "io": {"total_bytes_read": 2048},
           "operator": [{"type": "RESULT_COLLECTOR", "timing": 0.0, "children": [
               {"type": "PROJECTION", "timing": 0.1, "intermediate_rows": 3, "rows_scanned": 0,
                "extra_info": {}, "children": [
                    {"type": "TABLE_SCAN", "timing": 0.3, "intermediate_rows": 3, "rows_scanned": 30,
                     "extra_info": {"Table": "t"}}]}]}]}
    got = engine._profile_1x(raw)
    assert got["latency"] == 0.5 and got["cpu_time"] == 0.4 and got["cumulative_rows_scanned"] == 30
    assert got["system_peak_buffer_memory"] == 1024 and got["total_bytes_read"] == 2048
    [projection] = got["children"]
    assert projection["operator_name"] == "PROJECTION" and projection["operator_cardinality"] == 3
    [scan] = projection["children"]
    assert scan["operator_name"] == "TABLE_SCAN" and scan["operator_rows_scanned"] == 30
    assert scan["extra_info"] == {"Table": "t"} and scan["children"] == []


def test_a_1x_profile_is_left_as_it_is():
    raw = {"latency": 0.1, "children": [{"operator_name": "PROJECTION", "children": []}]}
    assert engine._profile_1x(raw) is raw


def test_a_column_given_to_the_merge_builder_is_spark_sql_whatever_duckdb_quotes():
    assert spark_local._sql_text(SimpleNamespace(expr='("s"."amount" * 2)')) == "(`s`.`amount` * 2)"
    assert spark_local._sql_text(SimpleNamespace(expr="(s.amount * 2)")) == "(s.amount * 2)"
    assert spark_local._sql_text(SimpleNamespace(expr='"odd name"')) == "`odd name`"
    assert spark_local._sql_text("s.amount > 1") == "s.amount > 1"
