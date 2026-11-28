"""academy/validators.py at its edges (mutation testing, batch 391).

test_academy_validators.py passes each reference answer and fails planted wrong ones by a word of the
message. These pin the exact messages, every bound at its value, the defaults, the 2,000-commit cap, the
"and N more" cuts, and the checks that a wrong answer reaches only when an earlier one passes. Delta logs
here are written by hand, a line of JSON per action.
"""

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
    c.execute("CREATE TABLE t (id INTEGER NOT NULL, name VARCHAR, tier VARCHAR)")
    c.execute("INSERT INTO t VALUES (1,'a','gold'),(2,NULL,'silver'),(3,'c','gold')")
    return c


def no(kind, detail):
    return {"kind": kind, "passed": False, "detail": detail}


def ok(kind, detail):
    return {"kind": kind, "passed": True, "detail": detail}


def log(work: Path, rel: str, commits: list[list[dict]]) -> None:
    folder = work / rel / "_delta_log"
    folder.mkdir(parents=True)
    for n, actions in enumerate(commits):
        (folder / f"{n:020d}.json").write_text("".join(json.dumps(a) + "\n" for a in actions), encoding="utf-8")


def op(name: str) -> dict:
    return {"commitInfo": {"operation": name}}


def add(path: str, size: int | None = 10) -> dict:
    body = {"path": path}
    if size is not None:
        body["size"] = size
    return {"add": body}


# ---- schema_matches


def test_schema_messages_name_the_column_not_its_type(con):
    assert v.schema_matches(con, "t", [{"name": "ID", "type": "VARCHAR"}]) == no(
        "schema_matches", "t.id is INTEGER; it should be VARCHAR.")
    assert v.schema_matches(con, "t", [{"name": "name", "nullable": False}]) == no(
        "schema_matches", "t.name must never be empty; 1 rows are NULL.")
    assert v.schema_matches(con, "t", [{"name": "tier"}, {"name": "id"}], ordered=True) == no(
        "schema_matches", "t's columns are in the order id, tier; the exercise asks for tier, id.")


def test_a_nullable_column_with_nulls_passes_when_nulls_are_allowed(con):
    assert v.schema_matches(con, "t", [{"name": "name"}]) == ok(
        "schema_matches", "t has the columns the exercise asks for.")


def test_by_default_other_columns_and_any_order_are_fine(con):
    assert v.schema_matches(con, "t", [{"name": "tier"}, {"name": "id"}])["passed"]


def test_the_pass_says_types_and_key_only_when_asked(con):
    assert v.schema_matches(con, "t", [{"name": "id", "type": "INT"}], key=["id"])["detail"] == (
        "t has the columns, types and key the exercise asks for.")


def test_a_missing_key_column_is_named(con):
    assert v.schema_matches(con, "t", [{"name": "id"}], key=["id", "nope"]) == no(
        "schema_matches", "t has no column nope for its key.")


# ---- data_quality


def dq(con, *rules):
    return v.data_quality(con, "t", list(rules))


def test_data_quality_without_the_table_says_so(con):
    assert v.data_quality(con, "nope", []) == no("data_quality", "There's no table nope yet.")


@pytest.mark.parametrize("rule,detail", [
    ({"rule": "sideways"}, "The sideways check couldn't run: Unknown data quality rule sideways."),
    ({"rule": "not_null", "column": "nope"}, "The not_null check couldn't run: t has no column nope."),
    ({"rule": "range", "column": "id"}, "The range check couldn't run: A range rule needs min or max."),
    ({"rule": "accepted", "column": "tier", "values": []}, "The accepted check couldn't run: An accepted rule needs values."),
])
def test_a_rule_that_cant_run_says_why(con, rule, detail):
    assert dq(con, rule) == no("data_quality", detail)


