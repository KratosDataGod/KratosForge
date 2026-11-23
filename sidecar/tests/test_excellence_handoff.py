"""Optimization planning handoff (FEA-604): approved findings go to the optimization engine and the F64
planner once, linked to the engine's own opportunities, with no savings of their own and none counted
twice."""

from __future__ import annotations

import copy
import inspect
import json
import random

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.excellence import adapters, assess, catalog, handoff
from fabric_desktop.opportunities import analyzers, assess as opp_assess, fixture

AT = "2026-11-18T02:00:00+00:00"
NB_KEEP, NB_COPY = "f1f1f1f1-0000-4000-8000-000000000101", "f1f1f1f1-0000-4000-8000-000000000102"


@pytest.fixture(scope="module")
def demo() -> tuple[list[dict], dict]:
    """The demonstration fixture through the optimization engine and the assessment, as the app runs them."""
    d = fixture.load()
    result = opp_assess.assess([d["source"]], usage_imports=d["usage_imports"], price=d["price"], now=d["now"],
                               fixture=True)
    ws = d["source"]["workspace"]
    records = (adapters.workspace(ws, d["source"]["items"], collected_at=AT)
               + adapters.optimization(result, collected_at=AT))
    snap = assess.assess(records, catalog.registry(), scope={"workspaces": [{"id": ws["id"]}], "capacities": []},
                         assessed_at=AT, fixture=True)
    return snap["findings"], result


def all_ids(findings: list[dict]) -> list[str]:
    return [f["id"] for f in findings]


def finding(fid: str, rule: str, item: str = "item-1", *, advisory: bool = False,
            confidence: str = "verified_configuration", ws: str = "ws-1") -> dict:
    return {"id": fid, "rule": rule, "advisory": advisory, "confidence": confidence,
            "resource": {"type": "item", "id": item, "name": item, "workspace_id": ws, "item_type": "Notebook"}}


def opp(oid: str, rule: str, items: list[str], *, counted: bool = True, cu: float | None = 3600.0,
        overlaps: list[str] | None = None, ws: str = "ws-1") -> dict:
    cu_m = (ms.make(ms.ESTIMATED, "fabric_cu_avoided", ms.CU_SECONDS, cu, method="test", assumptions=["test"])
            if cu is not None else ms.unknown("fabric_cu_avoided", ms.CU_SECONDS, "not measured"))
    return {"id": oid, "rule": rule, "artifacts": [{"workspace_id": ws, "item_id": i, "type": "Notebook", "name": i,
                                                    "role": "remove"} for i in items],
            "affects": items[-1:], "counted": counted, "overlaps": overlaps or [],
            "impact": {"basis": "whole workload", "cu_per_month": cu_m, "run_seconds_per_month": None,
                       "realizable": False, "realizable_reason": "frees capacity only"}}


# ---- linking and dedup


def test_findings_the_engine_already_reports_are_linked_not_duplicated(demo):
    findings, result = demo
    h = handoff.handoff(findings, all_ids(findings), result)
    assert h["fixture"] is True and h["planner"]["consumed"] is False
    by_rule = {i["finding_id"]: i for i in h["items"]}
    for f in findings:
        if f["rule"].startswith("FEA-GOV-"):
            assert f["id"] not in by_rule
            continue
        item = by_rule[f["id"]]
        assert item["action"] == "already_in_optimization" and item["match"] == "matched" and item["opportunity_ids"]
        for oid in item["opportunity_ids"]:
            o = next(o for o in result["opportunities"] if o["id"] == oid)
            assert o["rule"] in handoff.MATCHES[item["control_id"]]
            assert f["resource"]["id"] in {a["item_id"] for a in o["artifacts"]}
    # Both notebooks of the duplicate pair point at one opportunity, handed to the planner once.
    dup = next(o for o in result["opportunities"] if o["rule"] == "OE-DUP-NB")
    shared = {s["opportunity_id"]: s["finding_ids"] for s in h["dedup"]["shared_opportunities"]}
    assert len(shared[dup["id"]]) == 2
    keys = [p["key"] for p in h["planner_inputs"]]
    assert len(keys) == len(set(keys)) and keys.count("opp:" + dup["id"]) == 1
    assert next(p for p in h["planner_inputs"] if p["opportunity_id"] == dup["id"])["finding_ids"] == sorted(shared[dup["id"]])
    assert [u["control_id"] for u in h["dedup"]["unmapped"]] == ["FEA-GOV-001"]


