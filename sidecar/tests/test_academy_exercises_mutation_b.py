"""Mutation-testing follow-up for academy/exercises.py: how self_test picks the adapter's own self-test,
completes() at the edges of evidence strength, reference() and run_check(), and the checker's edges.
Nothing here runs a learner's or a planted answer outside an existing helper: the adapters' self-tests are
stood in for, and the one real run is a duckdb_lab reference in the in-memory grade()."""

from __future__ import annotations

import json
import shutil

import duckdb
import pytest

from fabric_desktop.academy import (configlab, dbsql_lab, exercises, gitlab, pipelab, pylab, pyspark_lab, quizlab,
                                    registry, sqlprojlab)

PACK = "dp-700"
DBX = "dbx-de-associate"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "packs"
    shutil.copytree(registry.ROOT, r)
    return r


def _edit(root, eid, change, files=None, pack=PACK):
    path = root / pack / "exercises" / eid / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update(change)
    path.write_text(json.dumps(ex), encoding="utf-8")
    for rel, text in (files or {}).items():
        target = root / pack / rel
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return [p for p in exercises.problems(root) if p.startswith(f"{pack} {eid}:")]


def _has(found, text):
    return any(text in p for p in found)


# --- self_test sends each adapter to its own self-test ------------------------------------------------


@pytest.fixture
def routed(monkeypatch):
    for name, mod in (("pylab", pylab), ("pipelab", pipelab), ("configlab", configlab), ("gitlab", gitlab),
                      ("sqlprojlab", sqlprojlab), ("quizlab", quizlab), ("dbsql_lab", dbsql_lab),
                      ("pyspark_lab", pyspark_lab)):
        monkeypatch.setattr(mod, "self_test", lambda *a, _n=name, **k: {"by": _n})


@pytest.mark.parametrize(("pack", "eid", "by"), [
    (DBX, "E15", "pylab"), (PACK, "E22", "pylab"), (PACK, "E19", "pipelab"), (PACK, "E01", "configlab"),
    (PACK, "E06", "gitlab"), (PACK, "E08", "sqlprojlab"), (PACK, "E05", "quizlab"), (DBX, "E20", "dbsql_lab"),
    (PACK, "E20", "pyspark_lab"),
])
def test_self_test_goes_to_the_adapters_own(routed, pack, eid, by):
    assert exercises.self_test(pack, eid) == {"by": by}


def test_a_duckdb_lab_self_test_grades_its_reference_here(routed):
    out = exercises.self_test(PACK, "E36")
    assert out["solution"]["passed"] is True
    assert sorted(out["wrong"]) == sorted(exercises.reference(PACK, "E36")["wrong"])
    assert not any(r["passed"] for r in out["wrong"].values())


def test_an_adapter_with_no_self_test_is_refused(routed, root):
    _edit(root, "E36", {"adapter": "fabric"})
    with pytest.raises(ValueError, match="E36: only duckdb_lab, dbsql_lab, pyspark_lab"):
        exercises.self_test(PACK, "E36", root)


# --- completes, get, exercises, reference, run_check ----------------------------------------------------


def test_local_evidence_is_weaker_than_a_platforms():
    assert exercises.completes({"best_local": "fabric_verified"}, "local_verified") is False
    assert exercises.completes({"best_local": "fabric_verified"}, "fabric_verified") is True
    assert exercises.STRENGTH["local_verified"] == 2


def test_knowledge_is_weaker_than_a_simulation():
    assert exercises.completes({"best_local": "simulated"}, "knowledge_only") is False
    assert exercises.completes({"best_local": "knowledge_only"}, "knowledge_only") is True


def test_an_unknown_best_local_counts_as_local_verified():
    assert exercises.completes({"best_local": "bogus"}, "local_verified") is True
    assert exercises.completes({"best_local": "bogus"}, "simulated") is False


@pytest.mark.parametrize("eid", ["E99", "X1", "../E36"])
def test_get_refuses_an_exercise_that_isnt_there(eid):
    with pytest.raises(ValueError, match=f"{PACK} has no exercise"):
        exercises.get(PACK, eid)


def test_exercises_lists_drafts_unless_asked_not_to():
    every = exercises.exercises(PACK)
    assert any(ex["status"] == "draft" for ex in every)
    assert all(ex["status"] == "live" for ex in exercises.exercises(PACK, include_drafts=False))


def test_a_learn_lab_exercise_takes_the_labs_starter():
    from fabric_desktop.learn import content as learn_content
    m = learn_content.get("LAB-04")
    lex = next(e for e in m["exercises"] if e["id"] == "rebuild")
    assert exercises.reference(PACK, "E37")["starter"] == learn_content.file_text("LAB-04", lex["starter"])


def test_reference_reads_the_planted_answers_from_grading(root):
    assert len(exercises.reference(PACK, "E36")["wrong"]) == 5
    shutil.rmtree(root / PACK / "grading" / "E36")
    (root / PACK / "grading" / "E36").mkdir()
    (root / PACK / "grading" / "E36" / "solution.sql").write_text("SELECT 1", encoding="utf-8")
    assert exercises.reference(PACK, "E36", root)["wrong"] == {}


