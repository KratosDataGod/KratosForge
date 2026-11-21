"""ESI-601: assessment findings and opportunities become optimization records keyed by their
fingerprint, a repeat links instead of duplicating, and only Verified resolves the source."""

from __future__ import annotations

import copy

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.excellence import controls as ct
from fabric_desktop.excellence import findings as fx
from fabric_desktop.opportunities import model as om
from fabric_desktop.optimize import schema
from fabric_desktop.savings import baselines as bl
from fabric_desktop.savings import links as lk

AT = "2026-11-20T09:00:00Z"
TEMPLATE = {"why": "Full refreshes waste CU", "solution": "Switch to incremental loads", "steps": ["one"],
            "benefit": "may help", "effort": "low", "risk": "low", "validation": "Compare row counts",
            "rollback": "Restore the full refresh notebook"}


def control(version: str = "1.0.0") -> ct.Control:
    return ct.Control(id="FEA-PERF-001", version=version, dimension="performance", title="Full refresh",
                      severity="high", requirement="microsoft_recommendation", scope="item",
                      evidence_kinds=("item.runs",), evaluate=lambda ctx: {}, remediation=TEMPLATE)


def finding(version: str = "1.0.0", subj: dict | None = None, **over) -> dict:
    subj = subj or {"type": "item", "id": "nb-1", "name": "Sales", "workspace_id": "ws-1", "item_type": "Notebook"}
    out = {"outcome": "fail", "subject": subj, "text": "runs a full refresh", "evidence_ids": ["e1"], "basis": "measured"}
    return fx.make(control(version), out, "high") | over


def opportunity(**over) -> dict:
    wl = {"workspace_id": "ws-1", "item_id": "nb-2", "type": "Notebook", "name": "Orders"}
    o = om.make(rule="duplicate_notebook", category="duplicate_processing", action="eliminate", title="Duplicate",
                artifacts=[om.artifact(wl, "remove"), om.artifact(wl | {"item_id": "nb-3"}, "keep")],
                current="two notebooks", problem="same code", evidence=[om.evidence("code", "same", "definition")],
                recommendation="drop one", alternatives=[], effort="low", risk="low", confidence="proven",
                validation=["compare"], rollback="restore it", source="analyzer", next_step={"kind": "open"})
    return o | over


# ---------- building records ----------

def test_a_finding_gives_a_proposed_record_with_the_v2_recommendation_columns():
    p = lk.from_finding(finding(), at=AT)
    rec = p["record"]
    v2 = set(schema.expected()[schema.table("recommendations")])
    assert set(rec) <= v2 and set(rec) == set(lk.RECORD_COLUMNS)
    assert v2 - set(rec) == {"installation_id", "created_at", "app_version", "schema_version"}
    assert rec["state"] == "proposed" and rec["version"] == 1 and rec["kind"] == lk.FEA
    assert rec["fingerprint"] == finding()["id"] and rec["optimization_id"] == lk.optimization_id(rec["fingerprint"])
    assert rec["category"] == "fea:performance" and rec["effort"] == "low" and rec["confidence"] == "measured"
    assert rec["rollback"] is None and rec["optimized_ref"] is None and rec["at"] == AT
    assert "user_id" not in rec


def test_link_rows_have_the_v2_optimization_link_columns():
    p = lk.from_finding(finding(), at=AT)
    cols = set(schema.expected()[schema.table("optimization_link")])
    assert p["links"] and all(set(r) <= cols and set(r) == set(lk.LINK_COLUMNS) for r in p["links"])
    assert p["links"][0]["artifact_key"] == "ws-1/nb-1" and p["links"][0]["workspace_id"] == "ws-1"


def test_workspace_and_capacity_findings_link_to_the_workspace_or_capacity():
    ws = lk.from_finding(finding(subj={"type": "workspace", "id": "ws-9", "name": "W"}), at=AT)
    cap = lk.from_finding(finding(subj={"type": "capacity", "id": "cap-1", "name": "C"}), at=AT)
    assert ws["links"][0]["workspace_id"] == "ws-9" and ws["links"][0]["artifact_key"] is None
    assert cap["links"][0]["capacity_id"] == "cap-1" and cap["links"][0]["workspace_id"] is None


