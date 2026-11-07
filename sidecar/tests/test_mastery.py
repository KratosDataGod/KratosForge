"""Developer classes and mastery (CM-02, CM-03): the catalog's graph rules, and mastery levels that only
verified evidence earns, the same every time, never from replaying an event."""

from __future__ import annotations

import copy
import json
import random

import pytest

from fabric_desktop import progress
from fabric_desktop.mastery import catalog, levels

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
                         "requires": [f"{sp}.{j + 1:02d}" for j in req[i]]} for i in range(10)]}
             for k, sp in enumerate(("alpha", "beta", "gamma"))]
    cls = {"schema": catalog.CLASS_SCHEMA, "id": "test-class", "version": 1, "title": "Test Warden", "professional": "Tester",
           "fantasy": "F", "competency": "C", "emblem": "E", "platforms": [], "technologies": [],
           "specializations": specs, "achievements": [{"id": f"a{i}", "name": f"Award {i}", "criteria": "x"} for i in range(5)]}
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
    sid = "measured-optimization"           # LAB-02, check faster_than, fix.checked
    p = profile(lab_pass("LAB-02", "2026-09-01T10:00:00+00:00", ("faster_than",)),
                lab_pass("LM-18", "2026-09-10T10:00:00+00:00", ("faster_than",)),
                fix("SQ001", "2026-09-11T10:00:00+00:00"), fix("NB001", "2026-09-12T10:00:00+00:00"))
    assert skill_level(p, sid) == 5
    progress.add(p, lab_pass("LAB-12", "2026-10-20T10:00:00+00:00", ("faster_than",)))
    assert skill_level(p, sid) == 6         # an advanced lab, and practice again 49 days after the first
    assert levels.evaluate(p, CAT)["skills"][sid]["level"] < 7


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
