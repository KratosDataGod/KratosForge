"""A metadata project (meta/project.py): what mutation testing (batch 14, docs/quality/mutation-testing.md)
showed no test checked. Each test works on the sample project, changed in memory or in its own folder;
nothing runs."""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib

import pytest

from fabric_desktop.meta import project as P
from fabric_desktop.meta.project import GateError, Layer, describe_exceptions, validate
from fabric_desktop.meta.templates import init_project

TODAY = dt.date(2026, 11, 14)


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "project", "3", "Chris", True)


def problems(proj, name):
    return [e for e in validate(proj, TODAY) if e.startswith(f"{name}:")]


def layers_of(proj):
    return [Layer(**{k: v for k, v in l.to_dict().items() if k != "lakehouse"}) for l in proj.layers]


# ---- small readers


def test_max_deletes_takes_up_to_a_hundred_percent():
    assert P.parse_max_deletes("100%") == ("share", 1.0)
    with pytest.raises(ValueError):
        P.parse_max_deletes("100.5%")


def test_settings_that_only_change_how_a_run_reads_dont_change_the_logic(proj):
    e = proj.entities["silver.orders"]
    before = e.logic_hash()
    assert len(before) == 8 and int(before, 16) >= 0
    e.spec["load"]["lookback"] = "2 hours"
    e.spec["load"]["batch_by"] = "order_date"
    assert e.logic_hash() == before
    e.spec["load"]["watermark"] = "order_date"  # how the rows are picked: a logic change
    assert e.logic_hash() != before


def test_soft_deletes_are_only_for_an_incremental_load(proj):
    e = proj.entities["silver.orders"]
    assert e.soft_deletes is False and not set(P.DELETE_COLUMNS) & e.mode_columns()
    e.spec["load"]["deletes"] = "soft"
    assert e.soft_deletes is True
    assert set(P.DELETE_COLUMNS) <= e.mode_columns()
    e.spec["load"]["mode"] = "full"
    assert e.soft_deletes is False


def test_mode_columns_by_load_mode(proj):
    dim = proj.entities["gold.dim_customer"]
    assert dim.mode_columns() == {"customer_sk", *P.SCD2_COLUMNS}
    dim.spec["load"]["mode"] = "scd1"
    assert dim.mode_columns() == {"customer_sk"}
    dim.spec["scd"].pop("surrogate_key")
    assert dim.mode_columns() == set()
    fact = proj.entities["gold.fact_sales"]
    assert "customer_sk" in fact.mode_columns()  # the lookup's column
    fact.spec["scd"] = {"surrogate_key": "sales_sk"}
    assert "sales_sk" not in fact.mode_columns()  # a surrogate key is a dimension's only


def test_summary_names_the_source(proj):
    e = proj.entities["bronze.orders"]
    assert e.summary()["source"] == "landing/orders/*.csv"
    e.spec["source"] = {"landing": "shop/*.csv"}
    assert e.summary()["source"] == "landing shop/*.csv"
    e.spec["source"] = {"link": "crm"}
    assert e.summary()["source"] == "link crm"
    assert proj.entities["silver.orders"].summary()["source"] == "bronze.orders"


# ---- the layers file


def test_the_layers_file_keeps_the_rules_and_a_retention_other_than_the_default(proj):
    layers = layers_of(proj)
    layers[2].retention_days = 30
    proj.set_layers(layers)
    text = (proj.root / "layers.toml").read_text(encoding="utf-8")
    assert "[rules]" in text and 'same_layer_reads = "dag"' in text
    assert text.count("retention_days") == 1 and "retention_days = 30" in text
    assert proj.rules == {"same_layer_reads": "dag"}
    assert [l.retention_days for l in proj.layers] == [7, 7, 30]


def test_the_layers_file_has_no_rules_section_without_rules(tmp_path):
    P.write_layers_file(tmp_path, [Layer("bronze")], {})
    text = (tmp_path / "layers.toml").read_text(encoding="utf-8")
    assert "[rules]" not in text and "retention_days" not in text
    assert 'name = "bronze"' in text


