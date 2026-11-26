"""ESI-301: optimization records. Version-aware lifecycle updates (scenario 9), reversal (10) and
retirement (11); fingerprints never code; the responsible device and never a user (D3)."""

from __future__ import annotations

import json
import random
import re
import uuid
from typing import Any

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.excellence import controls as ct
from fabric_desktop.excellence import findings as fx
from fabric_desktop.opportunities import model as om
from fabric_desktop.optimize import schema
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import corrections as co
from fabric_desktop.savings import links as lk
from fabric_desktop.savings import records as rd

AT = "2026-11-01T09:00:00Z"
PC_A = str(uuid.UUID(int=1))
PC_B = str(uuid.UUID(int=2))
ROLLBACK = {"method": "redeploy_definition", "targets": ["ws-1/nb-1"], "note": "Redeploy the full refresh notebook"}
TEMPLATE = {"why": "Full refreshes waste CU", "solution": "Switch to incremental loads", "steps": ["one"],
            "benefit": "may help", "effort": "low", "risk": "low", "validation": "Compare row counts",
            "rollback": "Restore the full refresh notebook"}


def control() -> ct.Control:
    return ct.Control(id="FEA-PERF-001", version="1.0.0", dimension="performance", title="Full refresh",
                      severity="high", requirement="microsoft_recommendation", scope="item",
                      evidence_kinds=("item.runs",), evaluate=lambda ctx: {}, remediation=TEMPLATE)


def finding() -> dict:
    subj = {"type": "item", "id": "nb-1", "name": "Sales", "workspace_id": "ws-1", "item_type": "Notebook"}
    out = {"outcome": "fail", "subject": subj, "text": "runs a full refresh", "evidence_ids": ["e1"], "basis": "measured"}
    return fx.make(control(), out, "high")


def opportunity() -> dict:
    wl = {"workspace_id": "ws-1", "item_id": "nb-2", "type": "Notebook", "name": "Orders"}
    return om.make(rule="OE-DUP-NB", category="duplicate_processing", action="eliminate", title="Duplicate",
                   artifacts=[om.artifact(wl, "remove"), om.artifact(wl | {"item_id": "nb-3"}, "keep")],
                   current="two notebooks", problem="same code", evidence=[om.evidence("code", "same", "definition")],
                   recommendation="drop one", alternatives=[], effort="low", risk="low", confidence="proven",
                   validation=["compare"], rollback="restore it", source="analyzer", next_step={"kind": "open"})


def proposal() -> dict:
    return lk.from_finding(finding(), at=AT, original_ref="def:sha256:aaa111")


def start() -> list[dict]:
    return [rd.create(proposal(), installation_id=PC_A)["record"]]


def when(i: int) -> str:
    return f"2026-11-{2 + i // 24:02d}T{i % 24:02d}:00:00Z"


def change(rows: list[dict], state: str, *, device: str = PC_A, at_: str | None = None, **kw: Any) -> dict:
    h = rd.head(rows)
    c: dict[str, Any] = {"based_on_version": h["version"], "state": state, "installation_id": device,
                         "at": at_ or when(h["version"]), "reason": f"moved to {state}"}
    if state == rd.APPLIED:
        c |= {"optimized_ref": "def:sha256:bbb222", "rollback": ROLLBACK}
    return c | kw


def step(rows: list[dict], state: str, **kw: Any) -> list[dict]:
    return rows + [rd.transition(rows, change(rows, state, **kw))["record"]]


def walk(*states: str) -> list[dict]:
    rows = start()
    for s in states:
        rows = step(rows, s)
    return rows


