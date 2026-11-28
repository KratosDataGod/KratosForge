"""academy/pipelab.py held to its exact limits and words: 200 activities in a pipeline and 500 in a DAG,
the first five names of a list and "and more" past them, the first three reasons a run can't start, error
text cut at 300 characters, a DAG's concurrency from 1 to the exercise's cap, child pipelines held to the
same rule, a check it doesn't know, and four activities at a time. Pipelines run on the local runner with
control flow only; nothing reaches Fabric or the network."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.academy import pipelab


def act(name, typ="SetVariable", **tp):
    return {"name": name, "type": typ, "dependsOn": [],
            "typeProperties": tp or {"variableName": "result", "value": "x"}}


def pipeline(*activities):
    return json.dumps({"name": "p", "properties": {"activities": list(activities),
                                                   "variables": {"result": {"type": "String"}}}})


def write(tmp_path, text, name="pipeline.json"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def dag(n):
    return {"activities": [{"name": f"n{i}", "path": f"n{i}"} for i in range(n)]}


@pytest.mark.parametrize("count, ok", [(200, True), (201, False)])
def test_a_pipeline_may_hold_200_activities(tmp_path, count, ok):
    got = pipelab.run_pipeline(write(tmp_path, pipeline(*[act(f"a{i}") for i in range(count)])), "pipeline.json")
    assert got["ok"] is ok
    if not ok:
        assert got == {"ok": False, "detail": "The pipeline has more than 200 activities.", "run": None}


@pytest.mark.parametrize("count, ok", [(500, True), (501, False)])
def test_a_dag_may_hold_500_activities(count, ok):
    got = pipelab.dag_matches(dag(count), ["n0"])
    assert got["passed"] is ok
    assert got["detail"] == ("The DAG runs 500 notebooks in a valid order." if ok else "The DAG has more than 500 activities.")


@pytest.mark.parametrize("count, said", [
    (5, "a0 (Copy), a1 (Copy), a2 (Copy), a3 (Copy), a4 (Copy)."),
    (6, "a0 (Copy), a1 (Copy), a2 (Copy), a3 (Copy), a4 (Copy) and more."),
])
def test_activities_it_cant_run_are_named_five_at_most_then_and_more(tmp_path, count, said):
    got = pipelab.run_pipeline(write(tmp_path, pipeline(*[act(f"a{i}", "Copy") for i in range(count)])), "pipeline.json")
    assert got == {"ok": False, "run": None,
                   "detail": "This exercise runs control-flow activities only, and the exercise doesn't stand in for " + said}


def test_a_run_that_cant_start_names_its_first_three_reasons(tmp_path):
    mocks = {f"ghost{i}": {"status": "Succeeded"} for i in range(4)}
    got = pipelab.run_pipeline(write(tmp_path, pipeline(act("a"))), "pipeline.json", mocks=mocks)
    assert got == {"ok": False, "run": None, "detail": "The pipeline couldn't start: There is no activity 'ghost0' to mock.; "
                   "There is no activity 'ghost1' to mock.; There is no activity 'ghost2' to mock."}


def test_a_definition_it_cant_read_is_named_with_300_characters_of_why(tmp_path):
    text = json.dumps({"definition": {"parts": [{"path": "pipeline-content.json", "payload": "x", "payloadType": "O" * 400}]}})
    got = pipelab.run_pipeline(write(tmp_path, text), "pipeline.json")
    why = "The pipeline-content.json part is '" + "O" * 400
    assert got == {"ok": False, "run": None, "detail": f"pipeline.json isn't a pipeline the app can read: {why[:300]}"}


def test_a_dag_that_isnt_valid_says_why_in_300_characters():
    got = pipelab.dag_matches({"activities": [{"name": "a", "path": "a", "dependencies": ["x" * 400]}]}, ["a"])
    assert got == {"kind": "dag_matches", "passed": False,
                   "detail": "The DAG isn't valid: " + ("runMultiple: unknown dependency " + "x" * 400)[:300]}


def test_missing_and_extra_notebooks_are_named_five_at_most():
    assert pipelab.dag_matches(dag(1), [f"m{i}" for i in range(7)])["detail"] == "The DAG doesn't run m0, m1, m2, m3, m4."
    assert pipelab.dag_matches(dag(7), ["n0"], exact=True)["detail"] == (
        "The DAG also runs n1, n2, n3, n4, n5, which the exercise doesn't ask for.")
    assert pipelab.dag_matches(dag(7), ["n0"])["passed"]   # extra notebooks are fine unless exact


@pytest.mark.parametrize("concurrency, ok", [(0, False), (1, True), (4, True), (5, False)])
def test_a_dags_concurrency_is_1_to_the_exercises_cap(concurrency, ok):
    got = pipelab.dag_matches({**dag(2), "concurrency": concurrency}, ["n0"], max_concurrency=4)
    assert got["passed"] is ok
    if not ok:
        assert got["detail"] == f"The DAG's concurrency is {concurrency}; the exercise needs 1 to 4."


def test_a_child_pipeline_is_held_to_the_same_rule(tmp_path):
    work = write(tmp_path, pipeline(act("a")))
    bad = pipelab.run_pipeline(work, "pipeline.json", children={"child": pipeline(act("c", "Copy"))})
    assert bad == {"ok": False, "run": None, "detail": "Child pipeline child holds activities the exercise can't run."}
    good = pipelab.run_pipeline(work, "pipeline.json", children={"child": pipeline(act("c"))})
    assert good["ok"] is True and good["run"]["state"] == "Succeeded"


def test_a_pipeline_runs_four_activities_at_a_time(tmp_path, monkeypatch):
    seen = {}
    real = pipelab.PipelineRun

    def record(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(pipelab, "PipelineRun", record)
    assert pipelab.run_pipeline(write(tmp_path, pipeline(act("a"))), "pipeline.json")["ok"]
    assert seen["max_parallel"] == 4 and seen["wait_scale"] == 0.0 and seen["timeout_seconds"] == 20.0


def test_a_check_it_doesnt_know_and_a_dag_it_cant_read_fail_in_words(tmp_path):
    ex = {"starter": "dag.json", "validation": [{"kind": "row_count"}, {"kind": "dag_matches", "notebooks": ["a"]}]}
    got = pipelab.grade(tmp_path, ex)
    first, second = got["results"]
    assert got["passed"] is False
    assert first == {"kind": "row_count", "passed": False, "detail": "A pipeline exercise can't check row_count."}
    assert second["kind"] == "dag_matches" and second["passed"] is False and second["detail"]


def test_grading_without_the_exercises_folder_runs_no_child_pipelines(tmp_path):
    write(tmp_path, pipeline(act("a")))
    ex = {"starter": "pipeline.json", "children": {"child": "child.json"},
          "validation": [{"kind": "pipeline_outcome", "activities": {"a": "Succeeded"}}]}
    assert pipelab.grade(tmp_path, ex)["passed"] is True


def test_only_a_pipeline_outcome_checks_its_status_words():
    assert pipelab.check_problems({"kind": "pipeline_outcome", "status": 3}) == [
        "check pipeline_outcome's status is Succeeded, Failed or Cancelled"]
    assert pipelab.check_problems({"kind": "dag_matches", "notebooks": ["a"], "status": 3}) == []