def test_a_mapped_finding_without_an_opportunity_is_a_new_candidate_with_no_figure():
    f = finding("fx-1", "FEA-DE-001@1.0.0", "nb-a")
    other = opp("opp-other", "OE-FULL-REFRESH", ["nb-b"])          # same rule, different item
    wrong_rule = opp("opp-dup", "OE-DUP-NB", ["nb-x", "nb-a"])      # same item, different concern
    h = handoff.handoff([f], ["fx-1"], {"opportunities": [other, wrong_rule]})
    [item] = h["items"]
    assert item["action"] == "new_candidate" and item["match"] == "no_opportunity" and item["opportunity_ids"] == []
    [p] = h["planner_inputs"]
    assert p["source"] == "excellence_assessment" and p["cu_per_month"] is None and p["run_seconds_per_month"] is None
    assert p["reason"] == handoff.NO_FIGURE


def test_same_item_in_another_workspace_is_not_a_match():
    h = handoff.handoff([finding("fx-1", "FEA-DE-001@1.0.0", "nb-a", ws="ws-1")], ["fx-1"],
                        {"opportunities": [opp("opp-1", "OE-FULL-REFRESH", ["nb-a"], ws="ws-2")]})
    assert h["items"][0]["action"] == "new_candidate"


def test_capacity_controls_have_no_engine_rule_and_hand_on_no_money():
    f = finding("fx-cap", "FEA-CAP-004@1.0.0") | {"resource": {"type": "capacity", "id": "cap-1", "name": "F64"},
                                                 "detail": {"cu_seconds": 999.0}}
    h = handoff.handoff([f], ["fx-cap"], {"opportunities": []})
    assert h["items"][0]["match"] == "no_rule" and h["items"][0]["action"] == "new_candidate"
    [p] = h["planner_inputs"]
    assert p["cu_per_month"] is None and "999" not in json.dumps(h)
    assert "currency" not in json.dumps(h["planner_inputs"]).replace('"currency": null', "")


def test_unmapped_controls_are_reported_not_guessed():
    h = handoff.handoff([finding("fx-s", "FEA-SEC-001@1.0.0", "ws-1"), finding("fx-z", "FEA-NEW-999@1.0.0")],
                        ["fx-s", "fx-z"], {"opportunities": [opp("opp-1", "OE-DUP-NB", ["ws-1", "item-1"])]})
    assert h["items"] == [] and h["planner_inputs"] == []
    assert [(u["finding_id"], u["control_id"]) for u in h["dedup"]["unmapped"]] == [("fx-s", "FEA-SEC-001"),
                                                                                    ("fx-z", "FEA-NEW-999")]


def test_without_an_optimization_result_engine_backed_findings_are_held_back():
    fs = [finding("fx-1", "FEA-PERF-001@1.0.0"), finding("fx-2", "FEA-CAP-002@1.0.0")]
    h = handoff.handoff(fs, ["fx-1", "fx-2"])
    by = {i["finding_id"]: i for i in h["items"]}
    assert by["fx-1"]["match"] == "not_checked" and by["fx-1"]["planner_keys"] == []
    assert by["fx-1"]["not_planned_reason"] and h["dedup"]["not_checked"] == ["fx-1"]
    assert [p["key"] for p in h["planner_inputs"]] == ["finding:fx-2"]