@pytest.mark.parametrize("days, ok", [(P.DEFAULT_RETENTION_DAYS, True), (P.DEFAULT_RETENTION_DAYS - 1, False),
                                      (P.MAX_RETENTION_DAYS, True), (P.MAX_RETENTION_DAYS + 1, False)])
def test_a_layer_keeps_versions_for_seven_days_to_ten_years(proj, days, ok):
    assert P.DEFAULT_RETENTION_DAYS == 7
    layers = layers_of(proj)
    layers[0].retention_days = days
    if ok:
        proj.set_layers(layers)
        assert proj.layers[0].retention_days == days
    else:
        with pytest.raises(GateError) as raised:
            proj.set_layers(layers)
        assert raised.value.errors == ["layer bronze: keep versions for 7 to 3650 days (Fabric needs at least 7)"]


def test_a_project_may_have_ten_layers_but_not_eleven(proj):
    base = layers_of(proj)
    ten = base + [Layer(f"extra{i}") for i in range(P.MAX_LAYERS - len(base))]
    proj.set_layers(ten)
    assert len(proj.layers) == 10
    with pytest.raises(GateError) as raised:
        proj.set_layers(ten + [Layer("one_more")])
    assert raised.value.errors == ["a project can have at most 10 layers"]


def test_a_layer_name_must_be_lowercase(proj):
    layers = layers_of(proj)
    layers[0].name = "Bronze"
    with pytest.raises(GateError) as raised:
        proj.set_layers(layers)
    assert any("layer name 'Bronze' must be lowercase" in e for e in raised.value.errors)


def test_a_layer_list_that_breaks_a_read_is_refused_and_the_old_one_kept(proj):
    before = (proj.root / "layers.toml").read_text(encoding="utf-8")
    layers = layers_of(proj)
    layers[2].reads_from = ["bronze"]  # gold reads silver
    with pytest.raises(GateError) as raised:
        proj.set_layers(layers)
    assert any("may only read from bronze, not silver" in e for e in raised.value.errors)
    assert (proj.root / "layers.toml").read_text(encoding="utf-8") == before
    assert proj.layers[2].reads_from == []


# ---- loading


def test_an_entity_file_named_unlike_its_entity_is_reported(proj):
    text = (proj.entities_dir / "bronze.orders.toml").read_text(encoding="utf-8")
    (proj.entities_dir / "bronze.orders2.toml").write_text(text, encoding="utf-8")
    proj.reload()
    assert "bronze.orders2.toml: file name must match its name (bronze.orders)" in proj.load_errors


def test_missing_environments_get_their_defaults(proj):
    (proj.root / "environments.toml").write_text('[test]\nworkspace_id = "w1"\n', encoding="utf-8")
    proj.reload()
    assert proj.environments["dev"] == {"target": "local"}
    assert proj.environments["test"] == {"workspace_id": "w1"}
    assert proj.environments["prod"] == {"workspace_id": ""}


def test_the_fingerprint_counts_the_python_an_entity_runs(proj):
    plain, custom = proj.entities["silver.customers"], proj.entities["silver.orders"]
    custom.spec["hooks"] = {"before_write": "transforms.common:round_money", "owner": "Chris"}
    fp_plain, fp_custom = proj.fingerprint(plain), proj.fingerprint(custom)
    assert len(fp_plain) == 8 and len(fp_custom) == 8
    assert fp_plain == hashlib.sha1(plain.logic_hash().encode()).hexdigest()[:8]  # no Python: the logic only
    (proj.root / "transforms" / "common.py").write_text("# changed\n", encoding="utf-8")
    assert proj.fingerprint(plain) == fp_plain  # no Python of its own
    assert proj.fingerprint(custom) != fp_custom


# ---- exceptions


