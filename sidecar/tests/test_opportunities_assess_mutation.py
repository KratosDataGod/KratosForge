"""Mutation-testing follow-up for opportunities/assess.py (docs/quality/mutation-testing.md, batch 44).

Totals once per workload: the first opportunity of equal strength keeps a workload, overlaps name
only the other opportunities that share one, the CU value is given only when the CU total is
complete, and coverage and counts add up item by item."""

from __future__ import annotations

import datetime as dt

from fabric_desktop.opportunities import assess

NOW = dt.datetime(2026, 11, 15, 12, tzinfo=dt.timezone.utc)
USAGE = {("sales", "load"): {"cu_seconds": 3600.0, "start": NOW - dt.timedelta(days=2), "end": NOW,
                             "rows": 1, "files": {"a.csv"}}}
PRICE = {"price_per_cu_hour": 0.5, "currency": "EUR", "source": "list price"}


def _w(item_id, name="Load", *, type_="Notebook", definition="read", read=True, problem=None):
    return {"item_id": item_id, "workspace_id": "w1", "workspace_name": "Sales", "name": name, "type": type_,
            "definition": definition, "problem": problem,
            "runs": {"read": read, "seconds_total": 3600.0, "days": 10, "runs": 5, "scheduled": 4}}


def _o(id_, affects, action="eliminate", confidence="high", *, category="duplicate", part=False):
    impact = ({"basis": "part of the workload", "run_seconds_per_month": {"value": None, "reason": "not yet"}}
              if part else {"basis": "whole workload", "run_seconds_per_month": {"value": 10.0}})
    return {"id": id_, "affects": list(affects), "action": action, "confidence": confidence, "effort": "low",
            "risk": "low", "category": category, "title": id_, "next_step": {"kind": "open"}, "impact": impact}


# ---- combine ----------------------------------------------------------------------------------------

def test_of_two_equally_strong_opportunities_the_first_keeps_the_workload():
    a, b = _o("a", ["i1"]), _o("b", ["i1"])
    assess.combine([a, b], [_w("i1")], {}, NOW, None)
    assert (a["counted"], b["counted"]) == (True, False)
    assert a["overlaps"] == ["b"] and b["overlaps"] == ["a"]


def test_overlaps_name_only_other_opportunities_that_share_a_workload():
    a, b, c = _o("a", ["i1"]), _o("b", ["i1", "i2"], action="consolidate"), _o("c", ["i3"])
    assess.combine([a, b, c], [_w("i1"), _w("i2"), _w("i3")], {}, NOW, None)
    assert a["overlaps"] == ["b"] and b["overlaps"] == ["a"] and c["overlaps"] == []
    assert (a["counted"], b["counted"], c["counted"]) == (True, False, True)


def test_the_cu_value_is_given_only_when_the_cu_total_is_complete():
    known = assess.combine([_o("a", ["i1"])], [_w("i1")], USAGE, NOW, PRICE)
    assert known["cu_per_month"]["complete"] is True
    assert known["cu_value_per_month"]["value"] == 3600.0 / 2 * 30 / 3600 * 0.5
    unknown = assess.combine([_o("a", ["i1"])], [_w("i1")], {}, NOW, PRICE)
    assert unknown["cu_value_per_month"]["value"] is None
    assert unknown["cu_value_per_month"]["reason"] == "Part of the CU figure isn't known, so no value is given."


# ---- coverage and counts ----------------------------------------------------------------------------

def test_coverage_counts_each_item_by_type_and_how_far_it_was_read():
    ws = [_w("i1"), _w("i2"), _w("i3", type_="DataPipeline", definition="not_read", problem="403"),
          _w("i4", type_="Report", definition="unsupported", read=False),
          _w("i5", type_="Report", definition="unsupported")]
    cov = assess._coverage(ws, 2)
    assert cov["by_type"] == {"DataPipeline": 1, "Notebook": 2, "Report": 2}
    assert cov["analyzed"] == 2
    assert cov["not_read"] == [{"item_id": "i3", "name": "Load", "type": "DataPipeline", "problem": "403"}]
    assert cov["not_analyzed_types"] == ["Report"]
    assert cov["history_not_read"] == 1 and cov["usage_items"] == 2


def test_count_adds_one_per_opportunity():
    opps = [_o("a", []), _o("b", []), _o("c", [], category="full_refresh")]
    assert assess._count(opps, "category") == {"duplicate": 2, "full_refresh": 1}
    assert assess._count([], "category") == {}


def test_counts_of_an_assessment_add_up(monkeypatch):
    found = [_o("a", ["i1"]), _o("b", ["i1"]), _o("c", ["i2"], confidence="medium", category="full_refresh")]
    monkeypatch.setattr(assess.analyzers, "ANALYZERS", [lambda workloads, ctx: [dict(o) for o in found]])
    monkeypatch.setattr(assess.inventory, "workloads", lambda *a, **k: [_w("i1"), _w("i2")])
    r = assess.assess([{"workspace": {"id": "w1"}, "items": []}], usage_imports=[], price=None, now=NOW)
    assert r["counts"] == {"opportunities": 3, "counted": 2,
                           "by_category": {"duplicate": 2, "full_refresh": 1},
                           "by_confidence": {"high": 2, "medium": 1}, "by_action": {"eliminate": 3}}
