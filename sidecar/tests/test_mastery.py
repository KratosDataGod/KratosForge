"""Developer classes and mastery (CM-02, CM-03, CM-05): the catalog's graph rules, and mastery levels that only
verified evidence earns, the same every time, never from replaying an event."""

from __future__ import annotations

import copy
import json
import random

import pytest

from fabric_desktop import progress
from fabric_desktop.mastery import catalog, levels
from fabric_desktop.server import handle

from test_profile import call, plain  # noqa: F401 - the signed-in service fixture
from test_profile_learn import lab, passed

CAT = catalog.load()
WARDEN = CAT["classes"]["lakehouse-warden"]


# ---------------------------------------------------------------- the shipped catalog

def test_the_shipped_catalog_checks_out_against_real_content():
    skills, classes = catalog.read()
    assert catalog.problems(skills, classes, catalog.known_ids()) == []


def test_lakehouse_warden_has_three_specializations_of_ten_and_five_achievements():
    assert len(WARDEN["specializations"]) == 3
    assert all(len(sp["nodes"]) == 10 for sp in WARDEN["specializations"])
    assert len(WARDEN["achievements"]) == 5
    # The vertical slice: every talent can be verified with content that exists today.
    assert all(any(CAT["skills"][n["skill"]]["evidence"].values()) for sp in WARDEN["specializations"] for n in sp["nodes"])


def test_all_fourteen_classes_ship_with_full_trees():
    assert len(CAT["classes"]) == 14
    for c in CAT["classes"].values():
        assert len(c["specializations"]) >= catalog.MIN_SPECIALIZATIONS, c["id"]
        assert all(len(sp["nodes"]) >= catalog.MIN_NODES for sp in c["specializations"]), c["id"]
        assert len(c["achievements"]) >= catalog.MIN_ACHIEVEMENTS, c["id"]
    # Each class is a different profession, so the Professional profile never shows two the same.
    assert len({c["professional"] for c in CAT["classes"].values()}) == 14


def test_no_name_is_or_resembles_a_god():
    for c in CAT["classes"].values():
        names = [c["title"], c["professional"]] + [sp["title"] for sp in c["specializations"]] \
            + [a["name"] for a in c["achievements"]]
        assert not any(catalog.BANNED_NAMES.search(n) for n in names), c["id"]
    assert not any(catalog.BANNED_NAMES.search(s["title"]) for s in CAT["skills"].values())


# ---------------------------------------------------------------- graph rules, on a small catalog

def tiny():
    skills = {"schema": catalog.SKILLS_SCHEMA, "version": 1, "competencies": [{"id": "c", "title": "C"}],
              "skills": [{"id": f"s{i}", "title": f"Skill {i}", "competency": "c", "cloud": False,
                          "evidence": {"learn": ["LM-01"]}} for i in range(30)]}
    tiers, req = [1, 2, 2, 3, 3, 4, 4, 5, 5, 6], [[], [0], [0], [1], [2], [3], [3, 4], [5], [6], [7, 8]]
    specs = [{"id": sp, "title": sp.title(), "professional": "P", "competency": "c",
              "nodes": [{"id": f"{sp}.{i + 1:02d}", "skill": f"s{k * 10 + i}", "tier": tiers[i],
                         "requires": [f"{sp}.{j + 1:02d}" for j in req[i]], **({"capstone": True} if i == 9 else {})}
                        for i in range(10)]}
             for k, sp in enumerate(("alpha", "beta", "gamma"))]
    cls = {"schema": catalog.CLASS_SCHEMA, "id": "test-class", "version": 1, "title": "Test Warden", "professional": "Tester",
           "fantasy": "F", "competency": "C", "emblem": "E", "platforms": [], "technologies": [],
           "specializations": specs, "achievements": [{"id": f"a{i}", "name": f"Award {i}", "criteria": "x",
                                                  "requires": {"level": 3, "talents": ["alpha.01"]}} for i in range(5)]}
    return skills, cls


def node(cls, nid):
    return next(n for sp in cls["specializations"] for n in sp["nodes"] if n["id"] == nid)


def test_a_good_catalog_has_no_problems():
    skills, cls = tiny()
    assert catalog.problems(skills, [cls]) == []


