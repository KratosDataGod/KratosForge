"""ACD-012: pipeline_lab exercises run the learner's pipeline on the local runner with control flow and
the exercise's mocks only, check runMultiple DAGs as data, and fail anything else in words."""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from fabric_desktop.academy import exercises, pipelab, registry


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("pipeline exercises must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def pipeline(*activities, variables=None):
    props = {"activities": list(activities), "variables": variables or {"result": {"type": "String"}}}
    return json.dumps({"name": "load_orders", "properties": props})


def act(name, typ, depends=(), conditions=("Succeeded",), **tp):
    deps = [{"activity": d, "dependencyConditions": list(conditions)} for d in depends]
    return {"name": name, "type": typ, "dependsOn": deps, "typeProperties": tp}


COPY = act("Copy orders", "Copy")
ON_FAIL = act("Log failure", "SetVariable", ["Copy orders"], ["Failed"], variableName="result", value="failed")
ON_OK = act("Log success", "SetVariable", ["Copy orders"], ["Succeeded"], variableName="result",
            value="@string(activity('Copy orders').output.rowsCopied)")
SOLUTION = pipeline(COPY, ON_OK, ON_FAIL)
WRONG = {"wrong-no-handler.json": pipeline(COPY, ON_OK),
         "wrong-always.json": pipeline(COPY, {**ON_OK, "dependsOn": [{"activity": "Copy orders",
                                                                      "dependencyConditions": ["Completed"]}]},
                                       {**ON_FAIL, "typeProperties": {"variableName": "result", "value": "ok"}})}
EX = {"id": "E90", "starter": "pipeline.json",
      "mocks": {"Copy orders": {"status": "Failed", "error": {"message": "source offline"}}},
      "validation": [{"kind": "pipeline_outcome", "status": "Failed",
                      "activities": {"Log failure": "Succeeded", "Log success": "Skipped"}}]}


def _write(tmp_path: Path, text: str, name: str = "pipeline.json") -> Path:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_reference_passes_and_wrong_answers_fail(tmp_path):
    folder, grading = tmp_path / "ex", tmp_path / "grading"
    folder.mkdir()
    grading.mkdir()
    (grading / "solution.json").write_text(SOLUTION)
    for name, text in WRONG.items():
        (grading / name).write_text(text)
    result = pipelab.self_test(folder, grading, EX)
    assert result["solution"]["passed"], result["solution"]
    no_handler = result["wrong"]["wrong-no-handler.json"]["results"][0]
    assert not no_handler["passed"] and "Log failure didn't run" in no_handler["detail"]
    assert not result["wrong"]["wrong-always.json"]["passed"]


def test_outputs_flow_from_a_mock(tmp_path):
    ex = {**EX, "mocks": {"Copy orders": {"output": {"rowsCopied": 120}}},
          "validation": [{"kind": "pipeline_outcome", "activities": {"Log success": "Succeeded"}}]}
    res = pipelab.grade(_write(tmp_path, SOLUTION), ex)
    assert res["passed"], res


@pytest.mark.parametrize("typ", ["Copy", "WebActivity", "TridentNotebook", "Script", "Lookup", "MadeUpActivity"])
def test_activities_outside_control_flow_need_the_exercises_mock(tmp_path, typ):
    text = pipeline(act("Call out", typ, url="https://example.com"), ON_FAIL)
    res = pipelab.grade(_write(tmp_path, text), EX)
    assert not res["passed"]
    assert "runs control-flow activities only" in res["results"][0]["detail"]
    assert f"Call out ({typ})" in res["results"][0]["detail"]


def test_waits_take_no_time_and_a_loop_that_never_ends_is_stopped(tmp_path):
    forever = act("Spin", "Until", expression={"value": "@equals(1, 2)", "type": "Expression"},
                  activities=[act("Pause", "Wait", waitTimeInSeconds=3600)], timeout="0.12:00:00")
    got = pipelab.run_pipeline(_write(tmp_path, pipeline(forever)), "pipeline.json", time_limit=1.0)
    assert got["ok"] and got["run"]["state"] == "Failed" and got["run"]["stop_reason"] == "timed_out"


@pytest.mark.parametrize(("value", "failed"), [
    ("@concat(variables('s'), variables('s'))", "Double"),
    ("@{variables('s')}@{variables('s')}", "Double"),
])
def test_a_value_that_doubles_in_a_loop_is_stopped_before_it_eats_memory(tmp_path, value, failed):
    grow = act("Grow", "Until", expression={"value": "@equals(1, 2)", "type": "Expression"}, timeout="0.00:10:00",
               activities=[act("Double", "SetVariable", variableName="t", value=value),
                           act("Keep", "SetVariable", ["Double"], variableName="s", value="@variables('t')")])
    text = pipeline(grow, variables={"s": {"type": "String", "defaultValue": "x" * 1000}, "t": {"type": "String"}})
    got = pipelab.run_pipeline(_write(tmp_path, text), "pipeline.json")
    assert got["ok"] and got["run"]["state"] == "Failed" and got["run"]["seconds"] < 10, got["run"]["seconds"]
    stopped = [a for a in got["run"]["activities"] if a["name"] == failed and a["status"] == "Failed"]
    assert stopped and "the limit for one pipeline value" in json.dumps(stopped[0].get("error"))


def test_an_array_that_grows_in_a_loop_is_stopped(tmp_path):
    grow = act("Grow", "Until", expression={"value": "@equals(1, 2)", "type": "Expression"}, timeout="0.00:10:00",
               activities=[act("Add", "AppendVariable", variableName="xs", value="@variables('s')")])
    text = pipeline(grow, variables={"s": {"type": "String", "defaultValue": "y" * 100_000},
                                     "xs": {"type": "Array", "defaultValue": []}})
    got = pipelab.run_pipeline(_write(tmp_path, text), "pipeline.json")
    assert got["ok"] and got["run"]["state"] == "Failed"
    added = [a for a in got["run"]["activities"] if a["name"] == "Add" and a["status"] == "Failed"]
    assert added and "LocalValueTooBig" in json.dumps(added[0].get("error"))


@pytest.mark.parametrize(("text", "said"), [
    ("{not json", "isn't a pipeline the app can read"),
    ("[" * 100_000, "isn't a pipeline the app can read"),
    (json.dumps({"name": "p", "properties": {"activities": [act(f"s{i}", "Wait") for i in range(201)]}}),
     "more than 200 activities"),
], ids=["bad-json", "deep-nesting", "too-many-activities"])
def test_a_pipeline_file_that_cant_be_read_fails_in_words(tmp_path, text, said):
    res = pipelab.grade(_write(tmp_path, text), EX)
    assert not res["passed"] and said in res["results"][0]["detail"]


def test_a_linked_pipeline_file_is_refused(tmp_path):
    secret = tmp_path / "elsewhere.json"
    secret.write_text(SOLUTION)
    work = tmp_path / "work"
    work.mkdir()
    try:
        (work / "pipeline.json").symlink_to(secret)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    res = pipelab.grade(work, EX)
    assert not res["passed"]


# --- runMultiple DAGs

DAG = {"activities": [{"name": "bronze", "path": "nb_bronze"},
                      {"name": "silver", "path": "nb_silver", "dependencies": ["bronze"]},
                      {"name": "gold", "path": "nb_gold", "dependencies": ["silver"]}],
       "concurrency": 2}


@pytest.mark.parametrize(("dag", "said"), [
    (DAG, None),
    ({**DAG, "activities": DAG["activities"][:2]}, "doesn't run gold"),
    ({**DAG, "activities": [DAG["activities"][0], {"name": "silver", "path": "nb_silver"}, DAG["activities"][2]]},
     "silver can start before bronze finishes"),
    ({**DAG, "activities": [*DAG["activities"], {"name": "extra", "path": "nb_x"}]}, "also runs extra"),
    ({**DAG, "concurrency": 50}, "concurrency is 50"),
    ({**DAG, "activities": [{**DAG["activities"][0], "dependencies": ["gold"]}, *DAG["activities"][1:]]}, "cycle"),
    ({**DAG, "schedule": "daily"}, "doesn't support schedule"),
    (["nb_a", "nb_a"], "listed twice"),
    ("not a dag", "isn't valid"),
])
def test_dag_matches(dag, said):
    out = pipelab.dag_matches(dag, ["bronze", "silver", "gold"], after=[["bronze", "silver"], ["bronze", "gold"]],
                              exact=True, max_concurrency=4)
    assert out["passed"] is (said is None), out
    if said:
        assert said in out["detail"]


def test_dag_checks_read_the_workspace_file(tmp_path):
    ex = {"starter": "dag.json", "validation": [{"kind": "dag_matches", "notebooks": ["bronze", "gold"]}]}
    assert pipelab.grade(_write(tmp_path, json.dumps(DAG), "dag.json"), ex)["passed"]


# --- wired into the exercise format

PACK = "dp-700"


@pytest.fixture
def pipeline_pack(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    folder, grading = root / PACK / "exercises" / "E36", root / PACK / "grading" / "E36"
    shutil.rmtree(grading)
    grading.mkdir()
    for p in folder.iterdir():
        if p.suffix == ".sql":
            p.unlink()
    path = folder / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update({"adapter": "pipeline_lab", "engine_label": "The pipeline runner on this PC", "starter": "pipeline.json",
               "mocks": EX["mocks"], "validation": EX["validation"]})
    ex.pop("setup", None)
    path.write_text(json.dumps(ex), encoding="utf-8")
    (folder / "pipeline.json").write_text(pipeline(COPY, ON_OK), encoding="utf-8")
    (grading / "solution.json").write_text(SOLUTION, encoding="utf-8")
    for name, text in WRONG.items():
        (grading / name).write_text(text, encoding="utf-8")
    return root


def test_a_pipeline_exercise_is_well_formed_and_self_tests(pipeline_pack):
    assert exercises.problems(pipeline_pack) == []
    result = exercises.self_test(PACK, "E36", pipeline_pack)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values())


@pytest.mark.parametrize(("change", "remove", "message"), [
    ({}, "grading/E36/solution.json", "reference solution at grading/E36/solution.json"),
    ({}, "grading/E36/wrong-always.json", "at least two planted wrong answers (grading/E36/wrong-*.json)"),
    ({"starter": "answer.py"}, None, "starts from a pipeline or DAG file"),
    ({"validation": [{"kind": "result_matches"}]}, None, "pipeline_lab can't check result_matches"),
    ({"validation": [{"kind": "dag_matches"}]}, None, "check dag_matches needs notebooks"),
    ({"mocks": ["Copy orders"]}, None, "mocks and children map"),
    ({"children": {"child": "../grading/E36/solution.json"}}, None, "isn't a plain file name"),
])
def test_a_pipeline_exercise_that_cant_be_graded_is_caught(pipeline_pack, change, remove, message):
    path = pipeline_pack / PACK / "exercises" / "E36" / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update(change)
    path.write_text(json.dumps(ex), encoding="utf-8")
    if remove:
        (pipeline_pack / PACK / remove).unlink()
    if change.get("starter") == "answer.py":
        (pipeline_pack / PACK / "exercises" / "E36" / "answer.py").write_text("x = 1")
    found = exercises.problems(pipeline_pack)
    assert any(message in p for p in found), found


def test_a_mocked_or_inactive_container_runs_none_of_its_children(tmp_path):
    each = act("Each", "ForEach", items={"value": "@createArray(1, 2)", "type": "Expression"},
               activities=[act("Copy one", "Copy")])
    off = {**act("Off", "IfCondition", expression={"value": "@true()", "type": "Expression"},
                 ifTrueActivities=[act("Copy two", "Copy")]), "state": "Inactive"}
    done = act("Done", "SetVariable", ["Each", "Off"], variableName="result", value="ok")
    got = pipelab.run_pipeline(_write(tmp_path, pipeline(each, off, done)), "pipeline.json",
                               mocks={"Each": {"status": "Succeeded"}})
    assert got["ok"], got["detail"]
    assert got["run"]["state"] == "Succeeded"
    # An active, unmocked container is still walked: its Copy needs a mock.
    got = pipelab.run_pipeline(_write(tmp_path, pipeline(each, done)), "pipeline.json")
    assert not got["ok"] and "Copy one (Copy)" in got["detail"]


def test_variables_hold_copies_not_references(tmp_path):
    steps = [act("Alias", "SetVariable", variableName="b", value="@variables('a')"),
             act("Grow", "AppendVariable", ["Alias"], variableName="a", value=2),
             act("Self", "AppendVariable", ["Grow"], variableName="a", value="@variables('a')"),
             act("Show", "SetVariable", ["Self"], variableName="result",
                 value="@{string(variables('b'))}|@{string(variables('a'))}")]
    text = pipeline(*steps, variables={"a": {"type": "Array", "defaultValue": [1]}, "b": {"type": "Array"},
                                       "result": {"type": "String"}})
    got = pipelab.run_pipeline(_write(tmp_path, text), "pipeline.json")
    assert got["ok"] and got["run"]["state"] == "Succeeded", got
    json.dumps(got["run"])   # no list holds itself, so the record still serialises
    show = [a for a in got["run"]["activities"] if a["name"] == "Show"][0]
    assert show["output"]["value"].replace(" ", "") == "[1]|[1,2,[1,2]]", show["output"]


@pytest.mark.parametrize("form", ["dag", "list"])
def test_a_dag_too_big_is_refused_before_it_is_ordered(monkeypatch, form):
    n = pipelab.MAX_DAG_ACTIVITIES + 1
    names = [f"nb{i}" for i in range(n)]
    dag = {"activities": [{"name": x, "path": x} for x in names]} if form == "dag" else names

    def never(_dag):
        raise AssertionError("a DAG over the limit must not be ordered")
    monkeypatch.setattr(pipelab, "_dag_order", never)
    got = pipelab.dag_matches(dag, ["nb0"])
    assert not got["passed"] and f"more than {pipelab.MAX_DAG_ACTIVITIES} activities" in got["detail"]