PATHS: dict[str, tuple[str, ...]] = {
    rd.PROPOSED: (), rd.OPEN: (rd.ACCEPTED, rd.OPEN), rd.ACCEPTED: (rd.ACCEPTED,),
    rd.APPLIED: (rd.ACCEPTED, rd.APPLIED), rd.VALIDATING: (rd.ACCEPTED, rd.APPLIED, rd.VALIDATING),
    rd.VERIFIED: (rd.ACCEPTED, rd.APPLIED, rd.VALIDATING, rd.VERIFIED),
    rd.MONITORING: (rd.ACCEPTED, rd.APPLIED, rd.VALIDATING, rd.VERIFIED, rd.MONITORING),
    rd.DISMISSED: (rd.DISMISSED,), rd.ACCEPTED_RISK: (rd.ACCEPTED_RISK,),
    rd.REVERTED: (rd.ACCEPTED, rd.APPLIED, rd.REVERTED), rd.RETIRED: (rd.RETIRED,),
    rd.SUPERSEDED: (rd.SUPERSEDED,), rd.REVERSED: (rd.ACCEPTED, rd.APPLIED, rd.REVERSED),
}


def measured(cu: float) -> dict:
    return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, cu, source="Capacity Metrics export",
                   collected_at="2026-11-01T00:00:00Z")


def estimated(cu: float) -> dict:
    return ms.make(ms.ESTIMATED, "fabric_job_cu_avoided", ms.CU_HOURS, cu, method="calibration",
                   assumptions=["median of 5 Fabric runs"])


def run(eid: str = "e1", day: int = 10) -> dict:
    return {"execution_id": eid, "workload_key": "nb:sales", "baseline_version": "b1",
            "at": f"2026-11-{day:02d}T12:00:00Z", "ran": at.PC}


def chain_sum(events: list[dict], layer: str) -> float:
    return round(sum(e["cu_hours"] or 0.0 for e in events if str(e["layer"]) == layer), 6)


# ---------- the record

def test_version_one_is_the_links_proposal_with_the_device_and_no_user():
    p = proposal()
    made = rd.create(p, installation_id=PC_A)
    rec = made["record"]
    assert rec["record_id"] == p["record"]["record_id"] and rec["version"] == 1 and rec["state"] == lk.PROPOSED
    assert rec["installation_id"] == PC_A and rec["original_ref"] == "def:sha256:aaa111"
    assert rec["rollback"] is None and rec["optimized_ref"] is None and rec["impact"] is None
    assert rec["rule_id"] == "FEA-PERF-001" and rec["rule_version"] == "1.0.0"
    assert made["links"] == p["links"] and made["written"] is False
    assert set(rec) == set(rd.ROW_COLUMNS)


def test_row_columns_are_the_v2_recommendation_columns_plus_the_listed_additions():
    v2 = set(schema.expected(2)[schema.table("recommendations")])
    assert set(rd.ROW_COLUMNS) - v2 == set(rd.NO_COLUMN)
    assert v2 - set(rd.ROW_COLUMNS) == {"created_at", "app_version", "schema_version"}
    for name in rd.NO_COLUMN:
        assert name in (rd.__doc__ or ""), name


def test_an_opportunity_proposal_becomes_a_record_too():
    rec = rd.create(lk.from_opportunity(opportunity(), at=AT), installation_id=PC_B)["record"]
    assert rec["kind"] == lk.OE and rec["installation_id"] == PC_B and rec["original_ref"] is None


def test_the_device_must_be_an_installation_id():
    for bad in ("Laptop", "", None, "ANN-PC", str(uuid.UUID(int=0xABC)).upper()):
        with pytest.raises(ValueError, match="installation"):
            rd.create(proposal(), installation_id=bad)  # type: ignore[arg-type]


def test_an_edited_proposal_is_refused():
    p = proposal()
    p["record"]["optimization_id"] = "opt-0000"
    with pytest.raises(ValueError, match="optimization id"):
        rd.create(p, installation_id=PC_A)
    p = proposal()
    p["record"]["state"] = rd.APPLIED
    with pytest.raises(ValueError, match="proposed"):
        rd.create(p, installation_id=PC_A)


# ---------- updates are insert-only versions

def test_each_change_is_a_new_version_row_and_the_old_rows_are_untouched():
    rows = start()
    before = [dict(r) for r in rows]
    out = rd.transition(rows, change(rows, rd.ACCEPTED))
    assert rows == before  # nothing changed in place
    new = out["record"]
    assert new["version"] == 2 and new["previous_state"] == lk.PROPOSED and out["new"] and out["written"] is False
    assert new["record_id"] == rd.version_record_id(new["optimization_id"], 2) != rows[0]["record_id"]
    assert out["previous"] == rows[0]