def test_a_cycle_is_refused():
    skills, cls = tiny()
    node(cls, "alpha.02")["requires"] = ["alpha.01", "alpha.04"]
    node(cls, "alpha.02")["tier"] = 9   # keep the tier rule quiet so the cycle is what's reported
    node(cls, "alpha.04")["tier"] = 10
    node(cls, "alpha.06")["tier"] = 11
    node(cls, "alpha.07")["tier"] = 11
    node(cls, "alpha.08")["tier"] = 12
    node(cls, "alpha.09")["tier"] = 12
    node(cls, "alpha.10")["tier"] = 13
    node(cls, "alpha.04")["requires"] = ["alpha.02"]
    found = catalog.problems(skills, [cls])
    assert any("cycle" in p for p in found), found


def test_unknown_prerequisites_unreachable_nodes_and_tiers_that_dont_rise_are_refused():
    skills, cls = tiny()
    node(cls, "beta.03")["requires"] = ["beta.99"]
    found = catalog.problems(skills, [cls])
    assert any("requires unknown node 'beta.99'" in p for p in found)
    assert any("beta.03 can't be reached" in p for p in found)       # its only prerequisite doesn't exist
    skills, cls = tiny()
    node(cls, "gamma.04")["tier"] = 2
    assert any("must sit on a lower tier" in p for p in catalog.problems(skills, [cls]))


def test_sizes_names_and_skills_are_checked():
    skills, cls = tiny()
    cls["specializations"][0]["nodes"].pop()
    cls["achievements"].pop()
    cls["specializations"][1]["title"] = "Godforge"
    node(cls, "gamma.01")["skill"] = "s0"           # alpha.01's skill again, in the same class
    node(cls, "gamma.02")["skill"] = "nope"
    found = " | ".join(catalog.problems(skills, [cls]))
    for want in ("needs at least 10", "needs at least 5", "resembles a god", "appears twice in this class", "unknown skill 'nope'"):
        assert want in found


def test_hooks_must_name_real_content():
    skills, cls = tiny()
    skills["skills"][0]["evidence"] = {"learn": ["LM-99"], "events": ["typing.fast"]}
    found = catalog.problems(skills, [cls], {"learn": {"LM-01"}, "academy": set(), "objectives": set(), "rules": set()})
    assert any("LM-99 names nothing" in p for p in found) and any("'typing.fast' isn't valid" in p for p in found)


def test_a_broken_catalog_file_is_refused_whole(tmp_path):
    (tmp_path / "classes").mkdir()
    skills, cls = tiny()
    (tmp_path / "skills.json").write_text(json.dumps(skills))
    (tmp_path / "classes" / "t.json").write_text("{ not json")
    with pytest.raises(catalog.CatalogError, match="can't be read"):
        catalog.load(tmp_path, check_hooks=False)
    node(cls, "alpha.02")["requires"] = ["alpha.10"]
    (tmp_path / "classes" / "t.json").write_text(json.dumps(cls))
    with pytest.raises(catalog.CatalogError) as e:
        catalog.load(tmp_path, check_hooks=False)
    assert e.value.problems


# ---------------------------------------------------------------- evidence rows

def lesson(mid, at):
    return {"id": progress.evidence_id("module.complete", mid, "v1"), "type": "module.complete", "at": at,
            "artifact": {"kind": "module", "name": mid}, "ref": {"source": "learn", "id": mid, "revision": "v1"},
            "status": "verified", "detail": {}, "measurement": None}


def exercise(eid, at, cls="local_verified", hints=0, seen=False, mode="challenge", objectives=()):
    return {"id": progress.evidence_id("academy.exercise", f"dp-700/{eid}", at[:10]), "type": "academy.exercise", "at": at,
            "artifact": {"kind": "exercise", "name": f"dp-700/{eid}"}, "ref": {"source": "academy", "id": f"dp-700/{eid}"},
            "status": "verified", "measurement": None,
            "detail": {"certification": "dp-700", "objectives": list(objectives), "class": cls, "mode": mode,
                       "hints_used": hints, "explanation_seen": seen}}


def notebook(name, at, measured=False):
    return {"id": progress.evidence_id("validate.pass", name, at), "type": "validate.pass", "at": at,
            "artifact": {"kind": "notebook", "name": name}, "ref": {"source": "validate", "id": "v", "revision": at},
            "status": "verified", "detail": {}, "measurement": {"kind": "measured"} if measured else None}


def fix(rule, at, measured=True):
    return {"id": progress.evidence_id("fix.checked", rule, at), "type": "fix.checked", "at": at,
            "artifact": {"kind": "rule", "name": rule}, "ref": {"source": "compare", "id": at, "revision": "b"},
            "status": "verified", "detail": {}, "measurement": {"kind": "measured"} if measured else None}


