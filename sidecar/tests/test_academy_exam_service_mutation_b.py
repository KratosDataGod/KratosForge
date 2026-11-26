"""Mutation batch 410: academy_exam_service.py. The published defaults, the drill and diagnostic refusals in
their own words, drill titles, the default flag, a heartbeat after the finish, and a result's domain rows."""

from __future__ import annotations

import pytest

from fabric_desktop import academy_exam_service as svc_mod
from fabric_desktop.academy import exam
from fabric_desktop.academy_exam_service import AcademyExamMixin


class Svc(AcademyExamMixin):
    def __init__(self, root):
        self.root = root


@pytest.fixture
def svc(tmp_path):
    return Svc(tmp_path)


def _first_answer(item):
    """An answer the question takes, whichever type was drawn: a multi-part one (yes or no, hot area,
    matching) has no options of its own, so each part gets its first choice."""
    if item["type"] == "code":         # a different token in each gap
        return [o["id"] for o in item["options"][:len(item["parts"])]]
    if item.get("parts"):
        return [p["options"][0]["id"] for p in item["parts"]]
    return [o["id"] for o in item["options"]] if item["type"] == "order" else [item["options"][0]["id"]]


def test_without_a_published_exam_practice_is_fifty_questions_in_a_hundred_minutes() -> None:
    assert svc_mod._practice({}, 999) == (50, 100)
    assert svc_mod._practice({"exam": {"scored_questions": 45, "minutes": 90}}, 30) == (30, 90)


def test_a_diagnostic_draws_two_questions_per_objective(svc) -> None:
    v = svc.academy_exam_start("dp-700", "diagnostic", domain="1")
    objectives = {it["objective"] for it in v["items"]}
    assert v["total"] == 2 * len(objectives)


def test_a_diagnostic_and_a_drill_refuse_in_their_own_words(svc) -> None:
    with pytest.raises(exam.ExamError, match="Pick one of the certification's domains"):
        svc.academy_exam_start("dp-700", "diagnostic", domain="9")
    with pytest.raises(exam.ExamError, match="Pick one or more of the certification's objectives"):
        svc.academy_exam_start("dp-700", "drill", objectives=["1.1.1", "9.9.9"])
    with pytest.raises(exam.ExamError, match="Pick one or more of the certification's objectives"):
        svc.academy_exam_start("dp-700", "drill", objectives=[])


def test_a_drill_title_names_up_to_three_objectives_then_counts_them(svc) -> None:
    three = ["1.1.1", "1.1.2", "1.1.3"]
    assert svc.academy_exam_start("dp-700", "drill", objectives=three)["title"] == \
        "Objective drill: 1.1.1, 1.1.2, 1.1.3"
    four = three + ["1.1.4"]
    assert svc.academy_exam_start("dp-700", "drill", objectives=four)["title"] == "Objective drill: 4 objectives"


def test_answer_returns_the_view_and_flag_turns_on_by_default(svc) -> None:
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    a, b = v["items"][0], v["items"][1]
    view = svc.academy_exam_answer(v["id"], a["q"], _first_answer(a))
    assert view["id"] == v["id"] and view["items"][0]["answer"]
    flagged = svc.academy_exam_flag(v["id"], b["q"])
    assert flagged["flagged"] == 1 and flagged["items"][1]["flagged"]


@pytest.mark.parametrize("seed", [10, 182])  # the first question drawn is a matching, then a hot area one
def test_answer_takes_a_first_question_of_any_type(svc, monkeypatch, seed) -> None:
    # The draw is random, so a multi-part first question (no options of its own) came up now and then.
    monkeypatch.setattr(exam.secrets, "randbelow", lambda n: seed)
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    a = v["items"][0]
    assert svc.academy_exam_answer(v["id"], a["q"], _first_answer(a))["items"][0]["answer"]


def test_a_heartbeat_after_the_finish_says_the_clock_is_stopped(svc) -> None:
    v = svc.academy_exam_start("dp-700", "practice")
    svc.academy_exam_finish(v["id"])
    assert svc.academy_exam_heartbeat(v["id"]) == {"status": "finished", "running": False}


def test_a_result_has_a_row_per_domain_and_objective_with_their_words(svc) -> None:
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    res = svc.academy_exam_finish(v["id"])["result"]
    assert res["by_domain"] and all(d["title"] and d["weight"] for d in res["by_domain"])
    assert res["by_objective"] and all(o["text"] for o in res["by_objective"])


def test_home_counts_each_objectives_questions_and_shows_zero_for_none(svc, monkeypatch) -> None:
    real = exam.load_bank
    full = real("dp-700")
    drop = "1.1.1"

    def fewer(pid):
        bank = real(pid)
        return {**bank, "questions": {k: q for k, q in bank["questions"].items() if q["objective"] != drop}}

    monkeypatch.setattr(exam, "load_bank", fewer)
    home = svc.academy_exam_home("dp-700")
    objs = {o["id"]: o["questions"] for d in home["domains"] for o in d["objectives"]}
    assert objs[drop] == 0
    want = sum(1 for q in full["questions"].values() if q["objective"] == "1.1.2")
    assert objs["1.1.2"] == want
    assert sum(objs.values()) == home["questions"]
