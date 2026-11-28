"""Mastery levels (mastery/levels.py), mutation batch 355: how each kind of row counts for a skill,
flag by flag; each level at the exact edge of its rule; and a class's talents, states and mastery at
the edges of their counts."""

from __future__ import annotations

import datetime as dt

from fabric_desktop import progress
from fabric_desktop.mastery import levels

DAY0 = dt.date(2026, 9, 1)

SKILL = {"evidence": {"learn": ["LM-01", "LAB-02", "LAB-11"], "academy": ["dp-700/E22"],
                      "objectives": ["dp-700:1.1"], "events": ["validate.pass", "prove.match"],
                      "rules": ["NB004"]}}


def flags(category, source, *, independent=True, advanced=False, measured=False, simulated=False, platform=False):
    return {"category": category, "source": source, "independent": independent, "advanced": advanced,
            "measured": measured, "simulated": simulated, "platform": platform}


# ---- how a row counts


def test_a_finished_lesson_is_knowledge_and_nothing_more():
    row = {"id": "r", "type": "module.complete", "ref": {"id": "LM-01/intro"}}
    assert levels.counts(row, SKILL) == flags("knowledge", "LM-01", independent=False)


def test_a_finished_capstone_lab_is_advanced_independent_practice():
    row = {"id": "r", "type": "module.complete", "ref": {"id": "LAB-11"}}
    assert levels.counts(row, SKILL) == flags("practice", "LAB-11", advanced=True)


def test_a_passed_lab_is_measured_by_a_faster_than_check_and_advanced_only_for_a_capstone():
    fast = {"id": "r", "type": "lab.pass", "ref": {"id": "LAB-02/x"}, "detail": {"checks": ["faster_than"]}}
    assert levels.counts(fast, SKILL) == flags("practice", "LAB-02", measured=True)
    plain = {"id": "r", "type": "lab.pass", "ref": {"id": "LAB-11/x"}, "detail": {"checks": []}}
    assert levels.counts(plain, SKILL) == flags("practice", "LAB-11", advanced=True)


def test_an_academy_exercise_finds_its_pack_from_its_id_when_the_row_doesnt_name_it():
    skill = {"evidence": {"objectives": ["dp-700:1.1"]}}
    row = {"id": "r", "type": "academy.exercise", "ref": {"id": "dp-700/E99"},
           "detail": {"objectives": ["1.1"], "class": "local_verified", "mode": "guided"}}
    assert levels.counts(row, skill) == flags("practice", "dp-700/E99")


def exercise(**detail):
    return {"id": "r", "type": "academy.exercise", "ref": {"id": "dp-700/E22"},
            "detail": {"certification": "dp-700", "class": "local_verified", "mode": "challenge", **detail}}


def test_one_hint_still_counts_as_independent_and_two_dont():
    assert levels.counts(exercise(hints_used=1), SKILL)["independent"] is True
    assert levels.counts(exercise(hints_used=2), SKILL)["independent"] is False


def test_a_knowledge_only_exercise_is_knowledge_and_nothing_more():
    assert levels.counts(exercise(**{"class": "knowledge_only"}), SKILL) == flags("knowledge", "dp-700/E22")


def test_only_an_independent_challenge_verified_here_is_advanced():
    assert levels.counts(exercise(), SKILL) == flags("practice", "dp-700/E22", advanced=True)
    assert levels.counts(exercise(mode="guided"), SKILL)["advanced"] is False
    simulated = levels.counts(exercise(**{"class": "simulated"}), SKILL)
    assert simulated == flags("practice", "dp-700/E22", simulated=True)
    assert levels.counts(exercise(**{"class": "fabric_verified"}), SKILL)["platform"] is True


def test_a_practice_exam_objective_counts_at_two_answers_both_right():
    row = {"id": "r", "type": "academy.assessment", "ref": {"id": "x"},
           "detail": {"certification": "dp-700", "per_objective": {"1.1": {"answered": 2, "correct": 2}}}}
    assert levels.counts(row, SKILL) == flags("knowledge", "dp-700:1.1")


def test_a_notebook_passing_validate_is_engineering_on_its_own_artifact():
    row = {"id": "row-1", "type": "validate.pass", "ref": {"id": "v"}, "artifact": {"name": "nb1"}}
    assert levels.counts(row, SKILL) == flags("engineering", "nb1")
    assert levels.counts(row, {"evidence": {"learn": ["LM-01"]}}) is None


def test_a_checked_fix_is_engineering_per_rule_and_check():
    row = {"id": "row-1", "type": "fix.checked", "ref": {"id": "cmp-1"}, "artifact": {"name": "NB004"},
           "measurement": {"kind": "measured"}}
    assert levels.counts(row, SKILL) == flags("engineering", "NB004@cmp-1", measured=True)