def test_an_exception_is_in_force_until_the_end_of_its_last_day(proj):
    proj.exceptions = [{"entity": "gold.fact_sales", "rule": "owner", "expires": "2026-11-14"},
                       {"entity": "gold.fact_sales", "rule": "custom_code_test", "expires": "permanent"}]
    assert proj.active_exception("gold.fact_sales", "owner", TODAY) is proj.exceptions[0]
    assert proj.active_exception("gold.fact_sales", "owner", TODAY + dt.timedelta(days=1)) is None
    assert proj.active_exception("gold.fact_sales", "custom_code_test", TODAY + dt.timedelta(days=999))
    assert proj.active_exception("gold.fact_sales", "key_unique", TODAY) is None
    assert proj.active_exception("silver.orders", "owner", TODAY) is None


def test_an_exception_waives_its_rule_only(proj):
    proj.entities["gold.fact_sales"].spec.pop("owner")
    assert "gold.fact_sales: needs an owner" in problems(proj, "gold.fact_sales")
    proj.exceptions = [{"entity": "gold.fact_sales", "rule": "owner", "expires": "permanent", "reason": "r",
                        "approver": "a"}]
    assert "gold.fact_sales: needs an owner" not in problems(proj, "gold.fact_sales")


@pytest.mark.parametrize("days, status, when", [(0, "expiring", "today"), (1, "expiring", "in 1 day"),
                                                (14, "expiring", "in 14 days"), (15, "active", None),
                                                (-1, "expired", None)])
def test_describe_exceptions_by_days_left(proj, days, status, when):
    expires = (TODAY + dt.timedelta(days=days)).isoformat()
    proj.exceptions = [{"entity": "gold.fact_sales", "rule": "owner", "expires": expires, "reason": "r",
                        "approver": "Ann"}]
    [row] = describe_exceptions(proj, TODAY)
    assert row["status"] == status and row["days_left"] == days
    assert row["owner"] == "Chris" and row["approver"] == "Ann" and row["reason"] == "r"
    if when:
        assert row["notice"] == f"gold.fact_sales: the exception for owner expires {when} ({expires})"
    elif status == "active":
        assert row["notice"] == ""
    else:
        assert row["notice"].startswith(f"gold.fact_sales: the exception for owner expired on {expires}.")


def test_describe_exceptions_permanent_and_unreadable(proj):
    proj.exceptions = [{"entity": "", "rule": "owner"}, {"entity": "nope", "rule": "owner", "expires": "soon"}]
    perm, bad = describe_exceptions(proj, TODAY)
    assert perm["status"] == "permanent" and perm["notice"] == "" and perm["days_left"] is None
    assert bad["status"] == "invalid" and bad["owner"] == ""
    assert bad["notice"] == "nope: the exception for owner has an expiry that isn't a date (soon)"


def test_the_summary_carries_the_exception_notices(proj):
    proj.exceptions = [{"entity": "gold.fact_sales", "rule": "owner", "expires": TODAY.isoformat(),
                        "reason": "r", "approver": "a"}]
    s = proj.summary(TODAY)
    assert [n["status"] for n in s["exception_notices"]] == ["expiring"]
    assert s["run_order"][:2] == ["bronze.customers", "bronze.orders"]


def test_the_save_gate_names_expired_and_unreadable_exceptions(proj):
    proj.exceptions = [{"entity": "a.b", "rule": "owner", "expires": "2026-11-13", "reason": "r", "approver": "x"},
                       {"entity": "a.c", "rule": "owner", "expires": "2026-11-14", "reason": "r", "approver": "x"},
                       {"entity": "a.d", "rule": "owner", "expires": "later", "reason": "r", "approver": "x"}]
    errs = validate(proj, TODAY)
    assert "a.b: exception for owner expired on 2026-11-13" in errs
    assert not any(e.startswith("a.c:") for e in errs)
    assert "a.d: exception expiry later is not a date" in errs


# ---- the save gate


