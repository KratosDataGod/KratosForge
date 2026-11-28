"""academy_exam_service.py paths that its first mutation run (batch 431, docs/quality/mutation/batch-431.md)
showed no test checked. Sessions are files in ``tmp_path``; nothing reaches the network."""

from __future__ import annotations

import pytest

from fabric_desktop import academy_exam_service as svc_mod
from fabric_desktop.academy import exam, registry
from fabric_desktop.academy_exam_service import AcademyExamMixin


class Svc(AcademyExamMixin):
    def __init__(self, root):
        self.root = root


@pytest.fixture
def svc(tmp_path):
    return Svc(tmp_path)


def _first(item):
    return [o["id"] for o in item["options"]] if item["type"] == "order" else [item["options"][0]["id"]]


def _choice_item(view):
    """An item answered by picking options: an exam is drawn at random, and a matching, yes/no, hot area or code
    item takes one entry per part instead, so _first can't answer it."""
    return next(i for i in view["items"] if i["type"] != "order" and i.get("parts") is None and i["options"])


# ---- the small helpers

def test_practice_uses_the_published_exam_only_when_its_numbers_are_whole():
    assert svc_mod._practice({}, 999) == (50, 100)
    assert svc_mod._practice({"exam": None}, 999) == (50, 100)
    assert svc_mod._practice({"exam": {"scored_questions": 45, "minutes": 90}}, 999) == (45, 90)
    assert svc_mod._practice({"exam": {"scored_questions": "45", "minutes": 90.0}}, 999) == (50, 100)
    assert svc_mod._practice({"exam": {"scored_questions": 45}}, 999) == (45, 100)
    assert svc_mod._practice({"exam": {"minutes": 90}}, 999) == (50, 90)
    assert svc_mod._practice({"exam": {"scored_questions": 45}}, 44) == (44, 100)
    assert svc_mod._practice({}, 50) == (50, 100)
    assert svc_mod._practice({}, 51) == (50, 100)


def test_the_published_defaults():
    assert (svc_mod.PRACTICE_COUNT, svc_mod.PRACTICE_MINUTES, svc_mod.DIAGNOSTIC_PER_OBJECTIVE) == (50, 100, 2)


def test_code_is_the_exam_code_for_fabric_else_the_title():
    assert svc_mod._code({"id": "dp-700"}) == "DP-700"
    assert svc_mod._code({"id": "dp-700", "platform": "fabric", "title": "T"}) == "DP-700"
    assert svc_mod._code({"id": "x-1", "platform": "databricks", "title": "The Title"}) == "The Title"
    assert svc_mod._code({"id": "x-1", "platform": "databricks", "title": ""}) == "x-1"
    assert svc_mod._code({"id": "x-1", "platform": "databricks"}) == "x-1"


def test_title_by_kind():
    cert = {"id": "dp-700", "domains": [{"id": "1", "title": "One"}, {"id": "2", "title": "Two"}]}
    assert svc_mod._title({"kind": "practice"}, cert) == "DP-700 practice exam"
    assert svc_mod._title({"kind": "diagnostic", "domain": "2"}, cert) == "Domain diagnostic: Two"
    assert svc_mod._title({"kind": "diagnostic", "domain": "9"}, cert) == "Domain diagnostic"
    assert svc_mod._title({"kind": "diagnostic", "domain": "1"}, {"id": "dp-700"}) == "Domain diagnostic"
    assert svc_mod._title({"kind": "drill", "objectives": ["1.1.1"]}, cert) == "Objective drill: 1.1.1"
    assert svc_mod._title({"kind": "drill", "objectives": ["a", "b", "c"]}, cert) == "Objective drill: a, b, c"
    assert svc_mod._title({"kind": "drill", "objectives": ["a", "b", "c", "d"]}, cert) == "Objective drill: 4 objectives"
    assert svc_mod._title({"kind": "drill"}, cert) == "Objective drill: "


# ---- the running set

