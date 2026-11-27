"""Mutation batch 414: academy/quizlab.py. Tests for the survivors of its first score (see
docs/quality/mutation-testing.md)."""

from __future__ import annotations

import copy
import json

import pytest

from fabric_desktop.academy import quizlab
from tests.test_academy_quizlab import EX, QUESTIONS


def _answers_of_bytes(n: int) -> str:
    head = '{"q1": ["'
    tail = '"]}'
    return head + "b" * (n - len(head) - len(tail)) + tail


def test_answers_of_exactly_64_kb_are_read_and_one_byte_more_arent():
    assert quizlab.MAX_BYTES == 64 * 1024
    answers, problem = quizlab.parse(_answers_of_bytes(quizlab.MAX_BYTES))
    assert problem is None and list(answers) == ["q1"]
    answers, problem = quizlab.parse(_answers_of_bytes(quizlab.MAX_BYTES + 1))
    assert answers is None and problem == "The answers are over 64 KB; a quiz's answers are much smaller."


def test_a_quiz_file_without_a_question_list_has_no_questions(tmp_path):
    (tmp_path / quizlab.QUIZ_FILE).write_text(json.dumps({"title": "no questions"}), encoding="utf-8")
    assert quizlab.load(tmp_path) == []
    (tmp_path / quizlab.QUIZ_FILE).write_text(json.dumps({"questions": "q1"}), encoding="utf-8")
    assert quizlab.load(tmp_path) == []


def test_min_correct_of_1_is_fine():
    assert quizlab.check_problems({"kind": "quiz_score", "min_correct": 1}) == []
    assert quizlab.check_problems({"kind": "quiz_score", "min_correct": 0}) == [
        "check quiz_score's min_correct is a whole number from 1"]


def test_the_planted_wrong_answers_are_wrong_on_every_question(tmp_path):
    (tmp_path / quizlab.QUIZ_FILE).write_text(json.dumps({"questions": QUESTIONS}), encoding="utf-8")
    out = quizlab.self_test(tmp_path, EX)
    assert out["solution"]["passed"] is True
    wrong = out["wrong"]["every answer wrong"]
    assert wrong["passed"] is False and wrong["results"][0]["detail"].startswith("0 of 3 right")
    assert quizlab._wrong_choice(QUESTIONS[0]) == ["a"]           # the first option that isn't the answer
    assert quizlab._wrong_choice(QUESTIONS[1]) == ["c"]
    assert quizlab._wrong_choice(QUESTIONS[2]) == ["a", "c", "b"]  # the steps reversed


def _grading(tmp_path, questions) -> tuple:
    root = tmp_path / "packs"
    grading = root / "p1" / "grading" / "E1"
    grading.mkdir(parents=True)
    if questions is not None:
        text = questions if isinstance(questions, str) else json.dumps({"questions": questions})
        (grading / quizlab.QUIZ_FILE).write_text(text, encoding="utf-8")
    return grading, root


def test_good_questions_have_no_problems_even_with_no_objectives_given(tmp_path):
    grading, root = _grading(tmp_path, QUESTIONS)
    assert quizlab.problems("E1", grading, {"id": "E1"}, root) == []
    assert quizlab.problems("E1", grading, {"id": "E1", "objectives": ["2.3"]}, root) == []


def test_a_quiz_file_that_cant_be_read_is_named(tmp_path):
    grading, root = _grading(tmp_path, "{not json")
    out = quizlab.problems("E1", grading, {"id": "E1"}, root)
    assert len(out) == 1 and out[0].startswith("E1: quiz.json can't be read (")


def test_a_question_that_isnt_an_object_is_named_by_its_place(tmp_path):
    grading, root = _grading(tmp_path, ["not a question", *QUESTIONS])
    assert "E1: question 1 isn't an object" in quizlab.problems("E1", grading, {"id": "E1"}, root)


def test_twenty_questions_are_allowed_and_twenty_one_arent(tmp_path):
    many = []
    for i in range(1, quizlab.MAX_QUESTIONS + 2):
        q = copy.deepcopy(QUESTIONS[0])
        q["id"] = f"q{i}"
        many.append(q)
    grading, root = _grading(tmp_path, many[:quizlab.MAX_QUESTIONS])
    assert quizlab.problems("E1", grading, {"id": "E1"}, root) == []
    (grading / quizlab.QUIZ_FILE).write_text(json.dumps({"questions": many}), encoding="utf-8")
    assert "E1: a quiz has 3 to 20 questions" in quizlab.problems("E1", grading, {"id": "E1"}, root)


@pytest.mark.parametrize("n", [3, 2])
def test_three_questions_are_the_fewest(tmp_path, n):
    grading, root = _grading(tmp_path, QUESTIONS[:n])
    out = quizlab.problems("E1", grading, {"id": "E1"}, root)
    assert ("E1: a quiz has 3 to 20 questions" in out) is (n < 3)
