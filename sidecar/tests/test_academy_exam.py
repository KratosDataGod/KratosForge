"""ACD-013: the Academy question bank is well formed and covers every DP-700 objective, and the exam engine
builds, times, scores and reviews practice exams honestly."""

from __future__ import annotations

import copy
import datetime as dt
import json
import shutil
from pathlib import Path

import pytest

from fabric_desktop.academy import exam, registry

T0 = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def bank():
    return exam.load_bank("dp-700")


def _objective_ids() -> list[str]:
    return [o["id"] for o in registry.objectives(registry.certification("dp-700"))]


# ---------------------------------------------------------------- bundled bank

def test_bundled_bank_has_no_problems_and_covers_every_objective():
    ids = _objective_ids()
    assert len(ids) == 49
    assert exam.problems("dp-700", ids) == []


def test_every_question_cites_microsoft_learn_and_explains_itself(bank):
    for q in bank["questions"].values():
        assert q["source"].startswith("https://learn.microsoft.com/"), q["id"]
        assert len(q["explanation"]) > 40, q["id"]


def test_option_ids_never_give_the_answer_away(bank):
    # Single answers aren't all on one letter, and no ordering question stores its steps in order.
    singles = [q["answer"][0] for q in bank["questions"].values() if q["type"] == "single"]
    assert max(singles.count(x) for x in set(singles)) < 0.4 * len(singles)
    for q in bank["questions"].values():
        if q["type"] == "order":
            assert q["answer"] != sorted(q["answer"]), q["id"]


def test_every_case_study_is_used(bank):
    used = {q.get("case") for q in bank["questions"].values()}
    assert set(bank["cases"]) <= used


# ---------------------------------------------------------------- problems() catches broken content

def _pack_copy(tmp_path: Path) -> Path:
    packs = tmp_path / "packs"
    shutil.copytree(exam.PACKS / "dp-700", packs / "dp-700")
    return packs


def _edit(packs: Path, objective: str, change) -> None:
    f = packs / "dp-700" / "questions" / f"{objective}.json"
    doc = json.loads(f.read_text(encoding="utf-8"))
    change(doc)
    f.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize(("change", "expect"), [
    (lambda d: d["questions"][0].update(source="https://example.com/x"), "source is the Microsoft Learn page"),
    (lambda d: d["questions"][0].update(answer=["z"]), "answer names options"),
    (lambda d: d["questions"][0].update(stem="Pick <b>one</b>"), "raw HTML"),
    (lambda d: d["questions"][0].update(type="essay"), "type is one of"),
    (lambda d: d["questions"][0].update(id="9.9.9-q1"), "id is <objective>-qN"),
    (lambda d: d["questions"][0].update(reviewed=None), "needs the date it was last reviewed"),
    (lambda d: d["questions"][0].update(case="nowhere"), "unknown case study"),
    (lambda d: d["questions"][0].update(explanation=""), "explanation is empty"),
    (lambda d: d.update(objective="2.2.8"), "objective doesn't match"),
    (lambda d: d["questions"].pop(), "has 3 live questions; needs 4"),
    (lambda d: d["questions"][1].update(status="draft"), "has 3 live questions; needs 4"),
])
def test_problems_catch_each_kind_of_broken_question(tmp_path, change, expect):
    packs = _pack_copy(tmp_path)
    _edit(packs, "2.2.9", change)
    found = exam.problems("dp-700", _objective_ids(), packs=packs)
    assert any(expect in p for p in found), found


def test_problems_catch_bad_answer_shapes(tmp_path):
    packs = _pack_copy(tmp_path)

    def broken(doc):
        single, multi, order = (next(q for q in doc["questions"] if q["type"] == t) for t in ("single", "multiple", "order"))
        single["answer"] = [o["id"] for o in single["options"][:2]]
        multi["answer"] = [multi["options"][0]["id"]]
        order["answer"] = order["answer"][:-1]

    _edit(packs, "2.1.1", broken)
    found = "\n".join(exam.problems("dp-700", packs=packs))
    assert "single-answer question has 3 to 6 options and one answer" in found
    assert "multiple-answer question has 4 to 8 options and at least 2 answers" in found
    assert "ordering question has 3 to 6 steps" in found


def test_problems_catch_an_ordering_question_stored_in_order(tmp_path):
    packs = _pack_copy(tmp_path)

    def in_order(doc):
        q = next(q for q in doc["questions"] if q["type"] == "order")
        q["answer"] = sorted(q["answer"])
    _edit(packs, "2.1.1", in_order)
    assert any("give the order away" in p for p in exam.problems("dp-700", packs=packs))