def test_the_same_finding_gives_the_same_ids_on_another_pc_or_a_rerun():
    a = lk.from_finding(finding(), at=AT)
    b = lk.from_finding(copy.deepcopy(finding()), at="2026-11-21T10:00:00+00:00")
    assert a["optimization_id"] == b["optimization_id"]
    assert a["record"]["record_id"] == b["record"]["record_id"]
    assert a["record"]["recommendation_id"] == b["record"]["recommendation_id"]


def test_a_new_control_version_keeps_the_same_optimization():
    assert lk.from_finding(finding("2.0.0"), at=AT)["optimization_id"] == lk.from_finding(finding(), at=AT)[
        "optimization_id"]


def test_different_resources_give_different_optimizations():
    other = finding(subj={"type": "item", "id": "nb-9", "name": "x", "workspace_id": "ws-1", "item_type": "Notebook"})
    assert lk.from_finding(other, at=AT)["optimization_id"] != lk.from_finding(finding(), at=AT)["optimization_id"]


def test_records_hold_fingerprints_never_text():
    f = finding()
    rec = lk.from_finding(f, at=AT)["record"]
    text = repr(rec)
    for leak in (f["title"], f["discovered"], f["solution"], f["rollback"], "Sales"):
        assert leak not in text


def test_an_original_reference_must_be_a_fingerprint_not_code():
    assert lk.from_finding(finding(), at=AT, original_ref="def-abc123")["record"]["original_ref"] == "def-abc123"
    with pytest.raises(ValueError, match="never code text"):
        lk.from_finding(finding(), at=AT, original_ref="spark.sql('select * from x')")


def test_an_opportunity_gives_a_proposed_record_and_one_link_per_artifact():
    o = opportunity()
    p = lk.from_opportunity(o, at=AT, capacity_id="cap-1")
    rec = p["record"]
    assert rec["fingerprint"] == o["id"] and rec["kind"] == lk.OE and rec["category"] == "duplicate_processing"
    assert rec["state"] == "proposed" and rec["confidence"] == "proven"
    assert {(r["artifact_key"], r["relation"], r["capacity_id"]) for r in p["links"]} == {
        ("ws-1/nb-2", "remove", "cap-1"), ("ws-1/nb-3", "keep", "cap-1")}
    assert lk.from_opportunity(copy.deepcopy(o), at=AT)["optimization_id"] == p["optimization_id"]


def test_finding_and_opportunity_ids_never_collide():
    assert lk.from_finding(finding(), at=AT)["optimization_id"] != lk.from_opportunity(opportunity(), at=AT)[
        "optimization_id"]


@pytest.mark.parametrize(("over", "message"), [
    ({"id": "abc"}, "fx- id"),
    ({"rule": "FEA-PERF-001"}, "id@version"),
    ({"resource": {"type": "item"}}, "doesn't name"),
    ({"id": fx.finding_id("FEA-PERF-001", "other")}, "may have been edited"),
    ({"fixture": True}, "demonstration fixture"),
    ({"advisory": True}, "advisory"),
    ({"status": "resolved"}, "not open"),
])
def test_bad_findings_are_refused_in_full_sentences(over, message):
    with pytest.raises(ValueError, match=message):
        lk.from_finding(finding(**over), at=AT)


@pytest.mark.parametrize("at", ["", None, "yesterday", "2026-11-20T09:00:00"])
def test_a_detection_needs_a_utc_time(at):
    with pytest.raises(ValueError, match="time"):
        lk.from_finding(finding(), at=at)


def test_a_finding_that_is_not_a_dict_is_refused():
    with pytest.raises(ValueError, match="finding record"):
        lk.from_finding(["x"], at=AT)  # type: ignore[arg-type]


