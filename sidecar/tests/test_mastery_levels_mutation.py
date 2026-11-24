"""mastery/levels.py's rules, read directly (mutation testing, batch 337).

test_mastery.py walks a profile through the levels; these pin each piece on its own: how every row type
counts for a skill (the whole record), each level's threshold at its edge, a cloud skill's ceiling, a
class's talents and mastery, and the XP category of each row type.
"""

from __future__ import annotations

import datetime as dt

from fabric_desktop.mastery import levels

SKILL = {"evidence": {"learn": ["M1", "LAB-10", "LAB-2"], "academy": ["pk/e1"], "objectives": ["pk:o1"],
                      "events": ["validate.pass", "prove.match"], "rules": ["R1"]}}
BARE = {"evidence": {"learn": ["M1"]}}


def record(category, source, *, independent=True, advanced=False, measured=False, simulated=False, platform=False):
    return {"category": category, "source": source, "independent": independent, "advanced": advanced,
            "measured": measured, "simulated": simulated, "platform": platform}


def row(t, ref="", **detail):
    out = {"id": "row-1", "type": t, "ref": {"id": ref}, "detail": detail}
    if "measurement" in detail:
        out["measurement"] = detail.pop("measurement")
    if "artifact" in detail:
        out["artifact"] = detail.pop("artifact")
    return out


def test_lessons_and_labs_count_as_the_module_they_belong_to():
    assert levels.counts(row("module.complete", "M1/x"), SKILL) == record("knowledge", "M1", independent=False)
    assert levels.counts(row("module.complete", "LAB-10/x"), SKILL) == record("practice", "LAB-10", advanced=True)
    assert levels.counts(row("lab.pass", "LAB-2/x"), SKILL) == record("practice", "LAB-2")
    assert levels.counts(row("lab.pass", "LAB-2/x", checks=["faster_than"]), SKILL) == \
        record("practice", "LAB-2", measured=True)
    assert levels.counts(row("lab.pass", "LAB-2/x", measurement={"s": 1}), SKILL) == \
        record("practice", "LAB-2", measured=True)


def test_academy_exercises_count_by_class_mode_and_how_much_help_was_used():
    knowledge = row("academy.exercise", "pk/e1", **{"class": "knowledge_only", "hints_used": 1})
    assert levels.counts(knowledge, SKILL) == {**record("knowledge", "pk/e1")}
    assert levels.counts(row("academy.exercise", "pk/e1", **{"class": "knowledge_only", "hints_used": 2}), SKILL)[
        "independent"] is False
    seen = row("academy.exercise", "pk/e1", **{"class": "knowledge_only", "explanation_seen": True})
    assert levels.counts(seen, SKILL)["independent"] is False
    challenge = row("academy.exercise", "pk/e1", **{"class": "local_verified", "mode": "challenge"})
    assert levels.counts(challenge, SKILL) == record("practice", "pk/e1", advanced=True)
    practice = row("academy.exercise", "pk/e1", **{"class": "local_verified", "mode": "practice"})
    assert levels.counts(practice, SKILL) == record("practice", "pk/e1")
    cloud = row("academy.exercise", "pk/e1", **{"class": "fabric_verified", "mode": "challenge"})
    assert levels.counts(cloud, SKILL) == record("practice", "pk/e1", advanced=True, platform=True)


def test_an_academy_exercise_counts_through_its_objectives_with_the_pack_from_its_id():
    by_pack = row("academy.exercise", "pk/e2", objectives=["o1"], **{"class": "local_verified"})
    assert levels.counts(by_pack, SKILL)["source"] == "pk/e2"
    named = row("academy.exercise", "zz/e2", certification="pk", objectives=["o1"], **{"class": "local_verified"})
    assert levels.counts(named, SKILL)["source"] == "zz/e2"
    assert levels.counts(row("academy.exercise", "zz/e2", objectives=["o1"]), SKILL) is None


def test_a_practice_exam_objective_counts_from_two_answers_at_seventy_percent():
    def exam(answered, correct):
        return row("academy.assessment", certification="pk", per_objective={"o1": {"answered": answered,
                                                                                  "correct": correct}})
    assert levels.counts(exam(2, 2), SKILL) == record("knowledge", "pk:o1")
    assert levels.counts(exam(10, 7), SKILL) == record("knowledge", "pk:o1")
    assert levels.counts(exam(10, 6), SKILL) is None


def test_own_work_and_prove_matches_count_only_where_the_skill_names_them():
    named = row("validate.pass", artifact={"name": "nb1"})
    assert levels.counts(named, SKILL) == record("engineering", "nb1")
    assert levels.counts(row("validate.pass"), SKILL) == record("engineering", "row-1")
    assert levels.counts(named, BARE) is None
    assert levels.counts(row("fix.checked", "nb1", artifact={"name": "R1"}), SKILL) == record("engineering", "R1@nb1")
    assert levels.counts(row("fix.checked", "nb1", artifact={"name": "R2"}), SKILL) is None
    proof = row("prove.match", artifact={"name": "p1"})
    assert levels.counts(proof, SKILL) == record("mastery", "p1", advanced=True, platform=True)
    assert levels.counts(proof, BARE) is None
    assert levels.counts(row("notebook.opened", artifact={"name": "p1"}), SKILL) is None


def test_a_cloud_skill_with_a_row_verified_on_the_platform_can_reach_master():
    assert levels.ceiling({**SKILL, "cloud": True}, [{"platform": True}])[0] == 6
    assert levels.ceiling({**SKILL, "cloud": True}, [{"platform": False}])[0] == 3


