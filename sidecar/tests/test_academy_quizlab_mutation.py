"""academy/quizlab.py at its edges (mutation testing, batch 394).

test_academy_quizlab.py grades, explains and catches broken quizzes in a pack. These pin the 64 KB answer
limit at its value, the 3-to-20 question range at its top, min_correct from 1, the wrong answers the self
test picks, what load makes of a document without a question list, and the problems for a quiz.json that
can't be read, a question that isn't an object and an exercise that names no objective.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from fabric_desktop.academy import quizlab

SRC = "https://learn.microsoft.com/en-us/fabric/fundamentals/decision-guide-data-store"
SINGLE = {"id": "q1", "type": "single", "stem": "Analysts want T-SQL with multi-table transactions. Which store fits?",
          "options": [{"id": "a", "text": "Lakehouse"}, {"id": "b", "text": "Warehouse"}, {"id": "c", "text": "Eventhouse"}],
          "answer": ["b"], "explanation": "A warehouse supports multi-table transactions in T-SQL.", "source": SRC}
ORDER = {"id": "q2", "type": "order", "stem": "Put the medallion layers in the order data flows.",
         "options": [{"id": "a", "text": "Gold"}, {"id": "b", "text": "Bronze"}, {"id": "c", "text": "Silver"}],
         "answer": ["b", "c", "a"], "explanation": "Raw lands in bronze, is cleaned in silver, served from gold.",
         "source": SRC}


def test_the_answer_limit_is_64_kb_and_an_answer_of_exactly_that_is_read():
    assert quizlab.MAX_BYTES == 65536
    exact = "{}" + " " * (quizlab.MAX_BYTES - 2)
    assert quizlab.parse(exact) == ({}, None)
    assert quizlab.parse(exact + " ")[0] is None


def test_min_correct_of_1_is_fine():
    assert quizlab.check_problems({"kind": "quiz_score", "min_correct": 1}) == []


def test_changed_says_false_not_just_nothing():
    assert quizlab.changed([SINGLE], {"q1": ["b"]}, {"q1": ["b"]}) is False


def test_the_self_test_picks_an_option_outside_the_answer_or_the_order_reversed():
    assert quizlab._wrong_choice(SINGLE) == ["a"]
    assert quizlab._wrong_choice(ORDER) == ["a", "c", "b"]


def test_load_gives_no_questions_for_a_document_without_a_question_list(tmp_path):
    for doc in ([1], {"questions": "q1"}, {}):
        (tmp_path / quizlab.QUIZ_FILE).write_text(json.dumps(doc), encoding="utf-8")
        assert quizlab.load(tmp_path) == []


def _grading(tmp_path: Path, doc) -> Path:
    grading = tmp_path / "packs" / "dp-700" / "grading" / "quiz-x"
    grading.mkdir(parents=True)
    text = doc if isinstance(doc, str) else json.dumps(doc)
    (grading / quizlab.QUIZ_FILE).write_text(text, encoding="utf-8")
    return grading


def _numbered(n: int) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        q = copy.deepcopy(SINGLE)
        q["id"] = f"q{i}"
        out.append(q)
    return out


def test_twenty_questions_are_enough_and_twenty_one_too_many(tmp_path):
    ex = {"objectives": ["2.2.1"]}
    assert quizlab.problems("quiz-x", _grading(tmp_path / "a", {"questions": _numbered(20)}), ex, tmp_path / "a" / "packs") == []
    found = quizlab.problems("quiz-x", _grading(tmp_path / "b", {"questions": _numbered(21)}), ex, tmp_path / "b" / "packs")
    assert found == ["quiz-x: a quiz has 3 to 20 questions"]


def test_a_quiz_file_that_isnt_json_cant_be_read(tmp_path):
    grading = _grading(tmp_path, "{")
    found = quizlab.problems("quiz-x", grading, {"objectives": ["2.2.1"]}, tmp_path / "packs")
    assert len(found) == 1 and found[0].startswith("quiz-x: quiz.json can't be read (")


def test_a_question_that_isnt_an_object_is_numbered_from_1(tmp_path):
    grading = _grading(tmp_path, {"questions": ["x", *_numbered(3)[1:]]})
    found = quizlab.problems("quiz-x", grading, {"objectives": ["2.2.1"]}, tmp_path / "packs")
    assert found == ["quiz-x: question 1 isn't an object"]


def test_an_exercise_naming_no_objective_is_checked_as_objective_1_1(tmp_path):
    grading = _grading(tmp_path, {"questions": _numbered(3)})
    assert quizlab.problems("quiz-x", grading, {}, tmp_path / "packs") == []
    assert quizlab.problems("quiz-x", grading, {"objectives": []}, tmp_path / "packs") == []
