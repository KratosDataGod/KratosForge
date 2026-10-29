"""LX-07: Learn evidence in Profile (rules v2). A Learn exercise whose checks all pass in a published
lesson is one evidence row; the lesson's last exercise also finishes the lesson. What keeps it honest:
drafts don't count, passing again adds nothing, excluding a row takes its achievement away, and
nothing grows with the number of runs."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import progress
from fabric_desktop.learn import content

from test_profile import call, plain  # noqa: F401 - the signed-in service fixture

LM01 = {"id": "LM-01", "kind": "module", "title": "Your first query on this PC", "status": "live", "version": 1,
        "level": "beginner", "path": "data-fundamentals", "exercises": [{"id": "north-big-orders"}]}


def passed(module=LM01, exercise="north-big-orders", kinds=("result_matches",), ok=True, version=1, measured=None):
    results = [{"kind": k, "passed": ok, "detail": "x", **({"measured": measured} if k == "faster_than" and measured else {})}
               for k in kinds]
    return {"module": module["id"], "exercise": exercise, "version": version, "passed": ok, "results": results}


def lab(mid, *exercises, kind="lab"):
    return {**LM01, "id": mid, "kind": kind, "title": f"Lab {mid}", "exercises": [{"id": e} for e in exercises]}


def profile_with(*pairs):
    p = progress.empty()
    for result, module in pairs:
        progress.add(p, progress.from_lab(result, module))
        done = progress.module_complete(p, module)
        if done:
            progress.add(p, done)
    return p


# ---- what counts


def test_only_a_full_pass_in_a_published_lesson_is_evidence():
    row = progress.from_lab(passed(), LM01)
    assert row["type"] == "lab.pass" and row["dimensions"] == ["Learning & Growth"]
    assert row["ref"] == {"source": "learn", "id": "LM-01/north-big-orders", "revision": "v1"}
    assert progress.from_lab(passed(ok=False), LM01) is None
    assert progress.from_lab(passed(), {**LM01, "status": "draft"}) is None   # a draft may still change
    assert progress.from_lab({**passed(), "results": []}, LM01) is None


def test_a_row_keeps_ids_and_check_kinds_never_the_answer():
    result = {**passed(kinds=("result_matches", "faster_than"), measured={"seconds": 0.1, "baseline_seconds": 0.4, "speedup": 4.0}),
              "answer": "SELECT secret_column FROM orders"}
    row = progress.from_lab(result, LM01)
    assert "secret_column" not in json.dumps(row)
    assert row["detail"]["checks"] == ["result_matches", "faster_than"]   # as the exercise lists them
    assert row["measurement"]["speedup"] == 4.0 and row["measurement"]["kind"] == "measured"
    assert "Performance & Optimization" in row["dimensions"]


def test_passing_the_same_exercise_again_is_the_same_row_and_a_new_version_earns_nothing_more():
    p = profile_with((passed(), LM01))
    assert not progress.add(p, progress.from_lab(passed(), LM01))
    xp = progress.progression(p)["xp"]
    v2 = {**LM01, "version": 2}
    assert progress.add(p, progress.from_lab(passed(version=2), v2))
    done = progress.module_complete(p, v2)
    assert progress.add(p, done)
    assert progress.progression(p)["xp"] == xp   # the same lesson again: evidence, no XP or award


def test_first_spark_and_finishing_a_lesson():
    p = profile_with((passed(), LM01))
    types = [r["type"] for r in p["evidence"]]
    assert types == ["lab.pass", "module.complete"]
    pr = progress.progression(p)
    assert [a["id"] for a in pr["achievements"]] == ["first-spark"]
    assert pr["xp"] == 50 + progress.MODULE_COMPLETE_XP


def test_a_lesson_is_finished_only_when_every_exercise_passed():
    two = lab("LAB-09", "a", "b")
    p = profile_with((passed(two, "a"), two))
    assert [r["type"] for r in p["evidence"]] == ["lab.pass"]
    progress.add(p, progress.from_lab(passed(two, "b"), two))
    assert progress.module_complete(p, two)["type"] == "module.complete"


def test_the_data_smith_needs_five_different_exercises_not_five_runs():
    p = profile_with(*[(passed(lab(f"LAB-{i}", "x"), "x"), lab(f"LAB-{i}", "x")) for i in range(4)])
    assert "data-smith" not in {a["id"] for a in progress.progression(p)["achievements"]}
    assert progress.progression(p)["next"][-1]["progress"] == "4 of 5 different Learn exercises"
    progress.add(p, progress.from_lab(passed(lab("LAB-0", "x"), "x", version=2), {**lab("LAB-0", "x"), "version": 2}))
    assert "data-smith" not in {a["id"] for a in progress.progression(p)["achievements"]}
    p = profile_with(*[(passed(lab(f"LAB-{i}", "x"), "x"), lab(f"LAB-{i}", "x")) for i in range(5)])
    assert "data-smith" in {a["id"] for a in progress.progression(p)["achievements"]}


@pytest.mark.parametrize("kind, award", [("faster_than", "query-forger"), ("table_shape", "architects-eye"),
                                         ("integrity", "architects-eye"), ("defects_found", "guardian")])
def test_each_learn_achievement_needs_its_kind_of_check(kind, award):
    p = profile_with((passed(kinds=("result_matches", kind)), LM01))
    assert award in {a["id"] for a in progress.progression(p)["achievements"]}
    plain_pass = profile_with((passed(), LM01))
    assert award not in {a["id"] for a in progress.progression(plain_pass)["achievements"]}


def test_module_xp_shares_the_daily_cap():
    mods = [lab(f"LAB-{i}", "x") for i in range(5)]
    p = profile_with(*[(passed(m, "x"), m) for m in mods])
    xp_rows = [x for x in progress.progression(p)["xp_by_row"].values() if x]
    assert xp_rows == [progress.MODULE_COMPLETE_XP] * progress.DAILY_NOTEBOOK_XP_CAP


def test_the_report_names_finished_lessons_and_cites_them():
    p = profile_with((passed(), LM01))
    md = progress.report(p, 90)["markdown"]
    assert "## Learning" in md
    assert "Finished KratosForge Learn lesson LM\\-01, Your first query on this PC" in md
    assert "Learn LM\\-01/north\\-big\\-orders · version 1 · passed" in md
    assert "No Learn lessons or labs in this period" in progress.report(progress.empty(), 90)["markdown"]


# ---- the service, end to end


@pytest.fixture
def live(monkeypatch):
    real = content.get
    monkeypatch.setattr(content, "get", lambda mid: {**real(mid), "status": "live"})


GOOD = content.file_text("LM-01", "solution.sql")


def test_a_passed_exercise_records_first_spark_and_checking_again_adds_nothing(plain, live):  # noqa: F811
    call(plain, "learn_lab_start", module_id="LM-01")
    out = call(plain, "learn_lab_check", module_id="LM-01", exercise_id="north-big-orders", answer=GOOD)
    assert out["passed"] and [a["name"] for a in out["earned"]] == ["The First Spark"]
    again = call(plain, "learn_lab_check", module_id="LM-01", exercise_id="north-big-orders", answer=GOOD)
    assert again["passed"] and again["earned"] == []
    got = call(plain, "profile_get")
    assert sorted(r["type"] for r in got["evidence"]) == ["lab.pass", "module.complete"]
    assert any(a["id"] == "first-spark" for a in got["catalog"])


def test_a_failed_check_or_a_draft_records_nothing(plain, monkeypatch):  # noqa: F811
    call(plain, "learn_lab_start", module_id="LM-01")
    out = call(plain, "learn_lab_check", module_id="LM-01", exercise_id="north-big-orders", answer="SELECT 1 AS x")
    assert not out["passed"] and out["earned"] == []
    real = content.get
    monkeypatch.setattr(content, "get", lambda mid: {**real(mid), "status": "draft"})
    out = call(plain, "learn_lab_check", module_id="LM-01", exercise_id="north-big-orders", answer=GOOD)
    assert out["passed"] and out["earned"] == []
    assert call(plain, "profile_get")["evidence"] == []


def test_excluding_the_exercise_takes_first_spark_away(plain, live):  # noqa: F811
    call(plain, "learn_lab_start", module_id="LM-01")
    call(plain, "learn_lab_check", module_id="LM-01", exercise_id="north-big-orders", answer=GOOD)
    rows = call(plain, "profile_get")["evidence"]
    lab_row = next(r for r in rows if r["type"] == "lab.pass")
    out = call(plain, "profile_exclude", id=lab_row["id"])
    assert "first-spark" not in {a["id"] for a in out["progression"]["achievements"]}


def test_a_profile_that_cannot_be_written_never_changes_the_check(plain, live, monkeypatch):  # noqa: F811
    def broken(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(progress, "save", broken)
    call(plain, "learn_lab_start", module_id="LM-01")
    out = call(plain, "learn_lab_check", module_id="LM-01", exercise_id="north-big-orders", answer=GOOD)
    assert out["passed"] and out["earned"] == []
