"""Column change impact: the paths the main tests leave out (own config refs, diamonds further on, Python
that can't be read, model relationships on a new type, and edit_impact's guards)."""

from __future__ import annotations

import pytest

from fabric_desktop.meta import change_impact
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def edit(svc, name, old, new="", append=""):
    """Change the entity's file on disk directly: the save gate's owner/test rules aren't what's tested here."""
    path = svc._project().root / "metadata" / "entities" / f"{name}.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new or old, 1) + append, encoding="utf-8")


def rows(res):
    return {(i["severity"], i["kind"], i["name"]) for i in res["items"]}


def why(res, name):
    return " / ".join(next(i for i in res["items"] if i["name"] == name)["why"])


def add_fact_x(svc):
    """gold.fact_sales.x = amount + net_amount: it reads silver.orders.amount directly and fact_sales.net_amount."""
    edit(svc, "gold.fact_sales", 'modified_at = "date"\n', 'modified_at = "date"\nx = "float64"\n',
         append='\n[[derive]]\ncolumn = "x"\nexpr = "amount + net_amount"\n')


# ---------------------------------------------------------------- the entity's own file


def test_dropping_the_watermark_and_a_cleaned_column_fails_the_save_gate(svc):
    res = svc.meta_impact("silver.orders", "modified_at", "drop")
    assert "modified_at is the incremental watermark" in why(res, "silver.orders")
    res = svc.meta_impact("silver.orders", "status", "drop")
    assert "a [[clean]] step names status; the save gate refuses the change until you update it" in why(res, "silver.orders")
    assert ("breaks", "table", "silver.orders") in rows(res)


def test_scd_tracked_and_type1_columns_are_own_config_refs(svc):
    res = svc.meta_impact("gold.dim_customer", "segment", "rename", new_name="seg")
    assert "[scd] tracked lists segment" in why(res, "gold.dim_customer")
    res = svc.meta_impact("gold.dim_customer", "email", "drop")
    assert "[scd] type1 lists email" in why(res, "gold.dim_customer")
    # The model shows email, so reports on it are flagged too.
    assert why(res, "SM_Gold") == "reports that use dim_customer[email] lose it"


def test_a_key_column_is_its_tables_key(svc):
    res = svc.meta_impact("silver.orders", "order_id", "drop")
    assert "order_id is the table's key" in why(res, "silver.orders")


def test_a_rename_with_no_new_name_and_a_retype_with_no_type_still_read(svc):
    res = svc.meta_impact("silver.orders", "customer_id", "rename")
    assert "once it's renamed, building gold.fact_sales fails" in why(res, "gold.fact_sales")
    assert res["new_name"] is None
    res = svc.meta_impact("silver.orders", "customer_id", "retype")
    assert "the two sides of the match must keep the same type" in why(res, "gold.fact_sales")
    assert res["counts"]["breaks"] == 0


# ---------------------------------------------------------------- further on


def test_a_column_reached_twice_further_on_is_listed_once(svc):
    add_fact_x(svc)
    res = svc.meta_impact("silver.orders", "amount", "drop")
    fact = next(i for i in res["items"] if i["name"] == "gold.fact_sales")
    assert fact["severity"] == "breaks"   # x reads amount by name
    assert "its expression for x uses amount; once it's dropped, building gold.fact_sales fails" in fact["why"]
    # silver.orders' net_amount still finds amount in bronze.orders, so it (and gold's copy of it) still builds;
    # x was reached directly, so it is never listed again as built from something further on.
    assert not any("net_amount is built from" in w or "x is built from" in w for w in fact["why"])


def test_a_new_type_stops_at_columns_that_dont_copy_it(svc):
    add_fact_x(svc)
    res = svc.meta_impact("silver.orders", "discount", "retype", new_type="decimal(18,2)")
    fact = why(res, "gold.fact_sales")
    assert "net_amount carries the new type on from silver.orders.net_amount" in fact
    assert "x carries" not in fact and "x is built" not in fact


