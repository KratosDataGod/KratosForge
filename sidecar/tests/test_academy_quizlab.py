"""quiz exercises: original questions about a scenario, KNOWLEDGE ONLY. The screen gets stems and options only;
the answer key stays in grading/ until the explanation; a check says which questions aren't right yet, never
the right answer."""

from __future__ import annotations

import functools
import json
import shutil

import pytest

from fabric_desktop.academy import exercises, lab, quizlab, registry
from fabric_desktop.academy_service import AcademyMixin

PACK, EID = "dp-700", "E36"
SRC = "https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-data-store"
QUESTIONS = [
    {"id": "q1", "type": "single", "stem": "Analysts want T-SQL with multi-table transactions. Which store fits?",
     "options": [{"id": "a", "text": "Lakehouse"}, {"id": "b", "text": "Warehouse"}, {"id": "c", "text": "Eventhouse"}],
     "answer": ["b"], "explanation": "A warehouse supports multi-table transactions in T-SQL.", "source": SRC},
    {"id": "q2", "type": "multiple", "stem": "Which two suit high-volume telemetry queried with KQL?",
     "options": [{"id": "a", "text": "Eventhouse"}, {"id": "b", "text": "KQL database"}, {"id": "c", "text": "Warehouse"},
                 {"id": "d", "text": "SQL database"}],
     "answer": ["a", "b"], "explanation": "An eventhouse holds KQL databases for telemetry.", "source": SRC},
    {"id": "q3", "type": "order", "stem": "Put the medallion layers in the order data flows.",
     "options": [{"id": "a", "text": "Gold"}, {"id": "b", "text": "Bronze"}, {"id": "c", "text": "Silver"}],
     "answer": ["b", "c", "a"], "explanation": "Raw lands in bronze, is cleaned in silver, served from gold.", "source": SRC},
]
KEY = {"q1": ["b"], "q2": ["b", "a"], "q3": ["b", "c", "a"]}
EX = {"id": EID, "validation": [{"kind": "quiz_score"}]}


def test_view_never_carries_answers_explanations_or_sources():
    shown = json.dumps(quizlab.view(QUESTIONS))
    assert "answer" not in shown and "explanation" not in shown and "learn.microsoft.com" not in shown
    assert quizlab.view(QUESTIONS)[1]["select"] == 2 and quizlab.view(QUESTIONS)[0]["select"] is None


def test_score_says_which_are_not_right_yet_but_never_the_answer():
    assert quizlab.grade(json.dumps(KEY), EX, QUESTIONS)["passed"]
    res = quizlab.grade(json.dumps({"q1": ["a"], "q2": ["a", "b"]}), EX, QUESTIONS)["results"][0]
    assert not res["passed"]
    assert res["detail"] == "1 of 3 right; you need 3. Not answered yet: q3."
    assert set(res) == {"kind", "passed", "detail"}   # nothing says which answered question is wrong


def test_a_failed_check_never_tells_a_right_answer_from_a_wrong_one():
    one_wrong = {**KEY, "q1": ["a"]}
    other_wrong = {**KEY, "q2": ["c", "d"]}
    a = quizlab.grade(json.dumps(one_wrong), EX, QUESTIONS)["results"][0]
    b = quizlab.grade(json.dumps(other_wrong), EX, QUESTIONS)["results"][0]
    assert a == b and a["detail"] == "2 of 3 right; you need 3."


def test_min_correct_and_a_single_string_answer():
    ex = {**EX, "validation": [{"kind": "quiz_score", "min_correct": 2}]}
    assert quizlab.grade(json.dumps({"q1": "b", "q2": ["a", "b"]}), ex, QUESTIONS)["passed"]
    assert not quizlab.grade(json.dumps({"q1": "b"}), ex, QUESTIONS)["passed"]


@pytest.mark.parametrize(("answer", "said"), [
    ("{", "isn't valid JSON"),
    ("[1]", "is an object of question ids"),
    (" " * (quizlab.MAX_BYTES + 1), "over 64 KB"),
    ("[" * 30_000, "isn't valid JSON"),
])
def test_answers_that_cant_be_read_fail_in_words(answer, said):
    res = quizlab.grade(answer, EX, QUESTIONS)
    assert not res["passed"] and said in res["results"][0]["detail"]


def test_odd_picks_are_ignored_not_crashed_on():
    res = quizlab.grade(json.dumps({"q1": [1], "q2": {"a": 1}, "zz": ["a"]}), EX, QUESTIONS)
    assert not res["passed"] and "Not answered yet: q1, q2, q3" in res["results"][0]["detail"]


