"""A metadata project's data mesh files (meta/mesh.py), the paths the main tests don't reach: files
that aren't TOML or have another name, every save-gate refusal for products, links and catalogs,
promises nobody can check, and the promote rules' quieter cases. All on local disk."""

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


# ---- files


def test_files_that_arent_toml_or_dont_match_their_name_are_reported(proj):
    put(proj, mesh.PROJECT_FILE, "[project\n")
    put(proj, "metadata/products/broken.toml", "name = ")
    put(proj, "metadata/products/sales.toml", 'name = "orders"\n')
    _, products, _, errors = mesh.load(proj.root)
    assert any(e.startswith(f"{mesh.PROJECT_FILE}: not valid TOML") for e in errors)
    assert any(e.startswith("products/broken.toml: not valid TOML") for e in errors)
    assert "products/sales.toml: file name must match its name (orders)" in errors and "orders" in products


@pytest.mark.parametrize("call, says", [
    (lambda root: mesh.write_product(root, {"name": "Bad Name"}), "A product name is lowercase"),
    (lambda root: mesh.write_link(root, {"alias": "Bad"}), "A link's name is lowercase"),
])
def test_a_name_that_cant_be_a_file_is_refused(tmp_path, call, says):
    with pytest.raises(ValueError, match=says):
        call(tmp_path)


def test_a_product_and_link_are_written_with_every_field(tmp_path):
    path = mesh.write_product(tmp_path, {"name": "orders", "promise": {"within_hours": 24, "max_rejected_pct": 1.5,
                                                                      "envs": ["test", "prod"]},
                                         "ports": [{"name": "main", "entity": "silver.orders"}]})
    text = path.read_text()
    assert "max_rejected_pct = 1.5" in text and 'envs = ["test", "prod"]' in text
    text = mesh.write_link(tmp_path, {"alias": "crm", "product": "crm/customers", "port": "main",
                                      "description": "CRM customers"}).read_text()
    assert 'description = "CRM customers"' in text
    path = mesh.write_project_file(tmp_path, {"name": "sales", "contacts": ["a@contoso.com"]})
    assert 'contacts = ["a@contoso.com"]' in path.read_text()


# ---- the save gate


def test_every_product_refusal(proj):
    put(proj, "metadata/products/orders.toml",
        'name = "orders"\naccess = "everyone"\nstatus = "gone"\npromise = "daily"\n'
        '[[port]]\nname = "Main"\nentity = "silver.orders"\n[[port]]\nname = "Main"\nentity = "silver.orders"\n')
    put(proj, "metadata/products/old.toml", 'name = "old"\nstatus = "deprecated"\n')
    put(proj, mesh.PROJECT_FILE, '[project]\nname = "Sales Team"\nsubdomain = "emea"\n')
    proj.reload()
    errors = mesh.validate(proj)
    for want in ("project.toml: name 'Sales Team' must be lowercase letters, digits, -, _ or .",
                 "project.toml: a subdomain needs its domain",
                 "products/orders: access must be one of public, domain, private",
                 "products/orders: status must be one of draft, published, deprecated",
                 "products/orders: port name 'Main' must be lowercase letters, digits or _",
                 "products/orders: port Main is listed twice",
                 "products/orders: [promise] must be a table, like within_hours = 24",
                 "products/old: a product needs at least one [[port]]",
                 "products/old: a deprecated product needs deprecated_on, the date it stops"):
        assert want in errors, want


def test_every_link_refusal(proj):
    put(proj, "metadata/links/crm.toml",
        'alias = "crm"\nproduct = "customers"\nport = ""\naccepts = "soon"\n'
        '[reads]\ndev = "staging"\n[location.dev]\ntable = "C:/lake/customers"\n')
    proj.reload()
    errors = mesh.validate(proj)
    assert 'links/crm: product is "<project>/<product>" and port names one of its ports' in errors
    assert any(e.startswith("links/crm: ") and "accepts" in e for e in errors)
    assert "links/crm: reads maps your environment to the product's (dev, test or prod)" in errors
    assert "links/crm: location.dev table must be a OneLake address (abfss://...)" in errors


def test_catalog_refusals(proj):
    path = proj.root / "metadata" / "entities" / "silver.orders.toml"
    path.write_text('catalog = "x"\n' + path.read_text())
    other = proj.root / "metadata" / "entities" / "silver.customers.toml"
    other.write_text(other.read_text() + '\n[catalog]\nclassification = "secret"\n[catalog.columns.ghost]\n'
                     'classification = "secret"\n')
    proj.reload()
    errors = mesh.validate(proj)
    assert "silver.orders: catalog must be a [catalog] table" in errors
    assert "silver.customers: classification must be one of public, internal, confidential, restricted" in errors
    assert "silver.customers: catalog describes column ghost, which the table doesn't have" in errors
    assert "silver.customers: column ghost classification must be one of public, internal, confidential, restricted" in errors


# ---- promises


def test_a_promise_nobody_can_check_is_left_out_and_logged(proj, monkeypatch):
    put(proj, "metadata/products/orders.toml",
        'name = "orders"\nstatus = "published"\n[promise]\nwithin_hours = -1\n[[port]]\nname = "main"\nentity = "silver.orders"\n')
    put(proj, "metadata/products/quiet.toml", 'name = "quiet"\n[[port]]\nname = "main"\nentity = "silver.orders"\n')
    proj.reload()
    said = []
    monkeypatch.setattr(mesh._log, "warning", lambda msg, *args: said.append(msg % args))
    assert mesh.promises(proj) == {}
    assert len(said) == 1 and said[0].startswith("Product orders's promise is left out")
    assert mesh.describe_promise(None) == ""


# ---- promote rules


def test_a_draft_or_unpublished_product_breaks_nobody(proj):
    put(proj, "metadata/products/orders.toml",
        'name = "orders"\nstatus = "draft"\n[[port]]\nname = "main"\nentity = "silver.orders"\n')
    put(proj, "metadata/products/new.toml",
        'name = "new"\nstatus = "published"\n[[port]]\nname = "main"\nentity = "silver.orders"\n')
    proj.reload()
    assert mesh.breaking_changes(proj, [], "prod", TODAY) == ([], [])


@pytest.mark.parametrize("product, kind, says", [
    ({"status": "published"}, None, None),
    ({"status": "deprecated"}, "note", "with no stop date set yet"),
    ({"status": "deprecated", "deprecated_on": "someday"}, "note", "its stop date someday can't be read"),
    ({"status": "deprecated", "deprecated_on": "2026-01-01"}, "error", "was deprecated on 2026-01-01"),
    ({"status": "deprecated", "deprecated_on": "2027-01-01"}, "note", "stops on 2027-01-01"),
])
def test_links_to_deprecated_products(proj, product, kind, says):
    put(proj, "metadata/links/crm.toml", 'alias = "crm"\nproduct = "crm/customers"\nport = "main"\n')
    proj.reload()
    errors, notes = mesh.deprecated_links(proj, [{"id": "crm/customers", **product}], TODAY)
    got = errors if kind == "error" else notes
    assert (errors, notes) == ([], []) if kind is None else any(says in x for x in got)


def test_an_accepts_the_app_cant_read_shuts_a_version_out():
    assert mesh._shut_out({"accepts": "soon"}, "2") is True
    assert mesh._days_text(["mon", "wed", "fri"]) == "Mon, Wed, Fri"
