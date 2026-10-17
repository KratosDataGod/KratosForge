"""Column lineage from the project's files: declared, inferred and opaque."""

import pytest

from fabric_desktop.meta import lineage
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


def steps(out):
    return [(s["entity"], s["column"], s["how"]) for s in out]


def test_every_column_says_where_it_comes_from(svc):
    cols = svc.meta_lineage("silver.orders")["columns"]
    assert cols["amount"] == {"how": "source", "label": "inferred", "source_column": "amount",
                              "inputs": [{"entity": "bronze.orders", "column": "amount"}]}
    assert cols["net_amount"]["how"] == "derived" and cols["net_amount"]["label"] == "declared"
    assert cols["net_amount"]["inputs"] == [{"entity": "silver.orders", "column": "amount"},
                                            {"entity": "silver.orders", "column": "discount"}]
    assert cols["status"]["cleaned"] == ["trim", "case"]
    assert cols["order_sk"] == {"how": "key", "label": "declared", "key_table": "order",
                                "inputs": [{"entity": "silver.orders", "column": "order_id"}],
                                "note": "surrogate key from key table order"}
    assert cols["_run_id"]["how"] == "framework"
    dim = svc.meta_lineage("gold.dim_customer")["columns"]
    assert dim["customer_sk"]["note"] == "dimension surrogate key"
    assert {c for c, r in dim.items() if r.get("note") == "Type 2 history"} == {"valid_from", "valid_to", "is_current", "row_hash"}
    bronze = svc.meta_lineage("bronze.orders")["columns"]
    assert bronze["amount"]["inputs"] == [{"origin": "files", "value": "landing/orders/*.csv", "column": "amount"}]


def test_upstream_follows_derives_lookups_and_sources_to_the_start(svc):
    out = svc.meta_lineage("gold.fact_sales", "net_amount")
    assert steps(out["upstream"]) == [
        ("gold.fact_sales", "net_amount", "source"), ("silver.orders", "net_amount", "derived"),
        ("silver.orders", "amount", "source"), ("bronze.orders", "amount", "source"),
        ("silver.orders", "discount", "source"), ("bronze.orders", "discount", "source")]
    key = svc.meta_lineage("gold.fact_sales", "customer_sk")["upstream"]
    assert key[0]["how"] == "lookup" and key[0]["note"] == "gold.dim_customer key matched on customer_id as of order_date"
    assert ("gold.dim_customer", "customer_sk", "framework") in steps(key)
    assert ("bronze.orders", "customer_id", "source") in steps(key)


def test_downstream_finds_every_column_built_from_one(svc):
    out = svc.meta_lineage("bronze.orders", "discount")
    assert steps(out["downstream"]) == [("silver.orders", "discount", "source"),
                                        ("silver.orders", "net_amount", "derived"),
                                        ("gold.fact_sales", "net_amount", "source")]


def test_a_rename_is_declared_and_points_at_the_source_name(svc):
    edit(svc, "silver.orders", 'entity = "bronze.orders"', 'entity = "bronze.orders"\nrename = { "status" = "order_status" }')
    edit(svc, "silver.orders", 'status = "string"', 'order_status = "string"')
    edit(svc, "silver.orders", 'column = "status"', 'column = "order_status"')
    edit(svc, "silver.orders", "status <> 'returned'", "order_status <> 'returned'")
    rec = svc.meta_lineage("silver.orders")["columns"]["order_status"]
    assert rec["label"] == "declared" and rec["inputs"] == [{"entity": "bronze.orders", "column": "status"}]


def test_custom_python_is_opaque_and_hooks_are_flagged(svc):
    edit(svc, "silver.orders", 'owner = "me"', 'owner = "me"\n\n[hooks]\nafter_clean = "transforms.common:noop"\nadds = ["score"]')
    edit(svc, "silver.orders", 'net_amount = "float64"', 'net_amount = "float64"\nscore = "float64"')
    cols = svc.meta_lineage("silver.orders")["columns"]
    assert cols["score"] == {"how": "custom", "label": "opaque", "inputs": [], "note": "added by your Python (hooks adds)"}
    assert cols["amount"]["custom_may_change"] == ["after_clean"]
    assert "custom_may_change" not in cols["_run_id"]
    assert svc.meta_lineage("gold.fact_sales", "score")["upstream"][0]["how"] == "unknown"  # gold doesn't write it


def test_a_custom_step_makes_its_columns_opaque():
    from fabric_desktop.meta.project import Entity, Project
    project = Project.__new__(Project)  # an entity with a custom step, without a project on disk
    spec = {"name": "silver.x", "source": {"entity": "bronze.x"}, "step": {"custom": "transforms.x:build"},
            "contract": {"columns": {"a": "int64"}}}
    project.entities = {"silver.x": Entity("silver.x", spec, None)}
    cols = lineage.entity_columns(project, "silver.x")
    assert cols["a"]["label"] == "opaque" and cols["a"]["note"] == "your Python step builds the table"


def test_unknown_entity_and_missing_upstream_columns(svc):
    with pytest.raises(LookupError):
        svc.meta_lineage("silver.nothing")
    edit(svc, "silver.orders", 'status = "string"', 'status = "string"\nregion = "string"')
    rec = svc.meta_lineage("silver.orders")["columns"]["region"]
    assert rec["inputs"] == [{"entity": "bronze.orders", "column": "region", "missing": True}]
    assert steps(svc.meta_lineage("silver.orders", "region")["upstream"]) == [("silver.orders", "region", "source")]