def test_applying_records_the_optimized_reference_and_the_rollback_plan():
    rows = walk(rd.ACCEPTED, rd.APPLIED)
    h = rd.head(rows)
    assert h["optimized_ref"] == "def:sha256:bbb222" and h["implemented_at"] == h["at"]
    plan = rd.rollback_plan(rows)
    assert plan is not None
    assert plan["restore_ref"] == "def:sha256:aaa111" and plan["from_ref"] == "def:sha256:bbb222"
    assert plan["method"] == "redeploy_definition" and plan["targets"] == ["ws-1/nb-1"] and plan["restorable"]
    assert json.loads(h["rollback"])["note"] == "Redeploy the full refresh notebook"
    # Later versions carry both forward.
    rows = step(rows, rd.VALIDATING)
    assert rd.head(rows)["optimized_ref"] == "def:sha256:bbb222" and rd.head(rows)["rollback"] == h["rollback"]


def test_reverting_returns_what_to_restore_and_from_which_reference():
    rows = walk(rd.ACCEPTED, rd.APPLIED, rd.VALIDATING)
    out = rd.transition(rows, change(rows, rd.REVERTED, reason="Row counts differ after the change"))
    plan = out["rollback"]
    assert plan["restore_ref"] == "def:sha256:aaa111" and plan["from_ref"] == "def:sha256:bbb222"
    assert plan["reason"] == "Row counts differ after the change" and out["record"]["reverted_at"] == out["record"]["at"]


def test_an_unknown_original_reference_stays_unknown_in_the_rollback_plan():
    rows = [rd.create(lk.from_finding(finding(), at=AT), installation_id=PC_A)["record"]]
    rows = step(step(rows, rd.ACCEPTED), rd.APPLIED)
    plan = rd.rollback_plan(rows)
    assert plan is not None and plan["restore_ref"] is None and plan["restorable"] is False


def test_applying_needs_the_optimized_reference_and_rollback():
    rows = walk(rd.ACCEPTED)
    with pytest.raises(rd.TransitionRefused, match="fingerprint"):
        rd.transition(rows, change(rows, rd.APPLIED, optimized_ref=None))
    with pytest.raises(rd.TransitionRefused, match="rollback"):
        rd.transition(rows, change(rows, rd.APPLIED, rollback=None))
    with pytest.raises(rd.TransitionRefused, match="applied"):
        rd.transition(rows, change(rows, rd.DISMISSED, optimized_ref="def:x"))


def test_moves_that_close_or_undo_need_a_reason():
    for target in sorted(rd.NEEDS_REASON):
        source = next(s for s, nxt in rd.TRANSITIONS.items() if target in nxt)
        rows = walk(*PATHS[source])
        with pytest.raises(ValueError, match="reason"):
            rd.transition(rows, change(rows, target, reason="  "))


def test_a_change_from_a_pc_whose_clock_is_behind_still_sorts_after_the_head():
    rows = walk(rd.ACCEPTED)
    out = rd.transition(rows, change(rows, rd.APPLIED, at_="2026-10-01T00:00:00Z"))
    assert ms.parse_time(out["record"]["at"]) > ms.parse_time(rows[-1]["at"])  # type: ignore[operator]


def test_a_time_without_a_time_zone_is_refused():
    rows = start()
    with pytest.raises(ValueError, match="time zone"):
        rd.transition(rows, change(rows, rd.ACCEPTED, at_="2026-11-05T00:00:00"))


# ---------- the transition graph