def lab_pass(mid, at, checks=("result_matches",)):
    return {"id": progress.evidence_id("lab.pass", f"{mid}/x", "v1"), "type": "lab.pass", "at": at,
            "artifact": {"kind": "lab", "name": mid}, "ref": {"source": "learn", "id": f"{mid}/x", "revision": "v1"},
            "status": "verified", "detail": {"checks": list(checks)}, "measurement": None}


def profile(*rows):
    p = progress.empty()
    for r in rows:
        progress.add(p, r)
    return p


def skill_level(p, sid):
    return levels.evaluate(p, CAT)["skills"][sid]["level"]


MERGE = "merge-upserts"                         # LM-11, dp-700/E22
WATERMARK = "incremental-loads-with-a-watermark"  # LM-12, E21, E57, objective 2.1.1


def test_no_evidence_is_not_started_and_nothing_is_awarded_for_time():
    assert skill_level(progress.empty(), MERGE) == 0


def test_each_level_needs_its_evidence():
    assert skill_level(profile(lesson("LM-11", "2026-10-01T10:00:00+00:00")), MERGE) == 2       # knowledge
    p = profile(lesson("LM-11", "2026-10-01T10:00:00+00:00"), exercise("E22", "2026-10-02T10:00:00+00:00", hints=3))
    assert skill_level(p, MERGE) == 3                                                           # practical on this PC
    progress.add(p, exercise("E22", "2026-10-05T10:00:00+00:00", hints=3))
    assert skill_level(p, MERGE) == 3       # the same exercise again: one source, and never independent
    progress.add(p, lab_pass("LM-11", "2026-10-12T10:00:00+00:00"))
    assert skill_level(p, MERGE) == 4       # two sources, 10 days apart, the Learn one independent


def test_simulated_practice_stops_at_apprentice_unless_the_skill_is_cloud_only():
    p = profile(exercise("E22", "2026-10-02T10:00:00+00:00", cls="simulated"))
    assert skill_level(p, MERGE) == 2
    lw = levels.evaluate(p, CAT)["skills"][MERGE]
    assert lw["ceiling"] == 6 and "reviewer" in lw["ceiling_reason"]


def test_expert_needs_your_own_work_and_a_measurement():
    smith = "best-practice-fixes-checked-with-compare"   # events: fix.checked
    rows = [fix("NB020", "2026-10-01T10:00:00+00:00"), fix("NB018", "2026-10-03T10:00:00+00:00")]
    assert skill_level(profile(*rows), smith) == 1     # engineering alone isn't practice
    vacuum = "optimize-and-vacuum-done-safely"          # LM-25, rule NB020
    p = profile(lesson("LM-25", "2026-10-01T10:00:00+00:00"), lab_pass("LM-25", "2026-10-01T11:00:00+00:00"),
                exercise("E56", "2026-10-09T10:00:00+00:00", objectives=["3.3.1"]),
                fix("NB020", "2026-10-10T10:00:00+00:00"))
    # E56 isn't a hook of this skill and only one artifact of own work: Journeyman.
    assert skill_level(p, vacuum) == 3


def test_a_skill_reaches_master_only_with_advanced_work_over_thirty_days():
    sid = "notebooks-that-pass-validate"    # events: validate.pass (Steadfast Loader)
    p = profile(notebook("Orders", "2026-09-01T10:00:00+00:00"), notebook("Sales", "2026-09-02T10:00:00+00:00", measured=True))
    assert skill_level(p, sid) == 1         # own work without a practical: Initiate only
    cat = copy.deepcopy(CAT)
    cat["skills"]["tuning"] = {"id": "tuning", "title": "Tuning", "competency": "delta-storage", "cloud": False,
                               "evidence": {"learn": ["LM-18", "LAB-02", "LAB-12"], "checks": ["faster_than"],
                                            "events": ["fix.checked"]}}
    p = profile(lab_pass("LAB-02", "2026-09-01T10:00:00+00:00", ("faster_than",)),
                lab_pass("LM-18", "2026-09-10T10:00:00+00:00", ("faster_than",)),
                fix("SQ001", "2026-09-11T10:00:00+00:00"), fix("NB001", "2026-09-12T10:00:00+00:00"))
    assert levels.evaluate(p, cat)["skills"]["tuning"]["level"] == 5
    progress.add(p, lab_pass("LAB-12", "2026-10-20T10:00:00+00:00", ("faster_than",)))
    assert levels.evaluate(p, cat)["skills"]["tuning"]["level"] == 6   # an advanced lab, practice again 49 days on