def test_a_link_source_reads_its_columns_from_the_link_then_the_contract(proj):
    e = proj.entities["silver.orders"]
    e.spec["source"] = {"link": "crm"}
    assert P.source_columns(proj, e) == set(e.columns)  # no such link: the contract
    e.spec["source"] = {"link": "crm", "columns": ["a"]}
    assert P.source_columns(proj, e) == {"a"}


def test_batch_by_must_be_a_column_the_source_has(proj):
    e = proj.entities["silver.orders"]
    assert "net_amount" in e.derived
    e.spec["load"]["batch_by"] = "net_amount"
    assert "silver.orders: load batch_by must be a column the source has, not one this entity makes" \
        in problems(proj, "silver.orders")
    e.spec["load"]["batch_by"] = "order_date"
    assert not any("batch_by" in p for p in problems(proj, "silver.orders"))
    e.spec["hooks"] = {"adds": ["order_date"]}
    assert any("batch_by must be a column the source has" in p for p in problems(proj, "silver.orders"))


def test_lookback_needs_a_watermark_and_keys(proj):
    e = proj.entities["silver.orders"]
    e.spec["load"]["lookback"] = "2 hours"
    assert not any("lookback" in p for p in problems(proj, "silver.orders"))
    e.spec["contract"]["keys"] = []
    assert "silver.orders: load lookback reads rows again, so it needs [contract] keys for them to replace " \
           "themselves instead of landing twice" in problems(proj, "silver.orders")
    e.spec["load"].pop("watermark")
    assert "silver.orders: load lookback is for incremental loads with a watermark" in problems(proj, "silver.orders")


def test_deletes_need_keys_the_source_has(proj):
    e = proj.entities["silver.orders"]
    e.spec["load"]["deletes"] = "hard"
    assert not any("deletes" in p for p in problems(proj, "silver.orders"))
    e.spec.setdefault("derive", []).append({"column": "order_id", "expr": "order_id"})
    assert "silver.orders: load deletes needs keys the source has; order_id is made by this entity" \
        in problems(proj, "silver.orders")


def test_soft_deletes_refuse_their_columns_in_the_contract(proj):
    e = proj.entities["silver.orders"]
    e.spec["load"]["deletes"] = "hard"
    e.spec["contract"]["columns"]["is_deleted"] = "bool"
    assert not any("adds is_deleted" in p for p in problems(proj, "silver.orders"))
    e.spec["load"]["deletes"] = "soft"
    assert any('load deletes = "soft" adds is_deleted itself' in p for p in problems(proj, "silver.orders"))


@pytest.mark.parametrize("path, bad", [("landing/x/*.csv", False), ("<abs>", True), ("C:x.csv", True)])
def test_a_source_path_on_this_pc_is_refused(proj, tmp_path, path, bad):
    # tmp_path is absolute on every platform ("/data/x.csv" has no drive, so Windows doesn't call it absolute)
    path = str(tmp_path / "x.csv") if path == "<abs>" else path
    e = proj.entities["bronze.orders"]
    e.spec["source"] = {"landing": path}
    hit = any("only exists on this PC" in p for p in problems(proj, "bronze.orders"))
    assert hit is bad


@pytest.mark.parametrize("path, bad", [("/data/x.csv", True), ("\\data\\x.csv", True), ("\\\\server\\share\\x.csv", True),
                                       ("landing/x.csv", False), ("Files/landing/x.csv", False)])
@pytest.mark.parametrize("flavour", [pathlib.PurePosixPath, pathlib.PureWindowsPath])
def test_a_rooted_source_path_is_on_this_pc_on_either_platform(proj, monkeypatch, flavour, path, bad):
    # On Windows "/data/x.csv" has no drive, so PureWindowsPath doesn't call it absolute; it's still this PC's drive
    monkeypatch.setattr(P, "Path", flavour)
    e = proj.entities["bronze.orders"]
    e.spec["source"] = {"landing": path}
    assert any("only exists on this PC" in p for p in problems(proj, "bronze.orders")) is bad


