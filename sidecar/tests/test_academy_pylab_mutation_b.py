"""Mutation batch 415: academy/pylab.py. Tests for the survivors of its first score (see
docs/quality/mutation-testing.md). Nothing here runs a learner's script: the checks read files the
test wrote, and grade() gets a stand-in for the learner process."""

from __future__ import annotations

import json

import duckdb
import pytest

from fabric_desktop.academy import pylab

RAN = {"status": "ok", "stdout": "", "stderr": "", "seconds": 1.0}
NO_Y = [{"name": "y"}]


def _parquet(work, name: str) -> None:
    duckdb.sql(f"COPY (SELECT 1 AS x) TO '{(work / name).as_posix()}' (FORMAT parquet)")


def test_a_delta_lab_exercise_cant_run_in_an_appcontainer():
    assert pylab.appcontainer_ok({"adapter": "delta_lab"}) is False
    assert pylab.appcontainer_ok({"adapter": "python_lab"}) is True


def test_a_python_exercise_cant_run_another_kind_of_check(tmp_path):
    assert pylab.run_check(tmp_path, {"kind": "sql_query"}, RAN) == {
        "kind": "sql_query", "passed": False, "detail": "A Python exercise can't check sql_query."}


def test_a_settings_file_the_script_wrote_is_checked(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"pool": {"nodes": 2}}), encoding="utf-8")
    c = {"kind": "config_matches", "path": "s.json", "rules": [{"path": "pool.nodes", "min": 1}]}
    assert pylab.run_check(tmp_path, c, RAN)["passed"] is True
    c["rules"] = [{"path": "pool.nodes", "min": 3}]
    assert pylab.run_check(tmp_path, c, RAN)["passed"] is False


def test_a_table_is_named_after_its_file_cut_to_40_characters(tmp_path):
    _parquet(tmp_path, "a" * 50 + ".parquet")
    out = pylab.run_check(tmp_path, {"kind": "schema_matches", "path": "a" * 50 + ".parquet", "columns": NO_Y}, RAN)
    assert out["detail"] == f"{'a' * 40} has no column y."


def test_a_table_whose_file_name_starts_with_a_digit_gets_a_letter_first(tmp_path):
    _parquet(tmp_path, "1abc.parquet")
    out = pylab.run_check(tmp_path, {"kind": "schema_matches", "path": "1abc.parquet", "columns": NO_Y}, RAN)
    assert out["detail"] == "t_1abc has no column y."


def test_a_path_with_no_file_name_names_the_table_answer(tmp_path):
    _parquet(tmp_path, "t.parquet")
    out = pylab.run_check(tmp_path, {"kind": "schema_matches", "path": ".", "columns": NO_Y}, RAN)
    assert out["detail"] == "answer has no column y."


def test_a_missing_table_file_is_a_fail_in_words(tmp_path):
    out = pylab.run_check(tmp_path, {"kind": "data_quality", "path": "out.parquet", "rules": [{"rule": "x"}]}, RAN)
    assert out["kind"] == "data_quality" and out["passed"] is False and "out.parquet" in out["detail"]


def test_a_file_duckdb_cant_read_is_named_with_200_characters_of_the_error(tmp_path):
    name = "b" * 150 + ".parquet"
    (tmp_path / name).write_bytes(b"x" * 100)
    out = pylab.run_check(tmp_path, {"kind": "schema_matches", "path": name, "columns": NO_Y}, RAN)
    head = f"{name} couldn't be read as parquet: "
    assert out["passed"] is False and out["detail"].startswith(head) and len(out["detail"]) == len(head) + 200


def test_an_error_line_is_cut_to_300_characters():
    out = pylab._ran({"status": "error", "stdout": "", "stderr": "Traceback\n" + "e" * 400 + "\n"}, None)
    assert out["detail"] == "Your script stopped with an error: " + "e" * 300


def test_grade_keeps_the_last_20000_characters_of_each_stream(tmp_path, monkeypatch):
    def fake_run(*_a, **_k):
        return {"status": "ok", "exit_code": 0, "seconds": 1.0, "limits": {}, "runs_as": "test",
                "stdout": "o" * 100 + "O" * 20_000, "stderr": "e" * 100 + "E" * 20_000}
    monkeypatch.setattr(pylab.sandbox, "run", fake_run)
    out = pylab.grade(tmp_path, "answer.py", [{"kind": "notebook_ran"}], tmp_path)
    assert out["passed"] is True
    assert out["run"]["stdout"] == "O" * 20_000 and out["run"]["stderr"] == "E" * 20_000


@pytest.mark.parametrize("kind", ["schema_matches", "data_quality"])
def test_a_table_check_with_a_bad_format_is_named(kind):
    assert f"check {kind}'s format is one of parquet, csv, json" in pylab.check_problems(
        {"kind": kind, "path": "a", "columns": ["x"], "rules": ["x"], "format": "xlsx"})
