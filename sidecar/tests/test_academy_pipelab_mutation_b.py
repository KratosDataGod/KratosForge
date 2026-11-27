"""More pipeline_lab tests, from a mutation run: the size limits at their edges, the exact words for a
blocked activity, a refused child pipeline and a run that can't start, and dag_matches' bounds. Pipelines
run only on the local runner with control flow and mocks, as the lab runs them."""

from __future__ import annotations

import json
import socket

import pytest

from fabric_desktop.academy import pipelab
from fabric_desktop.orchestration import definition
from tests.test_academy_pipelab import DAG, _write, act, pipeline

WAIT = act("Wait a bit", "Wait", waitTimeInSeconds=1)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("pipeline exercises must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _many(n, typ="Wait"):
    tp = {"waitTimeInSeconds": 1} if typ == "Wait" else {}
    return json.dumps({"name": "p", "properties": {"activities": [act(f"s{i}", typ, **tp) for i in range(n)]}})


# ---- limits at their edges

def test_a_pipeline_of_exactly_200_activities_runs(tmp_path):
    got = pipelab.run_pipeline(_write(tmp_path, _many(200)), "pipeline.json")
    assert got["ok"], got["detail"]


def test_a_dag_of_exactly_500_activities_is_ordered_and_501_is_not():
    five_hundred = {"activities": [{"name": f"nb{i}", "path": f"nb{i}"} for i in range(500)]}
    assert pipelab.dag_matches(five_hundred, ["nb0"])["passed"]
    got = pipelab.dag_matches([f"nb{i}" for i in range(501)], ["nb0"])
    assert got["detail"] == "The DAG has more than 500 activities."


def test_an_unreadable_definition_is_cut_to_300_characters(tmp_path):
    text = json.dumps({"parts": [{"path": definition.CONTENT_PART, "payloadType": "Z" * 600}]})
    got = pipelab.run_pipeline(_write(tmp_path, text), "pipeline.json")
    prefix = "pipeline.json isn't a pipeline the app can read: "
    assert got["ok"] is False and got["detail"].startswith(prefix) and len(got["detail"]) == len(prefix) + 300


def test_an_invalid_dag_is_cut_to_300_characters():
    got = pipelab.dag_matches({"activities": [{"name": "a", "path": "a", "dependencies": ["x" * 600]}]}, ["a"])
    prefix = "The DAG isn't valid: "
    assert got["detail"].startswith(prefix) and len(got["detail"]) == len(prefix) + 300


# ---- what a run refuses

def test_five_blocked_activities_are_named_and_a_sixth_says_and_more(tmp_path):
    head = "This exercise runs control-flow activities only, and the exercise doesn't stand in for "
    five = ", ".join(f"s{i} (Copy)" for i in range(5))
    got = pipelab.run_pipeline(_write(tmp_path, _many(5, "Copy")), "pipeline.json")
    assert got == {"ok": False, "run": None, "detail": head + five + "."}
    got = pipelab.run_pipeline(_write(tmp_path, _many(6, "Copy")), "pipeline.json")
    assert got["detail"] == head + five + " and more."


def test_a_child_pipeline_with_activities_it_cant_run_is_refused(tmp_path):
    work = _write(tmp_path, pipeline(WAIT))
    got = pipelab.run_pipeline(work, "pipeline.json", children={"child": pipeline(act("Copy", "Copy"))})
    assert got == {"ok": False, "run": None, "detail": "Child pipeline child holds activities the exercise can't run."}
    got = pipelab.run_pipeline(work, "pipeline.json", children={"child": pipeline(WAIT)})
    assert got["ok"] is True and got["run"]["state"] == "Succeeded"


def test_a_run_that_cant_start_names_its_first_three_problems(tmp_path):
    mocks = {name: {"status": "Succeeded"} for name in ("ma", "mb", "mc", "md")}
    got = pipelab.run_pipeline(_write(tmp_path, pipeline(WAIT)), "pipeline.json", mocks=mocks)
    assert got["ok"] is False and got["run"] is None
    assert got["detail"].startswith("The pipeline couldn't start: ")
    assert got["detail"].count("There is no activity") == 3 and got["detail"].count("; ") == 2


def test_the_run_uses_at_most_four_activities_at_once(tmp_path, monkeypatch):
    seen = {}
    real = pipelab.PipelineRun

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(pipelab, "PipelineRun", spy)
    assert pipelab.run_pipeline(_write(tmp_path, pipeline(WAIT)), "pipeline.json")["ok"]
    assert seen["max_parallel"] == 4 and seen["wait_scale"] == 0.0


# ---- dag_matches

def test_extra_notebooks_are_fine_unless_the_check_is_exact():
    assert pipelab.dag_matches(DAG, ["bronze"])["passed"]


def test_missing_and_extra_notebooks_are_named_five_at_most():
    names = [f"nb{i}" for i in range(7)]
    dag = {"activities": [{"name": "a", "path": "a"}]}
    assert pipelab.dag_matches(dag, ["a", *names])["detail"] == "The DAG doesn't run nb0, nb1, nb2, nb3, nb4."
    dag = {"activities": [{"name": x, "path": x} for x in ["a", *names]]}
    assert pipelab.dag_matches(dag, ["a"], exact=True)["detail"] == (
        "The DAG also runs nb0, nb1, nb2, nb3, nb4, which the exercise doesn't ask for.")


@pytest.mark.parametrize(("concurrency", "passed"), [(0, False), (1, True), (3, True), (4, False)])
def test_concurrency_is_between_one_and_the_cap(concurrency, passed):
    got = pipelab.dag_matches({**DAG, "concurrency": concurrency}, ["bronze"], max_concurrency=3)
    assert got["passed"] is passed, got
    if not passed:
        assert got["detail"] == f"The DAG's concurrency is {concurrency}; the exercise needs 1 to 3."


# ---- checks

def test_check_problems_only_reads_a_pipeline_outcomes_fields():
    assert pipelab.check_problems({"kind": "pipeline_outcome", "status": 5}) == [
        "check pipeline_outcome's status is Succeeded, Failed or Cancelled"]
    assert pipelab.check_problems({"kind": "dag_matches", "notebooks": ["a"], "status": 5}) == []


def test_an_unknown_check_kind_fails_in_words(tmp_path):
    assert pipelab.run_check(tmp_path, {"kind": "nope"}, {}) == {
        "kind": "nope", "passed": False, "detail": "A pipeline exercise can't check nope."}


def test_a_dag_check_without_its_file_fails_in_words(tmp_path):
    got = pipelab.run_check(tmp_path, {"kind": "dag_matches", "notebooks": ["a"]}, {"starter": "dag.json"})
    assert got["kind"] == "dag_matches" and got["passed"] is False and "dag.json" in got["detail"]


def test_grade_reads_child_pipelines_only_from_the_exercise_folder(tmp_path):
    folder, work = tmp_path / "ex", tmp_path / "work"
    folder.mkdir()
    work.mkdir()
    (folder / "child.json").write_text(pipeline(act("Copy", "Copy")), encoding="utf-8")
    _write(work, pipeline(WAIT))
    ex = {"starter": "pipeline.json", "children": {"child": "child.json"},
          "validation": [{"kind": "pipeline_outcome"}]}
    got = pipelab.grade(work, ex, folder)
    assert got["results"][0]["detail"] == "Child pipeline child holds activities the exercise can't run."
    assert pipelab.grade(work, ex)["passed"]