def test_every_state_is_in_the_graph_and_the_final_ones_lead_nowhere():
    assert set(rd.TRANSITIONS) == set(rd.STATES) == set(rd.LABELS) == set(PATHS)
    assert all(nxt <= set(rd.STATES) for nxt in rd.TRANSITIONS.values())
    assert rd.TRANSITIONS[rd.SUPERSEDED] == rd.TRANSITIONS[rd.REVERSED] == frozenset()
    # An implemented change never goes back to open or approved.
    for s in (rd.APPLIED, rd.VALIDATING, rd.VERIFIED, rd.MONITORING, rd.REVERTED):
        assert not rd.TRANSITIONS[s] & {rd.OPEN, rd.PROPOSED, rd.ACCEPTED, rd.APPLIED}
    # Verified only through Validating.
    assert [s for s, nxt in rd.TRANSITIONS.items() if rd.VERIFIED in nxt] == [rd.VALIDATING]


@pytest.mark.parametrize("source", sorted(PATHS))
def test_each_move_is_allowed_exactly_when_the_graph_has_it(source):
    rows = walk(*PATHS[source])
    assert rd.head(rows)["state"] == source
    for target in rd.STATES:
        if target in rd.TRANSITIONS[source]:
            out = rd.transition(rows, change(rows, target))
            assert out["record"]["state"] == target and out["record"]["previous_state"] == source
        else:
            with pytest.raises(rd.TransitionRefused) as err:
                rd.transition(rows, change(rows, target))
            assert source in err.value.reason and target in err.value.reason


def test_a_state_that_isnt_one_is_refused_with_the_list():
    rows = start()
    with pytest.raises(rd.TransitionRefused, match="isn't a state"):
        rd.transition(rows, change(rows, "done"))


def test_a_stored_history_that_breaks_the_graph_is_refused():
    rows = walk(rd.ACCEPTED, rd.APPLIED)
    forged = dict(rows[-1], state=rd.VERIFIED)
    with pytest.raises(ValueError, match="doesn't allow"):
        rd.history(rows[:-1] + [forged])
    with pytest.raises(ValueError, match="without gaps"):
        rd.history([rows[0], rows[2]])


# ---------- scenario 9: concurrent updates

def test_scenario_9_a_change_based_on_a_stale_version_is_a_conflict_never_merged():
    rows = walk(rd.ACCEPTED, rd.APPLIED, rd.VALIDATING)  # version 4
    seen_by_b = list(rows)
    a = rd.transition(rows, change(rows, rd.VERIFIED, device=PC_A))
    rows = rows + [a["record"]]
    stale = change(seen_by_b, rd.REVERTED, device=PC_B, reason="Outputs changed")
    with pytest.raises(rd.VersionConflict) as err:
        rd.transition(rows, stale)
    c = err.value
    assert c.based_on == 4 and c.latest == a["record"] and c.newer == [a["record"]]
    assert "version 4" in str(c) and "version 5" in str(c) and PC_A in str(c)
    d = c.as_dict()
    assert d["conflict"] and d["latest_version"] == 5 and d["latest"]["state"] == rd.VERIFIED
    assert rd.head(rows) == a["record"]  # nothing merged


def test_scenario_9_two_pcs_racing_for_one_version_share_its_id_so_one_row_survives():
    rows = walk(rd.ACCEPTED, rd.APPLIED, rd.VALIDATING)
    a = rd.transition(rows, change(rows, rd.VERIFIED, device=PC_A))["record"]
    b = rd.transition(rows, change(rows, rd.REVERTED, device=PC_B, reason="Outputs changed"))["record"]
    assert a["record_id"] == b["record_id"]  # the insert-only MERGE keeps one of them
    with pytest.raises(ValueError, match="two different rows at version 5"):
        rd.history(rows + [a, b])
    stored = rows + [a]  # what the lakehouse holds after the MERGE
    with pytest.raises(rd.VersionConflict):
        rd.transition(stored, change(rows, rd.REVERTED, device=PC_B, reason="Outputs changed"))


def test_scenario_9_a_retry_of_the_same_change_adds_nothing():
    rows = walk(rd.ACCEPTED)
    c = change(rows, rd.APPLIED)
    first = rd.transition(rows, c)
    rows = rows + [first["record"]]
    again = rd.transition(rows, c)
    assert again["new"] is False and again["record"] == first["record"]
    assert rd.history(rows + [first["record"]]) == rows  # the same row twice counts once