@pytest.mark.parametrize(("over", "message"), [
    ({"id": "x"}, "opp- id"),
    ({"rule": ""}, "rule"),
    ({"category": "nope"}, "unknown category"),
    ({"status": "odd"}, "unknown status"),
    ({"status": "rejected"}, "was rejected"),
    ({"status": "superseded"}, "was superseded"),
    ({"fixture": True}, "demonstration fixture"),
    ({"artifacts": []}, "artifacts"),
    ({"artifacts": [{"item_id": "nb-1"}]}, "workspace id"),
])
def test_bad_opportunities_are_refused_in_full_sentences(over, message):
    with pytest.raises(ValueError, match=message):
        lk.from_opportunity(opportunity(**over), at=AT)


# ---------- linking ----------

def test_a_new_fingerprint_gives_a_new_record():
    p = lk.from_finding(finding(), at=AT)
    out = lk.link([], [p])
    assert out["new"] == [p] and out["linked"] == [] and out["repeats"] == 0


def test_a_repeat_detection_links_to_the_existing_record():
    first = lk.from_finding(finding(), at=AT)
    again = lk.from_finding(finding(), at="2026-11-21T09:00:00Z")
    out = lk.link([first["record"]], [again])
    assert out["new"] == []
    (ev,) = out["linked"]
    assert ev["event"] == "linked" and ev["optimization_id"] == first["optimization_id"]
    assert ev["existing_state"] == "proposed" and not ev["came_back"] and ev["detected_at"] == "2026-11-21T09:00:00Z"
    assert "record" not in ev


def test_the_latest_state_is_reported_and_a_verified_one_coming_back_is_flagged():
    first = lk.from_finding(finding(), at=AT)["record"]
    verified = first | {"record_id": "r2", "state": "verified", "version": 3, "at": "2026-11-25T00:00:00Z"}
    applied = first | {"record_id": "r3", "state": "applied", "version": 2, "at": "2026-11-22T00:00:00Z"}
    out = lk.link([verified, first, applied], [lk.from_finding(finding(), at="2026-12-01T00:00:00Z")])
    (ev,) = out["linked"]
    assert ev["existing_state"] == "verified" and ev["existing_version"] == 3 and ev["came_back"]
    assert "may not have held" in ev["reason"]


def test_two_detections_of_one_fingerprint_in_a_batch_give_one_record():
    p = lk.from_opportunity(opportunity(), at=AT)
    q = lk.from_opportunity(opportunity(), at="2026-11-21T00:00:00Z")
    out = lk.link([], [p, q, lk.from_finding(finding(), at=AT)])
    assert len(out["new"]) == 2 and out["repeats"] == 1


def test_version_1_recommendations_without_an_optimization_id_are_left_alone():
    v1 = {"recommendation_id": "r", "fingerprint": "abc", "state": "open"}
    p = lk.from_finding(finding(), at=AT)
    assert lk.link([v1], [p])["new"] == [p]


def test_two_records_for_one_fingerprint_are_refused():
    rec = lk.from_finding(finding(), at=AT)["record"]
    other = rec | {"optimization_id": "opt-somethingelse", "record_id": "x"}
    with pytest.raises(ValueError, match="two different optimization records"):
        lk.link([rec, other], [])


def test_one_optimization_with_two_fingerprints_is_refused():
    rec = lk.from_finding(finding(), at=AT)["record"]
    with pytest.raises(ValueError, match="two fingerprints"):
        lk.link([rec, rec | {"fingerprint": "fx-other", "record_id": "x"}], [])


def test_a_detection_whose_id_does_not_match_its_fingerprint_is_refused():
    p = lk.from_finding(finding(), at=AT)
    p["record"] = p["record"] | {"optimization_id": "opt-forged"}
    with pytest.raises(ValueError, match="optimization id its fingerprint gives"):
        lk.link([], [p])


def test_an_existing_record_with_a_different_id_for_the_fingerprint_is_refused():
    p = lk.from_finding(finding(), at=AT)
    stale = p["record"] | {"optimization_id": "opt-old"}
    with pytest.raises(ValueError, match="already has optimization opt-old"):
        lk.link([stale], [p])


@pytest.mark.parametrize(("existing", "detections", "message"), [
    (None, [], "each as a list"),
    (["x"], [], "row"),
    ([{"optimization_id": "opt-1"}], [], "without a fingerprint"),
    ([], [{"x": 1}], "proposal"),
])
def test_bad_link_input_is_refused(existing, detections, message):
    with pytest.raises(ValueError, match=message):
        lk.link(existing, detections)


