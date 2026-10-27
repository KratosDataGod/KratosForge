"""Semantic model best practices on this PC: the model Update model writes, checked against BPA's metadata rules."""

import copy

import pytest

from fabric_desktop import governance_checks as gc
from fabric_desktop import model_practices as mp
from fabric_desktop.service import Service


def _item(**kw):
    item = {"kind": "model", "id": "SM_Gold", "layer": "gold", "tables": [
        {"name": "fact_sales", "entity": "gold.fact_sales", "dimension": False, "description": "Order lines.", "columns": [
            {"name": "customer_sk", "type": "int64", "hidden": True, "description": ""},
            {"name": "net_amount", "type": "decimal", "hidden": False, "description": "After discount."}]},
        {"name": "dim_customer", "entity": "gold.dim_customer", "dimension": True, "description": "Customers.", "columns": [
            {"name": "customer_sk", "type": "int64", "hidden": True, "description": ""},
            {"name": "city", "type": "string", "hidden": False, "description": "Where they live."}]}],
        "relationships": [{"from_table": "fact_sales", "from_column": "customer_sk", "to_table": "dim_customer",
                           "to_column": "customer_sk", "from_type": "int64", "to_type": "int64", "active": True}]}
    item.update(kw)
    return item


def _ids(out):
    return sorted(f["rule_id"] for f in out["findings"])


def test_a_well_built_model_has_no_findings():
    assert mp.check(_item()) == {"counts": {"Error": 0, "Warning": 0, "Info": 0}, "findings": []}


def test_relationship_types_integers_and_hidden_keys():
    item = _item()
    rel = item["relationships"][0]
    rel["from_type"], rel["to_type"] = "string", "int64"
    item["tables"][0]["columns"][0] |= {"hidden": False, "description": "The customer."}
    out = mp.check(item)
    assert _ids(out) == ["SM001", "SM002", "SM004"]
    first = out["findings"][0]
    assert (first["rule_id"], first["severity"], first["object_type"]) == ("SM001", "Error", "Relationship")
    assert first["object"] == "fact_sales.customer_sk to dim_customer.customer_sk"
    assert first["description"].startswith("customer_sk is string and dim_customer.customer_sk is int64.")
    assert first["fix"] == {"action": "open_entity", "label": "Open the entity", "entity": "gold.fact_sales"}
    # A type the project doesn't record is never guessed as a mismatch.
    rel["from_type"] = None
    assert "SM001" not in _ids(mp.check(item))


def test_doubles_descriptions_lonely_facts_and_odd_names():
    item = _item(relationships=[])
    fact = item["tables"][0]
    fact["columns"] += [{"name": "tax", "type": "double", "hidden": False, "description": "x"},
                        {"name": "_loaded_at", "type": "double", "hidden": True, "description": ""},
                        {"name": "region ", "type": "string", "hidden": False, "description": ""}]
    fact["description"] = ""
    out = mp.check(item)
    assert _ids(out) == ["SM003", "SM005", "SM006", "SM007"]
    by = {f["rule_id"]: f for f in out["findings"]}
    assert by["SM003"]["description"].startswith("tax is double.")  # the framework's own columns are left alone
    assert by["SM005"]["description"].startswith("No description for the table and 1 visible column (region )")
    assert by["SM005"]["fix"]["action"] == "open_model"
    assert by["SM007"]["object"] == "fact_sales.'region '"


def test_settings_turn_rules_off_and_change_severity():
    item = _item(relationships=[])
    out = mp.check(item, {"SM006": {"enabled": True, "severity": "Error"}})
    assert out["findings"][0]["severity"] == "Error" and out["counts"]["Error"] == 1
    assert mp.check(item, {"SM006": {"enabled": False}})["findings"] == []


def test_check_does_not_change_the_item():
    item = _item(relationships=[])
    before = copy.deepcopy(item)
    mp.check(item)
    assert item == before


def test_the_rules_are_registered_as_the_model_kind():
    ids = [r["id"] for r in gc.all_rules() if r["checker"] == "model"]
    assert ids == [f"SM00{n}" for n in range(1, 8)]
    assert all(r["kind"] == "model" and r["runs_in"] == "local" for r in gc.all_rules() if r["checker"] == "model")
    # A checker that runs in Fabric can't take the kind over (and replace these rules); it needs its own.
    with pytest.raises(ValueError, match="model rules already run in local"):
        gc.register("model", lambda: [], lambda i, s: [], runs_in="fabric")
    assert [r["id"] for r in gc.all_rules() if r["checker"] == "model"] == ids


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def test_the_model_item_is_what_update_model_writes(svc):
    item = mp.model_item(svc._project(), "gold", {})
    assert item["id"] == "SM_Gold" and item["layer"] == "gold"
    assert [t["name"] for t in item["tables"]] == ["dim_customer", "fact_sales"]
    rel = item["relationships"][0]
    assert (rel["from_type"], rel["to_type"]) == ("int64", "int64"), "surrogate keys are whole numbers"
    fact = next(t for t in item["tables"] if t["name"] == "fact_sales")
    assert {c["name"]: c["type"] for c in fact["columns"]}["net_amount"] == "double"
    assert next(c for c in fact["columns"] if c["name"] == "customer_sk")["hidden"] is True


def test_check_now_runs_model_rules_on_the_last_layer(svc):
    svc.governance_create({"id": "model", "name": "Model rules", "category": "quality", "rule": "SM003, SM005, SM006"})
    svc.governance_publish("model")
    svc.governance_assign("model", "enterprise")
    got = {p["id"]: p for p in svc.governance_check_now()["policies"]}["model"]
    assert got["status"] == "findings" and got["checked"] == 3
    found = {(f["rule_id"], f["object"]) for f in got["findings"]}
    assert ("SM003", "SM_Gold: fact_sales") in found
    assert ("SM005", "SM_Gold: dim_customer") in found and not any(r == "SM006" for r, _ in found)
    # A description saved in the catalog counts.
    svc.mesh_save_about("gold.fact_sales", {"description": "Order lines."})
    for c in ("order_id", "order_date", "net_amount", "modified_at"):
        svc.mesh_save_about("gold.fact_sales", {"description": f"The {c}."}, c)
    got = {p["id"]: p for p in svc.governance_check_now()["policies"]}["model"]
    assert ("SM005", "SM_Gold: fact_sales") not in {(f["rule_id"], f["object"]) for f in got["findings"]}
