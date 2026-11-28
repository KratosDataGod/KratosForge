"""Semantic model best practices (model_practices.py): the edges mutation testing found unpinned."""

import dataclasses
from types import SimpleNamespace

import pytest

from fabric_desktop import model_practices as mp
from fabric_desktop.semantic import ModelTable, Relationship


def table(name="fact_sales", entity="gold.fact_sales", dimension=False, description="Order lines.", columns=None):
    return {"name": name, "entity": entity, "dimension": dimension, "description": description,
            "columns": columns or [{"name": "amount", "type": "decimal", "hidden": False, "description": "Net."}]}


def ids(out):
    return [(f["rule_id"], f["object_type"], f["object"]) for f in out["findings"]]


def test_each_rule_links_to_its_docs_section():
    assert mp.url("SM001") == "semantic-model-rules.md#sm001"
    assert [r["url"] for r in mp.rules()] == [f"semantic-model-rules.md#{r.id.lower()}" for r in mp.RULES]


def test_a_rule_cant_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        mp.RULES[0].severity = "Info"


def entity(mode="scd1", surrogate_key="customer_sk", key_columns=("customer_id",), lookups=()):
    return SimpleNamespace(mode=mode, surrogate_key=surrogate_key, key_columns=list(key_columns),
                           spec={"lookup": [{"as": a} for a in lookups]})


def test_a_columns_model_type_comes_from_its_contract_first():
    assert mp._known_type(entity(), "customer_sk", "decimal(18,2)") == "decimal"
    assert mp._known_type(entity(mode="scd2"), "valid_from", "int64") == "int64"


def test_keys_without_a_contract_type_are_whole_numbers():
    e = entity(lookups=("region_sk",))
    assert mp._known_type(e, "customer_sk", "") == "int64"
    assert mp._known_type(e, "customer_id", "") == "int64"
    assert mp._known_type(e, "region_sk", "") == "int64"
    assert mp._known_type(e, "city", "") is None


def test_type_2_columns_have_their_types_only_in_a_type_2_entity():
    e = entity(mode="scd2", surrogate_key=None, key_columns=())
    assert mp._known_type(e, "valid_from", "") == "dateTime"
    assert mp._known_type(e, "is_current", "") == "boolean"
    assert mp._known_type(e, "city", "") is None
    assert mp._known_type(entity(mode="scd1", surrogate_key=None, key_columns=()), "valid_from", "") is None


def test_a_list_of_names_names_five_then_counts_the_rest():
    assert mp._list(list("abcde")) == "a, b, c, d, e"
    assert mp._list(list("abcdef")) == "a, b, c, d, e and 1 more"
    assert mp._list(list("abcdefgh")) == "a, b, c, d, e and 3 more"


def test_a_described_table_with_undescribed_visible_columns_is_found():
    cols = [{"name": "city", "type": "string", "hidden": False, "description": ""},
            {"name": "region_sk", "type": "int64", "hidden": True, "description": ""}]
    out = mp.check({"tables": [table("dim_c", "gold.dim_c", True, "Customers.", cols)], "relationships": []})
    assert ids(out) == [("SM005", "Table", "dim_c")]
    assert out["findings"][0]["description"].startswith("No description for 1 visible column (city).")


def test_an_odd_table_name_is_found_on_the_table_and_an_odd_column_on_the_column():
    cols = [{"name": "region ", "type": "string", "hidden": False, "description": "x"}]
    out = mp.check({"tables": [table(" dim_c", "gold.dim_c", True, "Customers.", cols)], "relationships": []})
    assert ids(out) == [("SM007", "Table", " dim_c"), ("SM007", "Column", " dim_c.'region '")]


def test_the_model_item_takes_each_tables_own_columns_and_types(monkeypatch):
    fact = ModelTable(name="fact_sales", entity="gold.fact_sales", schema="gold", table="fact_sales")
    dim = ModelTable(name="dim_customer", entity="gold.dim_customer", schema="gold", table="dim_customer",
                     is_dimension=True)
    rel = Relationship("fact_sales", "customer_sk", "dim_customer", "customer_sk")
    monkeypatch.setattr(mp.semantic, "plan", lambda project, layer: ([fact, dim], [rel]))

    def row(ent, kind, name, typ="", desc=""):
        return {"entity": ent, "kind": kind, "name": name, "type": typ, "hidden": name.endswith("_sk"), "description": desc}

    entries = [row("gold.fact_sales", "table", "fact_sales", desc="Lines."),
               row("gold.fact_sales", "column", "customer_sk", "decimal(18,2)"),
               row("gold.dim_customer", "table", "dim_customer", desc="Customers."),
               row("gold.dim_customer", "column", "customer_sk"),
               row("gold.dim_customer", "column", "city", "string")]
    monkeypatch.setattr(mp.model_dictionary, "entries", lambda project, layer, saved: entries)
    ents = {"gold.fact_sales": entity(surrogate_key=None, key_columns=()),
            "gold.dim_customer": entity(surrogate_key="customer_sk", key_columns=())}
    item = mp.model_item(SimpleNamespace(entities=ents), "gold", {})
    assert [[c["name"] for c in t["columns"]] for t in item["tables"]] == [["customer_sk"], ["customer_sk", "city"]]
    assert item["tables"][0]["description"] == "Lines." and item["tables"][1]["dimension"] is True
    assert item["relationships"][0]["from_type"] == "decimal" and item["relationships"][0]["to_type"] == "int64"
    assert item["id"] == "SM_Gold"