def test_self_test(tmp_path):
    (tmp_path / "quiz.json").write_text(json.dumps({"questions": QUESTIONS}))
    got = quizlab.self_test(tmp_path, EX)
    assert got["solution"]["passed"] and not any(r["passed"] for r in got["wrong"].values())


@pytest.mark.parametrize(("check", "ok"), [({"kind": "quiz_score"}, True), ({"kind": "quiz_score", "min_correct": 2}, True),
                                           ({"kind": "quiz_score", "min_correct": 0}, False),
                                           ({"kind": "quiz_score", "min_correct": True}, False)])
def test_check_problems(check, ok):
    assert (quizlab.check_problems(check) == []) is ok


# ---- in a pack

@pytest.fixture
def quiz_pack(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    folder, grading = root / PACK / "exercises" / EID, root / PACK / "grading" / EID
    shutil.rmtree(grading)
    grading.mkdir()
    for p in folder.iterdir():
        if p.suffix == ".sql":
            p.unlink()
    path = folder / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update({"adapter": "quiz", "class": "knowledge_only", "engine_label": "Questions; nothing runs",
               "validation": [{"kind": "quiz_score", "fail": "Read the decision guide, then try again."}], "parity": []})
    for k in ("setup", "starter"):
        ex.pop(k, None)
    path.write_text(json.dumps(ex), encoding="utf-8")
    (grading / "quiz.json").write_text(json.dumps({"questions": QUESTIONS}), encoding="utf-8")
    return root


def _edit_quiz(root, change):
    path = root / PACK / "grading" / EID / "quiz.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc["questions"])
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_quiz_exercise_is_well_formed_and_self_tests(quiz_pack):
    assert exercises.problems(quiz_pack) == []
    got = exercises.self_test(PACK, EID, quiz_pack)
    assert got["solution"]["passed"] and not any(r["passed"] for r in got["wrong"].values())


@pytest.mark.parametrize(("change", "message"), [
    (lambda qs: qs.pop(), "a quiz has 3 to 20 questions"),
    (lambda qs: qs[0].update(id="question-1"), "id is q1, q2 and so on"),
    (lambda qs: qs[1].update(id="q1"), "id is q1, q2 and so on"),
    (lambda qs: qs[0].update(source="https://example.com/x"), "question q1: source is the Microsoft Learn page"),
    (lambda qs: qs[0].update(answer=["z"]), "question q1: answer names options that aren't there"),
    (lambda qs: qs[2].update(answer=["a", "b", "c"], options=sorted(qs[2]["options"], key=lambda o: o["id"])),
     "give the order away"),
    (lambda qs: qs[0].update(explanation=""), "question q1 explanation is empty"),
    (lambda qs: qs[0].update(case="c1"), "takes no case"),
])
def test_a_quiz_that_cant_be_graded_is_caught(quiz_pack, change, message):
    _edit_quiz(quiz_pack, change)
    found = exercises.problems(quiz_pack)
    assert any(message in p for p in found), found


@pytest.mark.parametrize(("change", "message"), [
    ({"class": "simulated"}, "a quiz exercise is KNOWLEDGE ONLY"),
    ({"class": "local_verified"}, "a quiz exercise is KNOWLEDGE ONLY"),
    ({"starter": "lesson.md"}, "takes no starter"),
    ({"validation": [{"kind": "result_matches"}]}, "quiz can't check result_matches"),
])
def test_a_quiz_exercise_with_the_wrong_shape_is_caught(quiz_pack, change, message):
    path = quiz_pack / PACK / "exercises" / EID / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update(change)
    path.write_text(json.dumps(ex), encoding="utf-8")
    found = exercises.problems(quiz_pack)
    assert any(message in p for p in found), found


def test_missing_or_leaked_questions_are_caught(quiz_pack):
    src = quiz_pack / PACK / "grading" / EID / "quiz.json"
    shutil.copy(src, quiz_pack / PACK / "exercises" / EID / "quiz.json")
    assert any("quiz.json holds the answers" in p for p in exercises.problems(quiz_pack))
    (quiz_pack / PACK / "exercises" / EID / "quiz.json").unlink()
    src.unlink()
    assert any("needs its questions at grading/E36/quiz.json" in p for p in exercises.problems(quiz_pack))


# ---- through the workspace and the academy_* methods

class _Svc(AcademyMixin):
    def __init__(self, root):
        self.root = root
        self.rows = []

    def _progress_record(self, row):
        self.rows.append(row)
        return []

    def _profile_path(self):
        return self.root / "profile.json"


@pytest.fixture
def svc(quiz_pack, tmp_path, monkeypatch):
    monkeypatch.setattr(exercises, "get", functools.partial(exercises.get, root=quiz_pack))
    monkeypatch.setattr(exercises, "file_text", functools.partial(exercises.file_text, root=quiz_pack))
    monkeypatch.setattr(lab, "_exercise_folder", lambda pid, eid: quiz_pack / pid / "exercises" / eid)
    monkeypatch.setattr(lab, "_grading_folder", lambda pid, eid: quiz_pack / pid / "grading" / eid)
    data = tmp_path / "data"
    data.mkdir()
    return _Svc(data)


def test_start_answer_check_explain_and_reset(svc):
    shown = svc.academy_exercise(PACK, EID)
    assert [q["id"] for q in shown["quiz"]] == ["q1", "q2", "q3"]
    assert "explanation" not in json.dumps(shown["quiz"]) and "learn.microsoft.com" not in json.dumps(shown)
    st = svc.academy_start(PACK, EID)
    assert st["answer"] == "{}"
    assert sorted(p.name for p in lab.workspace(svc.root, PACK, EID).iterdir()) == ["answers.json", "state.json"]
    with pytest.raises(ValueError, match="nothing to run"):
        svc.academy_run(PACK, EID, "{}")

    svc.academy_save(PACK, EID, json.dumps({"q1": ["a"]}))
    res = svc.academy_validate(PACK, EID)
    assert not res["passed"] and res["verification"]["label"] == "KNOWLEDGE ONLY"
    assert res["results"][0]["message"] == "Read the decision guide, then try again."
    assert "Lakehouse" not in json.dumps(res) and '"b"' not in json.dumps(res)

    res = svc.academy_validate(PACK, EID, json.dumps(KEY))
    assert res["passed"] and svc.rows[0]["detail"]["class"] == "knowledge_only"

    why = svc.academy_explanation(PACK, EID)
    assert [q["answer"] for q in why["questions"]] == [["b"], ["a", "b"], ["b", "c", "a"]]
    assert why["questions"][0]["source"] == SRC

    svc.academy_reset(PACK, EID)
    assert lab.state(svc.root, PACK, EID)["answer"] == "{}"


def test_other_exercises_carry_no_quiz(svc, monkeypatch):
    monkeypatch.undo()
    assert AcademyMixin.academy_exercise(svc, "dp-700", "E37")["quiz"] is None


def test_changed_compares_like_the_scoring_rule():
    assert not quizlab.changed(QUESTIONS, {"q2": ["a", "b"]}, {"q2": ["b", "a"]})   # a pick set, any order
    assert quizlab.changed(QUESTIONS, {"q3": ["b", "c", "a"]}, {"q3": ["c", "b", "a"]})   # an order is an order
    assert quizlab.changed(QUESTIONS, {"q1": ["a"]}, {"q1": ["b"]})
    assert quizlab.changed(QUESTIONS, {"q1": ["a"]}, {})
    assert not quizlab.changed(QUESTIONS, {"zz": ["a"]}, {})


def test_changing_a_checked_answer_makes_the_pass_not_independent(svc):
    svc.academy_start(PACK, EID)
    # Answer q1 only, check, then answer the rest: q1 never changed, so the pass is independent.
    assert not svc.academy_validate(PACK, EID, json.dumps({"q1": ["b"]}))["passed"]
    res = svc.academy_validate(PACK, EID, json.dumps(KEY))
    assert res["passed"] and not res["answers_changed"] and "answers_changed" not in svc.rows[-1]["detail"]

    svc.academy_reset(PACK, EID)
    # A wrong answer checked, then changed until the count moves: the pass still counts, but not as independent.
    assert not svc.academy_validate(PACK, EID, json.dumps({**KEY, "q1": ["a"]}))["passed"]
    res = svc.academy_validate(PACK, EID, json.dumps(KEY))
    assert res["passed"] and res["answers_changed"] and svc.rows[-1]["detail"]["answers_changed"] is True
    # Sticky until Reset, even after a later pass.
    assert svc.academy_validate(PACK, EID, json.dumps(KEY))["answers_changed"]
    svc.academy_reset(PACK, EID)
    assert not lab.state(svc.root, PACK, EID).get("answers_changed")
