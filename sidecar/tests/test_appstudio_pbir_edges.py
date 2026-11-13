"""App Studio PBIR: the limits and warnings the PBIR tests don't reach. Nothing here calls Fabric."""

import json

import pytest

from fabric_desktop.appstudio import pbir
from fabric_desktop.appstudio.spec import SpecError
from tests.test_appstudio import sales

MODEL = "0f8fad5b-d9cb-469f-a165-70867728950e"
TABLES = {"Revenue": "Sales", "Profit": "Sales"}


def test_a_field_that_isnt_table_and_column_is_refused():
    assert pbir._field("'Customer Name'[First]") == ("Customer Name", "First")
    with pytest.raises(SpecError, match="isn't a Table\\[Column\\] reference"):
        pbir._field("Region")


def test_too_many_pages_or_visuals_are_refused_before_anything_is_compiled(monkeypatch):
    monkeypatch.setattr(pbir, "MAX_PAGES", 1)
    with pytest.raises(SpecError, match="at most 1 pages"):
        pbir.compile_report(sales(), MODEL, TABLES)
    monkeypatch.setattr(pbir, "MAX_PAGES", 1000)
    monkeypatch.setattr(pbir, "MAX_VISUALS", 2)
    with pytest.raises(SpecError, match="'Overview' has more than 2 visuals"):
        pbir.compile_report(sales(), MODEL, TABLES)


def test_a_text_component_compiles_as_an_empty_text_box_with_a_warning():
    doc = sales()
    doc["pages"][1]["components"].append({"id": "c_note", "type": "text", "title": "Note", "measures": [],
                                          "fields": [], "layout": {"x": 0, "y": 6, "w": 12, "h": 1}, "props": {}})
    out = pbir.compile_report(doc, MODEL, TABLES)
    assert out["warnings"][0] == "c_note: text boxes aren't compiled yet; it is an empty text box."
    visual = json.loads(out["parts"]["definition/pages/pg_customers/visuals/c_note/visual.json"])["visual"]
    assert visual == {"visualType": "textbox"}
