"""Promote stops a product from dropping or retyping columns other projects read, unless its version goes up."""

import pytest

from fabric_desktop.meta import mesh
from fabric_desktop.meta.project import GateError
from fabric_desktop.service import Service

from test_meta import edit_entity


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    s.mesh_save_product({"name": "customers", "access": "public", "status": "published", "version": "1",
                         "ports": [{"name": "customers", "entity": "silver.customers"}]})
    project = s._project()
    cols = project.entities["silver.customers"].columns
    # As the last promote to Test left it in the catalog, with finance reading Test from its Dev.
    s._catalog().call("register_product", product={"project": "crm", "name": "customers", "access": "public",
                                                   "status": "published", "version": "1"},
                      ports=[{"port": "customers", "entity": "silver.customers", "table": "customers", "columns": cols}],
                      env="test")
    s._catalog().call("register_link", consumer="finance", alias="customers", product_id="crm/customers",
                      port="customers", reads={"dev": "test", "test": "test", "prod": "prod"})
    return s


def change(svc, old, new):
    edit_entity(svc._project().root, "silver.customers", old, new)
    svc._project().reload()


def test_dropping_a_column_another_project_reads_blocks_the_promote(svc):
    change(svc, 'email = "string"\n', "")
    check = svc.meta_promote_check("dev", "test")
    blocked = [e for e in check["errors"] if "drops" in e]
    assert blocked == ["products/customers: port customers drops email, and 1 other project reads it in test "
                       "(finance). Keep the column, or raise version in products/customers.toml to publish a "
                       "breaking version"]
    with pytest.raises(GateError, match="drops email"):
        svc.meta_promote("dev", "test")


def test_a_new_version_lets_the_breaking_change_through_and_says_so(svc):
    change(svc, 'segment = "string"', 'segment = "int64"')
    project = svc._project()
    published = svc._catalog().call("list_products")
    errors, _ = mesh.breaking_changes(project, published, "test")
    assert errors and "changes segment from string to int64" in errors[0]
    svc.mesh_save_product({"name": "customers", "access": "public", "status": "published", "version": "2",
                           "ports": [{"name": "customers", "entity": "silver.customers"}]})
    errors, notes = mesh.breaking_changes(svc._project(), published, "test")
    assert errors == [] and notes and notes[0].endswith("publishing it as version 2")


def test_added_columns_and_widening_are_not_breaking(svc):
    change(svc, 'email = "string"', 'email = "string"\nphone = "string"')
    change(svc, 'customer_id = "int64"', 'customer_id = "int64"')
    project = svc._project()
    published = svc._catalog().call("list_products")
    for port in published[0]["ports"]:
        port["columns"]["customer_sk"] = "int32"
    assert mesh.breaking_changes(project, published, "test") == ([], [])
    published[0]["ports"][0]["columns"]["customer_id"] = "int32"  # int32 to int64 widens
    assert mesh.breaking_changes(project, published, "test") == ([], [])


def test_nobody_reading_that_environment_means_no_block(svc):
    change(svc, 'email = "string"\n', "")
    published = svc._catalog().call("list_products")
    assert mesh.breaking_changes(svc._project(), published, "prod") == ([], [])  # finance reads prod, but nothing is published there
    published[0]["consumers"][0]["reads"] = {"dev": "prod", "test": "prod", "prod": "prod"}
    assert mesh.breaking_changes(svc._project(), published, "test") == ([], [])
    published[0]["consumers"][0].update(consumer_project="crm", reads={"dev": "test"})
    assert mesh.breaking_changes(svc._project(), published, "test") == ([], [])  # the project itself


def test_an_unreachable_catalog_is_a_note_not_a_block(svc, monkeypatch):
    change(svc, 'email = "string"\n', "")

    class Down:
        def call(self, op, **kw):
            raise ConnectionError("platform offline")
    monkeypatch.setattr(svc, "_catalog", lambda: Down())
    errors, notes = svc._breaking_gate(svc._project(), "test")
    assert errors == [] and "platform offline" in notes[0]
