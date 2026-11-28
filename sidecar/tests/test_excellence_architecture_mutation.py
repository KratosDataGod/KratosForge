"""excellence/catalog_architecture.py's evidence and controls, read directly (mutation testing, batch 377).

test_excellence_architecture.py runs them through an assessment. These pin which items and notebooks make a
record, the names records carry, and the exact wording of each control: singular and plural, names (five shown,
the rest counted) and the default schema.
"""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import catalog_architecture as arch, controls as ct

AT = "2026-11-18T02:00:00+00:00"
WSID = "ws-1"


def naming_ctx(**payload) -> ct.Context:
    return ct.Context({"type": "workspace", "id": WSID}, {"workspace.naming": [{"id": "n1", "payload": payload}]})


def lh(i: str, *layers: str, name: str | None = None) -> dict:
    return {"id": i, "name": name if name is not None else i, "layers": list(layers)}


@pytest.mark.parametrize("n,text", [(1, "a0"), (5, "a0, a1, a2, a3, a4"), (6, "a0, a1, a2, a3, a4 and 1 more"),
                                    (7, "a0, a1, a2, a3, a4 and 2 more")])
def test_names_show_five_and_count_the_rest(n, text):
    assert arch._names([{"name": f"a{i}", "id": f"x{i}"} for i in range(n)]) == text


def test_a_row_without_a_name_is_named_by_its_id():
    assert arch._names([{"id": "x1"}, {"name": "", "id": "x2"}, {"name": "n3", "id": "x3"}]) == "x1, x2, n3"


def test_the_workspace_is_named_by_its_name_when_it_has_no_display_name():
    rec = arch.naming({"id": WSID, "name": "Sales"}, [], collected_at=AT)[0]
    assert rec["subject"]["name"] == "Sales"


def test_an_item_without_an_id_or_a_type_is_skipped():
    items = [{"id": "a", "displayName": "tmp lakehouse"}, {"type": "Lakehouse", "displayName": "old bronze"},
             {"id": "b", "type": "Notebook", "displayName": "nb copy"}]
    p = arch.naming({"id": WSID, "displayName": "Sales Prod"}, items, collected_at=AT)[0]["payload"]
    assert p["lakehouses"] == [] and [x["id"] for x in p["leftovers"]] == ["b"]


def test_only_lakehouse_items_are_read_as_lakehouses():
    items = [{"id": "nb1", "type": "Notebook", "displayName": "nb"}, {"type": "Lakehouse", "displayName": "x"},
             {"id": "lh1", "type": "Lakehouse"}]
    got = arch.evidence({"id": WSID}, items, {"workloads": []}, {"nb1": {"properties": {}}, "lh1": {
        "displayName": "Bronze", "properties": {"defaultSchema": "raw"}}}, collected_at=AT)
    props = [r for r in got if r["kind"] == "lakehouse.properties"]
    assert [r["subject"]["id"] for r in props] == ["lh1"]
    assert props[0]["subject"]["name"] == "Bronze"
    assert props[0]["payload"] == {"schemas_enabled": True, "default_schema": "raw"}


def test_only_notebooks_make_binding_records():
    opt = {"workloads": [{"item_id": "p1", "type": "DataPipeline", "definition": "read", "workspace_id": WSID},
                         {"item_id": "n1", "type": "Notebook", "definition": "missing", "workspace_id": WSID},
                         {"item_id": "n2", "type": "Notebook", "definition": "read", "workspace_id": WSID,
                          "default_lakehouse": "L"}]}
    got = arch.notebook_bindings(opt, collected_at=AT)
    assert [r["subject"]["id"] for r in got] == ["n2"]


@pytest.mark.parametrize("lakehouses,text", [
    ([], "None of the 0 lakehouse names says"), ([lh("a")], "None of the 1 lakehouse name says"),
    ([lh("a"), lh("b")], "None of the 2 lakehouse names says")])
def test_lakehouses_named_for_no_layer_are_counted(lakehouses, text):
    got = arch._medallion(naming_ctx(lakehouses=lakehouses))
    assert got["outcome"] == "not_evaluated"
    assert got["text"] == text + " bronze, silver or gold, so their layers can't be told."


def test_one_lakehouse_named_for_two_layers():
    got = arch._medallion(naming_ctx(lakehouses=[lh("a", "bronze", "silver"), lh("g", "gold")]))
    assert got["text"] == "a names more than one layer."
    assert got["detail"] == {"mixed": 1, "split": {}}


def test_two_lakehouses_named_for_two_layers_each():
    got = arch._medallion(naming_ctx(lakehouses=[lh("a", "bronze", "silver"), lh("b", "silver", "gold")]))
    assert got["text"] == "a, b name more than one layer."


def test_a_layer_split_over_two_lakehouses_alone():
    got = arch._medallion(naming_ctx(lakehouses=[lh("b1", "bronze"), lh("b2", "bronze")]))
    assert got["text"] == "2 lakehouses are named bronze (b1, b2)."
    assert got["detail"] == {"mixed": 0, "split": {"bronze": 2}}


def test_a_schema_enabled_lakehouse_without_a_default_schema_says_dbo():
    c = ct.Context({"type": "item", "id": "l"}, {"lakehouse.properties": [
        {"id": "p1", "payload": {"schemas_enabled": True, "default_schema": None}}]})
    assert arch._schemas(c)["text"] == "Schema-enabled (default schema dbo)."


@pytest.mark.parametrize("words,text", [
    (["dev", "prod"], "The workspace name doesn't say whether this is production (it says both: dev, prod)."),
    ([], "The workspace name doesn't say whether this is production.")])
def test_an_environment_not_said_names_the_words_it_found(words, text):
    got = arch._leftovers(naming_ctx(environment=None, environment_words=words))
    assert got["outcome"] == "not_evaluated" and got["text"] == text


@pytest.mark.parametrize("n,text", [
    (1, "1 item in this production workspace has a temporary or test name: x0."),
    (2, "2 items in this production workspace have a temporary or test name: x0, x1.")])
def test_leftovers_wording(n, text):
    left = [{"id": f"i{i}", "name": f"x{i}"} for i in range(n)]
    got = arch._leftovers(naming_ctx(environment="production", leftovers=left))
    assert got["text"] == text and got["detail"] == {"items": n}


def binding_ctx(binding, inventory=None) -> ct.Context:
    ev = {"item.binding": [binding] if binding else [], "workspace.inventory": [inventory] if inventory else []}
    return ct.Context({"type": "item", "id": "nb"}, ev)


def test_bound_is_unknown_without_a_binding_or_its_lakehouse():
    assert arch._bound(binding_ctx(None)) is None
    assert arch._bound(binding_ctx({"id": "b", "payload": {"definition": "not_read"}})) is None
    assert arch._bound(binding_ctx({"id": "b", "payload": None})) is None
    assert arch._bound(binding_ctx({"id": "b", "payload": {"default_lakehouse": None}})) is False
    assert arch._bound(binding_ctx({"id": "b", "payload": {"default_lakehouse": "L"}})) is True


def test_binding_needs_both_the_binding_and_the_inventory():
    with pytest.raises(AssertionError):
        arch._binding(binding_ctx({"id": "b", "payload": {"default_lakehouse": "L"}}))
    with pytest.raises(AssertionError):
        arch._binding(binding_ctx(None, {"id": "i", "payload": {"items": []}}))
