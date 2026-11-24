"""academy/exercises.py paths that its first mutation run (batch 427, docs/quality/mutation-testing.md)
showed no test checked. The adapters' own self-tests are replaced here by stand-ins, so no learner
process starts; the exercises are the bundled ones, or a copy of them a test breaks."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import configlab, exercises, gitlab, pipelab, pylab, quizlab, registry, sqlprojlab
from tests.test_academy_exercises import PACK, broken  # noqa: F401 - fixture

DBX = "dbx-de-associate"


def test_an_exercise_id_without_a_folder_is_not_an_exercise():
    with pytest.raises(ValueError, match="dp-700 has no exercise E99"):
        exercises.get(PACK, "E99")
    with pytest.raises(ValueError, match="has no exercise"):
        exercises.get(PACK, "../E01")


def test_drafts_are_listed_unless_asked_not_to():
    drafts = {e["id"] for e in exercises.exercises(PACK) if e["status"] == "draft"}
    assert drafts and not drafts & {e["id"] for e in exercises.exercises(PACK, include_drafts=False)}


@pytest.mark.parametrize("pack, eid, where", [
    (DBX, "E15", "pylab"), (PACK, "E22", "pylab"), (PACK, "E19", "pipelab"), (PACK, "C3", "configlab"),
    (PACK, "E06", "gitlab"), (PACK, "E08", "sqlprojlab"), (PACK, "E05", "quizlab"), (DBX, "E20", "dbsql_lab"),
    (PACK, "C1", "pyspark_lab"),
])
def test_each_adapter_s_exercise_is_self_tested_by_its_own_lab(monkeypatch, pack, eid, where):
    from fabric_desktop.academy import dbsql_lab, pyspark_lab

    labs = {"pylab": pylab, "pipelab": pipelab, "configlab": configlab, "gitlab": gitlab, "sqlprojlab": sqlprojlab,
            "quizlab": quizlab, "dbsql_lab": dbsql_lab, "pyspark_lab": pyspark_lab}
    for name, module in labs.items():
        monkeypatch.setattr(module, "self_test", lambda *a, name=name, **k: {"by": name, "args": a})
    out = exercises.self_test(pack, eid)
    assert out["by"] == where
    root = registry.ROOT
    if where in ("pylab", "pipelab", "gitlab", "sqlprojlab"):
        assert out["args"][:2] == (root / pack / "exercises" / eid, root / pack / "grading" / eid)
    elif where in ("configlab", "quizlab"):
        assert out["args"][0] == root / pack / "grading" / eid
    else:
        assert out["args"] == (pack, eid, root)


def test_an_adapter_with_no_self_test_says_which_can(monkeypatch):
    real = exercises.get
    monkeypatch.setattr(exercises, "get", lambda p, e, root=registry.ROOT: dict(real(p, e, root), adapter="zz_lab"))
    with pytest.raises(ValueError, match="E21: only duckdb_lab, dbsql_lab, pyspark_lab"):
        exercises.self_test(PACK, "E21")


@pytest.mark.parametrize("change, message", [
    ({"version": 0}, "version is a whole number from 1"),
    ({"version": "2"}, "version is a whole number from 1"),
    ({"minutes": 0}, "minutes is a whole number from 1 to 240"),
    ({"minutes": 241}, "minutes is a whole number from 1 to 240"),
    ({"minutes": 30.5}, "minutes is a whole number from 1 to 240"),
])
def test_version_and_minutes_are_whole_numbers_in_range(broken, change, message):  # noqa: F811
    assert any(f"E36: {message}" in p for p in broken("E36", change))


@pytest.mark.parametrize("change", [{"version": 1}, {"minutes": 1}, {"minutes": 240}])
def test_the_edges_of_version_and_minutes_are_fine(broken, change):  # noqa: F811
    assert not any("whole number" in p for p in broken("E36", change))


@pytest.mark.parametrize("eid, check, says", [
    ("E19", {"kind": "dag_matches"}, "E19: check dag_matches needs notebooks"),
    ("C3", {"kind": "config_matches"}, "C3: check config_matches needs rules"),
    ("E05", {"kind": "quiz_score", "min_correct": 0}, "E05: check quiz_score's min_correct is a whole number from 1"),
    ("E06", {"kind": "repo_matches"}, "E06: check repo_matches needs rules"),
])
def test_each_lab_checks_its_own_kind_of_check(broken, eid, check, says):  # noqa: F811
    assert says in [p.removeprefix(f"{PACK} ") for p in broken(eid, {"validation": [check]})]


def test_a_pipeline_s_child_file_and_a_notebook_must_be_there(broken):  # noqa: F811
    assert f"{PACK} E19: missing-child.json is missing" in broken("E19", {"children": {"Run child": "missing-child.json"}})
    assert f"{PACK} C1: missing.ipynb is missing" in broken("C1", {"notebooks": ["missing.ipynb"]})


def test_a_grading_folder_without_its_exercise_is_reported(broken, tmp_path):  # noqa: F811
    stray = tmp_path / "packs" / PACK / "grading" / "E98"
    stray.mkdir(parents=True)
    assert f"{PACK} grading/E98: no exercise has this id" in broken("E36", {})


def test_an_exercise_s_own_starter_is_its_reference_starter():
    ex = exercises.get(PACK, "E21")
    assert exercises.reference(PACK, "E21")["starter"] == exercises.file_text(PACK, "E21", ex["starter"]) != ""


def test_an_answer_that_ends_the_transaction_or_does_not_run_fails_its_check():
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (id INTEGER)")
    check = {"kind": "table_shape", "table": "t", "columns": ["id"]}
    out = exercises.run_check(con, check, "INSERT INTO t VALUES (1); COMMIT;", "")
    assert out == {"kind": "table_shape", "passed": False,
                   "detail": "Your SQL wasn't run: a lab answer can't use BEGIN, COMMIT or ROLLBACK; the lab undoes "
                             "every answer itself."}
    out = exercises.run_check(con, check, "INSERT INTO nowhere VALUES (1)", "")
    assert out["kind"] == "table_shape" and out["passed"] is False and out["detail"].startswith("Your SQL didn't run: ")
    assert con.sql("SELECT count(*) FROM t").fetchall() == [(0,)]


@pytest.mark.parametrize("change, says", [
    ({"notebooks": ["land.py"]}, "E36: notebooks and files_folder are for pyspark_lab exercises"),
    ({"files_folder": "data"}, "E36: notebooks and files_folder are for pyspark_lab exercises"),
    ({"files": ["../setup.sql"]}, "E36: '../setup.sql' isn't a plain file name in the exercise's folder"),
    ({"files": [3]}, "E36: 3 isn't a plain file name in the exercise's folder"),
    ({"files": ["has space.sql"]}, "E36: 'has space.sql' isn't a plain file name in the exercise's folder"),
    ({"adapter": "fabric"}, "E36: a fabric exercise is FABRIC VERIFIED"),
    ({"class": "databricks_verified"}, "E36: a Fabric pack can't hold Databricks exercises"),
    ({"mode": "troubleshoot", "fault": {"summary": "s", "symptom": "y"}}, "E36: a troubleshoot exercise needs a fault"),
])
def test_what_an_exercise_may_not_say_is_caught(broken, change, says):  # noqa: F811
    assert any(p.startswith(f"{PACK} {says}") for p in broken("E36", change)), says


def test_a_pipeline_s_mocks_map_names_to_stand_ins(broken):  # noqa: F811
    says = (f"{PACK} E19: mocks and children map activity names and pipeline keys to what stands in for them")
    assert says in broken("E19", {"mocks": "all"})
    assert says in broken("E19", {"children": ["a.json"]})


def test_two_planted_wrong_answers_are_enough(broken):  # noqa: F811
    found = broken("E36", {}, {"grading/E36/wrong-cube.sql": None, "grading/E36/wrong-order.sql": None,
                               "grading/E36/wrong-no-grand-total.sql": None})
    assert not any("E36" in p for p in found)


@pytest.mark.parametrize("eid", ["E19", "C3", "E06", "E08", "E22", "C1", "E36"])
def test_exactly_two_planted_wrong_answers_are_enough_for_every_lab(broken, eid):  # noqa: F811
    grading = registry.ROOT / PACK / "grading" / eid
    wrong = sorted(p.name for p in grading.iterdir() if p.name.startswith("wrong-"))
    assert len(wrong) > 2
    found = broken(eid, {}, {f"grading/{eid}/{name}": None for name in wrong[2:]})
    assert not any("planted wrong answers" in p for p in found)
    found = broken(eid, {}, {f"grading/{eid}/{wrong[1]}": None})
    assert any(f"{eid}: needs at least two planted wrong answers" in p for p in found)


@pytest.mark.parametrize("eid, change, says", [
    ("E36", {"prerequisites": ["E99"]}, "E36: unknown prerequisite E99"),
    ("C1", {"validation": [{"kind": "result_matches"}]}, "C1: check result_matches needs table"),
    ("E36", {"validation": [{"kind": "result_matches", "data": "hidden"}]},
     "E36: a duckdb_lab check reads the answer's last result on the setup's data"),
    ("E36", {"validation": [{"kind": "result_matches", "table": "out"}]},
     "E36: a duckdb_lab check reads the answer's last result on the setup's data"),
    ("E36", {"validation": [{"kind": "result_matches", "data": "hidden"}]}, "E36: a check on hidden data needs grading/E36/hidden.sql"),
    ("E36", {"difficulty": 4}, "E36: difficulty is 1, 2 or 3"),
    ("E36", {"hints": [""]}, "E36: hints is a list of sentences"),
])
def test_more_of_what_an_exercise_may_not_say_is_caught(broken, eid, change, says):  # noqa: F811
    assert any(p.startswith(f"{PACK} {says}") for p in broken(eid, change)), says


@pytest.mark.parametrize("change", [{"prerequisites": ["E01"]}, {"difficulty": 3}, {"difficulty": 1}, {"hints": ["Look at ROLLUP."]}])
def test_a_real_prerequisite_a_difficulty_of_three_and_a_hint_are_fine(broken, change):  # noqa: F811
    assert not any(p.startswith(f"{PACK} E36:") for p in broken("E36", change))


def test_only_folders_named_like_an_exercise_are_exercises(broken, tmp_path):  # noqa: F811
    root = tmp_path / "packs"
    broken("E36", {})
    (root / PACK / "exercises" / "notes").mkdir()
    (root / PACK / "exercises" / "E97").write_text("not a folder", encoding="utf-8")
    assert exercises.ids(PACK, root) == exercises.ids(PACK)
