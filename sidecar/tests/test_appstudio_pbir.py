"""App Studio: specification -> PBIR report definition (AS-12). Shapes follow Microsoft Learn's report
definition and PBIR pages; nothing here calls Fabric."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.appstudio import pbir, spec
from fabric_desktop.appstudio.spec import SpecError
from test_appstudio import sales

MODEL = "0f8fad5b-d9cb-469f-a165-70867728950e"
TABLES = {"Revenue": "Sales", "Profit": "Sales"}


def parts(doc=None, **kw):
    return pbir.compile_report(doc or sales(), MODEL, kw.get("tables", TABLES))


def test_pbir_binds_by_connection_to_the_model():
    got = json.loads(parts()["parts"]["definition.pbir"])
    assert got["version"] == "4.0"
    assert got["datasetReference"] == {"byConnection": {"connectionString": f"semanticmodelid={MODEL}"}}
    assert got["$schema"].endswith("/definitionProperties/2.0.0/schema.json")


def test_pbir_folder_not_legacy_report_json():
    names = set(parts()["parts"])
    assert "report.json" not in names and "definition/report.json" in names
    assert {"definition/version.json", "definition/pages/pages.json",
            "definition/pages/pg_overview/page.json", "definition/pages/pg_customers/page.json",
            "definition/pages/pg_overview/visuals/c_region/visual.json"} <= names
    assert all("\\" not in n and not n.startswith("/") for n in names)


def test_pages_in_order_and_page_shape():
    p = parts()["parts"]
    order = json.loads(p["definition/pages/pages.json"])
    assert order["pageOrder"] == ["pg_overview", "pg_customers"] and order["activePageName"] == "pg_overview"
    page = json.loads(p["definition/pages/pg_overview/page.json"])
    assert page == {"$schema": pbir.SCHEMAS["page"], "name": "pg_overview", "displayName": "Overview",
                    "displayOption": "FitToPage", "height": 720, "width": 1280}


def test_bar_chart_matches_microsofts_example_shape():
    v = json.loads(parts()["parts"]["definition/pages/pg_overview/visuals/c_region/visual.json"])
    assert v["visual"]["visualType"] == "barChart"
    qs = v["visual"]["query"]["queryState"]
    assert qs["Category"]["projections"][0] == {
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": "Region"}}, "Property": "Name"}},
        "queryRef": "Region.Name", "active": True}
    assert qs["Y"]["projections"][0]["field"] == {"Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Revenue"}}
    assert v["position"] == {"x": 320.0, "y": 0, "z": 2000, "height": 240, "width": 960.0, "tabOrder": 2000}


def test_quoted_table_names_and_card_values():
    p = parts()["parts"]
    table = json.loads(p["definition/pages/pg_customers/visuals/c_cust/visual.json"])["visual"]
    assert table["visualType"] == "tableEx"
    refs = [x["queryRef"] for x in table["query"]["queryState"]["Values"]["projections"]]
    assert refs == ["Customer.Name", "Sales.Revenue", "Sales.Profit"]
    card = json.loads(p["definition/pages/pg_overview/visuals/c_rev/visual.json"])["visual"]
    assert card["visualType"] == "card" and list(card["query"]["queryState"]) == ["Values"]


def test_an_edit_changes_only_that_visual_and_output_is_deterministic():
    from fabric_desktop.appstudio import patch
    before = parts()["parts"]
    assert parts()["parts"] == before
    edited = patch.apply(sales(), [{"op": "set", "id": "c_region", "field": "type", "value": "column"}])["spec"]
    after = pbir.compile_report(edited, MODEL, TABLES)["parts"]
    changed = [k for k in before if before[k] != after[k]]
    assert changed == ["definition/pages/pg_overview/visuals/c_region/visual.json"]
    assert json.loads(after[changed[0]])["visual"]["visualType"] == "columnChart"


def test_unsupported_parts_are_named_not_dropped():
    warnings = parts()["warnings"]
    assert any("Filter f_region" in w for w in warnings)
    assert any("Drillthrough i_drill" in w for w in warnings)
    assert any("Theme colours" in w for w in warnings)
    assert any("titles" in w for w in warnings)


def test_refusals():
    with pytest.raises(SpecError, match="no measure named 'Profit'"):
        parts(tables={"Revenue": "Sales"})
    with pytest.raises(SpecError, match="semantic model"):
        pbir.compile_report(sales(), "not-an-id", TABLES)
    doc = sales()
    doc["target"] = "kratosforge-app"
    doc["interactions"] = []
    with pytest.raises(SpecError, match="Only a Power BI report"):
        pbir.compile_report(doc, MODEL, TABLES)
    doc = sales()
    doc["pages"][0]["components"][0]["type"] = "hologram"
    with pytest.raises(SpecError):
        pbir.compile_report(doc, MODEL, TABLES)


def test_every_target_component_type_has_a_visual():
    assert set(pbir.VISUALS) == spec.TARGETS["powerbi-report"]["components"]


def test_tall_pages_grow():
    doc = sales()
    doc["pages"][1]["components"][0]["layout"]["h"] = 20
    page = json.loads(pbir.compile_report(doc, MODEL, TABLES)["parts"]["definition/pages/pg_customers/page.json"])
    assert page["height"] == 1200
