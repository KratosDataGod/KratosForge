"""Mutation batch 359: catalog_architecture.py's evidence fields and exact control texts, which
tests/test_excellence_architecture.py checks through whole assessments."""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import catalog_architecture as ca
from fabric_desktop.excellence import controls as ct

AT = "2026-11-18T02:00:00+00:00"


def naming_ctx(ws_name, items):
    return ct.Context({"id": "w"}, {"workspace.naming": ca.naming({"id": "W1", "displayName": ws_name}, items,
                                                                  collected_at=AT)})


def lh(i, name):
    return {"id": f"L{i}", "type": "Lakehouse", "displayName": name}


def test_the_workspace_subject_uses_its_id_and_display_name_or_name():
    assert ca._ws_subject({"id": "W1", "displayName": "Sales"}) == {"type": "workspace", "id": "W1", "name": "Sales"}
    assert ca._ws_subject({"id": "W1", "name": "Sales"})["name"] == "Sales"


def test_naming_skips_an_item_without_a_type():
    rec = ca.naming({"id": "W1", "displayName": "Prod"}, [{"id": "x", "displayName": "tmp"}, lh(1, "old copy")],
                    collected_at=AT)[0]
    assert [x["id"] for x in rec["payload"]["leftovers"]] == ["L1"]


def test_lakehouse_properties_name_and_default_schema():
    got = ca.lakehouse_properties("W1", [{"id": "L1", "displayName": "Mine"}],
                                  {"L1": {"displayName": "Theirs", "properties": {"defaultSchema": "sales"}}},
                                  collected_at=AT)[0]
    assert got["subject"]["name"] == "Mine"
    assert got["payload"] == {"schemas_enabled": True, "default_schema": "sales"}


def test_bindings_are_only_for_notebooks():
    opt = {"workloads": [{"type": "Pipeline", "definition": "read", "item_id": "P1"},
                         {"type": "Notebook", "definition": "other", "item_id": "N0"},
                         {"type": "Notebook", "definition": "read", "item_id": "N1", "default_lakehouse": "L1"}]}
    assert [r["subject"]["id"] for r in ca.notebook_bindings(opt, collected_at=AT)] == ["N1"]


def test_evidence_reads_properties_only_for_lakehouses():
    items = [{"id": "N1", "type": "Notebook", "displayName": "nb"}, lh(1, "LH")]
    read = {"N1": {"properties": {}}, "L1": {"properties": {}}}
    got = ca.evidence({"id": "W1", "displayName": "Prod"}, items, {}, read, collected_at=AT)
    assert [r["subject"]["id"] for r in got if r["kind"] == "lakehouse.properties"] == ["L1"]


def test_names_lists_five_and_counts_the_rest():
    rows = [{"name": f"n{i}"} for i in range(6)]
    assert ca._names(rows) == "n0, n1, n2, n3, n4 and 1 more"
    assert ca._names(rows[:5]) == "n0, n1, n2, n3, n4"
    assert ca._names([{"id": "x"}, {"name": "y"}]) == "x, y"


def test_medallion_with_one_unnamed_lakehouse_says_name_once():
    r = ca._medallion(naming_ctx("Prod", [lh(1, "data")]))
    assert r["text"] == "None of the 1 lakehouse name says bronze, silver or gold, so their layers can't be told."


def test_medallion_names_one_mixed_lakehouse_and_split_layers():
    one = ca._medallion(naming_ctx("Prod", [lh(1, "bronze silver")]))
    assert one["text"] == "bronze silver names more than one layer."
    two = ca._medallion(naming_ctx("Prod", [lh(1, "bronze silver"), lh(2, "gold silver")]))
    assert two["text"] == "bronze silver, gold silver name more than one layer."
    split = ca._medallion(naming_ctx("Prod", [lh(1, "gold a"), lh(2, "gold b")]))
    assert split["text"] == "2 lakehouses are named gold (gold a, gold b)."
    assert split["detail"] == {"mixed": 0, "split": {"gold": 2}}


def test_schemas_names_the_default_schema_or_dbo():
    def run(payload):
        return ca._schemas(ct.Context({"id": "l"}, {"lakehouse.properties": [{"id": "e", "payload": payload}]}))
    assert run({"schemas_enabled": True, "default_schema": "sales"})["text"] == "Schema-enabled (default schema sales)."
    assert run({"schemas_enabled": True})["text"] == "Schema-enabled (default schema dbo)."


def test_leftovers_not_evaluated_texts():
    both = ca._leftovers(naming_ctx("Prod Dev", []))
    assert both["text"] == "The workspace name doesn't say whether this is production (it says both: prod, dev)."
    none = ca._leftovers(naming_ctx("Sales", []))
    assert none["text"] == "The workspace name doesn't say whether this is production."


def test_one_leftover_has_a_temporary_name():
    r = ca._leftovers(naming_ctx("Prod", [lh(1, "old")]))
    assert r["text"] == "1 item in this production workspace has a temporary or test name: old."
    two = ca._leftovers(naming_ctx("Prod", [lh(1, "old"), lh(2, "tmp")]))
    assert two["text"].startswith("2 items in this production workspace have a temporary or test name: ")


def test_bound_without_a_binding_or_its_field_is_unknown():
    assert ca._bound(ct.Context({"id": "n"}, {"item.binding": []})) is None
    assert ca._bound(ct.Context({"id": "n"}, {"item.binding": [{"id": "b", "payload": {}}]})) is None
    assert ca._bound(ct.Context({"id": "n"}, {"item.binding": [{"id": "b", "payload": {"default_lakehouse": "L"}}]}))


def test_binding_needs_both_records():
    only_binding = ct.Context({"id": "n"}, {"item.binding": [{"id": "b", "payload": {"default_lakehouse": "L"}}],
                                            "workspace.inventory": []})
    with pytest.raises(AssertionError):
        ca._binding(only_binding)
