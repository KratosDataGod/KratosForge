"""meta/mesh.py: promises with only some parts, save-gate names that break the rules, ports whose entity is
gone, breaking changes nobody reads or that a link's unreadable accepts shuts out, and other projects'
products. Plain stand-in projects; nothing is read from disk but what the test writes."""

from pathlib import Path
from types import SimpleNamespace

from fabric_desktop.meta import mesh


def test_a_promise_in_words_names_only_the_parts_it_has():
    assert mesh.describe_promise({"by": "06:00"}) == "by 06:00 each day"
    assert mesh.describe_promise({"within_hours": 2}) == "every 2 h"
    assert mesh.describe_promise({"max_rejected_pct": 0.5}) == "under 0.5 % rows rejected"


def test_an_empty_promise_table_the_save_gate_refuses_is_left_out():
    product = SimpleNamespace(name="customers", status="published", spec={"promise": {}}, promise=None,
                              ports=[{"name": "c", "entity": "silver.c"}])
    assert mesh.promises(SimpleNamespace(products={"customers": product})) == {}


def test_a_promise_with_only_a_time_of_day_is_written_without_hours(tmp_path):
    path = mesh.write_product(tmp_path, {"name": "customers", "promise": {"by": "06:00"},
                                         "ports": [{"name": "c", "entity": "silver.c"}]})
    text = path.read_text(encoding="utf-8")
    assert '[promise]\nby = "06:00"' in text and "within_hours" not in text


def _project(**kw):
    base = dict(mesh_errors=[], info={"name": "crm"}, root=Path("crm"), layers=[], environments={}, products={},
                links={}, entities={})
    return SimpleNamespace(**(base | kw))


def test_the_save_gate_names_bad_layer_workspaces_names_aliases_and_links():
    product = SimpleNamespace(name="Bad-Name", access="public", status="published", spec={},
                              ports=[{"name": "c", "entity": "silver.c"}])
    link = SimpleNamespace(alias="Bad-Alias", product="sales/orders", port="o", accepts="", spec={})
    entity = SimpleNamespace(name="silver.c", spec={"source": {"link": "missing"}})
    errors = mesh.validate(_project(layers=[SimpleNamespace(name="silver", workspace="bogus")],
                                    products={"Bad-Name": product}, links={"Bad-Alias": link},
                                    entities={"silver.c": entity}))
    assert f"layer silver: workspace must be one of {', '.join(mesh.DESIGNATIONS)}" in errors
    assert "products/Bad-Name: name must be lowercase letters, digits or _" in errors
    assert "links/Bad-Alias: name must be lowercase letters, digits or _" in errors
    assert "silver.c: reads link missing, which isn't in metadata/links" in errors


def test_a_port_whose_entity_is_gone_isnt_checked_for_documentation():
    product = SimpleNamespace(name="customers", access="public", status="published",
                              spec={"description": "All customers", "owner": "ana"}, ports=[{"entity": "silver.gone"}])
    assert mesh.publish_problems(_project(products={"customers": product}), {}) == []


def _breaking(consumers, version="1", current_version="1"):
    product = SimpleNamespace(name="customers", status="published", spec={"version": version},
                              ports=[{"name": "c", "entity": "silver.c"}])
    project = _project(products={"customers": product},
                       entities={"silver.c": SimpleNamespace(columns={"id": "int64"})})
    published = [{"id": "crm/customers", "version": current_version, "consumers": consumers,
                  "ports": [{"port": "c", "env": "prod", "columns": {"id": "int64", "email": "string"}}]}]
    return mesh.breaking_changes(project, published, "prod")


def test_a_dropped_column_on_a_port_nobody_reads_says_nothing():
    reader = {"consumer_project": "finance", "reads": {"prod": "prod"}, "port": "other"}
    assert _breaking([reader]) == ([], [])


def test_a_link_whose_accepts_cant_be_read_is_shut_out_of_a_new_version():
    reader = {"consumer_project": "finance", "alias": "cust", "reads": {"prod": "prod"}, "port": "c", "accepts": "soon"}
    errors, notes = _breaking([reader], version="2")
    assert notes == [] and len(errors) == 1
    assert "finance (link cust accepts soon)" in errors[0] and 'accepts = "2"' in errors[0]


def test_another_projects_products_have_no_readers_here():
    product = SimpleNamespace(name="customers", status="published")
    published = [{"id": "sales/orders", "consumers": [{"consumer_project": "finance", "reads": {"prod": "prod"}}]},
                 {"id": "crm/customers", "consumers": [{"consumer_project": "hr", "reads": {}}]}]
    assert mesh.window_readers(_project(products={"customers": product}), published, "prod") == ["hr"]
