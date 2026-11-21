"""Academy mapping (FEA-601): every mapped lesson and exercise exists with its own title, every control
is mapped or listed as unmapped, and progress comes only from validated fixes and passed exercises."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import exercises as academy_exercises
from fabric_desktop.excellence import academy_map as am
from fabric_desktop.excellence import catalog
from fabric_desktop.excellence import findings as fx
from fabric_desktop.learn import content as learn_content


def _entries():
    return [(cid, e) for cid, es in sorted(am.MAP.items()) for e in es]


def test_every_mapped_lesson_exists_with_its_own_title():
    lessons = {m["id"]: m for m in learn_content.modules()}  # retired ones are left out
    for cid, e in _entries():
        if e["kind"] == "lesson":
            assert e["id"] in lessons, f"{cid}: unknown or retired lesson {e['id']}"
            assert e["title"] == lessons[e["id"]]["title"], f"{cid}: {e['id']} is now titled {lessons[e['id']]['title']!r}"


def test_every_mapped_exercise_exists_with_its_own_title():
    for cid, e in _entries():
        if e["kind"] == "exercise":
            known = {x["id"]: x for x in academy_exercises.exercises(e["pack"])}  # retired ones are left out
            assert e["id"] in known, f"{cid}: unknown or retired exercise {e['pack']}/{e['id']}"
            assert e["title"] == known[e["id"]]["title"], f"{cid}: {e['id']} is now titled {known[e['id']]['title']!r}"


def test_an_entry_is_a_lesson_or_an_exercise_with_exactly_its_fields():
    for cid, e in _entries():
        assert e["kind"] in ("lesson", "exercise"), cid
        want = {"kind", "id", "title"} | ({"pack"} if e["kind"] == "exercise" else set())
        assert set(e) == want, cid


def test_no_control_lists_the_same_lesson_twice_or_has_an_empty_list():
    for cid, es in am.MAP.items():
        assert es, f"{cid} has an empty list; list it in UNMAPPED instead"
        keys = [(e["kind"], e.get("pack"), e["id"]) for e in es]
        assert len(keys) == len(set(keys)), cid


def test_every_control_is_mapped_or_unmapped_and_none_is_both():
    ids = {c.id for c in catalog.registry().controls()}
    assert not set(am.MAP) & set(am.UNMAPPED)
    assert set(am.MAP) - ids == set(), "mapped controls that aren't in the registry"
    assert set(am.UNMAPPED) - ids == set(), "unmapped controls that aren't in the registry"
    assert ids - set(am.MAP) - set(am.UNMAPPED) == set(), "new controls: map them or list them in UNMAPPED"
    assert all(r.strip() for r in am.UNMAPPED.values())


def test_coverage_counts_mapped_and_unmapped():
    assert am.coverage() == {"mapped": len(am.MAP), "unmapped": len(am.UNMAPPED)}
    assert sum(am.coverage().values()) == len(catalog.registry())


def test_learn_returns_copies_in_order_and_empty_for_unmapped_or_unknown():
    got = am.learn("FEA-SEC-001")
    assert [e["id"] for e in got] == ["LM-32", "E10"]
    got[0]["title"] = "changed"
    assert am.MAP["FEA-SEC-001"][0]["title"] == "Least privilege in workspaces"
    assert am.learn("FEA-GOV-001") == []
    assert am.learn("FEA-XYZ-999") == []


def test_learn_is_deterministic():
    assert all(am.learn(c) == am.learn(c) for c in am.MAP)
    assert [am.learn(c) for c in sorted(am.MAP)] == [am.learn(c) for c in sorted(am.MAP)]


def test_a_finding_carries_where_to_learn_its_fix():
    reg = catalog.registry()
    out = {"outcome": "fail", "subject": {"id": "ws-1"}, "text": "5 admins", "evidence_ids": ["e1"], "basis": "measured"}
    assert fx.make(reg.get("FEA-SEC-001"), out, "high")["learn"] == am.learn("FEA-SEC-001")
    assert fx.make(reg.get("FEA-GOV-001"), out, "low")["learn"] == []
    # the finding's own copy, so changing it changes nothing shared
    fx.make(reg.get("FEA-SEC-001"), out, "high")["learn"].clear()
    assert am.learn("FEA-SEC-001")


# ---- progress

FID = fx.finding_id("FEA-SEC-001", "ws-1")


def _fix(**over):
    return {"kind": "fix_validated", "control_id": "FEA-SEC-001", "subject_id": "ws-1", "finding_id": FID,
            "outcome": "pass", "basis": "verified_configuration", **over}


def test_a_validated_fix_earns_credit_once_by_its_finding():
    got = am.credit_for(_fix())
    assert got["credit"] == 1 and got["key"] == "fix:" + FID
    assert am.credit_for(_fix(basis="measured"))["credit"] == 1


@pytest.mark.parametrize("outcome", ["not_evaluated", "fail", "not_applicable", "error", None])
def test_a_rerun_that_did_not_pass_earns_nothing(outcome):
    got = am.credit_for(_fix(outcome=outcome))
    assert got["credit"] == 0 and got["key"] is None


@pytest.mark.parametrize("basis", ["inferred", "insufficient", None])
def test_a_pass_without_configuration_or_measured_evidence_earns_nothing(basis):
    assert am.credit_for(_fix(basis=basis))["credit"] == 0


def test_a_fix_for_another_finding_earns_nothing():
    assert am.credit_for(_fix(finding_id=fx.finding_id("FEA-SEC-001", "ws-2")))["credit"] == 0
    assert am.credit_for(_fix(subject_id=""))["credit"] == 0
    assert am.credit_for(_fix(control_id=None))["credit"] == 0


def test_a_passed_exercise_earns_credit():
    got = am.credit_for({"kind": "exercise_passed", "pack": "dp-700", "exercise_id": "E21"})
    assert got == {"credit": 1, "key": "exercise:dp-700/E21", "reason": "The exercise was passed."}
    assert am.credit_for({"kind": "exercise_passed", "pack": "dp-700"})["credit"] == 0


@pytest.mark.parametrize("kind", ["dismissed", "accepted_risk", "not_evaluated", "exercise_started", "", None])
def test_dismissals_accepted_risks_and_anything_else_earn_nothing(kind):
    # even with every field a validated fix would have
    got = am.credit_for(_fix(kind=kind))
    assert got["credit"] == 0 and got["key"] is None and got["reason"]


def test_not_a_dict_earns_nothing():
    assert am.credit_for(None)["credit"] == 0  # type: ignore[arg-type]
    assert am.credit_for("fix_validated")["credit"] == 0  # type: ignore[arg-type]


def test_credit_is_deterministic_and_leaves_the_event_alone():
    ev = _fix()
    before = dict(ev)
    assert am.credit_for(ev) == am.credit_for(ev)
    assert ev == before


def test_the_credit_rule_lists_agree():
    assert set(am.CREDITED) == {"fix_validated", "exercise_passed"}
    assert not set(am.CREDITED) & set(am.NO_CREDIT)
    assert set(am.NO_CREDIT) == {"dismissed", "accepted_risk", "not_evaluated"}


# ---- adversarial review


def test_a_lone_surrogate_in_a_fix_earns_nothing_and_doesnt_crash():
    assert am.credit_for(_fix(subject_id="\ud800"))["credit"] == 0
    assert am.credit_for(_fix(control_id="FEA-SEC-\udfff"))["credit"] == 0


def test_a_fix_for_a_control_that_doesnt_exist_earns_nothing():
    fake = fx.finding_id("FEA-FAKE-999", "ws-1")
    assert am.credit_for(_fix(control_id="FEA-FAKE-999", finding_id=fake))["credit"] == 0


@pytest.mark.parametrize("pack, eid", [("dp-700/E1", "0"), ("dp-700", "E1/0"), ("dp-700", "../E10"),
                                       ("DP 700", "E10"), ("dp-700", "lesson-1")])
def test_a_passed_exercise_must_name_a_real_pack_and_exercise_id(pack, eid):
    # "dp-700/E1" + "0" used to mint the key of exercise dp-700/E10 without passing it
    got = am.credit_for({"kind": "exercise_passed", "pack": pack, "exercise_id": eid})
    assert got["credit"] == 0 and got["key"] is None