def test_the_running_set_is_kept_on_the_service(svc):
    live = svc._exam_live()
    assert live == set() and svc._exam_live() is live and svc._academy_exams_running is live


def test_save_and_load_keep_running_only_for_an_exam_in_progress(svc):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    assert v["id"] in svc._exam_live()
    assert svc._exam_load(v["id"])["running"] is True
    svc.academy_exam_pause(v["id"])
    assert v["id"] not in svc._exam_live() and svc._exam_load(v["id"])["running"] is False
    svc.academy_exam_open(v["id"])
    assert v["id"] in svc._exam_live()
    svc.academy_exam_finish(v["id"])
    assert v["id"] not in svc._exam_live()


def test_load_doesnt_mark_a_finished_exam_running(svc):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    svc.academy_exam_finish(v["id"])
    svc._exam_live().add(v["id"])
    assert svc._exam_load(v["id"])["running"] is False


# ---- home

def test_home_fields(svc):
    home = svc.academy_exam_home("dp-700")
    cert = registry.certification("dp-700")
    assert home["pack"] == "dp-700"
    assert home["certification"] == {"id": "dp-700", "title": cert["title"], "vendor": "Microsoft",
                                     "version": cert["version"], "code": "DP-700"}
    bank = exam.load_bank("dp-700")
    assert home["questions"] == len(bank["questions"])
    assert home["diagnostic_per_objective"] == 2
    d1 = home["domains"][0]
    assert d1["title"] == cert["domains"][0]["title"] and d1["weight"] == cert["domains"][0]["weight"]
    first = d1["objectives"][0]
    want = sum(1 for q in bank["questions"].values() if q["objective"] == first["id"])
    assert first["questions"] == want and first["text"]
    assert sum(o["questions"] for d in home["domains"] for o in d["objectives"]) == len(bank["questions"])


def test_home_lists_only_the_packs_exams_by_status(svc):
    a = svc.academy_exam_start("dp-700", "practice", timed=False)
    b = svc.academy_exam_start("dp-700", "practice", timed=False)
    svc.academy_exam_finish(b["id"])
    other = svc.academy_exam_start("dbx-de-associate", "practice", timed=False)
    home = svc.academy_exam_home("dp-700")
    assert [s["id"] for s in home["unfinished"]] == [a["id"]]
    assert [s["id"] for s in home["finished"]] == [b["id"]]
    assert other["id"] not in {s["id"] for s in home["unfinished"] + home["finished"]}


def test_home_practice_count_never_more_than_the_bank(svc, monkeypatch):
    real = exam.load_bank

    def small(pid):
        bank = real(pid)
        return {**bank, "questions": dict(list(bank["questions"].items())[:7])}
    monkeypatch.setattr(svc_mod.exam, "load_bank", small)
    assert svc.academy_exam_home("dp-700")["practice"] == {"count": 7, "minutes": 100}


# ---- start

def test_diagnostic_counts_two_per_objective_and_keeps_the_domain(svc):
    cert = registry.certification("dp-700")
    ids = [o["id"] for o in registry.objectives(cert) if o["domain"] == "1"]
    v = svc.academy_exam_start("dp-700", "diagnostic", domain=1)
    assert v["total"] == 2 * len(ids) and v["minutes"] is None
    assert svc._exam_load(v["id"])["domain"] == "1"
    assert v["title"] == f"Domain diagnostic: {cert['domains'][0]['title']}"


def test_start_refusals_say_what_to_pick(svc):
    with pytest.raises(exam.ExamError) as err:
        svc.academy_exam_start("dp-700", "diagnostic")
    assert str(err.value) == "Pick one of the certification's domains"
    for objs in (None, [], ["1.1.1", "9.9.9"]):
        with pytest.raises(exam.ExamError) as err:
            svc.academy_exam_start("dp-700", "drill", objectives=objs)
        assert str(err.value) == "Pick one or more of the certification's objectives"
    with pytest.raises(exam.ExamError) as err:
        svc.academy_exam_start("dp-700", "speedrun")
    assert str(err.value) == "An assessment is one of practice, diagnostic, drill"


