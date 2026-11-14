"""meta/mesh.py paths that mutation testing (batch 12, docs/quality/mutation-testing.md) showed no test
checked: the files written for links and the project, what summaries and catalogs hold, the save gate's
quieter rules, and the promote rule's advice. All on local disk."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop.meta import mesh
from fabric_desktop.meta.templates import init_project

TODAY = dt.date(2026, 11, 9)


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "project", "3", "Chris", True)


def put(p, rel, text):
    path = p.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _entity(proj, name, extra):
    path = proj.root / "metadata" / "entities" / f"{name}.toml"
    path.write_text(path.read_text() + "\n" + extra)


# ---- files written

def test_the_project_file_says_its_format(tmp_path):
    text = mesh.write_project_file(tmp_path, {"name": "sales"}).read_text()
    assert "format = 2\n" in text


def test_a_link_file_holds_its_reads_and_only_real_pinned_environments(tmp_path):
    text = mesh.write_link(tmp_path, {"alias": "crm", "product": "crm/customers", "port": "main",
                                      "reads": {"dev": "test", "qa": "prod", "prod": ""},
                                      "pinned": {"prod": "abfss://w@onelake/t", "qa": "abfss://x", "test": ""}}).read_text()
    assert '[reads]\ndev = "test"\n' in text and "qa" not in text and "prod = " not in text.split("[reads]")[1].split("\n\n")[0]
    assert '[location.prod]\ntable = "abfss://w@onelake/t"\n' in text
    assert "[location.test]" not in text and "[location.qa]" not in text
    plain = mesh.write_link(tmp_path, {"alias": "erp", "product": "erp/orders", "port": "main"}).read_text()
    assert "[reads]" not in plain and "[location" not in plain


def test_a_long_name_that_doesnt_match_its_file_is_cut_to_80_characters(proj):
    long = "x" * 100
    put(proj, "metadata/products/sales.toml", f'name = "{long}"\n')
    _, _, _, errors = mesh.load(proj.root)
    assert f"products/sales.toml: file name must match its name ({'x' * 79}…)" in errors


# ---- what summaries and catalogs hold

def test_a_link_summary_and_its_pinned_tables(tmp_path):
    path = mesh.write_link(tmp_path, {"alias": "crm", "product": "crm/customers", "port": "main",
                                      "reads": {"dev": "test"}, "pinned": {"prod": "abfss://w@onelake/t"}})
    _, _, links, _ = mesh.load(tmp_path)
    link = links["crm"]
    assert link.location("prod") == "abfss://w@onelake/t" and link.location("dev") == ""
    assert link.summary() == {"alias": "crm", "product": "crm/customers", "port": "main", "accepts": "",
                              "reads": {"dev": "test", "test": "test", "prod": "prod"},
                              "pinned": {"prod": "abfss://w@onelake/t"}, "columns": {}, "description": "",
                              "path": str(path)}


def test_a_product_without_a_version_is_version_1_and_a_project_without_a_format_is_1(proj):
    put(proj, "metadata/products/orders.toml", 'name = "orders"\n[[port]]\nname = "main"\nentity = "silver.orders"\n')
    put(proj, mesh.PROJECT_FILE, '[project]\nname = "sales"\nformat = 0\n')
    proj.reload()
    assert proj.products["orders"].summary()["version"] == "1"
    assert mesh.identity(proj)["format"] == 1
    put(proj, mesh.PROJECT_FILE, '[project]\nname = "sales"\n')
    proj.reload()
    assert mesh.identity(proj)["format"] == 1


def test_landing_readers_group_entities_by_source(proj):
    _entity(proj, "bronze.orders", "")
    readers = mesh.landing_readers(proj)
    assert isinstance(readers, dict)
    for e in proj.entities.values():
        e.spec.setdefault("source", {})["landing"] = None
    proj.entities["bronze.orders"].spec["source"]["landing"] = "/sap/orders/*.csv"
    proj.entities["bronze.customers"].spec["source"]["landing"] = "sap/customers/*.csv"
    assert mesh.landing_readers(proj) == {"sap": ["bronze.customers", "bronze.orders"]}


def test_an_entitys_starter_catalog_is_read_only_from_a_table(proj):
    _entity(proj, "silver.customers", '[catalog]\ndescription = "Customers"\nclassification = "internal"\n')
    proj.reload()
    cat = mesh.entity_catalog(proj.entities["silver.customers"])
    assert cat["description"] == "Customers" and cat["classification"] == "internal"
    assert mesh.entity_catalog(proj.entities["silver.orders"])["description"] == ""


def test_a_columns_saved_classification_wins_and_the_starter_fills_in(proj):
    _entity(proj, "silver.customers", '[catalog.columns.email]\nclassification = "confidential"\n'
                                      '[catalog.columns.name]\nclassification = "internal"\n')
    proj.reload()
    e = proj.entities["silver.customers"]
    key = f"{mesh.project_name(proj)}:{e.name}"
    out = mesh.merged_catalog(proj, e, {f"{key}#email": {"classification": "restricted"}})
    cols = {c["name"]: c["classification"] for c in out["columns"]}
    assert cols["email"] == "restricted" and cols["name"] == "internal"


# ---- the save gate

def test_known_workspace_designations_pass_and_unknown_ones_dont(proj):
    put(proj, "environments.toml", '[dev]\ntarget = "local"\n[dev.workspaces]\nproduct = "ws1"\n'
                                   '[test]\nworkspace_id = ""\n[test.workspaces]\nbogus = "ws2"\n[prod]\nworkspace_id = ""\n')
    proj.reload()
    errors = mesh.validate(proj)
    assert "environments.toml: test has an unknown workspace designation bogus" in errors
    assert not any("designation product" in e for e in errors)


def test_a_catalog_without_a_classification_and_a_known_link_pass(proj):
    _entity(proj, "silver.customers", '[catalog]\ndescription = "Customers"\n')
    put(proj, "metadata/links/crm.toml", 'alias = "crm"\nproduct = "crm/customers"\nport = "main"\n')
    path = proj.root / "metadata" / "entities" / "bronze.orders.toml"
    path.write_text(path.read_text().replace("[source]", '[source]\nlink = "crm"', 1))
    proj.reload()
    assert proj.entities["bronze.orders"].spec["source"].get("link") == "crm"
    assert mesh.validate(proj) == []


def test_a_promise_of_no_rejected_rows_is_allowed():
    assert mesh.promise_problems("products/x", {"max_rejected_pct": 0}) == []


# ---- promises and status

def test_a_promise_is_listed_once_per_product_for_each_entity(proj):
    for name in ("orders", "orders_eu"):
        put(proj, f"metadata/products/{name}.toml",
            f'name = "{name}"\nstatus = "published"\n[promise]\nwithin_hours = 24\n'
            '[[port]]\nname = "main"\nentity = "silver.orders"\n[[port]]\nname = "again"\nentity = "silver.orders"\n')
    proj.reload()
    assert [p["product"] for p in mesh.promises(proj)["silver.orders"]] == ["orders", "orders_eu"]


def test_a_status_with_more_than_two_reasons_counts_the_rest(proj):
    put(proj, "metadata/products/orders.toml",
        'name = "orders"\nstatus = "published"\n' + "".join(
            f'[[port]]\nname = "p{i}"\nentity = "{e}"\n' for i, e in enumerate(
                ("silver.orders", "silver.customers", "gold.fact_sales", "gold.dim_customer"))))
    proj.reload()
    incidents = [{"state": "open", "kind": "run_failed", "name": e}
                 for e in ("silver.orders", "silver.customers", "gold.fact_sales", "gold.dim_customer")]
    [status] = mesh.product_statuses(proj, incidents)
    assert status["status"] == "red"
    assert status["why"] == "Load failed: silver.orders; Load failed: silver.customers and 2 more"


# ---- promote

def test_a_deprecation_date_with_a_time_is_read_by_its_date():
    assert mesh._stops({"status": "deprecated", "deprecated_on": "2026-11-09T12:00:00Z"}) == dt.date(2026, 11, 9)


def test_the_advice_to_widen_accepts_keeps_the_readers_lowest_version(proj):
    put(proj, "metadata/products/orders.toml",
        'name = "orders"\nstatus = "published"\nversion = 4\n[[port]]\nname = "main"\nentity = "silver.orders"\n')
    proj.reload()
    me = mesh.project_name(proj)
    published = [{"id": f"{me}/orders", "version": "3",
                  "ports": [{"port": "main", "env": "prod", "columns": {"ghost": "string"}}],
                  "consumers": [{"consumer_project": "finance", "alias": "orders", "port": "main",
                                 "reads": {"prod": "prod"}, "accepts": "2-3"}]}]
    errors, _ = mesh.breaking_changes(proj, published, "prod", TODAY)
    assert len(errors) == 1 and 'accepts = "2-4"' in errors[0]
