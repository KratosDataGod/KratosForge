"""Mutation batch 412: academy/exam.py. Tests for the survivors of its first score (see
docs/quality/mutation-testing.md)."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from fabric_desktop.academy import exam

T0 = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def _q(qid: str = "1.1-q1", **over) -> dict:
    q = {"id": qid, "type": "single", "stem": "Which store fits?",
         "options": [{"id": "a", "text": "A lakehouse"}, {"id": "b", "text": "A warehouse"},
                     {"id": "c", "text": "An eventhouse"}],
         "answer": ["b"], "explanation": "Because the warehouse is the store for T-SQL.",
         "source": "https://learn.microsoft.com/fabric/x", "version": 1, "status": "draft"}
    q.update(over)
    return q


def _bank(n: int = 3) -> dict:
    qs = {f"1.1-q{i}": {**_q(f"1.1-q{i}"), "objective": "1.1"} for i in range(1, n + 1)}
    return {"pack": "p", "questions": qs, "cases": {}, "notice": exam.NOTICE}


def _pack(tmp_path: Path, name: str = "my-pack", cases: bool = False) -> Path:
    root = tmp_path / name
    (root / "questions").mkdir(parents=True)
    (root / "questions" / "1.1.json").write_text(json.dumps(
        {"schema": exam.QUESTION_SCHEMA, "objective": "1.1", "questions": [_q()]}), encoding="utf-8")
    if cases:
        (root / "cases.json").write_text(json.dumps({"c1": {"title": "T", "text": "X"}}), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- packs

def test_a_good_pack_name_with_no_folder_isnt_a_pack(tmp_path):
    with pytest.raises(exam.ExamError, match="Not an Academy pack"):
        exam._pack_root("no-such-pack", tmp_path)


def test_a_folder_with_a_bad_name_isnt_a_pack(tmp_path):
    (tmp_path / "Bad_Name").mkdir()
    with pytest.raises(exam.ExamError, match="Not an Academy pack"):
        exam._pack_root("Bad_Name", tmp_path)


def test_a_pack_without_cases_json_loads_with_no_cases(tmp_path):
    packs = _pack(tmp_path)
    bank = exam.load_bank("my-pack", packs, include_drafts=True)
    assert bank["cases"] == {} and list(bank["questions"]) == ["1.1-q1"]


def test_a_pack_with_cases_json_loads_them(tmp_path):
    packs = _pack(tmp_path, cases=True)
    assert exam.load_bank("my-pack", packs, include_drafts=True)["cases"] == {"c1": {"title": "T", "text": "X"}}


# ---------------------------------------------------------------- question rules

def test_an_option_with_no_text_is_named():
    q = _q(options=[{"id": "a", "text": "One"}, {"id": "b", "text": ""}, {"id": "c", "text": "Three"}])
    assert "question 1.1-q1 option b is empty" in exam._question_problems("1.1", q, {})


@pytest.mark.parametrize("version", [0, -1, "1", None, 1.0])
def test_a_version_that_isnt_a_whole_number_from_1_is_named(version):
    out = exam._question_problems("1.1", _q(version=version), {})
    assert "question 1.1-q1: version is a whole number from 1" in out


def test_version_1_is_fine():
    assert exam._question_problems("1.1", _q(version=1), {}) == []


# ---------------------------------------------------------------- sessions

def _session(bank: dict, kind: str = "practice", minutes: int | None = None) -> dict:
    s = exam.build(bank, cert_version="1", seed=3, kind=kind, minutes=minutes, now=T0)
    return exam.resume(s, now=T0)


def test_heartbeat_answer_and_finish_give_back_the_session():
    bank = _bank()
    s = _session(bank)
    assert exam.heartbeat(s, now=at(5)) is s
    qid = s["items"][0]["q"]
    assert exam.answer(s, qid, ["b"], now=at(6)) is s
    assert exam.finish(s, bank, now=at(7)) is s and s["status"] == "finished"


def test_checking_a_question_no_longer_in_the_bank_says_so():
    bank = _bank()
    s = _session(bank, kind="drill")
    qid = s["items"][0]["q"]
    exam.answer(s, qid, ["b"], now=at(1))
    smaller = copy.deepcopy(bank)
    del smaller["questions"][qid]
    with pytest.raises(exam.ExamError, match="no longer in the question bank"):
        exam.check(s, smaller, qid, now=at(2))
    assert qid not in s["checked"]


def test_review_needs_both_finished_and_a_result():
    bank = _bank()
    s = _session(bank)
    exam.finish(s, bank, now=at(1))
    no_result = {**s, "result": None}
    with pytest.raises(exam.ExamError, match="Finish the exam"):
        exam.review(no_result, bank)
    not_finished = {**s, "status": "in_progress"}
    with pytest.raises(exam.ExamError, match="Finish the exam"):
        exam.review(not_finished, bank)
    assert len(exam.review(s, bank)) == 3


@pytest.mark.parametrize("cases", [{"Bad Id": {"title": "T", "text": "X"}}, {"good-id": "just text"}])
def test_a_case_with_a_bad_id_or_that_isnt_an_object_is_named(tmp_path, cases):
    packs = _pack(tmp_path)
    (packs / "my-pack" / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    cid = next(iter(cases))
    out = exam.problems("my-pack", None, packs)
    assert f"case {cid}: ids are lowercase words joined by hyphens" in out


def test_view_numbers_the_questions_from_1():
    bank = _bank()
    s = _session(bank)
    assert [it["n"] for it in exam.view(s, bank)["items"]] == [1, 2, 3]


def test_a_finished_exam_with_every_answer_right_is_100_percent():
    bank = _bank()
    s = _session(bank)
    for it in s["items"]:
        exam.answer(s, it["q"], ["b"], now=at(1))
    assert exam.finish(s, bank, now=at(2))["result"]["percent"] == 100


def test_the_clock_is_kept_to_the_millisecond():
    bank = _bank()
    s = _session(bank)
    exam.heartbeat(s, now=T0 + dt.timedelta(microseconds=123))  # 0.000123 s
    assert s["elapsed"] == 0.0
    exam.heartbeat(s, now=T0 + dt.timedelta(microseconds=1600))  # last_seen keeps whole seconds: 0.0016 s again
    assert s["elapsed"] == 0.002


def test_an_exam_without_a_seed_gets_a_31_bit_seed():
    seeds = {exam.build(_bank(), cert_version="1", now=T0)["seed"] for _ in range(20)}
    assert all(isinstance(x, int) and 0 <= x < 2**31 for x in seeds) and len(seeds) > 1


def _order(**over) -> dict:
    return _q(type="order", options=[{"id": "a", "text": "Load"}, {"id": "b", "text": "Clean"},
                                     {"id": "c", "text": "Publish"}], answer=["b", "a", "c"], **over)


def test_also_accept_that_repeats_the_answer_is_named():
    out = exam._question_problems("1.1", _order(also_accept=[["b", "a", "c"]]), {})
    assert any("also_accept is for ordering questions" in p for p in out)


def test_a_different_full_order_in_also_accept_is_fine():
    assert exam._question_problems("1.1", _order(also_accept=[["a", "b", "c"]]), {}) == []


def _multiple(n_options: int, answer: list[str]) -> dict:
    ids = "abcdefgh"[:n_options]
    return _q(type="multiple", options=[{"id": i, "text": f"Option {i}"} for i in ids], answer=answer)


@pytest.mark.parametrize("n,answer", [(4, ["a", "b"]), (4, ["a", "b", "c"]), (8, ["a", "h"])])
def test_a_multiple_answer_question_at_its_limits_is_fine(n, answer):
    assert exam._question_problems("1.1", _multiple(n, answer), {}) == []


@pytest.mark.parametrize("n,answer", [(4, ["a", "b", "c", "d"]), (3, ["a", "b"]), (4, ["a"])])
def test_a_multiple_answer_question_past_its_limits_is_named(n, answer):
    out = exam._question_problems("1.1", _multiple(n, answer), {})
    assert any("a multiple-answer question has 4 to 8 options" in p for p in out)


def test_an_objective_with_exactly_the_minimum_of_live_questions_is_enough(tmp_path):
    root = tmp_path / "my-pack"
    (root / "questions").mkdir(parents=True)
    live = [_q(f"1.1-q{i}", status="live", reviewed="2026-11-01") for i in range(1, exam.MIN_PER_OBJECTIVE + 1)]
    (root / "questions" / "1.1.json").write_text(json.dumps(
        {"schema": exam.QUESTION_SCHEMA, "objective": "1.1", "questions": live}), encoding="utf-8")
    assert exam.problems("my-pack", ["1.1"], tmp_path) == []
    (root / "questions" / "1.1.json").write_text(json.dumps(
        {"schema": exam.QUESTION_SCHEMA, "objective": "1.1", "questions": live[:-1]}), encoding="utf-8")
    assert exam.problems("my-pack", ["1.1"], tmp_path) == [
        f"objective 1.1: has {exam.MIN_PER_OBJECTIVE - 1} live questions; needs {exam.MIN_PER_OBJECTIVE}"]
