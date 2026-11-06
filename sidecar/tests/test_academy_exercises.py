"""ACD-002/003: Academy exercises are well formed, each reference solution passes its own validation and
every planted wrong answer fails it, classes are honest, and the checker catches broken exercises."""

from __future__ import annotations

import json
import shutil

import duckdb
import pytest

from fabric_desktop.academy import configlab, exercises, pipelab, pylab, pyspark_lab, quizlab, registry, workspace

PACK = "dp-700"


def test_bundled_exercises_have_no_problems():
    assert exercises.problems() == []
    assert exercises.ids(PACK) == ["E09", "E12", "E14", "E18", "E19", "E21", "E22", "E23", "E24", "E25", "E29",
                                   "E32", "E33", "E35", "E36", "E37", "E38", "E39", "E43", "E44", "E48", "E49",
                                   "E50", "E51", "E53", "E56", "E57", "E58", "E59", "E61", "E62", "E63", "E64"]


@pytest.mark.parametrize("eid", exercises.ids(PACK))
def test_reference_passes_and_every_wrong_answer_fails(eid):
    result = exercises.self_test(PACK, eid)
    assert result["solution"]["passed"], result["solution"]
    assert len(result["wrong"]) >= 2
    for name, r in result["wrong"].items():
        assert not r["passed"], f"{eid} {name} should fail"
        assert all(c["detail"] for c in r["results"])


def test_the_starter_does_not_pass_yet(tmp_path):
    for eid in exercises.ids(PACK):
        ex = exercises.get(PACK, eid)
        if ex["adapter"] == pipelab.ADAPTER:   # the starter pipeline, run as the learner's answer
            folder = registry.ROOT / PACK / "exercises" / eid
            work = tmp_path / eid
            work.mkdir()
            shutil.copy(folder / ex["starter"], work / ex["starter"])
            assert not pipelab.grade(work, ex, folder)["passed"], eid
            continue
        if ex["adapter"] in pylab.ADAPTERS:   # the starter script, run in the learner process
            folder = registry.ROOT / PACK / "exercises" / eid
            data_root = tmp_path / f"{eid}-data"
            work = workspace.workspace_dir(data_root, "starter", eid)
            work.mkdir(parents=True)
            pylab.prepare(work, folder, ex, (folder / ex["starter"]).read_text(encoding="utf-8"))
            res = pylab.grade(work, ex["starter"], ex["validation"], data_root, appcontainer_ok=pylab.appcontainer_ok(ex))
            assert not res["passed"], eid
            continue
        if ex["adapter"] == quizlab.ADAPTER:   # a quiz starts with no answers
            assert not ex.get("starter"), eid
            continue
        if ex["adapter"] == configlab.ADAPTER:   # the starter settings document, checked like any answer
            starter = (registry.ROOT / PACK / "exercises" / eid / ex["starter"]).read_text(encoding="utf-8")
            assert not configlab.grade(starter, ex)["passed"], eid
            continue
        if ex["adapter"] == "pyspark_lab":   # the starter code, graded like any answer
            ref = pyspark_lab.reference(PACK, eid)
            res = pyspark_lab.grade(ex, ref["setup"], ref["hidden"], ref["starter"], ref["solution"], pyspark_lab.run_reference)
            assert not res["passed"], eid
            continue
        ref = exercises.reference(PACK, eid)
        assert not exercises.grade(ref["setup"], ex["validation"], ref["starter"], ref["solution"])["passed"], eid


def test_grading_material_never_sits_with_the_learner_files():
    for eid in exercises.ids(PACK):
        folder = registry.ROOT / PACK / "exercises" / eid
        assert not [p for p in folder.iterdir() if p.name.startswith(("solution", "wrong-"))]
    with pytest.raises(ValueError):
        exercises.file_text(PACK, "E36", "../../grading/E36/solution.sql")


def test_a_learn_lab_exercise_is_reused_not_copied():
    ref = exercises.reference(PACK, "E37")
    assert "row_number()" in ref["solution"] and "CREATE TABLE silver_orders" in ref["setup"]
    assert not (registry.ROOT / PACK / "exercises" / "E37" / "starter.sql").exists()


def test_result_label_says_what_was_and_was_not_verified():
    lab = exercises.label(exercises.get(PACK, "E37"))
    assert lab["label"] == "LOCAL VERIFIED" and lab["engine"] == "DuckDB on this PC"
    assert lab["verifies"] and lab["does_not_verify"]


@pytest.mark.parametrize(("objective", "cls", "done"), [
    ("2.2.9", "local_verified", True),       # portable: local evidence completes it
    ("2.2.9", "simulated", False),
    ("2.2.9", "fabric_verified", True),
    ("2.2.1", "knowledge_only", True),       # a choice: knowledge is the best this PC gives
    ("2.3.3", "local_verified", False),      # needs Fabric: only FABRIC VERIFIED completes it
    ("2.3.3", "fabric_verified", True),
    ("1.1.1", "simulated", False),
    ("2.2.9", "made_up", False),
])
def test_completes(objective, cls, done):
    cert = registry.certification(PACK)
    assert exercises.completes(registry.objective(cert, objective), cls) is done


# --- the checker catches broken exercises ----------------------------------------------------------


@pytest.fixture
def broken(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)

    def edit(eid, change, files=None):
        path = root / PACK / "exercises" / eid / "exercise.json"
        ex = json.loads(path.read_text(encoding="utf-8"))
        ex.update(change)
        path.write_text(json.dumps(ex), encoding="utf-8")
        for rel, text in (files or {}).items():
            target = root / PACK / rel
            if text is None:
                target.unlink()
            else:
                target.write_text(text, encoding="utf-8")
        return exercises.problems(root)

    return edit