def test_every_mapped_rule_is_an_engine_rule_and_every_mapped_control_exists():
    source = inspect.getsource(analyzers)
    for cid, rules in handoff.MATCHES.items():
        assert catalog.registry().get(cid) is not None, cid
        for r in rules:
            assert f'"{r}"' in source, r


# Controls deliberately left out of MATCHES: no optimization engine rule or planner input reports the
# same concern. Includes main's pipeline retry, timeout and failure-path controls (FEA-REL-004 to 006)
# and Git controls (FEA-DEVOPS-003 to 005): reliability and change control, not capacity freed.
NOT_MATCHED = {"FEA-ARCH-001", "FEA-ARCH-002", "FEA-ARCH-003", "FEA-ARCH-004", "FEA-CAP-001", "FEA-DE-003",
               "FEA-DEVOPS-001", "FEA-DEVOPS-002", "FEA-DEVOPS-003", "FEA-DEVOPS-004", "FEA-DEVOPS-005",
               "FEA-GOV-001", "FEA-REL-001", "FEA-REL-002", "FEA-REL-003", "FEA-REL-004", "FEA-REL-005",
               "FEA-REL-006", "FEA-SEC-001", "FEA-SEC-002", "FEA-SEC-003", "FEA-SEC-004", "FEA-SEC-005",
               "FEA-SM-001", "FEA-SM-002", "FEA-SM-003", "FEA-SM-004", "FEA-SM-005", "FEA-SM-006"}


def test_every_registered_control_is_matched_or_deliberately_not():
    ids = {c.id for c in catalog.registry().controls()}
    assert not set(handoff.MATCHES) & NOT_MATCHED
    assert set(handoff.MATCHES) | NOT_MATCHED == ids, "a new control: add it to MATCHES or to NOT_MATCHED"


# ---- no double counting


def test_planner_figures_never_add_up_to_more_than_the_engines_totals(demo):
    findings, result = demo
    h = handoff.handoff(findings, all_ids(findings), result)
    cited = [p["cu_per_month"] for p in h["planner_inputs"] if p["cu_per_month"] and p["cu_per_month"]["value"] is not None]
    assert cited, "the fixture has a measured duplicate; it should be cited"
    total = sum(ms.convert(m["value"], m["unit"], ms.CU_HOURS) for m in cited)
    engine = result["totals"]["cu_per_month"]
    assert total <= engine["value"] + 1e-6
    for p in h["planner_inputs"]:
        if p["counted_in_optimization_totals"]:
            o = next(o for o in result["opportunities"] if o["id"] == p["opportunity_id"])
            assert p["cu_per_month"] == o["impact"]["cu_per_month"]  # the engine's own figure, with its basis
        else:
            assert p["cu_per_month"] is None and p["run_seconds_per_month"] is None
    assert not any(k in json.dumps(h) for k in ('"total"', '"sum"', '"savings":'))


def test_overlapping_opportunities_cite_one_figure_per_workload():
    strong = opp("opp-a", "OE-DUP-NB", ["nb-keep", "nb-copy"], cu=7200.0)
    weak = opp("opp-b", "OE-FULL-REFRESH", ["nb-copy"], counted=False, cu=7200.0, overlaps=["opp-a"])
    fs = [finding("fx-1", "FEA-PERF-001@1.0.0", "nb-copy"), finding("fx-2", "FEA-DE-001@1.0.0", "nb-copy")]
    h = handoff.handoff(fs, ["fx-1", "fx-2"], {"opportunities": [weak, strong]})
    figures = [p["cu_per_month"]["value"] for p in h["planner_inputs"] if p["cu_per_month"]]
    assert figures == [7200.0]
    weak_in = next(p for p in h["planner_inputs"] if p["opportunity_id"] == "opp-b")
    assert weak_in["counted_under"] == ["opp-a"] and "twice" in weak_in["reason"]


def test_the_input_measurements_are_not_shared_with_the_output():
    o = opp("opp-a", "OE-DUP-NB", ["nb-keep", "item-1"])
    h = handoff.handoff([finding("fx-1", "FEA-PERF-001@1.0.0")], ["fx-1"], {"opportunities": [o]})
    h["planner_inputs"][0]["cu_per_month"]["value"] = -1
    assert o["impact"]["cu_per_month"]["value"] == 3600.0


