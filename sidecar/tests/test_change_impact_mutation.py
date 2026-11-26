"""Column change impact: what the first mutation run of meta/change_impact.py left alive (batch 257)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.meta import change_impact
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def add_fact_x(svc):
    """gold.fact_sales.x = amount + net_amount: a derived column that reads silver.orders.amount directly."""
    path = svc._project().root / "metadata" / "entities" / "gold.fact_sales.toml"
    text = path.read_text(encoding="utf-8")
    assert 'modified_at = "date"\n' in text
    text = text.replace('modified_at = "date"\n', 'modified_at = "date"\nx = "float64"\n', 1)
    path.write_text(text + '\n[[derive]]\ncolumn = "x"\nexpr = "amount + net_amount"\n', encoding="utf-8")


def item(res, name):
    return next(i for i in res["items"] if i["name"] == name)


def test_a_module_ref_with_a_part_that_isnt_a_name_is_never_read(tmp_path):
    root = tmp_path / "proj"
    (root / "transforms").mkdir(parents=True)
    (root / "transforms" / "bad-name.py").write_text("x = 1\n", encoding="utf-8")
    assert change_impact._module_file(root, "transforms.bad-name:run") is None


def test_an_unknown_entity_is_refused_by_name(svc):
    with pytest.raises(LookupError, match="No entity silver.nope"):
        change_impact.column_impact(svc._project(), "silver.nope", "amount", "drop")


def test_a_column_the_entity_doesnt_write_is_refused(svc):
    with pytest.raises(LookupError, match="silver.orders doesn't write nope"):
        change_impact.column_impact(svc._project(), "silver.orders", "nope", "drop")


def test_the_changed_table_is_this_table_and_the_others_are_table(svc):
    res = change_impact.column_impact(svc._project(), "silver.orders", "customer_id", "drop")
    assert item(res, "silver.orders")["detail"] == "this table"
    assert item(res, "gold.fact_sales")["detail"] == "table"


def test_clean_and_check_steps_on_other_columns_are_not_own_config_refs():
    e = SimpleNamespace(keys=[], spec={"clean": [{"column": "other"}], "check": [{"column": "other"}]})
    assert change_impact._own_config_refs(e, "amount") == []


def test_tables_come_before_python_and_python_before_the_model_whatever_the_names():
    items = change_impact._Items()
    items.add("model", "a", "check", "m")
    items.add("python", "b", "check", "p")
    items.add("table", "z", "check", "t")
    assert [i["kind"] for i in items.list()] == ["table", "python", "model"]


def test_a_new_type_flags_reports_only_on_columns_that_copy_it(svc):
    add_fact_x(svc)
    res = change_impact.column_impact(svc._project(), "silver.orders", "amount", "retype", new_type="int")
    # x is derived from amount, so it doesn't carry the new type on to reports
    assert item(res, "SM_Gold")["why"] == ["visuals and totals on fact_sales[net_amount] may change"]


def test_dropping_a_column_whose_direct_reader_is_derived_flags_no_report(svc):
    add_fact_x(svc)
    res = change_impact.column_impact(svc._project(), "silver.orders", "amount", "drop")
    assert {(i["kind"], i["name"]) for i in res["items"]} == {("table", "gold.fact_sales"), ("table", "silver.orders")}


def test_a_new_type_on_a_column_another_check_looks_up_is_a_check_on_both_sides(svc):
    res = change_impact.column_impact(svc._project(), "silver.customers", "customer_id", "retype", new_type="int")
    orders = item(res, "silver.orders")
    assert orders["severity"] == "check"
    assert orders["why"] == [("its [[check]] on customer_id looks values up in silver.customers.customer_id; "
                              "both sides must keep the same type")]


def test_a_dropped_column_copied_twice_over_flags_reports_on_the_last_copy(svc):
    res = change_impact.column_impact(svc._project(), "bronze.customers", "segment", "drop")
    assert item(res, "SM_Gold")["why"] == ["reports that use dim_customer[segment] lose it"]


def test_a_relationship_on_a_column_reached_by_copies_loses_a_side_when_the_source_goes(svc, monkeypatch):
    tables = [SimpleNamespace(entity="gold.dim_customer", name="dim_customer", surrogate_key="customer_sk")]
    rels = [SimpleNamespace(from_table="dim_customer", from_column="segment", to_table="seg", to_column="segment")]
    monkeypatch.setattr(change_impact.semantic, "plan", lambda project, layer: (tables, rels))
    res = change_impact.column_impact(svc._project(), "bronze.customers", "segment", "drop")
    assert item(res, "SM_Gold")["severity"] == "breaks"
    assert "its relationship dim_customer[segment] → seg[segment] loses a side" in item(res, "SM_Gold")["why"]


def test_a_key_column_dropped_in_the_model_layer_isnt_a_report_column(svc):
    res = change_impact.column_impact(svc._project(), "gold.fact_sales", "customer_sk", "drop")
    assert item(res, "SM_Gold")["why"] == [
        "its relationship fact_sales[customer_sk] → dim_customer[customer_sk] loses a side"]


def test_renaming_a_relationship_column_breaks_the_relationship(svc):
    res = change_impact.column_impact(svc._project(), "gold.fact_sales", "customer_sk", "rename", new_name="c_sk")
    assert item(res, "SM_Gold")["severity"] == "breaks"


def test_python_of_a_table_that_doesnt_read_the_changed_one_isnt_listed(svc, tmp_path):
    root = tmp_path / "crm"
    (root / "transforms").mkdir(exist_ok=True)
    (root / "transforms" / "plain.py").write_text("def run(df, ctx):\n    return df\n", encoding="utf-8")
    path = svc._project().root / "metadata" / "entities" / "bronze.orders.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("[contract]\n", '[hooks]\nbefore_write = "transforms.plain:run"\n\n[contract]\n', 1),
                    encoding="utf-8")
    res = change_impact.column_impact(svc._project(), "silver.customers", "segment", "drop")
    assert [i for i in res["items"] if i["kind"] == "python"] == []
    res = change_impact.column_impact(svc._project(), "bronze.orders", "status", "drop")
    assert [i["name"] for i in res["items"] if i["kind"] == "python"] == []  # its own hook doesn't name status


def test_a_second_reason_of_the_same_severity_keeps_the_first_chain():
    items = change_impact._Items()
    items.add("table", "t", "check", "first", chain=["a"])
    items.add("table", "t", "check", "second", chain=["b"])
    assert items.list()[0]["chain"] == ["a"]
