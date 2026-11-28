"""Academy > Assessments engine calls (academy_exam_service.py): start, answer, flag, drill checks, an honest
clock across a restart, finish once and review."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import exam
from fabric_desktop.academy_exam_service import (ALL_DOMAINS, DIAGNOSTIC_PER_OBJECTIVE, PRACTICE_COUNT,
                                                 PRACTICE_MINUTES, AcademyExamMixin)


class Svc(AcademyExamMixin):
    def __init__(self, root):
        self.root = root


@pytest.fixture
def svc(tmp_path):
    return Svc(tmp_path)


def _first_answer(item):
    if item["type"] == "code":         # a different token in each gap
        return [o["id"] for o in item["options"][:len(item["parts"])]]
    if item.get("parts"):
        return [p["options"][0]["id"] for p in item["parts"]]
    return [o["id"] for o in item["options"]] if item["type"] == "order" else [item["options"][0]["id"]]


def test_home_lists_domains_objectives_and_counts(svc):
    home = svc.academy_exam_home("dp-700")
    assert home["notice"] == exam.NOTICE and home["label"] == exam.RESULT_LABEL
    assert [d["id"] for d in home["domains"]] == ["1", "2", "3"]
    objs = [o for d in home["domains"] for o in d["objectives"]]
    assert len(objs) == 49 and all(o["questions"] >= exam.MIN_PER_OBJECTIVE for o in objs)
    assert home["practice"] == {"count": PRACTICE_COUNT, "minutes": PRACTICE_MINUTES}
    assert home["unfinished"] == [] and home["finished"] == []


def test_practice_start_is_timed_running_and_never_shows_answers(svc):
    v = svc.academy_exam_start("dp-700", "practice")
    assert v["total"] == PRACTICE_COUNT and v["minutes"] == PRACTICE_MINUTES and v["running"]
    assert v["title"] == "DP-700 practice exam"
    for it in v["items"]:
        assert "explanation" not in it and "source" not in it and "key" not in it
    untimed = svc.academy_exam_start("dp-700", "practice", timed=False)
    assert untimed["minutes"] is None and untimed["time_left"] is None
    assert len(svc.academy_exam_home("dp-700")["unfinished"]) == 2


def test_a_retake_draws_questions_the_learner_has_not_seen(svc):
    first = svc.academy_exam_start("dp-700", "practice", timed=False)
    for it in first["items"]:
        svc.academy_exam_answer(first["id"], it["q"], _first_answer(it))
    svc.academy_exam_finish(first["id"])
    again = svc.academy_exam_start("dp-700", "practice", timed=False)
    overlap = {it["q"] for it in first["items"]} & {it["q"] for it in again["items"]}
    assert not overlap


def test_diagnostic_takes_two_per_objective_in_one_domain(svc):
    v = svc.academy_exam_start("dp-700", "diagnostic", domain="2")
    assert {it["objective"].split(".")[0] for it in v["items"]} == {"2"}
    per = {}
    for it in v["items"]:
        per[it["objective"]] = per.get(it["objective"], 0) + 1
    assert set(per.values()) == {DIAGNOSTIC_PER_OBJECTIVE} and v["minutes"] is None
    assert v["title"] == "Domain diagnostic: Ingest and transform data"
    with pytest.raises(exam.ExamError):
        svc.academy_exam_start("dp-700", "diagnostic", domain="9")


def test_home_sizes_each_diagnostic_and_remembers_the_last_one(svc):
    home = svc.academy_exam_home("dp-700")
    two = next(d for d in home["domains"] if d["id"] == "2")
    assert two["diagnostic"]["questions"] == DIAGNOSTIC_PER_OBJECTIVE * len(two["objectives"])
    assert two["diagnostic"]["minutes"] == round(two["diagnostic"]["questions"] * PRACTICE_MINUTES / PRACTICE_COUNT)
    assert two["diagnostic"]["last"] is None
    assert home["diagnostic_all"]["questions"] == DIAGNOSTIC_PER_OBJECTIVE * 49
    v = svc.academy_exam_start("dp-700", "diagnostic", domain="2")
    svc.academy_exam_answer(v["id"], v["items"][0]["q"], _first_answer(v["items"][0]))
    svc.academy_exam_finish(v["id"])
    home = svc.academy_exam_home("dp-700")
    last = next(d for d in home["domains"] if d["id"] == "2")["diagnostic"]["last"]
    assert last["total"] == v["total"] and last["correct"] in (0, 1) and last["finished_at"]
    assert home["diagnostic_all"]["last"] is None and home["finished"][0]["domain"] == "2"


def test_a_diagnostic_over_every_domain(svc):
    v = svc.academy_exam_start("dp-700", "diagnostic", domain=ALL_DOMAINS)
    assert {it["objective"].split(".")[0] for it in v["items"]} == {"1", "2", "3"}
    assert v["total"] == DIAGNOSTIC_PER_OBJECTIVE * 49 and v["minutes"] is None
    assert v["title"] == "Diagnostic: all of DP-700"


def test_drill_checks_each_answer_and_locks_it(svc):
    v = svc.academy_exam_start("dp-700", "drill", objectives=["1.1.1"])
    assert v["total"] >= exam.MIN_PER_OBJECTIVE and all(it["objective"] == "1.1.1" for it in v["items"])
    it = v["items"][0]
    with pytest.raises(exam.ExamError, match="Answer the question first"):
        svc.academy_exam_check(v["id"], it["q"])
    svc.academy_exam_answer(v["id"], it["q"], _first_answer(it))
    out = svc.academy_exam_check(v["id"], it["q"])
    assert out["explanation"] and out["source"].startswith("https://") and isinstance(out["correct"], bool)
    assert out["view"]["items"][0]["checked"] is True
    with pytest.raises(exam.ExamError, match="already checked"):
        svc.academy_exam_answer(v["id"], it["q"], [])
    with pytest.raises(exam.ExamError):
        svc.academy_exam_start("dp-700", "drill", objectives=["9.9.9"])


def test_practice_refuses_checks(svc):
    v = svc.academy_exam_start("dp-700", "practice")
    it = v["items"][0]
    svc.academy_exam_answer(v["id"], it["q"], _first_answer(it))
    with pytest.raises(exam.ExamError, match="Only a drill"):
        svc.academy_exam_check(v["id"], it["q"])


def test_a_restarted_engine_leaves_the_exam_paused_until_opened(tmp_path):
    first = Svc(tmp_path)
    v = first.academy_exam_start("dp-700", "practice")
    assert first.academy_exam_heartbeat(v["id"])["running"] is True
    again = Svc(tmp_path)                       # the app closed and opened again
    assert again.academy_exam_heartbeat(v["id"])["running"] is False
    assert again.academy_exam_open(v["id"])["running"] is True
    assert again.academy_exam_pause(v["id"])["running"] is False
    assert again.academy_exam_heartbeat(v["id"])["running"] is False


def test_flag_finish_and_review(svc):
    v = svc.academy_exam_start("dp-700", "practice")
    a, b = v["items"][0], v["items"][1]
    svc.academy_exam_answer(v["id"], a["q"], _first_answer(a))
    flagged = svc.academy_exam_flag(v["id"], b["q"], True)
    assert flagged["flagged"] == 1 and flagged["items"][1]["flagged"]
    with pytest.raises(exam.ExamError):
        svc.academy_exam_review(v["id"])
    done = svc.academy_exam_finish(v["id"])
    res = done["result"]
    assert res["label"] == exam.RESULT_LABEL and res["total"] == PRACTICE_COUNT and "items" not in res
    assert all(d["title"] and d["weight"] for d in res["by_domain"])
    assert all(o["text"] for o in res["by_objective"])
    assert res["cert_version"] == "2026-10-30"
    rev = svc.academy_exam_review(v["id"])
    assert len(rev["items"]) == PRACTICE_COUNT and rev["items"][0]["explanation"]
    with pytest.raises(exam.ExamError):
        svc.academy_exam_finish(v["id"])        # scored once
    assert svc.academy_exam_open(v["id"])["status"] == "finished"
    home = svc.academy_exam_home("dp-700")
    assert [s["id"] for s in home["finished"]] == [v["id"]] and home["unfinished"] == []


def test_bad_ids_are_refused(svc):
    with pytest.raises(exam.ExamError):
        svc.academy_exam_open("../../etc/passwd")
    with pytest.raises(exam.ExamError):
        svc.academy_exam_start("dp-700", "speedrun")


def test_databricks_home_uses_the_published_exam_and_flat_domains(svc):
    home = svc.academy_exam_home("dbx-de-associate")
    assert home["practice"] == {"count": 45, "minutes": 90}
    assert home["certification"]["code"] == "Databricks Certified Data Engineer Associate"
    assert "Databricks" in home["notice"] and "Microsoft" not in home["notice"]
    assert [d["id"] for d in home["domains"]] == [str(i) for i in range(1, 8)]
    objs = [o for d in home["domains"] for o in d["objectives"]]
    assert len(objs) == 33 and all(o["questions"] >= exam.MIN_PER_OBJECTIVE for o in objs)


def test_databricks_practice_exam_runs_to_a_result_named_for_databricks(svc):
    v = svc.academy_exam_start("dbx-de-associate", "practice")
    assert v["total"] == 45 and v["minutes"] == 90 and v["title"].endswith("practice exam")
    res = svc.academy_exam_finish(v["id"])["result"]
    assert res["vendor"] == "Databricks" and res["total"] == 45 and "Databricks" in res["notice"]
    assert svc.academy_exam_home("dp-700")["certification"]["code"] == "DP-700"


def test_a_drill_with_multi_part_questions_scores_partial_credit(svc):
    # 2.2.9 has a code completion question: right in one gap only, it earns that share of its point.
    v = svc.academy_exam_start("dp-700", "drill", objectives=["2.2.9"])
    code = next(it for it in v["items"] if it["type"] == "code")
    assert code["snippet_label"] == "T-SQL code" and "{{1}}" in code["snippet"] and "key" not in code
    key = exam.load_bank("dp-700")["questions"][code["q"]]["answer"]
    other = [o["id"] for o in code["options"] if o["id"] not in key]
    mine = [key[0]] + other[:len(key) - 1]
    half = svc.academy_exam_answer(v["id"], code["q"], [mine[0], ""] + mine[2:])
    assert next(it for it in half["items"] if it["q"] == code["q"])["answered"] is False
    svc.academy_exam_answer(v["id"], code["q"], mine)
    out = svc.academy_exam_check(v["id"], code["q"])
    assert (out["parts_right"], out["part_count"], out["points"]) == (1, 4, 0.25)
    res = svc.academy_exam_finish(v["id"])["result"]
    assert res["correct"] == 0.25 and res["by_objective"][0]["correct"] == 0.25
    item = next(it for it in svc.academy_exam_review(v["id"])["items"] if it["q"] == code["q"])
    assert item["points"] == 0.25 and item["parts_right"] == 1 and item["key"] == key
