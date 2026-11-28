"""academy/exam.py at its edges (mutation testing, batch 392).

test_academy_exam.py checks the bundled bank and runs exams end to end. These pin each question check at its
bound (3 and 6 options, 4 and 8, 6 steps, version 1, four live questions), the bank's defaults, what the
session calls return, the clock's rounding, scores' rounding, the seed's range, and what history, review,
evidence and the store accept.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from fabric_desktop.academy import exam

T0 = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)
SRC = "https://learn.microsoft.com/fabric/x"


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def q(n: int = 1, type_: str = "single", options: int = 3, answer: list[str] | None = None, **kw) -> dict:
    ids = "abcdefghi"[:options]
    if answer is None:
        answer = {"single": ["a"], "multiple": ["a", "b"], "order": list(reversed(ids))}[type_]
    return {"id": f"1.1.1-q{n}", "type": type_, "stem": "Which?", "explanation": "Because.",
            "options": [{"id": i, "text": f"opt {i}"} for i in ids], "answer": answer, "source": SRC,
            "version": 1, "status": "draft", **kw}


def problems_of(question: dict) -> list[str]:
    return exam._question_problems("1.1.1", question, {})


def test_a_well_formed_question_has_no_problems():
    assert problems_of(q()) == []


@pytest.mark.parametrize("question,ok", [
    (q(options=3), True), (q(options=6), True), (q(options=7), False),
    (q(type_="multiple", options=4), True), (q(type_="multiple", options=8), True),
    (q(type_="multiple", options=9), False),
    (q(type_="multiple", options=4, answer=["a", "b", "c"]), True),
    (q(type_="multiple", options=4, answer=["a", "b", "c", "d"]), False),
    (q(type_="order", options=6), True), (q(type_="order", options=7), False),
    (q(version=0), False), (q(version="1"), False),
])
def test_question_shape_bounds(question, ok):
    assert (problems_of(question) == []) is ok


def test_option_ids_used_twice_are_a_problem():
    question = q()
    question["options"][1]["id"] = "a"
    assert "question 1.1.1-q1: options each need their own id, a to h" in problems_of(question)


def test_an_option_that_isnt_an_object_is_a_problem_not_a_crash():
    question = q()
    question["options"].append("d")
    assert "question 1.1.1-q1: options each need their own id, a to h" in problems_of(question)


def test_also_accepting_the_answer_itself_is_a_problem():
    question = q(type_="order")
    question["also_accept"] = [list(question["answer"])]
    assert problems_of(question) == [("question 1.1.1-q1: also_accept is for ordering questions: other full "
                                      "step orders, each different from the answer")]


# ---- the pack


def pack_copy(tmp_path: Path) -> Path:
    packs = tmp_path / "packs"
    shutil.copytree(exam.PACKS / "dp-700", packs / "dp-700")
    return packs


def write_questions(packs: Path, questions: list[dict], objective: str = "1.1.1") -> None:
    folder = packs / "dp-700" / "questions"
    shutil.rmtree(folder)
    folder.mkdir()
    (folder / f"{objective}.json").write_text(json.dumps({"schema": exam.QUESTION_SCHEMA, "objective": objective,
                                                          "questions": questions}), encoding="utf-8")


def test_a_folder_named_unlike_a_pack_isnt_one(tmp_path):
    (tmp_path / "DP_700").mkdir()
    with pytest.raises(exam.ExamError, match="Not an Academy pack: DP_700"):
        exam.load_bank("DP_700", tmp_path)


def test_drafts_are_left_out_by_default_and_retired_ones_always(tmp_path):
    packs = pack_copy(tmp_path)
    write_questions(packs, [q(1, status="draft"), q(2, status="retired"), q(3, status="live", reviewed="2026-11-01")])
    assert sorted(exam.load_bank("dp-700", packs)["questions"]) == ["1.1.1-q3"]
    assert sorted(exam.load_bank("dp-700", packs, include_drafts=True)["questions"]) == ["1.1.1-q1", "1.1.1-q3"]


def test_the_bank_has_the_packs_cases_or_none(tmp_path):
    packs = pack_copy(tmp_path)
    cases = json.loads((packs / "dp-700" / "cases.json").read_text(encoding="utf-8"))
    assert exam.load_bank("dp-700", packs)["cases"] == cases
    (packs / "dp-700" / "cases.json").unlink()
    assert exam.load_bank("dp-700", packs)["cases"] == {}


def test_unreadable_cases_are_the_one_problem(tmp_path):
    packs = pack_copy(tmp_path)
    (packs / "dp-700" / "cases.json").write_text("{", encoding="utf-8")
    [p] = exam.problems("dp-700", packs=packs)
    assert p.startswith("cases.json can't be read (")


@pytest.mark.parametrize("cases", [{"Bad Id": {"title": "t", "text": "x"}}, {"ok-id": "not an object"}])
def test_a_case_needs_an_id_and_an_object(tmp_path, cases):
    packs = pack_copy(tmp_path)
    (packs / "dp-700" / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    [cid] = cases
    assert f"case {cid}: ids are lowercase words joined by hyphens" in exam.problems("dp-700", packs=packs)


@pytest.mark.parametrize("live,problem", [(0, "objective 1.1.1: has 0 live questions; needs 4"),
                                          (3, "objective 1.1.1: has 3 live questions; needs 4"), (4, None)])
def test_each_objective_needs_four_live_questions(tmp_path, live, problem):
    packs = pack_copy(tmp_path)
    (packs / "dp-700" / "cases.json").unlink()
    write_questions(packs, [q(n, status="live", reviewed="2026-11-01") for n in range(1, live + 1)] or [q(9)])
    assert exam.problems("dp-700", ["1.1.1"], packs) == ([problem] if problem else [])


# ---- sessions


def small_bank() -> dict:
    qs = {f"1.1.1-q{n}": {**q(n), "objective": "1.1.1"} for n in (1, 2)}
    qs["1.2.1-q1"] = {**q(1, type_="multiple", options=4), "id": "1.2.1-q1", "objective": "1.2.1"}
    return {"pack": "dp-700", "questions": qs, "cases": {}}


def test_a_count_of_zero_still_asks_one_question():
    assert len(exam.build(small_bank(), cert_version="v", count=0, seed=1, now=T0)["items"]) == 1


def test_three_hundred_minutes_is_allowed():
    assert exam.build(small_bank(), cert_version="v", minutes=300, seed=1, now=T0)["minutes"] == 300


def test_a_random_seed_is_below_two_to_the_31(monkeypatch):
    asked = []
    monkeypatch.setattr(exam.secrets, "randbelow", lambda n: asked.append(n) or 7)
    assert exam.build(small_bank(), cert_version="v", now=T0)["seed"] == 7
    assert asked == [2 ** 31]


def test_session_calls_return_the_session():
    bank = small_bank()
    s = exam.build(bank, cert_version="v", seed=1, now=T0)
    qid = s["items"][0]["q"]
    assert exam.resume(s, T0) is s
    assert exam.heartbeat(s, at(1)) is s
    assert exam.answer(s, qid, [s["items"][0]["options"][0]], at(2)) is s
    assert exam.flag(s, qid, now=at(3)) is s
    assert exam.pause(s, at(4)) is s
    assert exam.finish(s, bank, now=at(5)) is s


def test_the_clock_keeps_milliseconds():
    s = exam.resume(exam.build(small_bank(), cert_version="v", seed=1, now=T0), T0)
    exam.heartbeat(s, T0 + dt.timedelta(microseconds=123450))
    assert s["elapsed"] == 0.123


def test_view_numbers_from_one_and_says_how_many_to_pick():
    bank = small_bank()
    v = exam.view(exam.build(bank, cert_version="v", seed=1, now=T0), bank)
    assert [i["n"] for i in v["items"]] == [1, 2, 3]
    assert {i["q"]: i["select"] for i in v["items"]} == {"1.1.1-q1": None, "1.1.1-q2": None, "1.2.1-q1": 2}


def test_scores_round_to_the_nearest_percent():
    bank = small_bank()
    s = exam.resume(exam.build(bank, cert_version="v", seed=1, now=T0), T0)
    exam.answer(s, "1.1.1-q1", ["a"], at(1))
    res = exam.finish(s, bank, now=at(2))["result"]
    assert res["percent"] == 33
    assert [t["percent"] for t in res["by_objective"]] == [50, 0]


def test_an_exam_whose_questions_all_left_the_bank_scores_zero():
    bank = small_bank()
    s = exam.resume(exam.build(bank, cert_version="v", seed=1, now=T0), T0)
    res = exam.finish(s, {**bank, "questions": {}}, now=at(1))["result"]
    assert (res["percent"], res["total"], len(res["left_out"])) == (0, 0, 3)


def test_a_drill_cant_check_a_question_that_left_the_bank():
    bank = small_bank()
    s = exam.resume(exam.build(bank, cert_version="v", seed=1, kind="drill", now=T0), T0)
    exam.answer(s, "1.1.1-q1", ["a"], at(1))
    with pytest.raises(exam.ExamError, match="1.1.1-q1 is no longer in the question bank"):
        exam.check(s, {**bank, "questions": {}}, "1.1.1-q1", at(2))


def test_review_keeps_flags_and_other_orders():
    bank = small_bank()
    bank["questions"]["1.1.1-q1"] = {**q(1, type_="order"), "objective": "1.1.1", "also_accept": [["c", "a", "b"]]}
    s = exam.resume(exam.build(bank, cert_version="v", seed=1, now=T0), T0)
    exam.flag(s, "1.1.1-q1", now=at(1))
    rows = {r["q"]: r for r in exam.review(exam.finish(s, bank, now=at(2)), bank)}
    assert rows["1.1.1-q1"]["flagged"] is True and rows["1.1.1-q2"]["flagged"] is False
    assert rows["1.1.1-q1"]["also_accept"] == [["c", "a", "b"]] and rows["1.1.1-q2"]["also_accept"] == []


@pytest.mark.parametrize("session", [{"status": "finished", "result": None}, {"status": "in_progress", "result": {"x": 1}}])
def test_review_and_evidence_need_a_finished_exam_with_a_result(session):
    with pytest.raises(exam.ExamError, match="Finish the exam"):
        exam.review(session, small_bank())
    with pytest.raises(exam.ExamError, match="Only a finished exam gives evidence"):
        exam.evidence_row(session)


def test_history_skips_rows_that_arent_question_records():
    s = {"status": "finished", "finished_at": "1", "result": {"items": ["x", {"q": 5}, {"q": "a", "correct": True}]}}
    assert exam.history([s]) == {"a": "right"}


def test_the_store_lists_only_this_packs_finished_exams(tmp_path):
    store = exam.ExamStore(tmp_path)
    ids = ["a" * 32, "b" * 32, "c" * 32, "d" * 32]
    docs = [{"pack": "dp-700", "status": "finished"}, {"pack": "dp-700", "status": "in_progress"},
            {"pack": "other", "status": "finished"}, ["not", "an", "object"]]
    for i, d in zip(ids, docs, strict=True):
        (tmp_path / f"{i}.json").write_text(json.dumps(d), encoding="utf-8")
    assert store.finished("dp-700") == [docs[0]]


def test_the_store_list_counts_each_exams_questions(tmp_path):
    store = exam.ExamStore(tmp_path)
    (tmp_path / f"{'a' * 32}.json").write_text(json.dumps({"id": "a", "items": [1, 2, 3]}), encoding="utf-8")
    assert store.list()[0]["total"] == 3


def test_an_objectives_percent_rounds_to_the_nearest_percent():
    bank = {"pack": "dp-700", "cases": {}, "questions": {f"1.1.1-q{n}": {**q(n), "objective": "1.1.1"} for n in (1, 2, 3)}}
    s = exam.resume(exam.build(bank, cert_version="v", seed=1, now=T0), T0)
    exam.answer(s, "1.1.1-q1", ["a"], at(1))
    assert exam.finish(s, bank, now=at(2))["result"]["by_objective"][0]["percent"] == 33


def test_nine_options_is_too_many_for_a_multiple_answer_question():
    found = problems_of(q(type_="multiple", options=9))
    assert "question 1.1.1-q1: a multiple-answer question has 4 to 8 options and at least 2 answers, not all" in found
