"""Mutation batch 406: academy/validators.py. Each test pins a behaviour a surviving mutant changed:
the message a check gives, the edge of a bound, or a check that must fail (or must pass)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from fabric_desktop.academy import validators as v
from fabric_desktop.academy import workspace as ws


@pytest.fixture
def work(tmp_path: Path) -> Path:
    folder = ws.workspace_dir(tmp_path, "dp-700", "E37")
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def con(work: Path) -> duckdb.DuckDBPyConnection:
    c = ws.connect(work)
    c.execute("CREATE TABLE t (id INTEGER, name VARCHAR, amount INTEGER)")
    c.execute("INSERT INTO t VALUES (1, 'a', 10), (2, 'b', 20), (3, 'c', 30)")
    return c


def _table(work: Path, commits: list[list[dict]], rel: str = "tables/sales") -> str:
    """A Delta table folder written by hand: one log file per commit, no data files."""
    log = work / rel / "_delta_log"
    log.mkdir(parents=True)
    for i, actions in enumerate(commits):
        (log / f"{i:020d}.json").write_text("".join(json.dumps(a) + "\n" for a in actions))
    return rel


def _op(name: str) -> dict:
    return {"commitInfo": {"operation": name}}


def _add(path: str, size: int) -> dict:
    return {"add": {"path": path, "size": size}}


def _meta(partitions: list[str] | None = None, fields: list[str] | None = None) -> dict:
    schema = {"fields": [{"name": f} for f in (fields or ["id"])]}
    return {"metaData": {"partitionColumns": partitions or [], "schemaString": json.dumps(schema)}}


# ---- schema_matches and data_quality

def test_the_column_order_message_names_the_columns(con: duckdb.DuckDBPyConnection) -> None:
    out = v.schema_matches(con, "t", [{"name": "name"}, {"name": "id"}], ordered=True)
    assert not out["passed"]
    assert "in the order id, name" in out["detail"]


def test_the_pass_message_says_key_only_when_there_is_one(con: duckdb.DuckDBPyConnection) -> None:
    assert "and key" in v.schema_matches(con, "t", [{"name": "id"}], key=["id"])["detail"]
    assert "and key" not in v.schema_matches(con, "t", [{"name": "id"}])["detail"]


def test_a_range_rule_without_bounds_and_an_unknown_rule_are_refused(con: duckdb.DuckDBPyConnection) -> None:
    out = v.data_quality(con, "t", [{"rule": "range", "column": "amount"}])
    assert not out["passed"] and "needs min or max" in out["detail"]
    out = v.data_quality(con, "t", [{"rule": "sorted", "column": "amount"}])
    assert not out["passed"] and "Unknown data quality rule sorted" in out["detail"]


def test_a_range_message_names_both_bounds(con: duckdb.DuckDBPyConnection) -> None:
    out = v.data_quality(con, "t", [{"rule": "range", "column": "amount", "min": 15, "max": 25}])
    assert "(at least 15 and at most 25)" in out["detail"]
    out = v.data_quality(con, "t", [{"rule": "range", "column": "amount", "max": 25}])
    assert "(at most 25)" in out["detail"]


def test_row_count_below_min_fails_and_a_max_only_message_starts_at_zero(con: duckdb.DuckDBPyConnection) -> None:
    out = v.data_quality(con, "t", [{"rule": "row_count", "min": 4}])
    assert not out["passed"] and "it has 3 rows" in out["detail"]
    assert v.data_quality(con, "t", [{"rule": "row_count", "min": 3}])["passed"]
    out = v.data_quality(con, "t", [{"rule": "row_count", "max": 2}])
    assert "between 0 and 2" in out["detail"]


# ---- config_matches

def test_values_are_said_by_shape() -> None:
    assert "is an object;" in v.config_matches({"a": {"b": 1}}, [{"path": "a", "equals": 1}])["detail"]
    forty = "x" * 40
    assert json.dumps(forty) in v.config_matches({"a": forty}, [{"path": "a", "equals": 1}])["detail"]
    out = v.config_matches({"a": "x" * 41}, [{"path": "a", "equals": 1}])["detail"]
    assert "a different value" in out and "x" * 41 not in out
    out = v.config_matches({"a": "café"}, [{"path": "a", "equals": 1}])["detail"]
    assert '"café"' in out


def test_a_list_of_eight_short_values_is_shown_and_nine_is_counted() -> None:
    eight = list(range(8))
    assert "[0, 1, 2, 3, 4, 5, 6, 7]" in v.config_matches({"a": eight}, [{"path": "a", "equals": 1}])["detail"]
    out = v.config_matches({"a": list(range(9))}, [{"path": "a", "equals": 1}])["detail"]
    assert "a list of 9" in out


def test_count_max_allows_exactly_the_max() -> None:
    doc = {"roles": [{"r": "admin"}, {"r": "admin"}]}
    assert v.config_matches(doc, [{"path": "roles", "count_max": 2}])["passed"]
    assert not v.config_matches(doc, [{"path": "roles", "count_max": 1}])["passed"]


def test_contains_fails_on_a_missing_item_and_on_a_number() -> None:
    out = v.config_matches({"a": ["x", "y"]}, [{"path": "a", "contains": "z"}])
    assert not out["passed"] and "doesn't include" in out["detail"]
    out = v.config_matches({"a": 5}, [{"path": "a", "contains": "z"}])
    assert not out["passed"] and "doesn't include" in out["detail"]
    assert v.config_matches({"a": ["x", "z"]}, [{"path": "a", "contains": "z"}])["passed"]


# ---- artifact_present

def test_a_delta_item_that_is_a_file_fails(work: Path) -> None:
    (work / "sales").write_text("not a table")
    out = v.artifact_present(work, [{"path": "sales", "kind": "delta"}])
    assert not out["passed"] and "Delta table folder" in out["detail"]


def test_a_file_smaller_than_min_bytes_fails(work: Path) -> None:
    (work / "out.txt").write_text("abc")
    out = v.artifact_present(work, [{"path": "out.txt", "min_bytes": 4}])
    assert not out["passed"] and "at least 4" in out["detail"]
    assert v.artifact_present(work, [{"path": "out.txt", "min_bytes": 3}])["passed"]


def test_a_json_item_that_isnt_json_fails(work: Path) -> None:
    (work / "out.json").write_text("{not json")
    out = v.artifact_present(work, [{"path": "out.json", "kind": "json"}])
    assert not out["passed"]


# ---- delta_table

def test_operations_out_of_order_pass_unless_order_is_asked_for(work: Path) -> None:
    rel = _table(work, [[_op("WRITE"), _meta()], [_op("MERGE")]])
    assert v.delta_table(work, rel, operations=["MERGE", "WRITE"])["passed"]
    out = v.delta_table(work, rel, operations=["MERGE", "WRITE"], operations_in_order=True)
    assert not out["passed"] and "in that order" in out["detail"]
    assert v.delta_table(work, rel, operations=["WRITE", "MERGE"], operations_in_order=True)["passed"]


def test_order_without_operations_is_no_check(work: Path) -> None:
    rel = _table(work, [[_op("WRITE"), _meta()]])
    assert v.delta_table(work, rel, operations_in_order=True)["passed"]


def test_a_long_history_is_cut_to_eight_names(work: Path) -> None:
    rel = _table(work, [[_op(f"OP{i}")] for i in range(9)] + [[_meta()]])
    out = v.delta_table(work, rel, operations=["MERGE"])["detail"]
    assert "OP7 and 1 more" in out and "OP8" not in out
    rel = _table(work, [[_op(f"OP{i}")] for i in range(8)] + [[_meta()]], "tables/eight")
    out = v.delta_table(work, rel, operations=["MERGE"])["detail"]
    assert out.endswith("OP7.") and "more" not in out


def test_an_operation_name_of_forty_one_characters_is_cut(work: Path) -> None:
    rel = _table(work, [[_op("A" * 41), _meta()]])
    out = v.delta_table(work, rel, operations=["MERGE"])["detail"]
    assert "A" * 40 + "…" in out
    rel = _table(work, [[_op("B" * 40), _meta()]], "tables/forty")
    out = v.delta_table(work, rel, operations=["MERGE"])["detail"]
    assert "B" * 40 + "." in out


def test_asking_for_no_partitions_says_so(work: Path) -> None:
    rel = _table(work, [[_op("WRITE"), _meta(partitions=["region"])]])
    out = v.delta_table(work, rel, partition_by=[])
    assert not out["passed"] and "asks for no partitions." in out["detail"]


def test_files_min_and_max_hold_at_the_edge(work: Path) -> None:
    rel = _table(work, [[_op("WRITE"), _meta(), _add("a.parquet", 10), _add("b.parquet", 10)]])
    assert v.delta_table(work, rel, files_min=2, files_max=2)["passed"]
    out = v.delta_table(work, rel, files_min=3)
    assert not out["passed"] and "at least 3" in out["detail"]


def test_average_file_size_below_the_minimum_fails_and_at_it_passes(work: Path) -> None:
    rel = _table(work, [[_op("WRITE"), _meta(), _add("a.parquet", 100), _add("b.parquet", 300)]])
    assert v.delta_table(work, rel, avg_file_bytes_min=200)["passed"]
    out = v.delta_table(work, rel, avg_file_bytes_min=201)
    assert not out["passed"] and "average 200 bytes" in out["detail"]


def test_one_version_and_one_file_are_said_in_the_singular(work: Path) -> None:
    rel = _table(work, [[_op("WRITE"), _meta(), _add("a.parquet", 10)]])
    out = v.delta_table(work, rel)["detail"]
    assert "with 1 version and 1 data file," in out
    rel = _table(work, [[_op("WRITE"), _meta(), _add("a.parquet", 10), _add("b.parquet", 10)], [_op("WRITE")]],
                 "tables/two")
    assert "with 2 versions and 2 data files," in v.delta_table(work, rel)["detail"]


# ---- unchanged

def _many(con: duckdb.DuckDBPyConnection, n: int) -> list[str]:
    names = [f"u{i}" for i in range(n)]
    for name in names:
        con.execute(f"CREATE TABLE {name} (x INTEGER)")
    return names


def test_nine_changed_tables_say_one_more_and_eight_dont(con: duckdb.DuckDBPyConnection) -> None:
    names = _many(con, 9)
    before = v.snapshot(con, names)
    for name in names:
        con.execute(f"INSERT INTO {name} VALUES (1)")
    out = v.unchanged(con, names, before)["detail"]
    assert out.endswith(" and 1 more.")
    out = v.unchanged(con, names[:8], before)["detail"]
    assert "more" not in out and "u7 went from 0 to 1 rows." in out


def test_the_pass_message_lists_eight_tables_then_says_others(con: duckdb.DuckDBPyConnection) -> None:
    names = _many(con, 9)
    before = v.snapshot(con, names)
    out = v.unchanged(con, names, before)
    assert out["passed"] and "u7 and others hold" in out["detail"] and "u8" not in out["detail"]
    out = v.unchanged(con, names[:8], before)
    assert "u7 hold" in out["detail"] and "others" not in out["detail"]