def test_range_rules_with_one_end(con):
    assert dq(con, {"rule": "range", "column": "id", "max": 2}) == no(
        "data_quality", "In t, 1 rows have a id outside the range (at most 2).")
    assert dq(con, {"rule": "range", "column": "id", "min": 2}) == no(
        "data_quality", "In t, 1 rows have a id outside the range (at least 2).")
    assert dq(con, {"rule": "range", "column": "id", "min": 0, "max": 2}) == no(
        "data_quality", "In t, 1 rows have a id outside the range (at least 0 and at most 2).")
    assert dq(con, {"rule": "range", "column": "id", "min": 1, "max": 3})["passed"]


@pytest.mark.parametrize("rule,detail", [
    ({"min": 4}, "it has 3 rows; it should have between 4 and ∞"),
    ({"max": 2}, "it has 3 rows; it should have between 0 and 2"),
    ({"min": 1, "max": 2}, "it has 3 rows; it should have between 1 and 2"),
    ({"equals": 4}, "it has 3 rows; the answer has 4"),
])
def test_row_count_outside_its_bounds(con, rule, detail):
    assert dq(con, {"rule": "row_count", **rule}) == no("data_quality", f"In t, {detail}.")


@pytest.mark.parametrize("rule", [{"min": 3}, {"max": 3}, {"min": 3, "max": 3}, {"equals": 3}, {}])
def test_row_count_at_its_bounds_passes(con, rule):
    assert dq(con, {"rule": "row_count", **rule}) == ok("data_quality", "t passes all 1 data quality rules.")


# ---- config_matches


def cfg(doc, **rule):
    return v.config_matches(doc, [{"path": "a", **rule}])


def test_a_list_position_past_the_end_isnt_set():
    assert v.config_matches({"a": [1, 2]}, [{"path": "a.2", "equals": 1}]) == no("config_matches", "a.2 isn't set.")


@pytest.mark.parametrize("rule,doc,detail", [
    ({"count": 0}, [1], "a has 1 entry; it should have exactly 0."),
    ({"count": 1}, [], "a has 0 entries; it should have exactly 1."),
    ({"count_min": 2}, [1], "a has 1 entry; it should have at least 2."),
    ({"count_max": 1}, [1, 2], "a has 2 entries; it should have at most 1."),
])
def test_count_messages(rule, doc, detail):
    assert cfg({"a": doc}, **rule) == no("config_matches", detail)


@pytest.mark.parametrize("rule", [{"count_min": 2}, {"count_max": 2}, {"min": 2}, {"max": 2}])
def test_counts_and_numbers_at_their_bound_pass(rule):
    doc = {"a": [1, 2]} if "count_min" in rule or "count_max" in rule else {"a": 2}
    assert cfg(doc, **rule)["passed"]


def test_contains_and_not_contains():
    assert cfg({"a": ["x", "y"]}, contains="y")["passed"]
    assert cfg({"a": "xyz"}, contains="y")["passed"]
    assert cfg({"a": ["x"]}, contains="y") == no("config_matches", 'a doesn\'t include "y".')
    assert cfg({"a": 5}, contains="y") == no("config_matches", 'a doesn\'t include "y".')
    assert cfg({"a": ["x", "y"]}, not_contains="y") == no("config_matches", 'a includes "y"; it shouldn\'t.')
    assert cfg({"a": ["x"]}, not_contains="y")["passed"]


@pytest.mark.parametrize("value,said", [
    ({"k": 1}, "an object"), ("café", '"café"'), ("x" * 40, json.dumps("x" * 40)), ("x" * 41, "a different value"),
    ([None, 1], "[null, 1]"), (list(range(8)), "[0, 1, 2, 3, 4, 5, 6, 7]"), (list(range(9)), "a list of 9"),
])
def test_how_a_value_is_said(value, said):
    assert v._say(value) == said


# ---- artifact_present


def present(work, **item):
    return v.artifact_present(work, [item])