def test_drill_keeps_its_objectives_and_names_them(svc):
    v = svc.academy_exam_start("dp-700", "drill", objectives=["1.1.1", "1.1.2"])
    assert svc._exam_load(v["id"])["objectives"] == ["1.1.1", "1.1.2"]
    assert v["title"] == "Objective drill: 1.1.1, 1.1.2" and v["minutes"] is None
    assert {it["objective"] for it in v["items"]} == {"1.1.1", "1.1.2"}


def test_start_saves_a_running_exam(svc):
    v = svc.academy_exam_start("dp-700", "practice")
    assert v["running"] is True and v["minutes"] == 100
    again = Svc(svc.root)
    assert again._exam_load(v["id"])["status"] == "in_progress"


# ---- the calls on one exam

def test_open_a_finished_exam_returns_its_result(svc):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    done = svc.academy_exam_finish(v["id"])
    opened = svc.academy_exam_open(v["id"])
    assert opened == {"id": v["id"], "status": "finished", "result": done["result"]}


def test_open_an_unfinished_exam_restarts_its_clock(svc):
    v = svc.academy_exam_start("dp-700", "practice")
    svc.academy_exam_pause(v["id"])
    again = Svc(svc.root)
    opened = again.academy_exam_open(v["id"])
    assert opened["running"] is True and opened["id"] == v["id"] and opened["title"] == "DP-700 practice exam"
    assert v["id"] in again._exam_live()


def test_answer_none_clears_and_choices_become_text(svc):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    it = _choice_item(v)
    got = svc.academy_exam_answer(v["id"], it["q"], _first(it))
    assert got["answered"] == 1 and got["title"] == "DP-700 practice exam"
    assert svc.academy_exam_answer(v["id"], it["q"], None)["answered"] == 0


def test_flag_on_by_default_and_off(svc):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    q = v["items"][0]["q"]
    assert svc.academy_exam_flag(v["id"], q)["flagged"] == 1
    assert svc.academy_exam_flag(v["id"], q, 0)["flagged"] == 0


def test_check_is_saved(svc):
    v = svc.academy_exam_start("dp-700", "drill", objectives=["1.1.1"])
    it = _choice_item(v)
    svc.academy_exam_answer(v["id"], it["q"], _first(it))
    out = svc.academy_exam_check(v["id"], it["q"])
    assert out["view"]["title"] == "Objective drill: 1.1.1"
    opened = Svc(svc.root).academy_exam_open(v["id"])["items"]
    assert next(i for i in opened if i["q"] == it["q"])["checked"] is True


def test_heartbeat_and_pause_report_the_clock(svc):
    v = svc.academy_exam_start("dp-700", "practice")
    beat = svc.academy_exam_heartbeat(v["id"])
    assert set(beat) == {"status", "running", "elapsed", "time_left"}
    assert beat["status"] == "in_progress" and beat["running"] is True
    assert 0 <= beat["elapsed"] < 60 and 100 * 60 - 60 < beat["time_left"] <= 100 * 60
    paused = svc.academy_exam_pause(v["id"])
    assert paused["status"] == "in_progress" and paused["running"] is False
    assert paused["time_left"] == pytest.approx(100 * 60 - paused["elapsed"])
    assert Svc(svc.root)._exam_load(v["id"])["running"] is False
    assert v["id"] not in svc._exam_live()


def test_heartbeat_and_pause_on_a_finished_exam(svc):
    v = svc.academy_exam_start("dp-700", "practice")
    svc.academy_exam_finish(v["id"])
    assert svc.academy_exam_heartbeat(v["id"]) == {"status": "finished", "running": False}
    paused = svc.academy_exam_pause(v["id"])
    assert paused["status"] == "finished" and paused["running"] is False
    assert set(paused) == {"status", "running", "elapsed", "time_left"}


