"""Impact of a column change: what breaks and what to check, worked out from the project's files."""

import pytest

from fabric_desktop.meta import change_impact
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


def rows(res):
    return {(i["severity"], i["kind"], i["name"]) for i in res["items"]}


def why(res, name):
    return " / ".join(next(i for i in res["items"] if i["name"] == name)["why"])


def test_dropping_a_lookup_column_breaks_the_fact_and_its_model_relationship(svc):
    res = svc.meta_impact("silver.orders", "customer_id", "drop")
    assert rows(res) == {("breaks", "table", "gold.fact_sales"), ("breaks", "table", "silver.orders"),
                         ("breaks", "model", "SM_Gold")}
    assert "matches customer_sk on customer_id" in why(res, "gold.fact_sales")
    assert "a [[check]] names customer_id" in why(res, "silver.orders")
    assert why(res, "SM_Gold") == "its relationship fact_sales[customer_sk] → dim_customer[customer_sk] loses a side"
    fact = next(i for i in res["items"] if i["name"] == "gold.fact_sales")
    assert fact["chain"] == ["silver.orders.customer_id", "gold.fact_sales.customer_sk"]
    assert res["counts"] == {"breaks": 3, "check": 0, "table_only": 0}


def test_a_rename_breaks_what_reads_the_old_name(svc):
    res = svc.meta_impact("silver.orders", "customer_id", "rename", new_name="cust_id")
    assert ("breaks", "table", "gold.fact_sales") in rows(res)
    assert "once it's renamed to cust_id" in why(res, "gold.fact_sales")


def test_a_dropped_source_column_breaks_its_copy_and_flags_what_is_built_further_on(svc):
    res = svc.meta_impact("bronze.orders", "discount", "drop")
    assert rows(res) == {("breaks", "table", "silver.orders"), ("check", "table", "gold.fact_sales")}
    assert "net_amount is built from silver.orders.net_amount, which breaks first" in why(res, "gold.fact_sales")
    # net_amount still exists once its expression is fixed, so reports on it aren't flagged


def test_a_copied_column_dropped_in_the_model_layer_shows_as_a_report_check(svc):
    res = svc.meta_impact("silver.customers", "segment", "drop")
    assert ("breaks", "table", "gold.dim_customer") in rows(res)
    assert ("check", "model", "SM_Gold") in rows(res)
    assert why(res, "SM_Gold") == "reports that use dim_customer[segment] lose it"
    assert res["table_only"] == ["silver.orders"]  # its check reads silver.customers.customer_id, not segment


def test_a_new_type_is_a_check_that_follows_plain_copies(svc):
    res = svc.meta_impact("silver.orders", "amount", "retype", new_type="decimal(18,2)")
    assert rows(res) == {("check", "table", "silver.orders"), ("check", "table", "gold.fact_sales"),
                         ("check", "model", "SM_Gold")}
    assert res["counts"]["breaks"] == 0
    assert why(res, "SM_Gold") == "visuals and totals on fact_sales[net_amount] may change"


def test_an_exists_in_check_elsewhere_breaks_when_its_column_goes(svc):
    res = svc.meta_impact("silver.customers", "customer_id", "drop")
    assert "its [[check]] on customer_id looks values up in silver.customers.customer_id" in why(res, "silver.orders")