def test_artifact_messages(work):
    (work / "f.txt").write_text("12345", encoding="utf-8")
    (work / "d").mkdir()
    (work / "e" / "_delta_log").mkdir(parents=True)
    (work / "bad.json").write_text("{", encoding="utf-8")
    assert present(work, path="f.txt", kind="folder") == no("artifact_present", "f.txt should be a folder.")
    assert present(work, path="f.txt", kind="delta") == no("artifact_present", "f.txt should be a Delta table folder.")
    assert present(work, path="e", kind="delta") == no(
        "artifact_present", "e has no commits in _delta_log, so it isn't a Delta table yet.")
    assert present(work, path="d", kind="file") == no("artifact_present", "d should be a file.")
    assert present(work, path="f.txt", min_bytes=6) == no("artifact_present", "f.txt is 5 bytes; it should be at least 6.")
    assert present(work, path="f.txt", min_bytes=5, max_bytes=5) == ok("artifact_present", "All 1 required items are there.")
    assert present(work, path="bad.json", kind="json")["passed"] is False


def test_a_delta_log_that_isnt_a_folder_isnt_a_delta_table(work):
    (work / "e").mkdir()
    (work / "e" / "_delta_log").write_text("", encoding="utf-8")
    assert present(work, path="e", kind="delta") == no(
        "artifact_present", "e has no commits in _delta_log, so it isn't a Delta table yet.")


# ---- the Delta log


@pytest.mark.parametrize("raw", [None, "", "a\x00b", 5, "/etc/passwd", "C:/x", "s3://b/k"])
def test_a_path_that_isnt_plain_text_is_unsafe(raw):
    assert v._safe_data_path(raw) is False


def test_a_safe_path_is_true():
    assert v._safe_data_path("part-0.parquet") is True


def test_a_log_without_commits_is_refused(work):
    (work / "t" / "_delta_log").mkdir(parents=True)
    with pytest.raises(ws.Refused, match="t has no commits in _delta_log"):
        v.check_log(work, "t")


def test_two_thousand_commits_are_read_and_one_more_is_refused(work):
    assert v.MAX_LOG_ENTRIES == 2000
    log(work, "t", [[op("WRITE")]] * 2000)
    assert len(v.check_log(work, "t")) == 2000
    (work / "t" / "_delta_log" / f"{2000:020d}.json").write_text(json.dumps(op("WRITE")) + "\n", encoding="utf-8")
    with pytest.raises(ws.Refused, match="t has more than 2,000 commits"):
        v.check_log(work, "t")


def test_a_table_folder_named_checkpoint_is_fine(work):
    log(work, "checkpoint", [[op("WRITE")]])
    assert len(v.check_log(work, "checkpoint")) == 1


@pytest.mark.parametrize("body", [b"\xff\xfe", b"[1]\n"])
def test_a_commit_that_isnt_json_objects_is_refused(work, body):
    log(work, "t", [[op("WRITE")]])
    (work / "t" / "_delta_log" / f"{0:020d}.json").write_bytes(body)
    with pytest.raises(ws.Refused, match="Commit 0 of t isn't valid Delta JSON."):
        v.check_log(work, "t")


@pytest.mark.parametrize("name,shown", [("A" * 40, "A" * 40), ("A" * 41, "A" * 40 + "…")])
def test_long_names_are_cut_at_forty(name, shown):
    assert v._name(name) == shown


@pytest.mark.parametrize("n,text", [(8, "a0, a1, a2, a3, a4, a5, a6, a7"),
                                    (9, "a0, a1, a2, a3, a4, a5, a6, a7 and 1 more"),
                                    (10, "a0, a1, a2, a3, a4, a5, a6, a7 and 2 more")])
def test_names_show_eight(n, text):
    assert v._names([f"a{i}" for i in range(n)]) == text


