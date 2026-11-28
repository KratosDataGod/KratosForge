"""Mutation batch 48: opportunities/share.py (docs/quality/mutation-testing.md).

The OE-004 tests run a whole assessment, where every opportunity has all of its fields. These
build small opportunities by hand to pin the gaps: the finding's detail text and what it leaves
out, which items count, when a run is partial, the run id's shape, and the JSON fields' layout
and redaction.
"""

from __future__ import annotations

import hashlib
import json

from fabric_desktop.opportunities import share

SECRET = "password=hunter2x"  # pragma: allowlist secret (a fake value to check redaction)


class _Odd:
    """A value json can't write itself, so it goes through default=str."""

    def __str__(self) -> str:
        return f"odd {SECRET}"


def _opp(**kw) -> dict:
    return {"id": "opp-1", "rule": "OE-DUP-NB", "action": "remove", "title": "Two copies",
            "artifacts": [{"workspace_id": "ws", "item_id": "nb1", "name": "Sales", "role": "keep"}]} | kw


def _rows(*opps: dict, **result) -> dict:
    return share.analysis_rows({"assessed_at": "2026-11-16T00:00:00Z", "opportunities": list(opps)} | result)


def _detail(**kw) -> str:
    return _rows(_opp(**kw))["findings"][0]["detail"]


def test_detail_names_each_item_by_name_or_id_with_its_role():
    arts = [{"workspace_id": "ws", "item_id": "nb1", "name": "Sales", "role": "keep"},
            {"workspace_id": "ws", "item_id": "nb2", "role": "remove"}]
    assert _detail(artifacts=arts, current="Two notebooks.", problem="Same code.") == \
        "Two notebooks. Same code. Items: Sales (keep), nb2 (remove)."


def test_detail_without_items_or_steps_is_just_the_text():
    assert share._detail({"current": "Now.", "problem": "Why."}) == "Now. Why."
    assert share._detail({}) == ""


def test_detail_names_overlaps_only_for_an_opportunity_not_counted():
    base = {"current": "Now."}
    assert share._detail(base | {"counted": False, "overlaps": ["opp-2", "opp-3"]}) == \
        "Now. Not counted in the totals: overlaps opp-2, opp-3."
    assert share._detail(base | {"counted": True, "overlaps": ["opp-2"]}) == "Now."
    assert share._detail(base | {"overlaps": ["opp-2"]}) == "Now."
    assert share._detail(base | {"counted": False, "overlaps": []}) == "Now."


def test_detail_lists_the_check_and_undo_steps_when_there_are_any():
    o = {"current": "Now.", "validation": ["Run it.", "Compare."], "rollback": ["Restore it."]}
    assert share._detail(o) == "Now. Check before you change it: Run it. Compare. How to undo it: Restore it."
    assert share._detail({"current": "Now.", "validation": [], "rollback": None}) == "Now."


def test_detail_is_redacted():
    assert SECRET not in _detail(current=f"Found {SECRET}.")
    assert "[removed]" in _detail(current=f"Found {SECRET}.")


def test_an_item_needs_both_a_workspace_and_an_item_id():
    arts = [{"workspace_id": "ws", "name": "no id"}, {"item_id": "nb9", "name": "no workspace"},
            {"workspace_id": "ws", "item_id": "nb1", "role": "keep"}]
    rows = _rows(_opp(artifacts=arts))
    assert [(a["workspace_id"], a["item_id"]) for a in rows["artifacts"]] == [("ws", "nb1")]
    only_partial = [{"workspace_id": "ws"}, {"item_id": "nb9"}]
    assert _rows(_opp(artifacts=only_partial))["findings"] == []


def test_a_run_is_partial_when_any_coverage_was_not_read():
    assert _rows()["run"]["status"] == "complete"
    assert _rows(coverage={"history_not_read": ["ws"]})["run"]["status"] == "partial"
    assert _rows(coverage={"not_read": ["ws"]})["run"]["status"] == "partial"
    assert _rows(coverage=None, scope={"budget": {"reached": True}})["run"]["status"] == "partial"


def test_the_run_scope_lists_the_workspaces_or_none():
    assert json.loads(_rows()["run"]["scope"])["workspaces"] == []
    scope = json.loads(_rows(workspaces=[{"id": "w2"}, {"id": "w1"}])["run"]["scope"])
    assert scope["workspaces"] == ["w2", "w1"]


def test_run_id_is_oe_and_sixteen_hex_digits_and_ignores_workspace_order():
    a = share.run_id({"assessed_at": "t", "workspaces": [{"id": "w1"}, {"id": "w2"}]})
    b = share.run_id({"assessed_at": "t", "workspaces": [{"id": "w2"}, {"id": "w1"}]})
    assert a == b and a.startswith("oe-") and len(a) == 3 + 16 and int(a[3:], 16) >= 0
    assert share.run_id({"assessed_at": "t"}) == share.run_id({"assessed_at": "t", "workspaces": None})
    assert share.run_id({"assessed_at": "t", "scope": {"a": 1, "b": 2}}) == \
        share.run_id({"assessed_at": "t", "scope": {"b": 2, "a": 1}})
    # The hash is of compact, key-sorted JSON, so a run id recorded by an earlier version still matches.
    raw = '["t",{"a":1,"b":2},null,[]]'
    assert share.run_id({"assessed_at": "t", "scope": {"b": 2, "a": 1}}) == \
        "oe-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def test_json_fields_are_compact_key_sorted_and_redacted():
    rows = _rows(_opp(evidence=[{"z": 1, "a": SECRET, "odd": _Odd()}], impact={"y": 2, "b": 1}))
    evidence = rows["findings"][0]["evidence"]
    assert evidence == '[{"a":"password=[removed]","odd":"odd password=[removed]","z":1}]'
    assert rows["recommendations"][0]["impact"] == '{"b":1,"y":2}'