def test_a_check_kind_never_credits_another_lessons_exercise():
    # Review repro: table_shape exercises in LM-09 and LM-14, 9 days apart, must not count for the Gold star
    # schema talent, whose own lessons are LAB-03 and E35.
    from fabric_desktop.learn import content
    mods = {m["id"]: m for m in content.modules(include_drafts=False)}
    p = progress.empty()
    for mid, day in (("LM-09", "2026-10-01"), ("LM-14", "2026-10-10")):
        m = mods[mid]
        for ex in m["exercises"]:
            row = progress.from_lab({"module": mid, "exercise": ex["id"], "version": m.get("version", 1), "passed": True,
                                     "results": [{"kind": "table_shape", "passed": True, "detail": "x"}]}, m)
            row["at"] = f"{day}T10:00:00+00:00"
            progress.add(p, row)
    for sid in ("gold-star-schema-for-reporting", "data-quality-checks-in-code", "measured-optimization"):
        assert skill_level(p, sid) == 0, sid


def test_finishing_a_lab_and_passing_its_exercise_are_one_source():
    p = profile(lab_pass("LAB-06", "2026-09-01T10:00:00+00:00"),
                {**lesson("LAB-06", "2026-09-20T10:00:00+00:00"), "artifact": {"kind": "lab", "name": "LAB-06"}})
    assert skill_level(p, "bronze-to-silver-that-survives-a-rerun") == 3     # not Adept: one source


def test_adept_needs_two_sources_whose_first_passes_are_a_week_apart():
    rows = [exercise("E22", "2026-10-01T10:00:00+00:00"), lab_pass("LM-11", "2026-10-02T10:00:00+00:00"),
            exercise("E22", "2026-10-20T10:00:00+00:00")]
    assert skill_level(profile(*rows), MERGE) == 3   # E22 again later doesn't spread the two sources apart


def test_knowledge_only_academy_rows_are_knowledge_xp():
    row = exercise("E27", "2026-10-01T10:00:00+00:00", cls="knowledge_only")
    assert levels._category(row) == "knowledge"


def test_practice_exam_objectives_count_as_knowledge_only_when_answered_well():
    def exam(correct, answered, at):
        return {"id": progress.evidence_id("academy.assessment", "s", f"{at}{correct}"), "type": "academy.assessment", "at": at,
                "artifact": {"kind": "exam", "name": "dp-700"}, "ref": {"source": "academy", "id": "s"}, "status": "verified",
                "detail": {"certification": "dp-700", "per_objective": {"2.1.1": {"correct": correct, "answered": answered}}},
                "measurement": None}
    assert skill_level(profile(exam(1, 3, "2026-10-01T10:00:00+00:00")), WATERMARK) == 0
    assert skill_level(profile(exam(1, 1, "2026-10-01T10:00:00+00:00")), WATERMARK) == 0
    assert skill_level(profile(exam(3, 4, "2026-10-01T10:00:00+00:00")), WATERMARK) == 2


def test_excluded_and_unverified_rows_count_for_nothing():
    row = exercise("E22", "2026-10-02T10:00:00+00:00")
    p = profile(row)
    assert skill_level(p, MERGE) == 3
    p["excluded"].append(row["id"])
    assert skill_level(p, MERGE) == 0
    p = profile({**row, "status": "revoked"})
    assert skill_level(p, MERGE) == 0


# ---------------------------------------------------------------- determinism and replay

def busy():
    return [lesson("LM-06", "2026-09-01T10:00:00+00:00"), lesson("LM-07", "2026-09-02T10:00:00+00:00"),
            lesson("LM-02", "2026-09-03T10:00:00+00:00"), lab_pass("LM-07", "2026-09-04T10:00:00+00:00"),
            lab_pass("LM-02", "2026-09-05T10:00:00+00:00"), exercise("E27", "2026-09-06T10:00:00+00:00", cls="knowledge_only"),
            exercise("E21", "2026-09-08T10:00:00+00:00"), lab_pass("LM-12", "2026-09-20T10:00:00+00:00"),
            exercise("E22", "2026-09-09T10:00:00+00:00"), lab_pass("LM-11", "2026-09-21T10:00:00+00:00"),
            lab_pass("LM-06", "2026-09-22T10:00:00+00:00"), notebook("Orders", "2026-09-23T10:00:00+00:00")]