def test_python_is_flagged_when_it_names_the_column_and_when_it_only_reads_the_table(svc, tmp_path):
    root = tmp_path / "crm"
    (root / "transforms").mkdir(exist_ok=True)
    (root / "transforms" / "__init__.py").write_text("", encoding="utf-8")
    (root / "transforms" / "fix.py").write_text("def run(df, ctx):\n    return df.drop('segment')\n", encoding="utf-8")
    (root / "transforms" / "plain.py").write_text("def run(df, ctx):\n    return df\n", encoding="utf-8")
    edit(svc, "gold.dim_customer", "[contract]", '[hooks]\nafter_clean = "transforms.fix:run"\n\n[contract]')
    edit(svc, "silver.orders", "[contract]", '[hooks]\nbefore_write = "transforms.plain:run"\nreads = ["silver.customers"]\n\n[contract]')
    res = svc.meta_impact("silver.customers", "segment", "drop")
    assert ("breaks", "python", "transforms.fix:run") in rows(res)
    assert "names segment (hook after_clean of gold.dim_customer)" in why(res, "transforms.fix:run")
    assert ("check", "python", "transforms.plain:run") in rows(res)
    assert "without naming segment" in why(res, "transforms.plain:run")
    (root / "transforms" / "plain.py").unlink()
    res = svc.meta_impact("silver.customers", "segment", "drop")
    assert "couldn't be read from transforms/" in why(res, "transforms.plain:run")


def test_python_outside_transforms_is_never_read(tmp_path):
    root = tmp_path / "proj"
    (root / "transforms").mkdir(parents=True)
    (tmp_path / "secret.py").write_text("segment = 1\n", encoding="utf-8")
    (root / "transforms" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert change_impact._module_file(root, "transforms.ok:run") == (root / "transforms" / "ok.py").resolve()
    for ref in ("transforms./../../secret:run", "transforms...secret:run", "transforms.a/../../../secret:run",
                "other.secret:run", "transforms:run"):
        assert change_impact._module_file(root, ref) is None, ref
    link = root / "transforms" / "out.py"
    try:
        link.symlink_to(tmp_path / "secret.py")
    except OSError:
        return  # no symlinks here (Windows without the right)
    assert change_impact._module_file(root, "transforms.out:run") is None


def test_a_transforms_folder_that_links_elsewhere_is_never_read(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "fix.py").write_text("segment = 1\n", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    try:
        (root / "transforms").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("no symlinks here (Windows without the right)")
    assert change_impact._module_file(root, "transforms.fix:run") is None


def test_bad_requests_are_refused(svc):
    with pytest.raises(LookupError):
        svc.meta_impact("silver.nope", "x", "drop")
    with pytest.raises(LookupError):
        svc.meta_impact("silver.orders", "nope", "drop")
    with pytest.raises(ValueError):
        svc.meta_impact("silver.orders", "amount", "explode")


def test_edit_changes_reads_drops_renames_and_new_types():
    old = {"a": "int64", "b": "string", "c": "date"}
    assert change_impact.edit_changes(old, {"a": "int64", "b": "string"}) == [{"column": "c", "change": "drop"}]
    assert change_impact.edit_changes(old, {"a": "int64", "b2": "string", "c": "date"}) == [
        {"column": "b", "change": "rename", "new_name": "b2"}]
    assert change_impact.edit_changes(old, {"a": "int64", "b2": "int64", "c": "date"}) == [{"column": "b", "change": "drop"}]
    assert change_impact.edit_changes(old, {"a": "float64", "b": "string", "c": "date"}) == [
        {"column": "a", "change": "retype", "new_type": "float64"}]
    assert change_impact.edit_changes(old, dict(old)) == []


def test_before_a_save_only_changes_that_touch_something_are_returned(svc):
    text = svc.meta_entity("silver.orders")["text"]
    out = svc.meta_edit_impact("silver.orders", text.replace('customer_id = "int64"\n', 'cust_id = "int64"\n', 1))
    assert [(c["column"], c["change"], c["new_name"]) for c in out["changes"]] == [("customer_id", "rename", "cust_id")]
    assert out["changes"][0]["counts"]["breaks"] == 3
    assert svc.meta_edit_impact("silver.orders", text)["changes"] == []
    assert svc.meta_edit_impact("silver.orders", "not [toml")["changes"] == []
    assert svc.meta_edit_impact("silver.brand_new", text)["changes"] == []
    # nothing was saved
    assert svc.meta_entity("silver.orders")["text"] == text