def test_overwrite_schema_is_true_or_false(proj):
    e = proj.entities["silver.orders"]
    e.spec["contract"]["overwrite_schema"] = True
    assert not any("overwrite_schema" in p for p in problems(proj, "silver.orders"))
    e.spec["contract"]["overwrite_schema"] = "yes"
    assert "silver.orders: contract overwrite_schema is true or false" in problems(proj, "silver.orders")


def test_same_layer_reads_follow_the_rule(proj):
    e = proj.entities["silver.orders"]
    e.spec["hooks"] = {"reads": ["silver.customers"]}
    assert not any("own layer" in p for p in problems(proj, "silver.orders"))
    proj.rules["same_layer_reads"] = "never"
    assert "silver.orders: reads silver.customers from its own layer" in problems(proj, "silver.orders")


def test_a_layer_may_read_the_layers_it_names(proj):
    proj.layers[2].reads_from = ["silver"]
    assert not any("may only read" in p for p in validate(proj, TODAY))
    proj.layers[2].reads_from = ["bronze"]
    assert "gold.fact_sales: layer gold may only read from bronze, not silver" in validate(proj, TODAY)


def test_a_not_null_check_counts_only_on_a_key(proj):
    e = proj.entities["silver.orders"]
    e.spec["check"] = [{"column": "order_id", "unique": True}, {"column": "status", "not_null": True}]
    assert "silver.orders: layer silver requires a key not null check on the key" in problems(proj, "silver.orders")
    e.spec["check"].append({"column": "order_id", "not_null": True})
    assert not any("requires" in p for p in problems(proj, "silver.orders"))


def test_a_custom_step_needs_no_source_columns(proj):
    e = proj.entities["silver.orders"]
    e.spec["contract"]["columns"]["brand_new"] = "string"
    assert "silver.orders: contract column brand_new is not in the source and nothing derives it" \
        in problems(proj, "silver.orders")
    e.spec["step"] = {"custom": "transforms.common:round_money", "owner": "Chris"}
    assert not any("brand_new" in p for p in problems(proj, "silver.orders"))


def test_a_custom_function_that_cant_be_imported_says_why(proj):
    assert P.check_python("transforms.nothing_here:fn").startswith("cannot be imported (ModuleNotFoundError: ")
    assert P.check_python("transforms.common:round_money") == "must take (df, ctx)"
    assert P.check_python("os:path") == "must be transforms.<module>:<function>"


# ---- order and cycles


def test_a_cycle_is_found_and_named(proj):
    assert P.find_cycle(proj) is None
    proj.entities["silver.customers"].spec["hooks"] = {"reads": ["gold.dim_customer"]}
    cycle = P.find_cycle(proj)
    assert cycle == ["gold.dim_customer", "silver.customers", "gold.dim_customer"]
    s = proj.summary(TODAY)
    assert s["run_order"] == [] and any(e.startswith("dependency cycle: ") for e in s["errors"])


def test_a_three_entity_cycle_is_named_from_where_it_closes(proj):
    proj.entities["bronze.customers"].spec["hooks"] = {"reads": ["gold.dim_customer"]}
    assert P.find_cycle(proj) == ["bronze.customers", "gold.dim_customer", "silver.customers", "bronze.customers"]


def test_impact_lists_everything_downstream(proj):
    assert P.impact(proj, "bronze.customers") == ["silver.customers", "gold.dim_customer", "silver.orders",
                                                     "gold.fact_sales"]
    assert P.impact(proj, "gold.fact_sales") == []


def test_the_logic_hash_doesnt_depend_on_the_order_settings_are_written_in(proj):
    e = proj.entities["silver.orders"]
    before = e.logic_hash()
    e.spec["contract"] = dict(reversed(list(e.spec["contract"].items())))
    e.spec["load"] = dict(reversed(list(e.spec["load"].items())))
    assert e.logic_hash() == before