def test_the_same_rows_in_any_order_give_the_same_answer():
    want = levels.evaluate(profile(*busy()), CAT)
    for seed in range(5):
        rows = busy()
        random.Random(seed).shuffle(rows)
        assert levels.evaluate(profile(*rows), CAT) == want


def test_replaying_an_event_adds_nothing():
    p = profile(*busy())
    before = copy.deepcopy(levels.evaluate(p, CAT))
    for r in busy():
        assert progress.add(p, copy.deepcopy(r)) is False
    assert levels.evaluate(p, CAT) == before
    assert levels.xp_breakdown(p) == levels.xp_breakdown(profile(*busy()))


def test_xp_breakdown_sorts_the_characters_xp_without_changing_it():
    p = profile(*busy())
    parts = levels.xp_breakdown(p)
    assert set(parts) == set(levels.CATEGORIES)
    assert sum(parts.values()) == progress.progression(p)["xp"]
    assert parts["community"] == 0


# ---------------------------------------------------------------- the talent tree

def talents(p):
    return {t["id"]: t for t in levels.evaluate(p, CAT)["classes"]["lakehouse-warden"]["talents"]}


def test_a_talent_opens_when_every_prerequisite_reaches_apprentice():
    t = talents(progress.empty())
    assert t["medallion.01"]["state"] == "available"
    assert t["medallion.02"]["state"] == "locked"
    t = talents(profile(lesson("LM-06", "2026-09-01T10:00:00+00:00")))
    assert t["medallion.01"]["level"] == 2 and t["medallion.02"]["state"] == "available"
    assert t["medallion.07"]["state"] == "locked"       # needs medallion.04 and .05


def test_evidence_for_a_locked_talent_is_banked_and_counts_once_it_opens():
    row = exercise("E22", "2026-10-02T10:00:00+00:00")     # MERGE upserts = delta.02, behind delta.01
    t = talents(profile(row))["delta.02"]
    assert t["locked"] and t["level"] == 0 and t["banked"] == 3
    t = talents(profile(row, lesson("LM-07", "2026-10-01T10:00:00+00:00")))["delta.02"]
    assert not t["locked"] and t["level"] == 3 and t["state"] == "in_progress"


def test_class_mastery_needs_ten_talents_and_two_in_each_specialization():
    view = levels.evaluate(profile(*busy()), CAT)["classes"]["lakehouse-warden"]
    assert view["mastery"] == 0
    assert view["at_journeyman"] < levels.CLASS_MIN_TALENTS


def test_a_cloud_only_skill_says_why_it_stops_at_journeyman():
    skill = {"evidence": {"academy": ["dp-700/E18"]}, "cloud": True}
    assert levels.ceiling(skill, [])[0] == 3
    assert levels.ceiling({"evidence": {}, "cloud": False})[0] == 0


# ---------------------------------------------------------------- the Skill trees tab's engine method

def test_profile_mastery_shows_every_class_and_one_tree_from_the_profile(plain):  # noqa: F811 - the fixture
    empty = call(plain, "profile_mastery")
    assert len(empty["classes"]) == 14 and empty["class"]["id"] == "lakehouse-warden"
    delta = next(s for s in empty["class"]["specializations"] if s["id"] == "delta")
    assert [n["state"] for n in delta["nodes"]][:3] == ["available", "locked", "locked"]
    assert all(n["level"] == 0 and n["next"] for n in delta["nodes"])
    # One pass of the Delta log lesson's exercise lifts its talent; nothing is stored by asking.
    p = progress.empty()
    progress.add(p, progress.from_lab(passed(lab("LM-07", "ex")), lab("LM-07", "ex", kind="module")))
    progress.save(plain._profile_path(), p)
    before = plain._profile_path().read_bytes()
    seen = call(plain, "profile_mastery", class_id="lakehouse-warden")
    root = next(s for s in seen["class"]["specializations"] if s["id"] == "delta")["nodes"][0]
    assert root["level"] >= 1 and root["evidence"] and "LM-07" in root["learn"]
    assert plain._profile_path().read_bytes() == before
    out = handle(plain, json.dumps({"id": 1, "method": "profile_mastery", "params": {"class_id": "no-such-class"}}))
    assert "isn't a developer class" in str(out["error"])