def test_heartbeat_on_an_untimed_exam(svc):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    assert svc.academy_exam_heartbeat(v["id"])["time_left"] is None


def test_pause_doesnt_write_a_finished_exam(svc, monkeypatch):
    v = svc.academy_exam_start("dp-700", "practice")
    svc.academy_exam_finish(v["id"])
    saved = []
    monkeypatch.setattr(Svc, "_exam_save", lambda self, s: saved.append(s))
    svc.academy_exam_pause(v["id"])
    svc.academy_exam_heartbeat(v["id"])
    assert saved == []


# ---- finish, review and the result's words

def test_finish_and_review_results(svc):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    done = svc.academy_exam_finish(v["id"])
    assert set(done) == {"id", "status", "result"} and done["id"] == v["id"] and done["status"] == "finished"
    res = done["result"]
    s = svc._exam_load(v["id"])
    assert res["title"] == "DP-700 practice exam" and res["kind"] == "practice" and res["pack"] == "dp-700"
    assert res["code"] == "DP-700" and res["vendor"] == "Microsoft" and res["cert_version"] == s["cert_version"]
    assert res["minutes"] is None and res["finished_at"] == s["finished_at"] and res["total_items"] == 50
    assert "items" not in res and "items" in s["result"]
    rev = svc.academy_exam_review(v["id"])
    assert set(rev) == {"id", "result", "items"} and rev["id"] == v["id"] and rev["result"] == res


def test_a_result_from_an_older_certification_version_has_no_titles(svc, monkeypatch):
    v = svc.academy_exam_start("dp-700", "practice", timed=False)
    svc.academy_exam_finish(v["id"])
    real = registry.certification
    monkeypatch.setattr(svc_mod.registry, "certification", lambda pid: {**real(pid), "version": "2099-01-01"})
    res = svc.academy_exam_open(v["id"])["result"]
    assert res["by_domain"] and all(d["title"] is None and d["weight"] is None for d in res["by_domain"])
    assert res["by_objective"] and all(o["text"] is None for o in res["by_objective"])
    monkeypatch.setattr(svc_mod.registry, "certification", real)
    res = svc.academy_exam_open(v["id"])["result"]
    cert = real("dp-700")
    titles = {d["id"]: d["title"] for d in cert["domains"]}
    assert all(d["title"] == titles[d["domain"]] for d in res["by_domain"])


def test_result_with_a_domain_the_certification_lost(svc, monkeypatch):
    session = {"pack": "dp-700", "kind": "practice", "cert_version": registry.certification("dp-700")["version"],
               "minutes": 5, "finished_at": "t", "items": [1, 2],
               "result": {"by_domain": [{"domain": "9"}], "by_objective": [{"objective": "9.9.9"}], "items": [1]}}
    res = svc_mod._result(session)
    assert res["by_domain"] == [{"domain": "9", "title": None, "weight": None}]
    assert res["by_objective"] == [{"objective": "9.9.9", "text": None}]
    assert "items" not in res and session["result"]["items"] == [1]
    assert res["total_items"] == 2 and res["minutes"] == 5 and res["finished_at"] == "t"
    bare = svc_mod._result({**session, "result": {}})
    assert bare["by_domain"] == [] and bare["by_objective"] == []


def test_home_an_objective_with_no_questions_shows_none(svc, monkeypatch):
    real = exam.load_bank

    def without_1_1_1(pid):
        bank = real(pid)
        return {**bank, "questions": {k: q for k, q in bank["questions"].items() if q["objective"] != "1.1.1"}}
    monkeypatch.setattr(svc_mod.exam, "load_bank", without_1_1_1)
    objs = {o["id"]: o["questions"] for d in svc.academy_exam_home("dp-700")["domains"] for o in d["objectives"]}
    assert objs["1.1.1"] == 0 and objs["1.1.2"] > 0