def test_problems_catch_bad_also_accept(tmp_path):
    packs = _pack_copy(tmp_path)

    def bad(doc):
        doc["questions"][0]["also_accept"] = [["a"]]
    _edit(packs, "2.2.9", bad)
    assert any("also_accept is for ordering questions" in p for p in exam.problems("dp-700", packs=packs))


def test_problems_catch_a_question_for_an_unknown_objective(tmp_path):
    packs = _pack_copy(tmp_path)
    found = exam.problems("dp-700", [i for i in _objective_ids() if i != "3.3.6"], packs=packs)
    assert any("3.3.6 isn't one of the certification's objectives" in p for p in found)


def test_problems_catch_an_unused_case_and_a_badly_named_file(tmp_path):
    packs = _pack_copy(tmp_path)
    cases = packs / "dp-700" / "cases.json"
    doc = json.loads(cases.read_text(encoding="utf-8"))
    doc["spare-case"] = {"title": "Spare", "text": "Nobody uses this."}
    cases.write_text(json.dumps(doc), encoding="utf-8")
    (packs / "dp-700" / "questions" / "notes.json").write_text("{}", encoding="utf-8")
    found = exam.problems("dp-700", packs=packs)
    assert any("spare-case: no question uses it" in p for p in found)
    assert any("questions/notes.json: a question file is named by its objective" in p for p in found)


def test_an_unknown_pack_is_a_problem_not_a_crash():
    assert exam.problems("../etc") == ["Not an Academy pack: ../etc"]


# ---------------------------------------------------------------- building an exam

def test_same_seed_gives_the_same_exam(bank):
    a = exam.build(bank, cert_version="2026-10-30", count=30, seed=7, now=T0)
    b = exam.build(bank, cert_version="2026-10-30", count=30, seed=7, now=T0)
    c = exam.build(bank, cert_version="2026-10-30", count=30, seed=8, now=T0)
    assert a["items"] == b["items"] and a["id"] != b["id"]
    assert a["items"] != c["items"]


def test_questions_spread_evenly_over_the_objectives(bank):
    s = exam.build(bank, cert_version="v", count=49, seed=1, now=T0)
    assert len(s["items"]) == 49
    assert len({it["objective"] for it in s["items"]}) == 49     # one from every objective
    s = exam.build(bank, cert_version="v", objectives=["2.2.9", "3.3.1"], seed=1, now=T0)
    assert {it["objective"] for it in s["items"]} == {"2.2.9", "3.3.1"}
    assert len(s["items"]) == len([q for q in bank["questions"].values() if q["objective"] in ("2.2.9", "3.3.1")])


def test_retakes_draw_unseen_then_missed_questions_first(bank):
    first = exam.build(bank, cert_version="v", count=49, seed=3, now=T0)
    asked = {it["q"] for it in first["items"]}
    history = {q: "right" for q in asked}
    second = exam.build(bank, cert_version="v", count=49, seed=3, now=T0, history=history)
    assert not asked & {it["q"] for it in second["items"]}      # every objective has an unseen question left
    one = sorted(q for q in bank["questions"] if bank["questions"][q]["objective"] == "1.1.1")
    missed = one[0]
    history = {q: "right" for q in one} | {missed: "wrong"}
    s = exam.build(bank, cert_version="v", objectives=["1.1.1"], count=1, seed=9, now=T0, history=history)
    assert [it["q"] for it in s["items"]] == [missed]
    again = exam.build(bank, cert_version="v", count=49, seed=3, now=T0, history=history)
    assert again["items"] == exam.build(bank, cert_version="v", count=49, seed=3, now=T0, history=history)["items"]


def test_history_keeps_each_questions_latest_finished_outcome():
    def done(at, rows, status="finished"):
        return {"status": status, "finished_at": at, "result": {"items": [{"q": q, "correct": c} for q, c in rows]}}
    sessions = [done("2026-11-02", [("a", True), ("b", True)]), done("2026-11-01", [("a", False), ("c", False)]),
                done(None, [("d", False)], status="in_progress"), {"status": "finished", "result": None}]
    assert exam.history(sessions) == {"a": "right", "b": "right", "c": "wrong"}


def test_case_study_questions_sit_together(bank):
    s = exam.build(bank, cert_version="v", seed=3, now=T0)
    for case in bank["cases"]:
        at_ = [i for i, it in enumerate(s["items"]) if bank["questions"][it["q"]].get("case") == case]
        assert at_ == list(range(at_[0], at_[0] + len(at_))), case