def test_linking_changes_nothing_it_is_given():
    rec = lk.from_finding(finding(), at=AT)
    before = copy.deepcopy(rec)
    lk.link([rec["record"]], [rec])
    assert rec == before


# ---------- reporting back ----------

def _validation(oid: str, result: str) -> dict:
    return {"row": {"validation_id": "va-1", "optimization_id": oid, "result": result}, "result": result}


def test_only_verified_resolves_a_finding():
    p = lk.from_finding(finding(), at=AT)
    got = {r: lk.report_back(p, _validation(p["optimization_id"], r)) for r in bl.RESULTS}
    assert got["verified"]["resolved"] and got["verified"]["status"] == "resolved"
    for r in ("validating", "regressed", "failed"):
        assert not got[r]["resolved"] and got[r]["status"] == "open"
    for r, out in got.items():
        assert out["reason"].count(".") == 1 and out["reason"].endswith(".")
        assert out["source_id"] == p["fingerprint"] and out["validation_result"] == r
    assert "resolved" in got["verified"]["reason"] and "stays open" in got["regressed"]["reason"]


def test_an_opportunity_is_verified_or_stays_validating():
    p = lk.from_opportunity(opportunity(), at=AT)
    assert lk.report_back(p["record"], _validation(p["optimization_id"], "verified"))["status"] == "verified"
    for r in ("validating", "regressed", "failed"):
        out = lk.report_back(p["record"], _validation(p["optimization_id"], r))
        assert out["status"] == "validating" and not out["resolved"] and "opportunity stays open" in out["reason"]


def test_a_real_verified_validation_resolves_and_a_regression_does_not():
    p = lk.from_finding(finding(), at=AT)
    oid = p["optimization_id"]

    def run(rid, v, day, month):
        return {"run_id": rid, "workload_key": "ws-1/nb-1", "definition_version": "d1", "input_scope": "s",
                "at": f"2026-{month:02d}-{day:02d}T09:00:00Z", "ran": "fabric",
                "cu": ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, v, run_id=rid,
                              source="Capacity Metrics export", collected_at="2026-11-01T00:00:00Z")}

    before = bl.baseline("ws-1/nb-1", [run("a", 10, 3, 10), run("b", 12, 10, 10), run("c", 9, 20, 10)],
                         definition_version="d1", input_scope="s", window_start="2026-10-01T00:00:00Z",
                         window_end="2026-11-01T00:00:00Z")
    good = bl.validate(oid, before, [run("x", 6, 3, 11), run("y", 7, 4, 11), run("z", 5, 5, 11)], outputs_match=True)
    worse = bl.validate(oid, before, [run("x", 16, 3, 11), run("y", 17, 4, 11), run("z", 15, 5, 11)],
                        outputs_match=True)
    assert lk.report_back(p, good)["resolved"]
    assert lk.report_back(p, good["row"])["validation_id"] == good["row"]["validation_id"]
    assert not lk.report_back(p, worse)["resolved"]


def test_reporting_back_refuses_a_validation_of_another_optimization():
    p = lk.from_finding(finding(), at=AT)
    with pytest.raises(ValueError, match="not opt-"):
        lk.report_back(p, _validation("opt-other", "verified"))


@pytest.mark.parametrize(("record", "validation", "message"), [
    (None, {}, "record"),
    ({"kind": "manual", "fingerprint": "x", "optimization_id": "y"}, {}, "nothing to report back"),
    ({"kind": lk.FEA, "fingerprint": "fx-1", "optimization_id": "opt-x"}, {}, "doesn't match its fingerprint"),
])
def test_bad_records_are_refused_when_reporting_back(record, validation, message):
    with pytest.raises(ValueError, match=message):
        lk.report_back(record, validation)


@pytest.mark.parametrize("validation", [None, {"result": "maybe"}, {"row": {"result": "done"}}])
def test_bad_validations_are_refused_when_reporting_back(validation):
    p = lk.from_finding(finding(), at=AT)
    with pytest.raises(ValueError, match="validation"):
        lk.report_back(p, validation)
