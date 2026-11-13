"""A metadata project's save gate (meta/project.py): the refusals the main tests don't reach. Each
check is tried on the sample project with one entity changed in memory; nothing runs."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop.meta import project as P
from fabric_desktop.meta.project import GateError, Layer, validate
from fabric_desktop.meta.templates import init_project


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "project", "3", "Chris", True)


def problems(proj, name):
    return [e for e in validate(proj, dt.date(2026, 11, 11)) if e.startswith(f"{name}:")]


@pytest.mark.parametrize("value", [True, -1, "soon", 3.5])
def test_a_lookback_that_cant_be_read_is_refused(value):
    with pytest.raises(ValueError):
        P.parse_lookback(value)


def test_a_lookback_in_minutes():
    assert P.parse_lookback("30 minutes") == dt.timedelta(minutes=30)
    assert P.parse_lookback(5) == 5 and P.parse_lookback(None) is None


def test_unreadable_max_deletes_falls_back_to_the_default(proj):
    e = proj.entities["silver.orders"]
    e.spec["load"]["max_deletes"] = "lots"
    assert e.max_deletes == P.parse_max_deletes(P.DEFAULT_MAX_DELETES)


def test_an_entity_file_that_isnt_toml_is_reported(proj):
    (proj.entities_dir / "broken.toml").write_text("name = ", encoding="utf-8")
    proj.reload()
    assert any(e.startswith("broken.toml: not valid TOML") for e in validate(proj))


@pytest.mark.parametrize("layers, why", [
    ([], "a project needs at least one layer"),
    ([Layer("bronze"), Layer("bronze"), Layer("silver"), Layer("gold")], "layer names must be unique"),
    ([Layer("bronze", drift="sometimes"), Layer("silver"), Layer("gold")], "layer bronze: drift must be one of"),
])
def test_a_layer_list_that_breaks_the_rules_is_refused_and_kept(proj, layers, why):
    before = (proj.root / "layers.toml").read_text(encoding="utf-8")
    with pytest.raises(GateError) as raised:
        proj.set_layers(layers)
    assert any(why in p for p in raised.value.errors)
    assert (proj.root / "layers.toml").read_text(encoding="utf-8") == before


def test_an_expression_that_doesnt_parse_is_named():
    (problem,) = P.check_expr("amount +* 2", {"amount"})
    assert problem.startswith("expression 'amount +* 2' does not parse (")


@pytest.mark.parametrize("ref", ["transforms.clean", "other.module:fn"])
def test_custom_python_must_be_a_transforms_function(ref):
    assert P.check_python(ref) == "must be transforms.<module>:<function>"


def test_batch_by_is_for_full_and_incremental_loads_of_a_delta_source(proj):
    dim = proj.entities["gold.dim_customer"]
    dim.spec["load"]["batch_by"] = "customer_id"
    assert "gold.dim_customer: load batch_by works for full and incremental loads; dimensions are merged by key" \
        in problems(proj, "gold.dim_customer")
    bronze = proj.entities["bronze.orders"]
    bronze.spec["load"]["batch_by"] = "order_id"
    assert "bronze.orders: load batch_by needs a Delta source (table, link or entity) and no custom step" \
        in problems(proj, "bronze.orders")


def test_deletes_need_keys_the_source_has_and_no_custom_step(proj):
    e = proj.entities["silver.orders"]
    e.spec["load"]["deletes"] = "soft"
    e.spec["derive"] = e.spec.get("derive", []) + [{"column": "order_id", "expr": "order_id"}]
    assert any("load deletes needs keys the source has; order_id is made by this entity" in p
               for p in problems(proj, "silver.orders"))
    e.spec["step"] = {"custom": "transforms.orders:build"}
    assert "silver.orders: load deletes can't be used with a custom step, which makes its own rows" \
        in problems(proj, "silver.orders")


def test_the_basic_entity_rules(proj):
    e = proj.entities["silver.orders"]
    e.spec["load"]["mode"] = "sometimes"
    e.spec["contract"]["keys"] = ["no_such_key"]
    e.spec["contract"]["drift"] = "maybe"
    e.spec["source"]["rename"] = {"cust": 3}
    found = problems(proj, "silver.orders")
    assert "silver.orders: load mode must be one of " + ", ".join(P.LOAD_MODES) in found
    assert "silver.orders: key no_such_key is not a contract column" in found
    assert "silver.orders: contract drift must be one of " + ", ".join(P.DRIFT_POLICIES) in found
    assert any("source rename maps a source column to a contract column" in p for p in found)


def test_an_entity_with_no_columns_and_an_incremental_load_without_a_watermark(proj):
    bronze = proj.entities["bronze.orders"]
    bronze.spec["contract"] = {}
    assert "bronze.orders: contract needs at least one column" in problems(proj, "bronze.orders")
    silver = proj.entities["silver.customers"]
    del silver.spec["load"]["watermark"]
    assert "silver.customers: incremental loads need a watermark column" in problems(proj, "silver.customers")


def test_an_entity_name_without_a_layer_is_refused_before_anything_else(proj):
    e = proj.entities.pop("silver.orders")
    proj.entities["orders"] = P.Entity("orders", e.spec, e.path)
    assert problems(proj, "orders") == ["orders: name must be <layer>.<table> or <layer>.<schema>.<table>"]


def test_an_entity_in_a_layer_that_doesnt_exist(proj):
    e = proj.entities.pop("silver.orders")
    proj.entities["platinum.orders"] = P.Entity("platinum.orders", e.spec, e.path)
    assert problems(proj, "platinum.orders") == ["platinum.orders: layer platinum does not exist"]


def test_dimensions_need_a_surrogate_key_a_business_key_and_tracked_columns(proj):
    e = proj.entities["gold.dim_customer"]
    e.spec["scd"] = {}
    e.spec["contract"]["keys"] = []
    found = problems(proj, "gold.dim_customer")
    assert "gold.dim_customer: dimensions need [scd] surrogate_key" in found
    assert "gold.dim_customer: dimensions need a business key in [contract] keys" in found
    assert "gold.dim_customer: Type 2 dimensions need [scd] tracked columns" in found


def test_checks_and_lookups_on_columns_and_tables_that_dont_fit(proj):
    fact = proj.entities["gold.fact_sales"]
    fact.spec["check"] = [{"name": "a", "column": "nope", "not_null": True},
                          {"name": "b", "column": "order_id", "on_fail": "shrug"},
                          {"name": "c", "column": "order_id", "exists_in": "gold.dim_customer.no_col"}]
    fact.spec["lookup"] = [{"dim": "silver.orders", "on": "nowhere", "as": "x"}]
    found = problems(proj, "gold.fact_sales")
    assert "gold.fact_sales: check on unknown column nope" in found
    assert "gold.fact_sales: on_fail must be stop, quarantine or warn" in found
    assert "gold.fact_sales: check exists_in gold.dim_customer.no_col: no such column" in found
    assert "gold.fact_sales: lookup silver.orders is not a dimension" in found
    assert "gold.fact_sales: lookup uses unknown column nowhere" in found


def test_an_exception_needs_a_reason_and_an_approver(proj):
    proj.exceptions.append({"entity": "silver.orders", "rule": "owner", "expires": "permanent"})
    assert "silver.orders: exception for owner needs a reason and an approver" in validate(proj)
