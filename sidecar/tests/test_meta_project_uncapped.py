"""Metadata project paths that the uncapped mutation run (batch 183, docs/quality/mutation-testing.md)
showed no test checked."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop.meta import project as mp


def test_a_lookback_in_hours_or_days_is_that_long():
    assert mp.parse_lookback("2 hours") == dt.timedelta(hours=2)
    assert mp.parse_lookback("1 hour") == dt.timedelta(hours=1)
    assert mp.parse_lookback("3 days") == dt.timedelta(days=3)
    assert mp.parse_lookback("30 min") == dt.timedelta(minutes=30)


def test_max_deletes_as_a_whole_number_is_a_count():
    assert mp.parse_max_deletes(5) == ("count", 5.0)
    assert mp.parse_max_deletes(0) == ("count", 0.0)
    assert mp.parse_max_deletes("20%") == ("share", 0.2)
    for bad in (True, False, -1, "101%", "x", 1.5):
        with pytest.raises(ValueError):
            mp.parse_max_deletes(bad)


def test_the_layers_file_writes_a_description_and_a_workspace_only_when_set(tmp_path):
    mp.write_layers_file(tmp_path, [mp.Layer("bronze", description="Raw", workspace="ingest"), mp.Layer("silver")], {})
    text = (tmp_path / "layers.toml").read_text(encoding="utf-8").replace("\r\n", "\n")
    bronze, silver = text.split("[[layer]]")[1:]
    assert 'description = "Raw"' in bronze and 'workspace = "ingest"' in bronze
    assert "description" not in silver and "workspace" not in silver


def test_an_entity_of_a_layer_the_project_does_not_have_sorts_last(tmp_path):
    from fabric_desktop.meta.templates import init_project
    proj = init_project(tmp_path / "project", "3", "Chris", True)
    first = proj.layer_names()[0]
    assert proj._layer_pos(mp.Entity(f"{first}.t", {}, tmp_path / "t.toml")) == 0
    assert proj._layer_pos(mp.Entity("nowhere.t", {}, tmp_path / "t.toml")) == 99


@pytest.mark.parametrize("batch_by", ["nope", 5])
def test_batch_by_must_name_a_contract_column(tmp_path, batch_by):
    e = mp.Entity("silver.t", {"load": {"batch_by": batch_by}, "contract": {"columns": {"a": "int64"}}},
                  tmp_path / "t.toml")
    assert mp._batch_by_problems(e) == ["silver.t: load batch_by names one contract column"]


def test_an_entity_name_needs_every_part_and_no_path_characters():
    assert mp.entity_name_problem("silver.orders") is None
    for bad in ("silver..orders", "silver.or/ders", "silver.or:ders", "silver.or\\ders"):
        assert mp.entity_name_problem(bad) == ("each part of the name needs at least one character, and none can "
                                               "hold / \\ : * ? \" < > | or a control character")
    assert mp.entity_name_problem("orders") == "name must be <layer>.<table> or <layer>.<schema>.<table>"


@pytest.mark.parametrize("bad", ["silver.orders?", "silver.ord*rs", 'silver."orders"', "silver.<orders>",
                                 "silver.a|b", "silver.or\tders", "silver.orders\x01"])
def test_an_entity_name_windows_cant_use_in_its_file_name_is_refused(bad):
    """The entity is saved as <name>.toml: Windows refuses these characters in a file name with a bare
    "The filename, directory name, or volume label syntax is incorrect" (WinError 123)."""
    assert "none can hold" in mp.entity_name_problem(bad)


def test_an_entity_is_in_dbo_unless_its_name_has_a_schema(tmp_path):
    assert mp.Entity("silver.sales.orders", {}, tmp_path / "x.toml").schema == "sales"
    assert mp.Entity("silver.orders", {}, tmp_path / "x.toml").schema == "dbo"


def test_exceptions_are_read_from_their_file_and_none_without_it(tmp_path):
    from fabric_desktop.meta.templates import init_project
    root = tmp_path / "project"
    init_project(root, "3", "Chris", True)
    (root / "exceptions.toml").write_text('[[exception]]\nentity = "silver.orders"\nrule = "owner"\n', encoding="utf-8")
    assert mp.Project(root).exceptions == [{"entity": "silver.orders", "rule": "owner"}]
    (root / "exceptions.toml").unlink()
    assert mp.Project(root).exceptions == []


def test_a_lookback_of_a_whole_number_is_that_number():
    assert mp.parse_lookback(0) == 0
    assert mp.parse_lookback(5) == 5
    for bad in (-1, True, "soon"):
        with pytest.raises(ValueError):
            mp.parse_lookback(bad)


def test_a_layers_lakehouse_capitalises_its_first_letter():
    assert mp.Layer("bronze").lakehouse == "LH_Bronze"


def test_an_entitys_table_is_the_last_part_of_its_name(tmp_path):
    assert mp.Entity("silver.sales.orders", {}, tmp_path / "x.toml").table == "orders"
    assert mp.Entity("silver.orders", {}, tmp_path / "x.toml").table == "orders"


def test_an_entity_with_no_sections_has_a_logic_hash(tmp_path):
    h = mp.Entity("silver.orders", {}, tmp_path / "x.toml").logic_hash()
    assert len(h) == 8 and int(h, 16) >= 0


def test_an_expression_names_only_the_columns_that_are_not_there():
    assert mp.check_expr("a + b", {"a"}) == ["uses unknown column b"]
    assert mp.check_expr("a + b", {"a", "b"}) == []


def test_a_custom_function_may_take_more_than_df_and_ctx(tmp_path, monkeypatch):
    pkg = tmp_path / "transforms"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "u3_extra.py").write_text("def step(df, ctx, extra=None):\n    return df\n\n"
                                     "def wrong(ctx, df):\n    return df\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [m for m in list(mp.sys.modules) if m == "transforms" or m.startswith("transforms.")]:
        monkeypatch.delitem(mp.sys.modules, name)
    assert mp.check_python("transforms.u3_extra:step") is None
    assert mp.check_python("transforms.u3_extra:wrong") == "must take (df, ctx)"


def test_purging_code_forgets_transforms_and_its_modules_only(tmp_path, monkeypatch):
    import types
    for name in ("transforms", "transforms.a", "transformsx"):
        monkeypatch.setitem(mp.sys.modules, name, types.ModuleType(name))
    proj = mp.Project.__new__(mp.Project)
    proj.root = tmp_path
    monkeypatch.setattr(mp.sys, "path", list(mp.sys.path))
    proj.purge_code()
    assert "transforms" not in mp.sys.modules and "transforms.a" not in mp.sys.modules
    assert "transformsx" in mp.sys.modules
    assert mp.sys.path[0] == str(tmp_path)


def _sample(tmp_path):
    from fabric_desktop.meta.templates import init_project
    return init_project(tmp_path / "project", "3", "Chris", True)


def test_the_summary_counts_each_layers_entities_and_names_who_uses_each_key_table(tmp_path):
    s = _sample(tmp_path).summary(dt.date(2026, 11, 14))
    assert [(l["name"], l["entities"]) for l in s["layers"]] == [("bronze", 2), ("silver", 2), ("gold", 2)]
    assert [(k["name"], k["used_by"]) for k in s["key_tables"]] == [
        ("customer", ["silver.customers", "silver.orders"]), ("order", ["silver.orders"])]


def test_an_entity_summary_falls_back_to_a_custom_step_and_reads_its_criticality(tmp_path):
    e = mp.Entity("gold.t", {"step": {"custom": "transforms.x:f"}, "objectives": {"criticality": "high"}},
                  tmp_path / "t.toml")
    s = e.summary()
    assert s["source"] == "custom step" and s["criticality"] == "high"
    assert mp.Entity("gold.t", {"objectives": "high"}, tmp_path / "t.toml").summary()["criticality"] is None
    assert mp.Entity("gold.t", {}, tmp_path / "t.toml").summary()["source"] == ""


# ---- the save gate

TODAY = dt.date(2026, 11, 14)


def _problems(proj, name):
    return [p for p in mp.validate(proj, TODAY) if p.startswith(f"{name}:")]


def test_the_sample_passes_the_gate(tmp_path):
    assert mp.validate(_sample(tmp_path), TODAY) == []


def test_three_part_names_are_allowed_and_four_are_not():
    assert mp.entity_name_problem("silver.sales.orders") is None
    assert mp.entity_name_problem("a.b.c.d") == "name must be <layer>.<table> or <layer>.<schema>.<table>"


def test_a_lookback_on_a_file_source_needs_a_delta_source(tmp_path):
    proj = _sample(tmp_path)
    e = proj.entities["silver.orders"]
    e.spec["source"] = {"files": "landing/orders/*.csv"}
    e.spec["load"]["lookback"] = "2 hours"
    assert "silver.orders: load lookback needs a Delta source (table, link or entity); file sources read new files " \
           "instead of a watermark" in _problems(proj, "silver.orders")


def test_a_watermark_the_source_renames_must_be_given_by_its_new_name(tmp_path):
    proj = _sample(tmp_path)
    e = proj.entities["silver.orders"]
    e.spec["source"]["rename"] = {"changed": "modified_at"}
    e.spec["load"]["watermark"] = "changed"
    assert ('silver.orders: the watermark is a contract column: the source renames changed to modified_at, so use '
            'watermark = "modified_at"') in _problems(proj, "silver.orders")


def test_a_not_null_check_alone_is_not_a_unique_check(tmp_path):
    proj = _sample(tmp_path)
    e = proj.entities["silver.orders"]
    e.spec["check"][0] = {"column": "order_id", "not_null": True, "on_fail": "stop"}
    assert _problems(proj, "silver.orders") == ["silver.orders: layer silver requires a key unique check on the key"]


def test_a_clean_rule_on_an_unknown_column_is_named(tmp_path):
    proj = _sample(tmp_path)
    proj.entities["silver.orders"].spec["clean"].append({"column": "nope", "trim": True})
    assert _problems(proj, "silver.orders") == ["silver.orders: clean rule uses unknown column nope"]


def test_on_fail_must_be_stop_quarantine_or_warn(tmp_path):
    proj = _sample(tmp_path)
    proj.entities["silver.orders"].spec["check"][1]["on_fail"] = "shout"
    assert _problems(proj, "silver.orders") == ["silver.orders: on_fail must be stop, quarantine or warn"]


def test_exists_in_a_missing_column_of_a_managed_entity_is_named(tmp_path):
    proj = _sample(tmp_path)
    proj.entities["silver.orders"].spec["check"][1]["exists_in"] = "silver.customers.nope"
    assert _problems(proj, "silver.orders") == ["silver.orders: check exists_in silver.customers.nope: no such column"]


def test_exists_in_an_entity_that_is_not_managed_is_a_read_problem_only(tmp_path):
    proj = _sample(tmp_path)
    proj.entities["silver.orders"].spec["check"][1]["exists_in"] = "silver.gone.customer_id"
    assert _problems(proj, "silver.orders") == ["silver.orders: reads silver.gone, which is not a managed entity"]


def test_a_lookup_on_an_entity_that_is_not_a_dimension_is_named(tmp_path):
    proj = _sample(tmp_path)
    proj.entities["gold.fact_sales"].spec["lookup"][0]["dim"] = "gold.dim_gone"
    assert _problems(proj, "gold.fact_sales") == ["gold.fact_sales: reads gold.dim_gone, which is not a managed entity"]
    proj.entities["gold.fact_sales"].spec["lookup"][0]["dim"] = "silver.orders"
    assert "gold.fact_sales: lookup silver.orders is not a dimension" in _problems(proj, "gold.fact_sales")


def test_a_lookup_without_an_at_column_is_fine_and_an_unknown_one_is_named(tmp_path):
    proj = _sample(tmp_path)
    lookup = proj.entities["gold.fact_sales"].spec["lookup"][0]
    del lookup["at"]
    assert _problems(proj, "gold.fact_sales") == []
    lookup["at"] = "nope"
    assert _problems(proj, "gold.fact_sales") == ["gold.fact_sales: lookup uses unknown column nope"]


def test_custom_code_needs_a_good_reference_an_owner_and_a_test_file(tmp_path):
    proj = _sample(tmp_path)
    (proj.root / "tests").mkdir(exist_ok=True)
    (proj.root / "tests" / "test_rules.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    checks = proj.entities["silver.orders"].spec["check"]
    checks.append({"name": "c", "python": "bad", "owner": "Chris", "test": "tests/test_rules.py::test_a"})
    assert _problems(proj, "silver.orders") == [
        "silver.orders: custom check bad: must be transforms.<module>:<function>"]
    checks[-1] = {"name": "c", "python": "bad"}
    assert _problems(proj, "silver.orders") == [
        "silver.orders: custom check bad: must be transforms.<module>:<function>",
        "silver.orders: custom check bad needs an owner", "silver.orders: custom check bad needs a test"]


def test_an_exception_needs_both_a_reason_and_an_approver(tmp_path):
    proj = _sample(tmp_path)
    proj.exceptions = [{"entity": "silver.orders", "rule": "owner", "reason": "r"},
                       {"entity": "silver.orders", "rule": "owner", "approver": "a"},
                       {"entity": "silver.orders", "rule": "owner", "reason": "r", "approver": "a"}]
    assert mp.validate(proj, TODAY) == ["silver.orders: exception for owner needs a reason and an approver"]


def test_new_layers_are_kept_when_only_problems_unrelated_to_layers_remain(tmp_path):
    proj = _sample(tmp_path)
    path = proj.entities["silver.orders"].path
    path.write_text("\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                              if not line.startswith("owner")) + "\n", encoding="utf-8")
    proj.reload()
    assert _problems(proj, "silver.orders") == ["silver.orders: needs an owner"]
    layers = [mp.Layer(**{k: v for k, v in l.to_dict().items() if k != "lakehouse"}) for l in proj.layers]
    layers[0].description = "Raw files"
    proj.set_layers(layers)
    assert proj.layers[0].description == "Raw files"