def test_a_change_based_on_a_version_that_doesnt_exist_yet_is_refused():
    rows = start()
    with pytest.raises(ValueError, match="no version 5"):
        rd.transition(rows, change(rows, rd.ACCEPTED, based_on_version=5))
    for bad in (None, 0, True, "1", 1.0):
        with pytest.raises(ValueError, match="based on"):
            rd.transition(rows, change(rows, rd.ACCEPTED, based_on_version=bad))


# ---------- scenario 10: reversal

def _verified() -> tuple[list[dict], str]:
    rows = start()
    rows = step(rows, rd.ACCEPTED, at_="2026-11-03T00:00:00Z")
    rows = step(rows, rd.APPLIED, at_="2026-11-05T00:00:00Z")
    rows = step(rows, rd.VALIDATING, at_="2026-11-06T00:00:00Z")
    rows = step(rows, rd.VERIFIED, at_="2026-11-07T00:00:00Z")
    return rows, rd.head(rows)["optimization_id"]


def test_scenario_10_reversal_moves_to_reversed_and_nets_the_accruals_to_zero_through_corrections():
    rows, oid = _verified()
    opt = rd.for_attribution(rows, after=measured(6), action="optimize_in_place")
    assert opt["implemented_at"] and opt["reverted_at"] is None
    ledger: list[dict] = []
    for eid, day in (("e1", 10), ("e2", 12)):
        ledger += at.attribute(run(eid, day), measured(10), [opt], local_avoided=estimated(9))["layers"]
    assert chain_sum(ledger, oid) == 8  # 4 per run
    redo = [at.attribute(run(eid, day), measured(10), [], local_avoided=estimated(9)) for eid, day in
            (("e1", 10), ("e2", 12))]
    out = rd.reverse(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="It was never deployed",
                     installation_id=PC_B, ledger_events=ledger, reattributed=redo)
    rec = out["record"]
    assert rec["state"] == rd.REVERSED and rec["reason"] == "It was never deployed" and rec["version"] == 6
    assert rec["reversed_at"] == rec["at"] and rec["installation_id"] == PC_B and out["written"] is False
    full = ledger + out["events"]
    assert chain_sum(full, oid) == 0
    assert {e["event_type"] for e in out["events"] if e["layer"] == oid} == {co.REVERSAL}
    # The PC layer it capped is corrected up to what the run avoided without it, not guessed.
    assert chain_sum(full, at.LOCAL) == 18
    assert co.reconcile(full, redo)["reconciled"] and out["needs_reattribution"] == []
    # Reversed: attribution leaves it out of every run, before the reversal too.
    reversed_rows = rows + [rec]
    assert at.in_effect([rd.for_attribution(reversed_rows, after=measured(6))], "2026-11-10T12:00:00Z") == []
    assert not rd.accrues(reversed_rows, "2026-11-10T12:00:00Z")


def test_scenario_10_without_a_re_attribution_its_own_layer_is_reversed_and_later_layers_are_flagged():
    rows, oid = _verified()
    opt = rd.for_attribution(rows, after=measured(6))
    ledger = at.attribute(run(), measured(10), [opt], local_avoided=estimated(9))["layers"]
    out = rd.reverse(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="Applied to the wrong item",
                     installation_id=PC_A, ledger_events=ledger)
    assert [e["event_type"] for e in out["events"]] == [co.REVERSAL]
    assert chain_sum(ledger + out["events"], oid) == 0
    assert chain_sum(ledger + out["events"], at.LOCAL) == 6  # untouched, and flagged
    assert [n["execution_id"] for n in out["needs_reattribution"]] == ["e1"]
    assert out["events"][0]["reason"].startswith("Reversed: Optimization")


def test_scenario_10_an_unsplit_share_is_flagged_never_guessed():
    rows, oid = _verified()
    opt = rd.for_attribution(rows, after=None)  # its after isn't measured yet
    ledger = at.attribute(run(), measured(10), [opt], local_avoided=estimated(9))["layers"]
    assert any(x["layer"] == at.UNATTRIBUTED for x in ledger)
    out = rd.reverse(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="Never deployed",
                     installation_id=PC_A, ledger_events=ledger)
    assert out["events"] == [] and [n["execution_id"] for n in out["needs_reattribution"]] == ["e1"]


