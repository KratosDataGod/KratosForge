"""Mutation tests for column change impact (meta/change_impact.py): the order and worst-of rule of the
list, which table an item is on, what a drop carries on to the semantic model, the save-gate reasons in
an entity's own file and the exists_in checks elsewhere. On the three-layer template. Found by
tools/mutate.py."""

import pytest

from fabric_desktop.meta import change_impact
from tests.test_change_impact import edit, rows, svc, why  # noqa: F401  (svc is a fixture)


def test_the_list_puts_tables_before_python_before_the_model_and_keeps_the_first_chain_of_a_tie():
    items = change_impact._Items()
    items.add("model", "a_model", "check", "m")
    items.add("python", "b_python", "check", "p")
    items.add("table", "z_table", "check", "t", chain=["first"])
    items.add("table", "z_table", "check", "t2", chain=["second"])
    out = items.list()
    assert [i["kind"] for i in out] == ["table", "python", "model"]
    assert out[0]["chain"] == ["first"] and out[0]["why"] == ["t", "t2"]


def test_an_entity_that_isnt_there_is_not_found(svc):  # noqa: F811
    with pytest.raises(LookupError, match="No entity gold.nope"):
        change_impact.column_impact(svc._project(), "gold.nope", "x", "drop")


def test_a_column_of_the_same_table_built_from_it_is_on_this_table(svc):  # noqa: F811
    res = svc.meta_impact("silver.orders", "amount", "retype", new_type="decimal(18,2)")
    detail = {i["name"]: i["detail"] for i in res["items"] if i["kind"] == "table"}
    assert detail == {"silver.orders": "this table", "gold.fact_sales": "table"}


def test_dropping_a_column_an_expression_uses_doesnt_flag_reports_on_the_expressions_result(svc):  # noqa: F811
    res = svc.meta_impact("silver.orders", "amount", "drop")
    assert ("breaks", "table", "silver.orders") in rows(res)
    assert not any(i["kind"] == "model" for i in res["items"])
    reasons = why(res, "silver.orders")
    assert "[[clean]]" not in reasons and "[[check]] names" not in reasons


def test_only_the_clean_and_check_steps_that_name_the_column_are_reasons(svc):  # noqa: F811
    assert "a [[clean]] step names status" in why(svc.meta_impact("silver.orders", "status", "drop"), "silver.orders")
    assert "a [[check]] names order_id" in why(svc.meta_impact("silver.orders", "order_id", "drop"), "silver.orders")


def test_a_hidden_key_column_given_a_new_type_isnt_a_report_check(svc):  # noqa: F811
    res = svc.meta_impact("gold.fact_sales", "customer_sk", "retype", new_type="bigint")
    assert not any("visuals and totals" in w for i in res["items"] for w in i["why"])


def test_renaming_a_relationship_column_breaks_the_relationship(svc):  # noqa: F811
    res = svc.meta_impact("gold.fact_sales", "customer_sk", "rename", new_name="cust_sk")
    assert why(res, "SM_Gold") == "its relationship fact_sales[customer_sk] → dim_customer[customer_sk] loses a side"


def test_an_exists_in_check_breaks_when_its_column_goes_and_is_a_check_for_a_new_type(svc):  # noqa: F811
    gone = svc.meta_impact("silver.customers", "customer_id", "drop")
    assert ("breaks", "table", "silver.orders") in rows(gone)
    assert "looks values up in silver.customers.customer_id; the save gate refuses it" in why(gone, "silver.orders")
    typed = svc.meta_impact("silver.customers", "customer_id", "retype", new_type="string")
    assert ("check", "table", "silver.orders") in rows(typed)
    assert "looks values up in silver.customers.customer_id; both sides must keep the same type" in why(typed, "silver.orders")


def test_a_drop_carries_on_through_plain_copies_to_the_model(svc):  # noqa: F811
    res = svc.meta_impact("bronze.customers", "segment", "drop")
    assert ("check", "model", "SM_Gold") in rows(res)
    assert why(res, "SM_Gold") == "reports that use dim_customer[segment] lose it"


def test_python_of_a_table_that_doesnt_read_the_changed_one_isnt_flagged(svc, tmp_path):  # noqa: F811
    root = tmp_path / "crm"
    (root / "transforms").mkdir(exist_ok=True)
    (root / "transforms" / "__init__.py").write_text("", encoding="utf-8")
    (root / "transforms" / "plain.py").write_text("def run(df, ctx):\n    return df\n", encoding="utf-8")
    edit(svc, "bronze.customers", "[contract]", '[hooks]\nbefore_write = "transforms.plain:run"\n\n[contract]')
    res = svc.meta_impact("silver.customers", "segment", "drop")
    assert not any(i["kind"] == "python" for i in res["items"])
