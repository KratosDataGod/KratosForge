"""Mutation batch 117: excellence/adapters.py (docs/quality/mutation-testing.md). The survivors were the
fallbacks for values Fabric leaves out (a workspace's name, an item's or a capacity's fields, a role's
principal) and the rule that a listed item needs both an id and a type."""

from __future__ import annotations

from fabric_desktop.excellence import adapters

AT = "2026-11-18T02:00:00+00:00"


def test_a_workspace_without_a_display_name_is_named_by_its_name():
    recs = adapters.workspace({"id": "w1", "name": "Sales"}, None, collected_at=AT)
    assert recs[0]["subject"]["name"] == "Sales"
    recs = adapters.workspace({"id": "w1", "displayName": "Shown", "name": "Sales"}, None, collected_at=AT)
    assert recs[0]["subject"]["name"] == "Shown"


def test_a_workspace_without_an_id_is_refused():
    try:
        adapters.workspace({"displayName": "x"}, None, collected_at=AT)
    except ValueError as exc:
        assert "id" in str(exc)
    else:
        raise AssertionError("a workspace without an id gave evidence")


def test_a_missing_description_is_empty_and_a_given_one_is_kept():
    info = adapters.workspace({"id": "w1"}, None, collected_at=AT)[0]
    assert info["payload"] == {"description": "", "capacity_id": None}
    info = adapters.workspace({"id": "w1", "description": "Reports"}, None, collected_at=AT)[0]
    assert info["payload"]["description"] == "Reports"


def test_an_item_needs_an_id_and_a_type_and_a_missing_name_is_empty():
    items = [{"id": "a", "type": "Notebook"}, {"id": "b"}, {"type": "Report"},
             {"id": "c", "type": "Report", "displayName": "Sales"}]
    inv = adapters.workspace({"id": "w1"}, items, collected_at=AT, info_read=False)
    assert len(inv) == 1
    assert inv[0]["payload"]["items"] == [{"id": "a", "type": "Notebook", "name": ""},
                                          {"id": "c", "type": "Report", "name": "Sales"}]


def test_a_principal_with_missing_fields_reads_as_empty_text():
    assert adapters.principal({}) == {"id": "", "type": "", "role": "", "name": "", "guest": False}
    p = adapters.principal({"role": "Admin", "principal": {"id": "p1", "type": "Group"}})
    assert (p["id"], p["role"], p["name"]) == ("p1", "Admin", "p1")


def test_a_capacity_with_missing_fields_reads_as_empty_text():
    rec = adapters.capacity("CAP", [{"id": "cap"}], collected_at=AT)[0]
    assert rec["payload"] == {"state": "", "sku": "", "region": ""}
    rec = adapters.capacity("cap", [{"id": "CAP", "state": "Active", "sku": "F2", "region": "West Europe"}],
                            collected_at=AT)[0]
    assert rec["payload"] == {"state": "Active", "sku": "F2", "region": "West Europe"}


def test_an_unread_peer_without_a_name_is_named_by_its_id():
    workloads = [{"item_id": "a", "type": "Notebook", "workspace_id": "w1", "definition": "read", "name": "A"},
                 {"item_id": "b", "type": "Notebook", "workspace_id": "w1", "definition": "not_read"},
                 {"item_id": "c", "type": "Notebook", "workspace_id": "w1", "definition": "not_read", "name": "C"}]
    recs = {r["subject"]["id"]: r for r in adapters.optimization({"workloads": workloads}, collected_at=AT)}
    assert recs["a"]["payload"]["peers_not_read"] == ["C", "b"]
    assert recs["b"]["payload"]["peers_not_read"] == ["C"]