def test_a_check_then_a_break_on_one_consumer_keeps_the_worst_and_its_chain():
    items = change_impact._Items()
    items.add("table", "t", "check", "first", chain=["a"])
    items.add("table", "t", "breaks", "second", chain=["a", "b"])
    items.add("table", "t", "breaks", "second")
    items.add("table", "u", "check", "only")
    items.add("table", "u", "breaks", "worse")         # no chain: the first one stays
    items.add("python", "p", "check", "x")
    items.add("model", "m", "breaks", "y")
    out = items.list()
    assert [(i["kind"], i["name"], i["severity"]) for i in out] == [
        ("table", "t", "breaks"), ("table", "u", "breaks"), ("model", "m", "breaks"), ("python", "p", "check")]
    assert out[0]["why"] == ["first", "second"] and out[0]["chain"] == ["a", "b"]
    assert out[1]["chain"] == []
    assert items.names("table") == {"t", "u"}


# ---------------------------------------------------------------- Python


def test_python_that_isnt_utf8_is_flagged_as_unreadable(svc, tmp_path, caplog):
    root = tmp_path / "crm"
    (root / "transforms" / "bin.py").write_bytes(b"\xff\xfe\x00segment")
    edit(svc, "gold.dim_customer", "[contract]", '[hooks]\nafter_clean = "transforms.bin:run"\n\n[contract]')
    with caplog.at_level("INFO", logger="fabric_desktop.meta.change_impact"):
        res = svc.meta_impact("silver.customers", "segment", "drop")
    assert "couldn't be read from transforms/" in why(res, "transforms.bin:run")
    assert any("bin.py" in r.getMessage() for r in caplog.records)


def test_a_tables_own_hook_that_doesnt_name_the_column_isnt_listed(svc, tmp_path):
    root = tmp_path / "crm"
    (root / "transforms" / "plain.py").write_text("def run(df, ctx):\n    return df\n", encoding="utf-8")
    (root / "transforms" / "named.py").write_text("def run(df, ctx):\n    return df['status']\n", encoding="utf-8")
    edit(svc, "silver.orders", "[contract]",
         '[hooks]\nbefore_write = "transforms.plain:run"\nafter_clean = "transforms.named:run"\n\n[contract]')
    res = svc.meta_impact("silver.orders", "status", "retype", new_type="int64")
    names = {i["name"] for i in res["items"] if i["kind"] == "python"}
    assert names == {"transforms.named:run"}
    assert "names status (hook after_clean of silver.orders); check it works with the new type" in why(
        res, "transforms.named:run")


def test_a_python_ref_that_isnt_a_string_is_skipped(svc, monkeypatch):
    project = svc._project()
    e = project.entities["silver.orders"]
    monkeypatch.setattr(type(e), "python_refs", lambda self: [("hook", 5, None)], raising=True)
    res = change_impact.column_impact(project, "silver.orders", "status", "drop")
    assert not any(i["kind"] == "python" for i in res["items"])


# ---------------------------------------------------------------- the semantic model


def test_a_new_type_on_a_relationship_column_asks_for_both_sides(svc):
    res = svc.meta_impact("gold.fact_sales", "customer_sk", "retype", new_type="int32")
    assert ("check", "model", "SM_Gold") in rows(res)
    assert "its relationship fact_sales[customer_sk] → dim_customer[customer_sk] needs both sides to keep one type" in why(
        res, "SM_Gold")


# ---------------------------------------------------------------- edit_impact


def test_edit_impact_refuses_columns_that_arent_tables(svc):
    project = svc._project()
    assert change_impact.edit_impact(project, "silver.orders", {"contract": {"columns": ["a"]}}) == []


def test_edit_impact_skips_changes_that_touch_nothing_and_columns_lineage_doesnt_know(svc, monkeypatch):
    project = svc._project()
    old = dict(project.entities["silver.orders"].spec["contract"]["columns"])
    # Without its check expression, status is read by nothing, and silver.orders has no hooks: a new type
    # touches nothing.
    spec = project.entities["silver.orders"].spec
    spec["check"] = [c for c in spec["check"] if "expr" not in c]
    assert change_impact.edit_impact(project, "silver.orders",
                                     {"contract": {"columns": {**old, "status": "int64"}}}) == []
    real = change_impact.lineage.entity_columns

    def without_amount(p, name):
        cols = real(p, name)
        return {k: v for k, v in cols.items() if not (name == "silver.orders" and k == "amount")}

    monkeypatch.setattr(change_impact.lineage, "entity_columns", without_amount)
    assert change_impact.edit_impact(project, "silver.orders",
                                     {"contract": {"columns": {**old, "amount": "string"}}}) == []