@pytest.mark.parametrize(("change", "message"), [
    ({"class": "fabric_verified"}, "only the fabric adapter can give FABRIC VERIFIED"),
    ({"adapter": "quiz"}, "LOCAL VERIFIED needs an adapter that runs"),
    ({"engine_label": "Fabric Spark"}, "can't claim a Fabric engine"),
    ({"verifies": ["Proves it works on the Fabric Warehouse."]}, "can't claim a Fabric engine"),
    ({"does_not_verify": []}, "needs does_not_verify"),
    ({"objectives": ["2.2.7"]}, "doesn't list this exercise"),
    ({"objectives": ["9.9.9"]}, "unknown objective 9.9.9"),
    ({"validation": [{"kind": "faster_than"}]}, "can't check faster_than"),
    ({"validation": [{"kind": "table_shape"}]}, "needs table"),
    ({"status": "live", "reviewed": None}, "needs the date it was last reviewed"),
    ({"prerequisites": ["LM-99"]}, "unknown prerequisite LM-99"),
    ({"remediation": ["LAB-99"]}, "unknown remediation lesson LAB-99"),
    ({"mode": "lecture"}, "mode is one of"),
    ({"mode": "troubleshoot"}, "needs a fault with summary"),
    ({"validation": [{"kind": "result_matches", "fail": ""}]}, "fail message is a sentence"),
], ids=["fabric-class", "quiz-local", "engine", "verifies", "does-not", "objective-list", "objective", "check",
        "check-needs", "live", "prereq", "remediation", "mode", "fault", "message"])
def test_bad_fields_are_caught(broken, change, message):
    assert any(message in p for p in broken("E36", change)), message


def test_missing_reference_solution_is_caught(broken):
    found = broken("E36", {}, {"grading/E36/solution.sql": None})
    assert any("independently runnable reference solution" in p for p in found)


def test_too_few_wrong_answers_are_caught(broken):
    found = broken("E36", {}, {"grading/E36/wrong-cube.sql": None, "grading/E36/wrong-order.sql": None})
    assert any("at least two planted wrong answers" in p for p in found)


def test_a_solution_beside_the_starter_is_caught(broken):
    found = broken("E36", {}, {"exercises/E36/solution.sql": "SELECT 1"})
    assert any("is grading material" in p for p in found)


def test_uses_must_name_a_real_learn_exercise(broken):
    assert any("isn't a Learn exercise" in p for p in broken("E37", {"uses": "LAB-04/nope"}))
    assert any("comes from the Learn lab" in p for p in broken("E37", {"starter": "starter.sql"}))


def test_raw_html_in_a_lesson_is_caught(broken):
    found = broken("E36", {}, {"exercises/E36/lesson.md": "# Hi\n\n<script>alert(1)</script>\n"})
    assert any("raw HTML" in p for p in found)


SETUP = """CREATE TABLE src (id INTEGER, v INTEGER); INSERT INTO src VALUES (1, 10), (2, 20);
CREATE TABLE log (run VARCHAR, status VARCHAR); INSERT INTO log VALUES ('r1', 'failed');
CREATE TABLE out (id INTEGER, v INTEGER);"""
FIX = "INSERT INTO out SELECT * FROM src;\nSELECT * FROM out ORDER BY id;"
KEEP = [{"kind": "unchanged", "tables": ["src", "log"]}]


def test_unchanged_passes_when_only_the_target_changes():
    res = exercises.grade(SETUP, KEEP, FIX, FIX)
    assert res["passed"], res
    assert "Nothing else changed" in res["results"][0]["detail"]


@pytest.mark.parametrize(("answer", "said"), [
    ("DELETE FROM log;\n" + FIX, "log went from 1 to 0 rows"),
    ("UPDATE src SET v = 0 WHERE id = 1;\n" + FIX, "src has 2 rows as before, but some of them changed"),
    ("INSERT INTO src VALUES (1, 10);\n" + FIX, "src went from 2 to 3 rows"),
    ("DROP TABLE log;\n" + FIX, "log is gone"),
])
def test_unchanged_fails_an_answer_that_touches_a_protected_table(answer, said):
    res = exercises.grade(SETUP, KEEP, answer, FIX)
    assert not res["passed"]
    assert said in res["results"][0]["detail"]


def test_unchanged_leaves_the_lab_as_it_was():
    con = duckdb.connect(":memory:")
    con.execute(SETUP)
    r = exercises.run_check(con, KEEP[0], "DELETE FROM log;", FIX)
    assert not r["passed"]
    assert con.execute("SELECT count(*) FROM log").fetchone() == (1,)   # rolled back


def test_no_findings_reads_the_answer_without_running_it():
    rule = [{"kind": "no_findings", "rules": ["SQ002"]}]
    wrapped = "SELECT count(*) FROM src WHERE year(d) = 2026"   # src has no column d: it would fail if run
    assert not exercises.grade(SETUP, rule, wrapped, FIX)["passed"]
    assert exercises.grade(SETUP, rule, "SELECT count(*) FROM src WHERE d >= DATE '2026-01-01'", FIX)["passed"]


def test_unchanged_and_no_findings_need_their_fields(broken):
    assert any("check unchanged needs tables" in p for p in broken("E36", {"validation": [{"kind": "unchanged"}]}))
    assert any("check no_findings needs rules" in p for p in broken("E36", {"validation": [{"kind": "no_findings"}]}))