# ---------------------------------------------------------------- class achievements, from their rule

def award_cat(*rules):
    """The tiny catalog with one skill per talent (s<i> taught by LM-<i> and LAB-<i>) and these achievements."""
    skills, cls = tiny()
    for i, s in enumerate(skills["skills"]):
        s["evidence"] = {"learn": [f"LM-{i:02d}", f"LAB-{i:02d}"]}
    cls["achievements"] = [{"id": f"a{i}", "name": f"Award {i}", "criteria": "x", "requires": r} for i, r in enumerate(rules)]
    assert [x for x in catalog.problems(skills, [cls]) if "requires" in x] == []
    return {"version": 1, "competencies": {"c": {"id": "c"}}, "skills": {s["id"]: s for s in skills["skills"]},
            "classes": {cls["id"]: cls}}


def awards(p, cat):
    return {a["id"]: a for a in levels.evaluate(p, cat)["classes"]["test-class"]["achievements"]}


DAY1, DAY9 = "2026-09-01T10:00:00+00:00", "2026-09-09T10:00:00+00:00"


def test_an_achievement_is_earned_from_its_rule_not_its_text():
    cat = award_cat({"level": 3, "talents": ["alpha.01"]},
                    {"level": 4, "talents": ["alpha.01"], "measured": True},
                    {"level": 2, "specialization": "alpha", "max_tier": 2},
                    {"level": 3, "talents": ["alpha.02", "alpha.03"], "any": True},
                    {"level": 2, "any_specialization": True})
    assert not any(a["earned"] for a in awards(progress.empty(), cat).values())
    one = profile(lab_pass("LM-00", DAY1))
    a = awards(one, cat)
    assert a["a0"]["earned"] and a["a0"]["evidence"] == [r["id"] for r in one["evidence"]] and not a["a2"]["earned"]
    assert a["a2"]["progress"] == pytest.approx(0.33)
    # Adept from two sources a week apart is still not the measured achievement until a run is measured.
    adept = profile(lab_pass("LM-00", DAY1), lab_pass("LAB-00", DAY9))
    unmeasured = awards(adept, cat)["a1"]
    assert not unmeasured["earned"] and unmeasured["progress"] == 0.99 and unmeasured["evidence"] == []
    assert set(a["a0"]["evidence"]) <= {r["id"] for r in adept["evidence"]}
    assert awards(profile(lab_pass("LM-00", DAY1), lab_pass("LAB-00", DAY9, checks=("faster_than",))), cat)["a1"]["earned"]
    # Any one of the two talents is enough; every tier-1-and-2 talent of alpha is needed for a2.
    a = awards(profile(lab_pass("LM-00", DAY1), lab_pass("LM-02", DAY1)), cat)
    assert a["a3"]["earned"] and not a["a2"]["earned"]
    a = awards(profile(*(lab_pass(f"LM-{i:02d}", DAY1) for i in range(3))), cat)
    assert a["a2"]["earned"] and not a["a4"]["earned"]
    assert awards(profile(*(lab_pass(f"LM-{i:02d}", DAY1) for i in range(10))), cat)["a4"]["earned"]


def test_a_locked_talents_banked_level_earns_nothing_until_it_opens():
    cat = award_cat({"level": 3, "talents": ["alpha.02"]})
    early = lab_pass("LM-01", DAY1)                       # alpha.02 sits behind alpha.01
    assert not awards(profile(early), cat)["a0"]["earned"]
    assert awards(profile(early, lab_pass("LM-00", DAY9)), cat)["a0"]["earned"]


def test_an_achievement_is_never_granted_twice():
    cat = award_cat({"level": 3, "talents": ["alpha.01"]}, {"level": 2, "any_specialization": True})
    rows = [lab_pass(f"LM-{i:02d}", DAY1) for i in range(10)] + [lab_pass("LAB-00", DAY9)]
    p = profile(*rows)
    first = levels.evaluate(p, cat)["classes"]["test-class"]
    for r in rows:                                        # replaying every event is refused
        assert progress.add(p, copy.deepcopy(r)) is False
    again = levels.evaluate(p, cat)["classes"]["test-class"]
    assert again == first and again["earned"] == 2
    assert [a["id"] for a in again["achievements"]] == ["a0", "a1"]
    # Every specialization meets a1 here, and it is still one achievement.
    assert awards(profile(*(lab_pass(f"LM-{i:02d}", DAY1) for i in range(30))), cat)["a1"]["earned"] is True