def test_build_refuses_bad_requests(bank):
    with pytest.raises(exam.ExamError, match="no questions"):
        exam.build(bank, cert_version="v", objectives=["9.9.9"], now=T0)
    with pytest.raises(exam.ExamError, match="1 to 300 minutes"):
        exam.build(bank, cert_version="v", minutes=0, now=T0)
    with pytest.raises(exam.ExamError, match="practice, diagnostic"):
        exam.build(bank, cert_version="v", kind="final", now=T0)


# ---------------------------------------------------------------- the screen never sees answers

def test_view_never_carries_answers_explanations_or_sources(bank):
    s = exam.build(bank, cert_version="v", count=20, seed=2, now=T0)
    v = exam.view(s, bank)
    text = json.dumps(v)
    assert '"explanation"' not in text and '"source"' not in text and '"key"' not in text
    for item in v["items"]:
        q = bank["questions"][item["q"]]
        assert set(item) >= {"stem", "options", "type", "answer", "flagged"}
        assert item["answer"] is None
        assert sorted(o["id"] for o in item["options"]) == sorted(o["id"] for o in q["options"])
        assert item["select"] == (len(q["answer"]) if q["type"] == "multiple" else None)
    assert v["notice"] == exam.NOTICE and "not a Microsoft score" in v["notice"]


# ---------------------------------------------------------------- the clock

def test_clock_counts_only_while_running_and_caps_each_gap(bank):
    s = exam.build(bank, cert_version="v", count=5, minutes=10, seed=1, now=T0)
    exam.heartbeat(s, at(100))                 # not started: nothing counted
    assert s["elapsed"] == 0
    exam.resume(s, at(100))
    exam.heartbeat(s, at(130))
    assert s["elapsed"] == 30
    exam.heartbeat(s, at(130 + 3600))          # the PC slept for an hour: only MAX_GAP counts
    assert s["elapsed"] == 30 + exam.MAX_GAP
    exam.pause(s, at(130 + 3600 + 10))
    exam.heartbeat(s, at(130 + 3600 + 500))    # paused: nothing counted
    assert s["elapsed"] == 30 + exam.MAX_GAP + 10
    assert exam.time_left(s) == 600 - s["elapsed"]


def test_the_clock_never_runs_backwards(bank):
    s = exam.resume(exam.build(bank, cert_version="v", count=3, seed=1, now=T0), T0)
    exam.heartbeat(s, at(-500))                # the system clock moved back
    assert s["elapsed"] == 0


def test_a_saved_exam_reopens_paused_at_its_last_heartbeat(bank, tmp_path):
    store = exam.ExamStore(tmp_path)
    s = exam.resume(exam.build(bank, cert_version="v", count=3, minutes=5, seed=1, now=T0), T0)
    exam.heartbeat(s, at(40))
    store.save(s)                              # the app closes here without pausing
    back = store.load(s["id"])
    assert back["running"] is False and back["elapsed"] == 40
    exam.resume(back, at(40 + 86400))          # a day later: the time away isn't counted
    exam.heartbeat(back, at(40 + 86400 + 20))
    assert back["elapsed"] == 60


def test_untimed_exams_have_no_time_left(bank):
    s = exam.build(bank, cert_version="v", count=3, seed=1, now=T0)
    assert exam.time_left(s) is None


def test_no_answers_once_time_is_up(bank):
    s = exam.resume(exam.build(bank, cert_version="v", count=3, minutes=1, seed=1, now=T0), T0)
    qid = s["items"][0]["q"]
    exam.heartbeat(s, at(59))
    exam.answer(s, qid, [s["items"][0]["options"][0]], now=at(59.5))
    with pytest.raises(exam.ExamError, match="Time is up"):
        exam.answer(s, qid, [s["items"][0]["options"][1]], now=at(61))
    exam.finish(s, bank, now=at(62))
    assert s["result"]["time_up"] is True


# ---------------------------------------------------------------- answering, flags, scoring

def _right(bank, it):
    return list(bank["questions"][it["q"]]["answer"])


def _wrong(bank, it):
    q = bank["questions"][it["q"]]
    if q["type"] == "order":
        return list(reversed(q["answer"]))
    return [next(o for o in it["options"] if o not in q["answer"])]


