"""ACD-002/003: Academy exercises are well formed, each reference solution passes its own validation and
every planted wrong answer fails it, classes are honest, and the checker catches broken exercises."""

from __future__ import annotations

import json
import shutil

import pytest

from fabric_desktop.academy import exercises, registry

PACK = "dp-700"


def test_bundled_exercises_have_no_problems():
    assert exercises.problems() == []
    assert exercises.ids(PACK) == ["E21", "E36", "E37"]


@pytest.mark.parametrize("eid", exercises.ids(PACK))
def test_reference_passes_and_every_wrong_answer_fails(eid):
    result = exercises.self_test(PACK, eid)
    assert result["solution"]["passed"], result["solution"]
    assert len(result["wrong"]) >= 2
    for name, r in result["wrong"].items():
        assert not r["passed"], f"{eid} {name} should fail"
        assert all(c["detail"] for c in r["results"])


def test_the_starter_does_not_pass_yet():
    for eid in exercises.ids(PACK):
        ex = exercises.get(PACK, eid)
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
    ({"status": "live"}, "needs the date it was last reviewed"),
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
