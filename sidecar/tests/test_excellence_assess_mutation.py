"""excellence/assess.py's snapshot, read directly (mutation testing, batch 339).

test_excellence.py runs the assessment end to end; these pin the counts by severity, the defaults, items
the inventory lists badly, why an item wasn't inspected, each workspace's own score, and compare's deltas.
"""

from __future__ import annotations

from fabric_desktop.excellence import assess, profiles as pf
from tests.test_excellence import AT, estate, nb, rec, roles

SCOPE = {"workspaces": ["ws-1"], "capacities": ["cap-1"]}


def overridden(**severity: str) -> pf.Profile:
    return pf.Profile("custom", "1.0.0", "Custom", dict(pf.get().weights),
                      severity_overrides={k.replace("_", "-"): v for k, v in severity.items()})


def test_the_counts_are_by_severity_of_the_scored_findings():
    r, records = estate()
    snap = assess.assess(records, r, scope=SCOPE, assessed_at=AT)
    assert snap["counts"] == {"findings": 2, "advisory": 0, "critical": 0, "high": 1}
    snap = assess.assess(records, r, scope=SCOPE, assessed_at=AT, profile=overridden(FEA_SEC_001="critical"))
    assert snap["counts"] == {"findings": 2, "advisory": 0, "critical": 1, "high": 0}
    snap = assess.assess(records, r, scope=SCOPE, assessed_at=AT, profile=overridden(FEA_DE_001="high"))
    assert snap["counts"] == {"findings": 2, "advisory": 0, "critical": 0, "high": 2}


def test_a_snapshot_is_not_a_fixture_by_default_and_its_ids_are_sixteen_characters():
    r, records = estate()
    snap = assess.assess(records, r, scope=SCOPE, assessed_at=AT)
    assert snap["fixture"] is False and snap["fixture_note"] is None
    assert len(snap["policy_version"]) == 16 and len(snap["fingerprint"]) == 16


def test_inventory_entries_without_an_id_and_a_type_are_skipped():
    r, _ = estate()
    bad = rec("workspace.inventory", payload={"items": ["n9", {"id": "x"}, {"type": "Notebook"},
                                                       {"id": "n1", "type": "Notebook", "name": "n1"}]},
              source="GET /v1/workspaces/{id}/items")
    snap = assess.assess([bad, nb("n1", "merge into")], r, scope={"workspaces": ["ws-1"]}, assessed_at=AT)
    assert [c["item"]["id"] for c in snap["item_coverage"]] == ["n1"]


def test_an_item_not_inspected_says_why():
    r, records = estate()
    snap = assess.assess(records, r, scope=SCOPE, assessed_at=AT)
    n3 = [c for c in snap["item_coverage"] if c["item"]["id"] == "n3"][0]
    text = [o["text"] for o in snap["outcomes"] if o["subject"]["id"] == "n3"]
    assert n3["state"] == "not_inspected" and n3["reason"] == text[0] and text[0]
    off = pf.Profile("custom", "1.0.0", "Custom", dict(pf.get().weights), disabled={"FEA-DE-001": "By design."})
    snap = assess.assess(records, r, scope=SCOPE, assessed_at=AT, profile=off)
    n3 = [c for c in snap["item_coverage"] if c["item"]["id"] == "n3"][0]
    assert (n3["state"], n3["reason"]) == ("not_inspected", "No control applies to it. Turned off in the Custom profile: By design.")


WS2 = {"type": "workspace", "id": "ws-2", "name": "Ops"}


def test_a_workspace_scores_its_own_controls_and_items_only():
    r, records = estate()
    other = [rec("workspace.roles", WS2, payload=roles(1))]
    both = assess.assess(records + other, r, scope={"workspaces": ["ws-1", "ws-2"]}, assessed_at=AT)
    alone1 = assess.assess(records, r, scope={"workspaces": ["ws-1"]}, assessed_at=AT)
    alone2 = assess.assess(other, r, scope={"workspaces": ["ws-2"]}, assessed_at=AT)
    assert both["workspace_scores"]["ws-1"] == alone1["workspace_scores"]["ws-1"]
    assert both["workspace_scores"]["ws-2"] == alone2["workspace_scores"]["ws-2"]
    fixed = [x for x in records if x["kind"] != "notebook.code"] + [nb("n1", "merge into"), nb("n2", "merge into")]
    better = assess.assess(fixed, r, scope={"workspaces": ["ws-1"]}, assessed_at=AT)
    assert better["workspace_scores"]["ws-1"]["score"] > alone1["workspace_scores"]["ws-1"]["score"]


def snapshot(score, assured, coverage, at, findings=()):
    return {"scores": {"overall": {"score": score, "assured": assured, "coverage": coverage},
                       "dimensions": {"security": {"score": score, "assured": assured}}},
            "findings": [{"id": f, "advisory": False} for f in findings], "policy_version": "p", "assessed_at": at}


def test_compare_gives_each_change_to_a_tenth_and_none_where_one_side_has_none():
    out = assess.compare(snapshot(50.0, None, 40.0, "a"), snapshot(62.4, 70.0, 45.55, "b"))
    assert (out["score"], out["assured"], out["coverage"]) == (12.4, None, 5.5)
    assert out["dimensions"] == {"security": {"score": 12.4, "assured": None}}
    assert assess.compare(snapshot(None, 1.0, 1.0, "a"), snapshot(3.0, 1.0, 1.0, "b"))["score"] is None