def test_a_long_name_in_a_misnamed_file_is_cut_to_eighty_characters(proj):
    long = "bronze." + "x" * 100
    (proj.entities_dir / "short.toml").write_text(f'name = "{long}"\n', encoding="utf-8")
    proj.reload()
    [err] = [e for e in proj.load_errors if e.startswith("short.toml:")]
    shown = err.split("(", 1)[1][:-1]
    assert len(shown) == 80 and shown.endswith("…") and long.startswith(shown[:-1])


def test_the_run_order_is_given_whatever_other_problems_the_project_has(proj):
    proj.entities["gold.fact_sales"].spec.pop("owner")
    s = proj.summary(TODAY)
    assert "gold.fact_sales: needs an owner" in s["errors"]
    assert s["run_order"] == P.run_order(proj) and len(s["run_order"]) == 6


def with_graph(proj, edges):
    """The project's entities replaced by ones that read only what ``edges`` gives, in that order."""
    proj.entities = {n: P.Entity(n, {"hooks": {"reads": r}}, proj.entities_dir / f"{n}.toml") for n, r in edges.items()}
    return proj


@pytest.mark.parametrize(("edges", "order", "cycle"), [
    ({"gold.z": ["silver.b", "silver.a"], "silver.b": ["bronze.y"], "silver.a": ["bronze.y", "bronze.x"],
      "bronze.y": [], "bronze.x": [], "other.q": ["gold.z"], "bronze.lone": []},
     ["bronze.lone", "bronze.x", "bronze.y", "silver.a", "silver.b", "gold.z", "other.q"], None),
    ({"bronze.top": ["gold.b", "gold.a"], "gold.b": ["silver.d"], "gold.a": ["silver.c", "silver.d"],
      "silver.c": [], "silver.d": []},
     ["silver.d", "gold.b", "silver.c", "gold.a", "bronze.top"], None),
    ({"bronze.a": ["silver.b"], "silver.b": ["silver.c"], "silver.c": ["gold.d"], "gold.d": ["silver.b"], "bronze.e": []},
     ["gold.d", "silver.c", "silver.b", "bronze.a", "bronze.e"], ["silver.b", "silver.c", "gold.d", "silver.b"]),
    ({"silver.m": ["silver.n", "bronze.k"], "silver.n": ["silver.m"], "bronze.k": ["gold.j"], "gold.j": ["bronze.k"]},
     ["gold.j", "bronze.k", "silver.n", "silver.m"], ["bronze.k", "gold.j", "bronze.k"]),
    ({"bronze.s": ["bronze.s", "bronze.nowhere"], "silver.t": ["bronze.s"]}, ["bronze.s", "silver.t"], None),
])
def test_order_and_cycles_on_small_graphs_are_pinned(proj, edges, order, cycle):
    with_graph(proj, edges)
    assert P.run_order(proj) == order
    assert P.find_cycle(proj) == cycle


def chain(n):
    # Each reads the next, so the names sort the opposite way to the order they run in.
    return {f"silver.t{i:04d}": [f"silver.t{i + 1:04d}"] if i + 1 < n else [] for i in range(n)}


def test_a_chain_of_five_thousand_tables_runs_in_order(proj):
    with_graph(proj, chain(5000))
    assert P.run_order(proj) == [f"silver.t{i:04d}" for i in reversed(range(5000))]
    assert P.find_cycle(proj) is None


@pytest.mark.parametrize("n", [3, 5000])
def test_a_long_cycle_is_named_like_a_short_one(proj, n):
    edges = chain(n)
    edges[f"silver.t{n - 1:04d}"] = ["silver.t0000"]
    with_graph(proj, edges)
    assert P.find_cycle(proj) == [f"silver.t{i:04d}" for i in range(n)] + ["silver.t0000"]
    assert P.run_order(proj) == [f"silver.t{i:04d}" for i in reversed(range(n))]