def test_a_prove_match_is_mastery_only_for_a_skill_that_takes_it():
    row = {"id": "row-1", "type": "prove.match", "ref": {"id": "p"}, "artifact": {"name": "orders"}}
    assert levels.counts(row, SKILL) == flags("mastery", "orders", advanced=True, platform=True)
    assert levels.counts(row, {"evidence": {"events": ["validate.pass"]}}) is None
    other = {"id": "row-2", "type": "validate.pass", "ref": {"id": "v"}, "artifact": {"name": "nb"}}
    assert levels.counts(other, {"evidence": {"events": ["prove.match"]}}) is None


# ---- levels at their edges


def counted(category, source, days, **kw):
    base = flags(category, source, independent=kw.pop("independent", True), **kw)
    return {"at": (DAY0 + dt.timedelta(days=days)).isoformat(), **base}


PLAIN = {"evidence": {"learn": ["x"]}}


def test_adept_takes_two_sources_exactly_a_week_apart():
    assert levels.ADEPT_DAYS == 7
    rows = [counted("practice", "A", 0), counted("practice", "B", 7)]
    assert levels.level(PLAIN, rows) == 4
    assert levels.level(PLAIN, [counted("practice", "A", 0), counted("practice", "B", 6)]) == 3


def test_expert_takes_engineering_work_not_practice_measured_on_two_sources():
    rows = [counted("practice", "A", 0, measured=True), counted("practice", "B", 7, measured=True)]
    assert levels.level(PLAIN, rows) == 4


def expert(*more):
    return [counted("practice", "A", 0), counted("engineering", "N1", 1), counted("engineering", "N2", 2, measured=True),
            *more]


def test_master_takes_thirty_days_from_the_first_real_practice_to_the_last():
    assert levels.MASTER_DAYS == 30
    assert levels.level(PLAIN, expert(counted("practice", "B", 20, advanced=True), counted("practice", "C", 31))) == 6
    assert levels.level(PLAIN, expert(counted("practice", "B", 30, advanced=True))) == 6
    assert levels.level(PLAIN, expert(counted("practice", "B", 29, advanced=True))) == 5


def test_the_rules_version_is_one():
    assert levels.RULES_VERSION == 1
    out = levels.evaluate(progress.empty(), {"version": 3, "skills": {}, "classes": {}})
    assert out["rules_version"] == 1


# ---- a class


def cls(*sizes):
    specs = [{"id": f"sp{i}", "nodes": [{"id": f"sp{i}.{j}", "skill": f"s{i}.{j}", "tier": 1, "requires": []}
                                        for j in range(n)]} for i, n in enumerate(sizes)]
    return {"id": "c", "version": 1, "specializations": specs}


def skills_at(c, level):
    return {n["skill"]: {"level": level} for sp in c["specializations"] for n in sp["nodes"]}


def test_ten_open_talents_at_adept_two_per_specialization_make_the_class_adept_and_mastered():
    assert (levels.MASTERED_LEVEL, levels.CLASS_MIN_TALENTS) == (4, 10)
    c = cls(5, 5)
    view = levels.class_view(c, skills_at(c, 4))
    assert view["mastery"] == 4 and view["mastery_name"] == "Adept"
    assert view["mastered"] == 10 and view["at_journeyman"] == 10
    assert {(t["state"], t["banked"], t["locked"]) for t in view["talents"]} == {("mastered", 0, False)}


def test_nine_talents_dont_make_a_class_level_and_journeyman_counts_from_three():
    c = cls(5, 4)
    view = levels.class_view(c, skills_at(c, 3))
    assert view["mastery"] == 0
    assert view["at_journeyman"] == 9 and view["mastered"] == 0
    assert {t["state"] for t in view["talents"]} == {"in_progress"}


def test_every_specialization_needs_two_even_an_empty_one():
    c = cls(8, 2, 0)
    assert levels.class_view(c, skills_at(c, 4))["mastery"] == 0


# ---- character XP categories


def test_xp_categories_by_row_kind():
    assert levels._category({"type": "academy.exercise", "detail": {"class": "local_verified"}}) == "practice"
    assert levels._category({"type": "module.complete", "ref": {"id": "LAB-02/x"}}) == "practice"
    assert levels._category({"type": "module.complete", "ref": {"id": "LM-01"}}) == "knowledge"


def test_a_talent_at_initiate_is_in_progress_and_one_not_started_is_available():
    c = cls(2)
    skills = {"s0.0": {"level": 1}, "s0.1": {"level": 0}}
    states = {t["id"]: t["state"] for t in levels.class_view(c, skills)["talents"]}
    assert states == {"sp0.0": "in_progress", "sp0.1": "available"}
