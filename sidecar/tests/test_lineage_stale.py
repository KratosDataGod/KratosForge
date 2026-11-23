"""Lineage after a column is dropped, a table is deleted or re-created: every answer comes from the files as
they are now, so nothing points at a column or table that is gone."""

import pytest

from fabric_desktop.meta import change_impact, lineage, openlineage
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def edit(svc, name, old, new):
    text = svc.meta_entity(name)["text"]
    assert old in text, old
    svc.meta_save_entity(name, text.replace(old, new, 1))


def edges(svc, name, column):
    out = svc.meta_lineage(name, column)
    return {(s["entity"], s["column"]) for s in out["downstream"]}


def every_input(project):
    for name in project.entities:
        for col, rec in lineage.entity_columns(project, name).items():
            for inp in rec.get("inputs") or []:
                yield name, col, inp


def assert_no_stale_edge(svc):
    project = svc._project()
    for name, col, inp in every_input(project):
        if "entity" in inp:
            assert inp["entity"] in project.entities, (name, col, inp)
            if inp["entity"] != name:
                assert inp["column"] in lineage.entity_columns(project, inp["entity"]), (name, col, inp)


def test_a_dropped_column_leaves_no_edge_behind(svc):
    assert ("gold.dim_customer", "segment") in edges(svc, "silver.customers", "segment")
    edit(svc, "gold.dim_customer", 'tracked = ["segment", "city"]', 'tracked = ["city"]')
    edit(svc, "gold.dim_customer", 'segment = "string"\n', "")
    assert ("gold.dim_customer", "segment") not in edges(svc, "silver.customers", "segment")
    assert "segment" not in lineage.entity_columns(svc._project(), "gold.dim_customer")
    res = svc.meta_impact("silver.customers", "segment", "drop")
    assert "gold.dim_customer" not in {i["name"] for i in res["items"]}
    assert_no_stale_edge(svc)


def test_a_deleted_table_is_gone_from_lineage_impact_and_export(svc):
    svc.meta_delete_entity("gold.fact_sales")
    assert not any(e == "gold.fact_sales" for e, _ in edges(svc, "silver.orders", "net_amount"))
    with pytest.raises(LookupError):
        svc.meta_lineage("gold.fact_sales")
    res = svc.meta_impact("silver.orders", "customer_id", "drop")
    assert "gold.fact_sales" not in {i["name"] for i in res["items"]}
    runs = [{"run_id": "r", "entity": "gold.fact_sales", "status": "ok", "started_at": "2026-11-14T10:00:00+00:00"}]
    out = openlineage.run_events(svc._project(), runs, "dev")
    assert out["events"] == [] and "gold.fact_sales" not in {x["entity"] for x in out["left_out"]}
    assert_no_stale_edge(svc)


def test_a_re_created_table_shows_its_new_shape_not_the_old_one(svc):
    old = svc.meta_entity("gold.fact_sales")["text"]
    svc.meta_delete_entity("gold.fact_sales")
    # back under the same name, now without net_amount
    text = old.replace('net_amount = "float64"\n', "")
    assert text != old
    (svc._project().entities_dir / "gold.fact_sales.toml").write_text(text, encoding="utf-8")
    cols = lineage.entity_columns(svc._project(), "gold.fact_sales")
    assert "net_amount" not in cols
    assert ("gold.fact_sales", "net_amount") not in edges(svc, "silver.orders", "net_amount")
    assert_no_stale_edge(svc)


def test_a_renamed_table_answers_under_its_new_name_only(svc, tmp_path):
    project = svc._project()
    text = svc.meta_entity("gold.fact_sales")["text"].replace('name = "gold.fact_sales"', 'name = "gold.fact_orders"')
    svc.meta_delete_entity("gold.fact_sales")
    (project.entities_dir / "gold.fact_orders.toml").write_text(text, encoding="utf-8")
    down = {e for e, _ in edges(svc, "silver.orders", "customer_id")}
    assert "gold.fact_orders" in down and "gold.fact_sales" not in down
    res = change_impact.column_impact(svc._project(), "silver.orders", "customer_id", "drop")
    names = {i["name"] for i in res["items"]}
    assert "gold.fact_orders" in names and "gold.fact_sales" not in names
    assert_no_stale_edge(svc)
