"""Mutation tests for savings/links.py (batch 456): the input checks, which stored row is the latest,
and matching an opportunity to a finding record."""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import controls as ct
from fabric_desktop.excellence import findings as fx
from fabric_desktop.opportunities import model as om
from fabric_desktop.savings import links as lk

AT = "2026-11-20T09:00:00Z"
TEMPLATE = {"why": "w", "solution": "s", "steps": ["one"], "benefit": "b", "effort": "low", "risk": "low",
            "validation": "v", "rollback": "r"}
ON_NB2 = {"type": "item", "id": "nb-2", "name": "Orders", "workspace_id": "ws-1", "item_type": "Notebook"}
MATCHES = {"FEA-PERF-001": ["OE-DUP-NB"]}


def finding(version: str = "1.0.0", subj: dict | None = None) -> dict:
    c = ct.Control(id="FEA-PERF-001", version=version, dimension="performance", title="Full refresh",
                   severity="high", requirement="microsoft_recommendation", scope="item",
                   evidence_kinds=("item.runs",), evaluate=lambda ctx: {}, remediation=TEMPLATE)
    subj = subj or {"type": "item", "id": "nb-1", "name": "Sales", "workspace_id": "ws-1", "item_type": "Notebook"}
    out = {"outcome": "fail", "subject": subj, "text": "t", "evidence_ids": ["e1"], "basis": "measured"}
    return fx.make(c, out, "high")


def opportunity() -> dict:
    wl = {"workspace_id": "ws-1", "item_id": "nb-2", "type": "Notebook", "name": "Orders"}
    return om.make(rule="OE-DUP-NB", category="duplicate_processing", action="eliminate", title="Duplicate",
                   artifacts=[om.artifact(wl, "remove"), om.artifact(wl | {"item_id": "nb-3"}, "keep")],
                   current="c", problem="p", evidence=[om.evidence("code", "same", "definition")],
                   recommendation="r", alternatives=[], effort="low", risk="low", confidence="proven",
                   validation=["compare"], rollback="r", source="analyzer", next_step={"kind": "open"})


def test_an_opportunity_that_isnt_a_dict_is_refused_with_a_value_error():
    with pytest.raises(ValueError, match="opportunity record"):
        lk.from_opportunity(["opp-1"], at=AT)  # type: ignore[arg-type]


def test_an_existing_link_that_isnt_a_dict_is_refused_with_a_value_error():
    with pytest.raises(ValueError, match="optimization_link table"):
        lk.link([], [], existing_links=["not a row"])  # type: ignore[list-item]


def test_items_leave_out_links_without_an_artifact():
    assert lk._items([{"artifact_key": None, "workspace_id": "w"}, {"artifact_key": "w/i"}, "x"]) == {"w/i"}


def test_a_workspace_finding_doesnt_match_a_record_whose_links_have_no_artifact():
    opp = lk.from_opportunity(opportunity(), at=AT)
    row = opp["record"] | {"rule_id": "OE-DUP-NB"}
    ws_link = {"optimization_id": opp["optimization_id"], "artifact_key": None, "workspace_id": "ws-1"}
    f = lk.from_finding(finding(subj={"type": "workspace", "id": "ws-1", "name": "W"}), at=AT)
    out = lk.link([row], [f], matches=MATCHES, existing_links=[ws_link])
    assert out["new"] == [f] and out["linked"] == []


def test_a_detection_without_a_rule_id_is_new_and_matches_nothing():
    opp = lk.from_opportunity(opportunity(), at=AT)
    f = lk.from_finding(finding(subj=ON_NB2), at=AT)
    del f["rule_id"]
    out = lk.link([opp], [f], matches=MATCHES)
    assert out["new"] == [f] and out["linked"] == []


def test_an_opportunity_doesnt_match_a_finding_record_on_another_item():
    f = lk.from_finding(finding(), at=AT)  # nb-1, not an item of the opportunity
    opp = lk.from_opportunity(opportunity(), at=AT)
    out = lk.link([f], [opp], matches=MATCHES)
    assert out["new"] == [opp] and out["linked"] == []


def test_a_matched_link_says_the_rule_version_wasnt_checked_and_it_didnt_come_back():
    f = lk.from_finding(finding(subj=ON_NB2), at=AT)
    opp = lk.from_opportunity(opportunity(), at=AT)
    (ev,) = lk.link([f], [opp], matches=MATCHES)["linked"]
    assert ev["matched"] is True and ev["rule_version_checked"] is False and ev["came_back"] is False


def _rows(*specs: dict) -> tuple[dict, list[dict]]:
    p = lk.from_finding(finding(), at=AT)
    return p, [p["record"] | s for s in specs]


def _state(rows: list[dict], p: dict) -> str:
    (ev,) = lk.link(rows, [p])["linked"]
    return ev["existing_state"]


def test_the_latest_row_is_the_highest_version_whatever_its_time():
    p, rows = _rows({"version": 2, "at": "2026-01-01T00:00:00Z", "state": "validating"},
                    {"version": 1, "at": "2026-02-01T00:00:00Z", "state": "proposed"})
    assert _state(rows, p) == "validating"


def test_a_row_without_a_version_counts_as_version_0():
    p, rows = _rows({"version": None, "at": "2026-02-01T00:00:00Z", "state": "regressed"},
                    {"version": 1, "at": "2026-01-01T00:00:00Z", "state": "validating"})
    assert _state(rows, p) == "validating"


def test_on_the_same_version_the_later_row_is_the_latest():
    p, rows = _rows({"version": 1, "at": "2026-01-01T00:00:00Z", "record_id": "z", "state": "regressed"},
                    {"version": 1, "at": "2026-02-01T00:00:00Z", "record_id": "a", "state": "validating"})
    assert _state(rows, p) == "validating"


def test_on_the_same_version_and_time_the_higher_record_id_is_the_latest():
    p, rows = _rows({"version": 1, "at": AT, "record_id": "a", "state": "regressed"},
                    {"version": 1, "at": AT, "record_id": "b", "state": "validating"})
    assert _state(rows, p) == "validating"


def _stored_rule_version(*specs: dict) -> str:
    p = lk.from_finding(finding("9.0.0"), at=AT)
    rows = [p["record"] | {"rule_id": "FEA-PERF-001"} | s for s in specs]
    (ev,) = lk.link(rows, [p])["review"]
    return ev["stored_rule_version"]


def test_the_stored_rule_version_comes_from_the_highest_version_row_that_names_one():
    assert _stored_rule_version({"version": 2, "at": "2026-01-01T00:00:00Z", "rule_version": "2.0.0"},
                                {"version": 1, "at": "2026-02-01T00:00:00Z", "rule_version": "1.0.0"}) == "2.0.0"


def test_a_rule_version_row_without_a_version_counts_as_version_0():
    assert _stored_rule_version({"version": None, "at": "2026-02-01T00:00:00Z", "rule_version": "1.0.0"},
                                {"version": 1, "at": "2026-01-01T00:00:00Z", "rule_version": "2.0.0"}) == "2.0.0"


def test_on_the_same_version_the_later_rule_version_row_wins():
    assert _stored_rule_version({"version": 1, "at": "2026-01-01T00:00:00Z", "rule_version": "1.0.0"},
                                {"version": 1, "at": "2026-02-01T00:00:00Z", "rule_version": "2.0.0"}) == "2.0.0"