def test_scenario_10_a_re_attribution_that_still_credits_it_is_refused_and_runs_of_others_are_left_alone():
    rows, oid = _verified()
    opt = rd.for_attribution(rows, after=measured(6))
    a = at.attribute(run(), measured(10), [opt], local_avoided=estimated(9))
    other = at.attribute(run("e9"), measured(10), [], local_avoided=estimated(3))["layers"]
    with pytest.raises(ValueError, match="still credits"):
        rd.reverse(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="Never deployed",
                   installation_id=PC_A, ledger_events=a["layers"], reattributed=[a])
    out = rd.reverse(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="Never deployed",
                     installation_id=PC_A, ledger_events=a["layers"] + other)
    assert {e["execution_id"] for e in out["events"]} == {"e1"}


def test_scenario_10_a_revert_is_not_a_reversal_runs_before_it_keep_their_accruals():
    rows, _ = _verified()
    rows = step(rows, rd.REVERTED, at_="2026-11-15T00:00:00Z", reason="Outputs changed")
    opt = rd.for_attribution(rows, after=measured(6))
    assert [o["optimization_id"] for o in at.in_effect([opt], "2026-11-10T12:00:00Z")] == [opt["optimization_id"]]
    assert at.in_effect([opt], "2026-11-16T00:00:00Z") == []
    assert rd.accrues(rows, "2026-11-10T12:00:00Z") and not rd.accrues(rows, "2026-11-16T00:00:00Z")


def test_scenario_10_a_reversal_is_refused_before_anything_was_applied_or_on_a_stale_version():
    rows = walk(rd.ACCEPTED)
    with pytest.raises(rd.TransitionRefused):
        rd.reverse(rows, based_on_version=2, at="2026-11-20T00:00:00Z", reason="x", installation_id=PC_A,
                   ledger_events=[])
    rows, _ = _verified()
    with pytest.raises(rd.VersionConflict):
        rd.reverse(rows, based_on_version=4, at="2026-11-20T00:00:00Z", reason="x", installation_id=PC_A,
                   ledger_events=[])
    with pytest.raises(ValueError, match="reason"):
        rd.reverse(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="", installation_id=PC_A,
                   ledger_events=[])


# ---------- scenario 11: retirement

def test_scenario_11_retirement_stops_accruals_and_the_recurring_projection():
    rows, oid = _verified()
    results = [at.attribute(run(f"e{d}", d), measured(10), [rd.for_attribution(rows, after=measured(6))],
                            local_avoided=estimated(5)) for d in (10, 11, 12, 13)]
    now = "2026-11-25T00:00:00Z"
    live = rd.project(rows, results, workload_key="nb:sales", baseline_version="b1", now=now)
    assert live["state"] == "projected" and live["projection"]["classification"] == ms.ESTIMATED
    out = rd.retire(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="The notebook was deleted",
                    installation_id=PC_A)
    rows = rows + [out["record"]]
    assert out["record"]["state"] == rd.RETIRED and out["record"]["retired_at"] == out["record"]["at"]
    stopped = rd.project(rows, results, workload_key="nb:sales", baseline_version="b1", now=now)
    assert stopped["state"] == "stopped" and stopped["projection"]["value"] is None
    assert "retired" in stopped["reason"]
    # Runs before the retirement keep counting; nothing accrues from then on.
    assert rd.accrues(rows, "2026-11-12T12:00:00Z") and not rd.accrues(rows, "2026-11-21T00:00:00Z")
    opt = rd.for_attribution(rows, after=measured(6))
    assert opt["reverted_at"] is not None and at.in_effect([opt], "2026-11-21T00:00:00Z") == []
    assert at.in_effect([opt], "2026-11-12T12:00:00Z") == [opt]
    # Retired is final, except for a reversal of a change that never counted.
    assert rd.TRANSITIONS[rd.RETIRED] == {rd.REVERSED}
    with pytest.raises(rd.TransitionRefused):
        rd.transition(rows, change(rows, rd.MONITORING))
    with pytest.raises(ValueError, match="from the record"):
        rd.project(rows, results, workload_key="nb:sales", baseline_version="b1", now=now, retired_at=None)


