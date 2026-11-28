"""Mutation batch 53: App Studio PBIR limits, file text and visual positions. Nothing here calls Fabric."""

import json

import pytest

from fabric_desktop.appstudio import pbir
from fabric_desktop.appstudio.spec import SpecError
from tests.test_appstudio import sales

MODEL = "0f8fad5b-d9cb-469f-a165-70867728950e"
TABLES = {"Revenue": "Sales", "Profit": "Sales"}


def test_a_report_may_have_up_to_1000_pages_and_visuals_per_page():
    assert pbir.MAX_PAGES == 1000 and pbir.MAX_VISUALS == 1000


def test_exactly_the_page_and_visual_limits_compile(monkeypatch):
    monkeypatch.setattr(pbir, "MAX_PAGES", 2)
    monkeypatch.setattr(pbir, "MAX_VISUALS", 3)
    assert pbir.compile_report(sales(), MODEL, TABLES)["parts"]


def test_parts_are_two_space_json_with_text_kept_as_written():
    doc = sales()
    doc["pages"][0]["title"] = "Übersicht"
    parts = pbir.compile_report(doc, MODEL, TABLES)["parts"]
    assert parts["definition/version.json"] == ('{\n  "$schema": "' + pbir.SCHEMAS["version"] + '",\n'
                                                '  "version": "2.0.0"\n}\n')
    assert '"displayName": "Übersicht"' in parts["definition/pages/pg_overview/page.json"]


def test_visuals_with_data_cross_filter_the_others():
    parts = pbir.compile_report(sales(), MODEL, TABLES)["parts"]
    v = json.loads(parts["definition/pages/pg_overview/visuals/c_region/visual.json"])["visual"]
    assert v["drillFilterOtherVisuals"] is True


def test_positions_are_grid_columns_rounded_to_two_places():
    doc = sales()
    doc["pages"][1]["components"][0]["layout"] = {"x": 1, "y": 2, "w": 5, "h": 3}
    v = json.loads(pbir.compile_report(doc, MODEL, TABLES)["parts"]["definition/pages/pg_customers/visuals/c_cust/visual.json"])
    assert v["position"] == {"x": 106.67, "y": 120, "z": 1000, "height": 180, "width": 533.33, "tabOrder": 1000}


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "a.b", "a b"])
def test_a_visual_name_with_other_characters_is_refused(monkeypatch, bad):
    # The specification check refuses these first; this pins the compiler's own check behind it.
    monkeypatch.setattr(pbir.specs, "check", lambda s: s)
    doc = sales()
    doc["pages"][1]["components"][0]["id"] = bad
    with pytest.raises(SpecError, match="can't be a Power BI visual name"):
        pbir.compile_report(doc, MODEL, TABLES)