def test_delta_table_wording(work):
    log(work, "one", [[op("WRITE"), add("a.parquet")]])
    assert v.delta_table(work, "one") == ok("delta_table", "one is a Delta table with 1 version and 1 data file, "
                                                          "as the exercise asks.")
    log(work, "two", [[op("WRITE"), add("a.parquet")], [op("MERGE"), add("b.parquet")]])
    assert v.delta_table(work, "two")["detail"] == "two is a Delta table with 2 versions and 2 data files, as the exercise asks."
    log(work, "none", [[{"metaData": {"partitionColumns": ["region"]}}]])
    assert v.delta_table(work, "none")["detail"] == ("none is a Delta table with 1 version and 0 data files, "
                                                     "as the exercise asks.")
    assert v.delta_table(work, "none", operations=["WRITE"]) == no(
        "delta_table", "none's history has no WRITE; it has no operations.")
    assert v.delta_table(work, "none", partition_by=[]) == no(
        "delta_table", "none is partitioned by region; the exercise asks for no partitions.")


def test_delta_table_bounds(work):
    log(work, "two", [[op("MERGE"), add("a.parquet", 100)], [op("WRITE"), add("b.parquet", 200)]])
    assert v.delta_table(work, "two", versions_max=1) == no(
        "delta_table", "two has 2 versions; the exercise allows at most 1.")
    assert v.delta_table(work, "two", files_min=3) == no(
        "delta_table", "two has 2 data files; the exercise expects at least 3.")
    assert v.delta_table(work, "two", avg_file_bytes_min=151) == no(
        "delta_table", "two's data files average 150 bytes; the exercise expects at least 151.")
    assert v.delta_table(work, "two", versions_max=2, files_min=2, avg_file_bytes_min=150)["passed"]
    assert v.delta_table(work, "two", operations=["WRITE", "MERGE"])["passed"]  # in any order by default
    assert v.delta_table(work, "two", operations_in_order=True)["passed"]  # no operations named: nothing to order
    assert v.delta_table(work, "two", operations=["WRITE", "MERGE"], operations_in_order=True) == no(
        "delta_table", "two's history is MERGE, WRITE; the exercise needs WRITE, MERGE in that order.")


def test_an_added_file_without_a_size_counts_as_empty(work):
    log(work, "t", [[op("WRITE"), add("a.parquet", None)]])
    assert v.delta_table(work, "t", avg_file_bytes_min=1) == no(
        "delta_table", "t's data files average 0 bytes; the exercise expects at least 1.")


# ---- pipeline_outcome


def test_an_output_of_an_activity_that_didnt_run():
    run = {"state": "Succeeded", "activities": []}
    assert v.pipeline_outcome(run, outputs={"Copy.rows": 3}) == no(
        "pipeline_outcome", "The activity Copy didn't run, so it has no output.")


# ---- unchanged


def make_tables(con, n):
    names = [f"t{i}" for i in range(n)]
    for t in names:
        con.execute(f"CREATE TABLE {t} (x INTEGER)")
        con.execute(f"INSERT INTO {t} VALUES (1)")
    return names


def test_unchanged_names_eight_tables_and_counts_the_rest(con):
    names = make_tables(con, 10)
    before = v.snapshot(con, names)
    for t in names:
        con.execute(f"INSERT INTO {t} VALUES (2)")
    said = "; ".join(f"{t} went from 1 to 2 rows" for t in names[:8])
    assert v.unchanged(con, names, before) == no(
        "unchanged", f"The answer changed tables it shouldn't touch: {said} and 2 more.")
    assert v.unchanged(con, names[:9], before)["detail"].endswith("t7 went from 1 to 2 rows and 1 more.")
    assert v.unchanged(con, names[:8], before)["detail"].endswith("t7 went from 1 to 2 rows.")


def test_unchanged_names_eight_tables_and_says_others(con):
    names = make_tables(con, 9)
    before = v.snapshot(con, names)
    shown = ", ".join(names[:8])
    assert v.unchanged(con, names, before) == ok("unchanged", f"Nothing else changed: {shown} and others hold the same rows.")
    assert v.unchanged(con, names[:8], before)["detail"] == f"Nothing else changed: {shown} hold the same rows."