def test_achievements_survive_a_restart(tmp_path):
    cat = award_cat({"level": 3, "talents": ["alpha.01"]}, {"level": 4, "talents": ["alpha.01"]})
    p = profile(lab_pass("LM-00", DAY1), lab_pass("LAB-00", DAY9))
    before = awards(p, cat)
    path = tmp_path / "profile.json"
    progress.save(path, p)
    after = awards(progress.load(path), cat)              # what the app sees the next time it starts
    assert after == before and all(a["earned"] for a in after.values())
    progress.save(path, progress.load(path))
    assert awards(progress.load(path), cat) == before


def test_achievement_rules_are_checked():
    skills, cls = tiny()
    cls["achievements"][0]["requires"] = {"level": 9, "talents": ["alpha.01", "alpha.01"]}
    cls["achievements"][1]["requires"] = {"level": 3, "talents": ["nope.01"], "specialization": "alpha"}
    cls["achievements"][2]["requires"] = {"level": 3, "specialization": "delta", "max_tier": 0, "any": True}
    cls["achievements"][3]["requires"] = {"level": 3, "any_specialization": True, "bonus": 1}
    del cls["achievements"][4]["requires"]
    found = "\n".join(catalog.problems(skills, [cls]))
    for want in ("requires.level must be 2 to 6", "each once", "exactly one of", "isn't in this class",
                 "max_tier must be", "any must be true", "requires.bonus isn't a known rule", "a4 has no requires"):
        assert want in found


def test_every_shipped_achievement_names_the_talents_its_rule_checks():
    for cls in CAT["classes"].values():
        titles = {n["id"]: CAT["skills"][n["skill"]]["title"] for n in catalog.nodes(cls)}
        for a in cls["achievements"]:
            r = a["requires"]
            assert a["criteria"].startswith(f"Reach {levels.LEVELS[r['level']]} in"), a["id"]
            for t in r.get("talents", []):
                assert titles[t] in a["criteria"], (cls["id"], a["id"], t)
            if r.get("measured"):
                assert "measured" in a["criteria"]


def test_profile_mastery_reports_earned_achievements_and_stores_nothing(plain):  # noqa: F811 - the fixture
    p = progress.empty()
    progress.add(p, progress.from_lab(passed(lab("LM-07", "ex")), lab("LM-07", "ex", kind="module")))
    progress.save(plain._profile_path(), p)
    before = plain._profile_path().read_bytes()
    one = call(plain, "profile_mastery", class_id="lakehouse-warden")
    two = call(plain, "profile_mastery", class_id="lakehouse-warden")
    assert one == two and plain._profile_path().read_bytes() == before
    got = {a["id"]: a for a in one["class"]["achievements"]}
    assert set(got) == {a["id"] for a in CAT["classes"]["lakehouse-warden"]["achievements"]}
    assert all({"earned", "progress", "level_name", "criteria"} <= set(a) for a in got.values())
    assert one["class"]["earned"] == sum(a["earned"] for a in got.values())


def test_an_achievement_no_one_can_earn_yet_says_why():
    cat = award_cat({"level": 3, "talents": ["alpha.01"]}, {"level": 3, "talents": ["alpha.01", "beta.01"]},
                    {"level": 3, "talents": ["alpha.01", "beta.01"], "any": True}, {"level": 2, "any_specialization": True})
    cat["skills"]["s10"]["evidence"] = {}                 # beta.01 is planned content: no lesson or exercise yet
    a = awards(progress.empty(), cat)
    assert a["a0"]["available"] and a["a0"]["unavailable_reason"] == ""
    assert not a["a1"]["available"] and "No lesson, exercise or check" in a["a1"]["unavailable_reason"]
    assert a["a2"]["available"] and a["a3"]["available"]  # alpha.01 alone, or alpha's tree alone, still can
    assert awards(profile(lab_pass("LM-00", DAY1)), cat)["a2"]["earned"]


def test_every_lakehouse_warden_achievement_can_be_earned_on_this_pc():
    view = levels.evaluate(progress.empty(), CAT)["classes"]["lakehouse-warden"]
    assert all(a["available"] and not a["earned"] and a["progress"] == 0 for a in view["achievements"])
    for cls in levels.evaluate(progress.empty(), CAT)["classes"].values():
        for a in cls["achievements"]:
            assert a["available"] or a["unavailable_reason"], (cls["id"], a["id"])