def test_scenario_11_a_proposal_never_applied_can_be_retired_and_never_accrued():
    rows = walk(rd.RETIRED)
    assert not rd.accrues(rows, "2026-11-03T00:00:00Z")
    assert at.in_effect([rd.for_attribution(rows, after=measured(6))], "2026-11-03T00:00:00Z") == []


# ---------- never code text

@pytest.mark.parametrize("ref", ["SELECT * FROM sales", "df = spark.read.table('x')\n", "a" * 129, "def:x\n",
                                 "print('hi')", 42])
def test_code_text_as_a_reference_is_refused(ref):
    rows = walk(rd.ACCEPTED)
    with pytest.raises(ValueError, match="never code"):
        rd.transition(rows, change(rows, rd.APPLIED, optimized_ref=ref))
    rb = dict(ROLLBACK, restore_ref=ref)
    with pytest.raises(ValueError, match="never code"):
        rd.transition(rows, change(rows, rd.APPLIED, rollback=rb))
    p = proposal()
    p["record"]["original_ref"] = ref
    with pytest.raises(ValueError, match="never code"):
        rd.create(p, installation_id=PC_A)


@pytest.mark.parametrize("key", ["code", "definition", "content", "notebook_content", "sql", "diff"])
def test_a_field_that_would_hold_code_is_refused_anywhere(key):
    rows = walk(rd.ACCEPTED)
    with pytest.raises(ValueError, match="code"):
        rd.transition(rows, change(rows, rd.APPLIED, **{key: "x = 1"}))
    with pytest.raises(ValueError, match="code"):
        rd.transition(rows, change(rows, rd.APPLIED, rollback=dict(ROLLBACK, **{key: "x = 1"})))
    p = proposal()
    p["links"][0][key] = "x = 1"
    with pytest.raises(ValueError, match="code"):
        rd.create(p, installation_id=PC_A)


def test_a_block_of_text_as_a_reason_or_note_is_refused_and_rollback_targets_are_ids():
    rows = walk(rd.ACCEPTED)
    with pytest.raises(ValueError, match="one line"):
        rd.transition(rows, change(rows, rd.DISMISSED, reason="for x in y:\n    run(x)"))
    with pytest.raises(ValueError, match="one line"):
        rd.transition(rows, change(rows, rd.APPLIED, rollback=dict(ROLLBACK, note="a\nb")))
    with pytest.raises(ValueError, match="at most 500"):
        rd.transition(rows, change(rows, rd.DISMISSED, reason="x" * 501))
    with pytest.raises(ValueError, match="never names or code"):
        rd.transition(rows, change(rows, rd.APPLIED, rollback=dict(ROLLBACK, targets=["Sales notebook"])))
    with pytest.raises(ValueError, match="method"):
        rd.transition(rows, change(rows, rd.APPLIED, rollback=dict(ROLLBACK, method="delete")))


def test_reasons_and_notes_go_through_the_repos_redaction():
    rows = walk(rd.ACCEPTED)
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"
    out = rd.transition(rows, change(rows, rd.APPLIED, rollback=dict(ROLLBACK, note=f"Bearer {token}")))
    assert token not in out["record"]["rollback"] and "[removed]" in json.loads(out["record"]["rollback"])["note"]
    rows = rows + [out["record"]]
    out = rd.transition(rows, change(rows, rd.REVERTED, reason="Asked by ann@contoso.com, key sig=abc123"))
    reason = out["record"]["reason"]
    assert "ann@contoso.com" not in reason and "[email]" in reason and "abc123" not in reason


# ---------- the device, never a user (D3)

IDENTITY = ("user", "user_id", "email", "upn", "oid", "tenant_id", "userPrincipalName", "Email", "owner_oid")