def test_sql_that_fails_to_run_fails_the_check():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (a INTEGER)")
    out = exercises.run_check(con, {"kind": "table_shape", "table": "t"}, "INSERT INTO t VALUES ('x')", "")
    assert out["passed"] is False and out["detail"].startswith("Your SQL didn't run:")


# --- the checker's edges -------------------------------------------------------------------------------


def test_version_starts_at_one(root):
    assert _has(_edit(root, "E36", {"version": 0}), "version is a whole number from 1")
    assert not _has(_edit(root, "E36", {"version": 1}), "version is a whole number")


@pytest.mark.parametrize(("minutes", "bad"), [(0, True), (1, False), (240, False), (241, True), ("5", True)])
def test_minutes_run_from_1_to_240(root, minutes, bad):
    assert _has(_edit(root, "E36", {"minutes": minutes}), "minutes is a whole number from 1 to 240") is bad


def test_a_fabric_pack_cant_hold_databricks_work(root):
    assert _has(_edit(root, "E36", {"class": "databricks_verified"}), "a Fabric pack can't hold Databricks exercises")


def test_blank_hints_are_caught(root):
    assert _has(_edit(root, "E36", {"hints": ["  "]}), "hints is a list of sentences")


def test_a_prerequisite_exercise_must_exist(root):
    assert _has(_edit(root, "E36", {"prerequisites": ["E99"]}), "unknown prerequisite E99")
    assert not _has(_edit(root, "E36", {"prerequisites": ["E21", "LAB-04"]}), "unknown prerequisite")


def test_a_fault_needs_every_part(root):
    found = _edit(root, "E36", {"mode": "troubleshoot", "fault": {"summary": "s", "symptom": "y"}})
    assert _has(found, "needs a fault with summary")


def test_each_adapters_own_check_problems_are_reported(root):
    assert _has(_edit(root, "E19", {"validation": [{"kind": "pipeline_outcome", "status": 1}]}),
                "pipeline_outcome's status is Succeeded")
    assert _has(_edit(root, "E01", {"validation": [{"kind": "config_matches"}]}), "check config_matches needs rules")
    assert _has(_edit(root, "E05", {"validation": [{"kind": "quiz_score", "min_correct": 0}]}),
                "quiz_score's min_correct is a whole number")
    assert _has(_edit(root, "E06", {"validation": [{"kind": "repo_matches"}]}), "check repo_matches needs rules")


def test_a_pyspark_result_check_needs_a_table(root):
    checks = [{"kind": "result_matches"}, {"kind": "table_shape", "table": "t"}]
    assert _has(_edit(root, "E20", {"validation": checks}), "check result_matches needs table")


def test_a_duckdb_lab_check_takes_no_table_or_hidden_data(root):
    msg = "takes no table or hidden data"
    assert _has(_edit(root, "E36", {"validation": [{"kind": "result_matches", "table": "x"}]}), msg)
    assert _has(_edit(root, "E36", {"validation": [{"kind": "result_matches", "data": "hidden"}]}), msg)
    assert not _has(_edit(root, "E36", {"validation": [{"kind": "result_matches"}]}), msg)


def test_a_missing_child_pipeline_is_caught(root):
    assert _has(_edit(root, "E19", {"children": {"k": "nope.json"}}), "nope.json is missing")


def test_a_missing_notebook_is_caught(root):
    assert _has(_edit(root, "E20", {"notebooks": ["nope.ipynb"]}), "nope.ipynb is missing")


def test_files_folder_alone_is_only_for_pyspark(root):
    assert _has(_edit(root, "E36", {"files_folder": "data"}), "notebooks and files_folder are for pyspark_lab")


def test_a_pipelines_mocks_must_be_a_map(root):
    assert _has(_edit(root, "E19", {"mocks": ["x"]}), "mocks and children map activity names")


def _keep(root, eid, pattern, n):
    for p in sorted((root / PACK / "grading" / eid).glob(pattern))[n:]:
        p.unlink()


@pytest.mark.parametrize(("eid", "pattern"), [("E01", "wrong-*.json"), ("E06", "wrong-*.git"), ("E08", "wrong-*.sql")])
def test_two_planted_answers_are_enough(root, eid, pattern):
    _keep(root, eid, pattern, 2)
    assert not _has(_edit(root, eid, {}), "at least two planted")
    _keep(root, eid, pattern, 1)
    assert _has(_edit(root, eid, {}), "at least two planted")


def test_a_hidden_data_check_needs_hidden_sql(root):
    found = _edit(root, "E20", {}, {"grading/E20/hidden.sql": None})
    assert _has(found, "a check on hidden data needs grading/E20/hidden.sql")


def test_grading_with_no_exercise_is_caught(root):
    (root / PACK / "grading" / "E99").mkdir()
    assert f"{PACK} grading/E99: no exercise has this id" in exercises.problems(root)