# ---- approval and advisory


def test_findings_not_approved_are_excluded(demo):
    findings, result = demo
    keep = [f["id"] for f in findings if f["resource"]["id"] == NB_KEEP]
    h = handoff.handoff(findings, keep + ["fx-nosuchfinding"], result)
    assert {i["finding_id"] for i in h["items"]} == set(keep)
    assert all(fid in keep for p in h["planner_inputs"] for fid in p["finding_ids"])
    assert h["dedup"]["not_approved"] == len(findings) - len(keep)
    assert h["dedup"]["unknown_approved_ids"] == ["fx-nosuchfinding"]
    assert handoff.handoff(findings, [], result)["items"] == []


@pytest.mark.parametrize("kw", [{"advisory": True, "confidence": "inferred"}, {"confidence": "inferred"},
                                {"confidence": "insufficient"}])
def test_advisory_or_inferred_findings_never_become_planner_inputs(kw):
    fs = [finding("fx-1", "FEA-PERF-001@1.0.0", **kw), finding("fx-2", "FEA-CAP-003@1.0.0", **kw)]
    h = handoff.handoff(fs, ["fx-1", "fx-2"], {"opportunities": [opp("opp-1", "OE-DUP-NB", ["x", "item-1"])]})
    assert h["planner_inputs"] == []
    assert all(i["planner_keys"] == [] and i["not_planned_reason"] for i in h["items"])
    assert h["items"][0]["action"] == "already_in_optimization"  # still linked, so nobody adds it twice


def test_an_advisory_finding_doesnt_join_a_planner_input_another_finding_made():
    o = opp("opp-1", "OE-DUP-NB", ["nb-a", "nb-b"])
    fs = [finding("fx-1", "FEA-PERF-001@1.0.0", "nb-a"), finding("fx-2", "FEA-PERF-001@1.0.0", "nb-b", advisory=True)]
    h = handoff.handoff(fs, ["fx-1", "fx-2"], {"opportunities": [o]})
    assert [p["finding_ids"] for p in h["planner_inputs"]] == [["fx-1"]]
    assert h["dedup"]["shared_opportunities"] == [{"opportunity_id": "opp-1", "finding_ids": ["fx-1", "fx-2"]}]


# ---- determinism


def test_same_inputs_in_any_order_give_the_same_handoff(demo):
    findings, result = demo
    first = handoff.handoff(findings, all_ids(findings), result)
    rng = random.Random(604)
    for _ in range(5):
        fs, r = copy.deepcopy(findings), copy.deepcopy(result)
        rng.shuffle(fs)
        rng.shuffle(r["opportunities"])
        ids = all_ids(fs)
        rng.shuffle(ids)
        again = handoff.handoff(fs, iter(ids), r)
        assert json.dumps(again, sort_keys=True) == json.dumps(first, sort_keys=True)


def test_the_handoff_changes_neither_input(demo):
    findings, result = demo
    before = json.dumps([findings, result], sort_keys=True, default=str)
    handoff.handoff(findings, all_ids(findings), result)
    assert json.dumps([findings, result], sort_keys=True, default=str) == before


# ---- malformed inputs


@pytest.mark.parametrize("findings, approved, result", [
    ("not a list", [], None), ({"id": "fx"}, [], None), ([], "fx-1", None), ([], 5, None), ([], [], ["opps"]),
])
def test_wrong_kinds_of_input_are_refused(findings, approved, result):
    with pytest.raises(ValueError):
        handoff.handoff(findings, approved, result)