def test_answers_and_flags(bank):
    s = exam.resume(exam.build(bank, cert_version="v", count=4, seed=5, now=T0), T0)
    a, b = s["items"][0], s["items"][1]
    exam.answer(s, a["q"], a["options"][:1], now=at(1))
    exam.flag(s, b["q"], now=at(2))
    exam.flag(s, b["q"], now=at(3))            # flagging twice keeps one flag
    v = exam.view(s, bank)
    assert v["answered"] == 1 and v["flagged"] == 1
    assert v["items"][0]["answer"] == a["options"][:1] and v["items"][1]["flagged"]
    exam.flag(s, b["q"], on=False, now=at(4))
    exam.answer(s, a["q"], [], now=at(5))      # an empty answer clears it
    v = exam.view(s, bank)
    assert v["answered"] == 0 and v["flagged"] == 0
    with pytest.raises(exam.ExamError, match="option the question doesn't have"):
        exam.answer(s, a["q"], ["zz"], now=at(6))
    with pytest.raises(exam.ExamError, match="isn't in this exam"):
        exam.answer(s, "9.9.9-q1", ["a"], now=at(6))


def test_scoring_is_all_or_nothing_and_by_objective(bank):
    s = exam.resume(exam.build(bank, cert_version="2026-10-30", objectives=["2.1.1", "2.2.9"], seed=4, now=T0), T0)
    right = [it for it in s["items"] if it["objective"] == "2.1.1"]
    wrong = [it for it in s["items"] if it["objective"] == "2.2.9"]
    for it in right:
        exam.answer(s, it["q"], _right(bank, it), now=at(10))
    for it in wrong[:-1]:
        exam.answer(s, it["q"], _wrong(bank, it), now=at(10))
    multi = next(it for it in right if bank["questions"][it["q"]]["type"] == "multiple")
    exam.flag(s, multi["q"], now=at(11))       # flags never change the score
    exam.finish(s, bank, now=at(20))
    res = s["result"]
    assert res["label"] == exam.RESULT_LABEL and res["notice"] == exam.NOTICE
    by = {t["objective"]: t for t in res["by_objective"]}
    assert by["2.1.1"]["correct"] == by["2.1.1"]["total"] == len(right)
    assert by["2.2.9"] == {"objective": "2.2.9", "correct": 0, "total": len(wrong), "percent": 0}
    assert res["by_domain"] == [{"domain": "2", "correct": len(right), "total": len(s["items"]),
                                 "percent": round(100 * len(right) / len(s["items"]))}]
    assert s["status"] == "finished" and s["running"] is False
    # One right option short on a multiple-answer question is wrong.
    q = bank["questions"][multi["q"]]
    assert not exam._correct(q, q["answer"][:-1]) and exam._correct(q, list(reversed(q["answer"])))


def test_ordering_accepts_an_equally_right_order(bank):
    q = next(q for q in bank["questions"].values() if q.get("also_accept"))
    assert exam._correct(q, q["answer"]) and exam._correct(q, q["also_accept"][0])
    assert not exam._correct(q, list(reversed(q["answer"])))


def test_a_finished_exam_is_never_scored_again(bank):
    s = exam.resume(exam.build(bank, cert_version="v", count=6, seed=9, now=T0), T0)
    for it in s["items"]:
        exam.answer(s, it["q"], _right(bank, it), now=at(5))
    exam.finish(s, bank, now=at(6))
    before = copy.deepcopy(s["result"])
    # The bank changes later: a key is edited and a question is retired.
    changed = copy.deepcopy(bank)
    first, second = s["items"][0]["q"], s["items"][1]["q"]
    changed["questions"][first] = {**changed["questions"][first], "answer": ["zz"],
                                  "version": changed["questions"][first]["version"] + 1}
    del changed["questions"][second]
    with pytest.raises(exam.ExamError, match="finished"):
        exam.finish(s, changed, now=at(7))
    with pytest.raises(exam.ExamError, match="finished"):
        exam.answer(s, first, [], now=at(7))
    assert s["result"] == before
    rv = exam.review(s, changed)
    assert rv[0]["correct"] is True and rv[0]["changed_since"] is True
    assert rv[1] == {"n": 2, "q": second, "missing": True}


def test_review_needs_a_finished_exam_and_shows_why(bank):
    s = exam.resume(exam.build(bank, cert_version="v", count=3, seed=2, now=T0), T0)
    with pytest.raises(exam.ExamError, match="Finish the exam"):
        exam.review(s, bank)
    exam.finish(s, bank, now=at(3))            # nothing answered: all wrong, nothing lost
    rv = exam.review(s, bank)
    assert [r["correct"] for r in rv] == [False, False, False]
    for r in rv:
        q = bank["questions"][r["q"]]
        assert r["key"] == q["answer"] and r["explanation"] == q["explanation"] and r["source"] == q["source"]


