"""What a column change breaks (meta/change_impact.py): what mutation testing (docs/quality/mutation-testing.md)
showed no test checked. Each test works on the sample project in its own folder; nothing runs."""

from __future__ import annotations

import pytest

from fabric_desktop.meta import change_impact
from fabric_desktop.meta.templates import init_project


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "crm", "3", "Chris", True)


def impact(proj, name, column, change, **kw):
    return change_impact.column_impact(proj, name, column, change, **kw)


def item(res, kind, name):
    return next(i for i in res["items"] if (i["kind"], i["name"]) == (kind, name))


def rows(res):
    return {(i["severity"], i["kind"], i["name"]) for i in res["items"]}


def write(proj, name, text):
    (proj.entities_dir / f"{name}.toml").write_text(f'name = "{name}"\nowner = "Chris"\n{text}', encoding="utf-8")


def test_items_list_tables_then_python_then_the_model_whatever_their_names():
    items = change_impact._Items()
    items.add("model", "SM_Gold", "check", "m")
    items.add("python", "transforms.fix:run", "check", "p")
    items.add("table", "work.orders", "check", "t")
    assert [i["kind"] for i in items.list()] == ["table", "python", "model"]


def test_a_second_reason_of_the_same_severity_keeps_the_first_chain():
    items = change_impact._Items()
    items.add("table", "gold.f", "check", "first", chain=["a.x", "gold.f.x"])
    items.add("table", "gold.f", "check", "second", chain=["a.y", "gold.f.y"])
    [it] = items.list()
    assert it["chain"] == ["a.x", "gold.f.x"] and it["why"] == ["first", "second"]


def test_a_module_name_that_isnt_an_identifier_is_never_read(tmp_path):
    (tmp_path / "transforms").mkdir()
    (tmp_path / "transforms" / "bad-name.py").write_text("segment = 1\n", encoding="utf-8")
    assert change_impact._module_file(tmp_path, "transforms.bad-name:run") is None


def test_an_unknown_entity_is_named_in_the_refusal(proj):
    with pytest.raises(LookupError, match="^No entity silver.nope$"):
        impact(proj, "silver.nope", "x", "drop")


def test_a_consumer_is_this_table_or_another_table(proj):
    res = impact(proj, "silver.orders", "amount", "drop")  # net_amount, made here, reads it; gold copies that on
    assert item(res, "table", "silver.orders")["detail"] == "this table"
    assert item(res, "table", "gold.fact_sales")["detail"] == "table"
    res = impact(proj, "silver.customers", "customer_id", "drop")
    assert item(res, "table", "silver.orders")["detail"] == "table"  # its exists_in check


def test_an_exists_in_check_is_a_check_on_a_new_type_and_a_break_on_a_drop(proj):
    ref = "its [[check]] on customer_id looks values up in silver.customers.customer_id"
    res = impact(proj, "silver.customers", "customer_id", "retype", new_type="string")
    it = item(res, "table", "silver.orders")
    assert it["severity"] == "check" and f"{ref}; both sides must keep the same type" in it["why"]
    res = impact(proj, "silver.customers", "customer_id", "drop")
    it = item(res, "table", "silver.orders")
    assert it["severity"] == "breaks" and f"{ref}; the save gate refuses it" in it["why"]


def test_dropping_a_hidden_key_column_breaks_its_relationship_without_a_report_check(proj):
    res = impact(proj, "gold.fact_sales", "customer_sk", "drop")
    assert item(res, "model", "SM_Gold")["why"] == [
        "its relationship fact_sales[customer_sk] → dim_customer[customer_sk] loses a side"]


def test_renaming_a_relationship_column_breaks_the_relationship(proj):
    res = impact(proj, "gold.fact_sales", "customer_sk", "rename", new_name="cust_sk")
    assert ("breaks", "model", "SM_Gold") in rows(res)
    assert item(res, "model", "SM_Gold")["why"] == [
        "its relationship fact_sales[customer_sk] → dim_customer[customer_sk] loses a side"]


def test_a_drop_carried_two_steps_on_breaks_a_relationship_by_key_name(proj):
    # gold.returns has customer_sk only as a copy, two steps from bronze.returns; by its name it relates to
    # dim_customer, so dropping it at the start takes that relationship with it.
    write(proj, "bronze.returns", '[source]\nfiles = "landing/returns/*.csv"\n[load]\nmode = "full"\n'
          '[contract]\nkeys = ["return_id"]\n[contract.columns]\nreturn_id = "int64"\ncustomer_sk = "int64"\n')
    write(proj, "silver.returns", '[source]\nentity = "bronze.returns"\n[load]\nmode = "full"\n'
          '[contract]\nkeys = ["return_id"]\n[contract.columns]\nreturn_id = "int64"\ncustomer_sk = "int64"\n')
    write(proj, "gold.returns", '[source]\nentity = "silver.returns"\n[load]\nmode = "full"\n'
          '[contract]\nkeys = ["return_id"]\n[contract.columns]\nreturn_id = "int64"\ncustomer_sk = "int64"\n')
    proj.reload()
    res = impact(proj, "bronze.returns", "customer_sk", "drop")
    assert ("breaks", "model", "SM_Gold") in rows(res)
    assert "its relationship returns[customer_sk] → dim_customer[customer_sk] loses a side" in item(
        res, "model", "SM_Gold")["why"]


def test_python_of_a_table_that_doesnt_read_the_changed_one_isnt_listed(proj):
    (proj.root / "transforms").mkdir(exist_ok=True)
    (proj.root / "transforms" / "plain.py").write_text("def run(df, ctx):\n    return df\n", encoding="utf-8")
    path = proj.entities_dir / "bronze.customers.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("[contract]", '[hooks]\nafter_clean = "transforms.plain:run"\n\n[contract]', 1),
                    encoding="utf-8")
    proj.reload()
    assert proj.entities["bronze.customers"].spec["hooks"]["after_clean"] == "transforms.plain:run"
    res = impact(proj, "silver.orders", "customer_id", "drop")
    assert ("breaks", "table", "gold.fact_sales") in rows(res)
    assert not [i for i in res["items"] if i["kind"] == "python"]