def test_malformed_findings_are_reported_and_never_handed_off():
    good = finding("fx-ok", "FEA-CAP-002@1.0.0")
    bad = ["text", {"rule": "FEA-CAP-002@1.0.0"}, finding("fx-norule", ""), finding("fx-nores", "FEA-CAP-002@1.0.0") | {"resource": None},
           finding("fx-adv", "FEA-CAP-002@1.0.0") | {"advisory": "no"},
           finding("fx-dup", "FEA-CAP-002@1.0.0"), finding("fx-dup", "FEA-CAP-003@1.0.0")]
    ids = ["fx-ok", "fx-norule", "fx-nores", "fx-adv", "fx-dup"]
    h = handoff.handoff([good, *bad], ids, {"opportunities": []})
    assert [i["finding_id"] for i in h["items"]] == ["fx-ok"]
    reasons = {(r["finding_id"], r["reason"]) for r in h["dedup"]["rejected"]}
    assert (None, "not a finding record") in reasons and (None, "no finding id") in reasons
    assert {r[0] for r in reasons} >= {"fx-norule", "fx-nores", "fx-adv", "fx-dup"}
    assert h["dedup"]["unknown_approved_ids"] == []


def test_malformed_opportunities_are_skipped_and_counted():
    result = {"opportunities": ["x", {"id": "opp-1"}, {"id": "opp-2", "rule": "OE-DUP-NB", "artifacts": "nb"},
                                opp("opp-3", "OE-DUP-NB", ["nb", "item-1"])]}
    h = handoff.handoff([finding("fx-1", "FEA-PERF-001@1.0.0")], ["fx-1"], result)
    assert h["items"][0]["opportunity_ids"] == ["opp-3"] and h["dedup"]["malformed_opportunities"] == 3
    assert handoff.handoff([finding("fx-1", "FEA-PERF-001@1.0.0")], ["fx-1"], {})["items"][0]["match"] == "no_opportunity"


def test_a_fixture_result_isnt_matched_with_a_real_assessment(demo):
    findings, result = demo
    real = [{k: v for k, v in f.items() if k != "fixture"} for f in findings]
    with pytest.raises(ValueError, match="fixture"):
        handoff.handoff(real, all_ids(real), result)


# ---- adversarial review


def test_an_unhashable_confidence_is_held_back_not_a_crash():
    f = finding("fx-1", "FEA-CAP-002@1.0.0") | {"confidence": ["measured"]}
    h = handoff.handoff([f], ["fx-1"], {"opportunities": []})
    assert h["planner_inputs"] == [] and h["items"][0]["not_planned_reason"]


def test_duplicate_opportunity_ids_are_malformed_not_linked_or_cited():
    a = opp("opp-1", "OE-DUP-NB", ["nb-x", "item-1"], cu=7200.0)
    b = opp("opp-1", "OE-DUP-NB", ["nb-y", "item-1"], counted=False, cu=99999.0)
    outs = []
    for opps in ([a, b], [b, a]):
        h = handoff.handoff([finding("fx-1", "FEA-PERF-001@1.0.0")], ["fx-1"], {"opportunities": opps})
        assert h["items"][0]["opportunity_ids"] == [] and h["dedup"]["malformed_opportunities"] == 2
        assert all(p["cu_per_month"] is None for p in h["planner_inputs"])
        outs.append(json.dumps(h, sort_keys=True))
    assert outs[0] == outs[1]


def test_real_findings_mixed_with_fixture_findings_are_refused(demo):
    findings, result = demo
    real = finding("fx-real", "FEA-PERF-001@1.0.0", NB_KEEP)
    with pytest.raises(ValueError, match="fixture"):
        handoff.handoff([*findings, real], [*all_ids(findings), "fx-real"], result)


def test_overlaps_given_as_text_are_not_split_into_letters():
    o = opp("opp-1", "OE-DUP-NB", ["nb-x", "item-1"], counted=False) | {"overlaps": "opp-a"}
    h = handoff.handoff([finding("fx-1", "FEA-PERF-001@1.0.0")], ["fx-1"], {"opportunities": [o]})
    assert h["planner_inputs"][0]["counted_under"] in ([], ["opp-a"])