def test_questions_removed_mid_exam_are_left_out_not_counted_wrong(bank):
    s = exam.resume(exam.build(bank, cert_version="v", count=4, seed=6, now=T0), T0)
    gone = s["items"][0]["q"]
    trimmed = copy.deepcopy(bank)
    del trimmed["questions"][gone]
    assert exam.view(s, trimmed)["items"][0]["missing"] is True
    exam.finish(s, trimmed, now=at(3))
    assert s["result"]["left_out"] == [gone] and s["result"]["total"] == 3


# ---------------------------------------------------------------- evidence and storage

def test_evidence_row_has_results_by_objective_but_no_answers(bank):
    s = exam.resume(exam.build(bank, cert_version="2026-10-30", objectives=["1.3.4"], kind="diagnostic", seed=1,
                               minutes=10, now=T0), T0)
    with pytest.raises(exam.ExamError, match="finished"):
        exam.evidence_row(s)
    it = s["items"][0]
    exam.answer(s, it["q"], _right(bank, it), now=at(30))
    exam.finish(s, bank, now=at(45))
    row = exam.evidence_row(s)
    assert row["type"] == "academy.assessment" and row["cert_version"] == "2026-10-30" and row["kind"] == "diagnostic"
    assert row["objectives"] == {"1.3.4": {"correct": 1, "total": len(s["items"])}}
    assert row["timing"] == {"elapsed": 45.0, "minutes": 10, "time_up": False}
    assert "answer" not in json.dumps(row) and "key" not in json.dumps(row)


def test_store_saves_lists_and_refuses_bad_ids(bank, tmp_path):
    store = exam.ExamStore(tmp_path / "exams")
    assert store.list() == []
    a = exam.build(bank, cert_version="v", count=2, seed=1, now=T0)
    b = exam.build(bank, cert_version="v", count=2, seed=1, now=at(60))
    exam.finish(exam.resume(b, at(60)), bank, now=at(70))
    store.save(a)
    store.save(b)
    listed = store.list()
    assert [x["id"] for x in listed] == [b["id"], a["id"]]
    assert listed[0]["status"] == "finished" and listed[0]["label"] == exam.RESULT_LABEL
    assert store.load(b["id"])["result"] == b["result"]
    for bad in ("../../x", "A" * 32, ""):
        with pytest.raises(exam.ExamError, match="Not an exam id"):
            store.load(bad)
    with pytest.raises(exam.ExamError, match="isn't on this PC"):
        store.load("0" * 32)
    (tmp_path / "exams" / ("1" * 32 + ".json")).write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    with pytest.raises(exam.ExamError, match="can open"):
        store.load("1" * 32)


# ---------------------------------------------------------------- Databricks pack (DBA-012)

DBX = "dbx-de-associate"


def test_databricks_bank_covers_every_objective_with_two_level_ids():
    ids = [o["id"] for o in registry.objectives(registry.certification(DBX))]
    assert len(ids) == 33 and all(i.count(".") == 1 for i in ids)
    assert exam.problems(DBX, ids) == []
    bank = exam.load_bank(DBX)
    assert {q["objective"] for q in bank["questions"].values()} == set(ids)


def test_databricks_questions_cite_databricks_docs_and_its_notice_names_databricks():
    bank = exam.load_bank(DBX)
    assert exam.source_hosts(DBX) == ("docs.databricks.com", "www.databricks.com")
    assert all(q["source"].startswith("https://docs.databricks.com/") for q in bank["questions"].values())
    assert "not Databricks exam questions" in bank["notice"] and "Microsoft" not in bank["notice"]
    assert "not Databricks' exam interface" in bank["notice"]
    assert exam.notice("dp-700") == exam.NOTICE == exam.load_bank("dp-700")["notice"]


def test_databricks_questions_never_cite_another_platforms_docs(tmp_path):
    packs = tmp_path / "packs"
    shutil.copytree(exam.PACKS / DBX, packs / DBX)
    f = packs / DBX / "questions" / "7.2.json"
    doc = json.loads(f.read_text(encoding="utf-8"))
    doc["questions"][0]["source"] = "https://learn.microsoft.com/en-us/fabric/"
    f.write_text(json.dumps(doc), encoding="utf-8")
    assert any("source is the Databricks documentation page" in p for p in exam.problems(DBX, packs=packs))


def test_databricks_drill_scores_by_two_level_objective():
    bank = exam.load_bank(DBX)
    s = exam.build(bank, cert_version="v", objectives=["7.2"], kind="drill", seed=3, now=T0)
    assert s["items"] and all(it["objective"] == "7.2" for it in s["items"])
    exam.finish(s, bank, now=at(1))
    assert s["result"]["by_domain"][0]["domain"] == "7" and s["result"]["notice"] == bank["notice"]