def _keys_of(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(str(k))
            out |= _keys_of(v)
    elif isinstance(value, list):
        for v in value:
            out |= _keys_of(v)
    return out


@pytest.mark.parametrize("key", IDENTITY)
def test_no_user_email_upn_or_oid_is_accepted(key):
    rows = walk(rd.ACCEPTED)
    with pytest.raises(ValueError, match="D3"):
        rd.transition(rows, change(rows, rd.APPLIED, **{key: "ann@contoso.com"}))
    with pytest.raises(ValueError, match="D3"):
        rd.transition(rows, change(rows, rd.APPLIED, rollback=dict(ROLLBACK, **{key: "x"})))
    p = proposal()
    p["record"][key] = "ann@contoso.com"
    with pytest.raises(ValueError, match="D3"):
        rd.create(p, installation_id=PC_A)
    with pytest.raises(ValueError, match="D3"):
        rd.history(rows[:-1] + [dict(rows[-1], **{key: "x"})])


def test_no_user_email_upn_or_oid_is_emitted():
    rows, _ = _verified()
    opt = rd.for_attribution(rows, after=measured(6))
    ledger = at.attribute(run(), measured(10), [opt], local_avoided=estimated(9))["layers"]
    rev = rd.reverse(rows, based_on_version=5, at="2026-11-20T00:00:00Z", reason="Never deployed",
                     installation_id=PC_A, ledger_events=ledger)
    retired = rd.retire(walk(rd.ACCEPTED), based_on_version=2, at="2026-11-20T00:00:00Z", reason="Gone",
                        installation_id=PC_A)
    emitted = [rd.create(proposal(), installation_id=PC_A), rev, retired, rd.rollback_plan(rows), opt, rows,
               list(rd.ROW_COLUMNS)]
    keys = set().union(*(_keys_of(x) for x in emitted)) | set(rd.ROW_COLUMNS)
    pattern = re.compile(r".*(user|email|upn|oid|tenant|principal).*", re.IGNORECASE)
    assert not [k for k in keys if pattern.fullmatch(k)]
    assert "installation_id" in keys  # the device is there


# ---------- property: any sequence of valid updates has strictly increasing versions and one head

def test_any_sequence_of_valid_updates_gives_increasing_versions_and_a_single_head():
    for seed in (7, 11, 2026):
        rng = random.Random(seed)
        for case in range(60):
            rows = [rd.create(lk.from_finding(finding(), at=AT, original_ref=rng.choice([None, "def:a1"])),
                              installation_id=PC_A)["record"]]
            for _ in range(rng.randint(0, 12)):
                h = rd.head(rows)
                nxt = sorted(rd.TRANSITIONS[h["state"]])
                if not nxt:
                    break
                device = rng.choice([PC_A, PC_B])
                if rng.random() < 0.2 and h["version"] > 1:  # a stale change is refused, never merged
                    stale = change(rows[:rng.randint(1, h["version"] - 1)], rng.choice(rd.STATES), device=device)
                    stale["based_on_version"] = rng.randint(1, h["version"] - 1)
                    stale["at"] = "2026-12-31T00:00:00Z"
                    with pytest.raises((rd.VersionConflict, rd.TransitionRefused)):
                        rd.transition(rows, stale)
                    assert rd.head(rows) == h
                    continue
                rows = step(rows, rng.choice(nxt), device=device,
                            at_=rng.choice([None, "2026-10-01T00:00:00Z"]))  # a clock that is behind, too
            versions = [r["version"] for r in rows]
            msg = f"seed {seed} case {case}: {[(r['version'], r['state']) for r in rows]}"
            assert versions == list(range(1, len(rows) + 1)), msg
            times = [ms.parse_time(r["at"]) for r in rows]
            assert all(a < b for a, b in zip(times, times[1:], strict=False)), msg  # type: ignore[operator]
            shuffled = rows + rng.sample(rows, k=min(2, len(rows)))
            rng.shuffle(shuffled)
            hist = rd.history(shuffled)
            assert hist == rows, msg
            heads = [r for r in hist if r["version"] == max(versions)]
            assert len(heads) == 1 and rd.head(shuffled) == rows[-1], msg
            assert len({r["record_id"] for r in rows}) == len(rows), msg