def test_each_specialization_has_one_capstone_alone_on_its_top_tier():
    for change in (lambda c: c["specializations"][0]["nodes"][9].pop("capstone"),
                   lambda c: c["specializations"][1]["nodes"][8].update(capstone=True),
                   lambda c: c["specializations"][2]["nodes"][8].update(tier=6, requires=["gamma.07"])):
        skills, cls = tiny()
        change(cls)
        assert any("one capstone talent" in x for x in catalog.problems(skills, [cls]))
def test_each_level_says_what_the_next_one_needs():
    assert len(levels.NEXT_STEP) == len(levels.LEVELS)
    assert levels.NEXT_STEP[0].startswith("Finish one of its lessons")          # Not started -> Initiate
    assert levels.NEXT_STEP[2] == "Pass a hands-on exercise on this PC."         # Apprentice -> Journeyman
    assert "7 days apart" in levels.NEXT_STEP[3]                                 # Journeyman -> Adept
    assert "Validate" in levels.NEXT_STEP[4]                                     # Adept -> Expert
    assert "30 days" in levels.NEXT_STEP[5]                                      # Expert -> Master
    assert "reviewer" in levels.NEXT_STEP[6] and levels.NEXT_STEP[7] == ""


def test_profile_mastery_gives_each_talent_the_step_for_its_own_level(plain):  # noqa: F811 - the fixture
    p = progress.empty()
    progress.add(p, progress.from_lab(passed(lab("LM-07", "ex")), lab("LM-07", "ex", kind="module")))
    progress.save(plain._profile_path(), p)
    out = call(plain, "profile_mastery", class_id="lakehouse-warden")
    for sp in out["class"]["specializations"]:
        for n in sp["nodes"]:
            want = levels.NEXT_STEP[n["level"]] if n["level"] < n["ceiling"] else n["ceiling_reason"]
            assert n["next"] == want, n["id"]
    assert any(n["level"] >= 1 for sp in out["class"]["specializations"] for n in sp["nodes"])


def test_an_achievement_counts_the_talents_it_has_met_out_of_those_it_needs():
    cat = award_cat({"level": 3, "talents": ["alpha.01", "beta.01", "gamma.01"]},
                    {"level": 3, "talents": ["alpha.01", "beta.01"], "any": True},
                    {"level": 2, "any_specialization": True})
    a = awards(profile(lab_pass("LM-00", DAY1), lab_pass("LM-10", DAY1)), cat)
    assert (a["a0"]["met"], a["a0"]["of"], a["a0"]["earned"]) == (2, 3, False)
    assert (a["a1"]["met"], a["a1"]["of"], a["a1"]["earned"]) == (1, 1, True)
    assert (a["a2"]["met"], a["a2"]["of"]) == (1, 10)        # the closest specialization: alpha or beta, 1 of 10
    assert (awards(progress.empty(), cat)["a0"]["met"]) == 0


def test_an_achievement_is_dated_the_first_day_its_rule_was_met():
    cat = award_cat({"level": 3, "talents": ["alpha.01"]}, {"level": 4, "talents": ["alpha.01"]},
                    {"level": 3, "talents": ["alpha.02"]})
    cls = cat["classes"]["test-class"]
    rows = [lab_pass("LM-00", DAY1), lab_pass("LAB-00", DAY9), lab_pass("LM-01", "2026-09-20T10:00:00+00:00")]
    on = levels.earned_on(profile(*rows), cls, cat)
    assert on == {"a0": "2026-09-01", "a1": "2026-09-09", "a2": "2026-09-20"}  # Adept needs a second source a week on
    assert levels.earned_on(profile(*rows[:2]), cls, cat)["a2"] is None
    shuffled = rows[::-1]
    assert levels.earned_on(profile(*shuffled), cls, cat) == on
    # A row without a date can't be placed, so the day is left out, never guessed.
    undated = {**lab_pass("LM-00", DAY1), "at": None}
    assert levels.earned_on(profile(undated), cls, cat)["a0"] is None
    assert awards(profile(undated), cat)["a0"]["earned"]


def test_profile_mastery_dates_earned_achievements_only(plain):  # noqa: F811 - the fixture
    out = call(plain, "profile_mastery", class_id="lakehouse-warden")
    assert all(a["earned_on"] is None and "met" in a and "of" in a for a in out["class"]["achievements"])