START = dt.date(2026, 1, 1)


def counted(category, source, day, **flags):
    return {"at": (START + dt.timedelta(days=day)).isoformat(), **record(category, source, **flags)}


def test_adept_needs_two_sources_exactly_a_week_apart_and_one_independent():
    rows = [counted("practice", "a", 0, independent=False), counted("practice", "b", 7)]
    assert levels.level(SKILL, rows) == 4
    assert levels.level(SKILL, [counted("practice", "a", 0), counted("practice", "b", 6)]) == 3


ADEPT = [counted("practice", "a", 0), counted("practice", "b", 7)]


def test_expert_needs_two_of_your_own_artifacts_not_two_practice_sources():
    own = [counted("engineering", "nb", 8, measured=True), counted("engineering", "nb", 9)]
    assert levels.level(SKILL, ADEPT + own) == 4
    assert levels.level(SKILL, ADEPT + own + [counted("engineering", "nb2", 9)]) == 5


EXPERT = [counted("engineering", "nb", 8, measured=True), counted("engineering", "nb2", 9)]


def test_master_needs_practice_thirty_days_after_the_first():
    advanced = [counted("mastery", "p", 9, advanced=True)]
    assert levels.level(SKILL, ADEPT + EXPERT + advanced + [counted("practice", "c", 30)]) == 6
    assert levels.level(SKILL, ADEPT + EXPERT + advanced + [counted("practice", "c", 29)]) == 5
    early = [counted("practice", "a", 0), counted("practice", "a", 1), counted("practice", "b", 7)]
    assert levels.level(SKILL, early + EXPERT + advanced + [counted("practice", "c", 30)]) == 6


def make_class(counts_by_spec: dict[str, int]):
    specs = [{"id": sp, "nodes": [{"id": f"{sp}.{i}", "skill": f"{sp}-{i}", "tier": 1, "requires": []}
                                  for i in range(10)]} for sp in counts_by_spec]
    return {"id": "c", "version": 1, "specializations": specs}


def skills_at(counts_by_spec: dict[str, int], lv: int):
    return {f"{sp}-{i}": {"level": lv if i < n else 0} for sp, n in counts_by_spec.items() for i in range(10)}


def test_class_mastery_needs_ten_talents_and_two_in_every_specialization():
    for spread, mastery in [({"a": 8, "b": 2}, 3), ({"a": 7, "b": 2}, 0), ({"a": 9, "b": 1}, 0),
                            ({"a": 8, "b": 2, "c": 0}, 0), ({"a": 10, "b": 2}, 3)]:
        view = levels.class_view(make_class(spread), skills_at(spread, 3))
        assert view["mastery"] == mastery, spread
        assert view["mastery_name"] == levels.LEVELS[mastery]


def test_each_talent_shows_its_level_state_and_what_is_banked():
    cls = {"id": "c", "version": 2, "specializations": [{"id": "s", "nodes": [
        {"id": "s.1", "skill": "k1", "tier": 1, "requires": []},
        {"id": "s.2", "skill": "k2", "tier": 2, "requires": ["s.1"]},
        {"id": "s.3", "skill": "k3", "tier": 2, "requires": []},
        {"id": "s.4", "skill": "k4", "tier": 2, "requires": []}]}]}
    view = levels.class_view(cls, {"k1": {"level": 4}, "k2": {"level": 3}, "k3": {"level": 1}, "k4": {"level": 0}})
    assert view["talents"] == [
        {"id": "s.1", "skill": "k1", "specialization": "s", "tier": 1, "requires": [], "level": 4, "banked": 0,
         "locked": False, "state": "mastered"},
        {"id": "s.2", "skill": "k2", "specialization": "s", "tier": 2, "requires": ["s.1"], "level": 3, "banked": 0,
         "locked": False, "state": "in_progress"},
        {"id": "s.3", "skill": "k3", "specialization": "s", "tier": 2, "requires": [], "level": 1, "banked": 0,
         "locked": False, "state": "in_progress"},
        {"id": "s.4", "skill": "k4", "specialization": "s", "tier": 2, "requires": [], "level": 0, "banked": 0,
         "locked": False, "state": "available"}]
    assert (view["id"], view["version"], view["mastered"], view["at_journeyman"]) == ("c", 2, 1, 2)
    locked = levels.class_view(cls, {"k1": {"level": 1}, "k2": {"level": 5}, "k3": {"level": 0}, "k4": {"level": 0}})
    second = locked["talents"][1]
    assert (second["level"], second["banked"], second["locked"], second["state"]) == (0, 5, True, "locked")
    assert locked["mastered"] == 0 and locked["at_journeyman"] == 0


def test_each_row_type_has_its_xp_category():
    assert levels._category({"type": "module.complete", "ref": {"id": "M1/x"}}) == "knowledge"
    assert levels._category({"type": "module.complete", "ref": {"id": "LAB-1/x"}}) == "practice"
    assert levels._category({"type": "academy.exercise", "detail": {"class": "local_verified"}}) == "practice"
    assert levels._category({"type": "academy.exercise", "detail": {"class": "knowledge_only"}}) == "knowledge"
    assert levels._category({"type": "validate.pass"}) == "engineering"
    assert levels._category({"type": "prove.match"}) == "mastery"


def test_the_rules_version_is_given_with_every_answer(monkeypatch):
    from fabric_desktop import progress
    monkeypatch.setattr(progress, "included", lambda profile: [])
    out = levels.evaluate({}, {"version": 9, "skills": {}, "classes": {}})
    assert (out["rules_version"], out["catalog_version"]) == (1, 9)
