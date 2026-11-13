"""App Studio plans from a model: what check_plan leaves out and refuses, and what the context
hides. Pure checks on dicts; no model is called and nothing is written."""

import pytest

from fabric_desktop.appstudio import generate as gen
from fabric_desktop.appstudio.spec import SpecError

META = {"tables": [
    {"name": "Sales", "description": "One row per order line",
     "columns": [{"name": "Region", "type": "string"}, {"name": "Key", "hidden": True}],
     "measures": [{"name": "Revenue", "expression": "SUM(Sales[Amount])"}]},
    {"name": "Staging", "hidden": True, "columns": [{"name": "Raw"}]}]}


def test_the_context_skips_hidden_tables_and_columns_and_keeps_table_descriptions():
    data = gen.context(META)["data"]
    assert [t["name"] for t in data["tables"]] == ["Sales"]
    assert data["tables"][0]["description"] == "One row per order line"
    assert data["tables"][0]["columns"] == [{"name": "Region", "type": "string"}]


@pytest.mark.parametrize("plan, target, says", [
    ({"pages": []}, "excel", "target must be one of"),
    ({"pages": "one"}, "powerbi-report", "The model's plan has no pages."),
    ([], "powerbi-report", "The model's plan has no pages."),
])
def test_a_plan_that_cant_be_read_is_refused(plan, target, says):
    with pytest.raises(SpecError) as raised:
        gen.check_plan(plan, META, target)
    assert says in raised.value.problems[0]


def test_each_component_that_doesnt_fit_its_type_is_left_out_with_why():
    plan = {"name": "Sales", "pages": ["not a page", {"title": "Overview", "components": [
        "not a component",
        {"type": "slicer", "title": "Pick", "measure": "Revenue", "field": "Sales[Region]"},
        {"type": "card", "title": "Empty card"},
        {"type": "bar", "title": "Half a bar", "measure": "Revenue"},
        {"type": "card", "title": "Total", "measure": "Revenue"}]}]}
    out = gen.check_plan(plan, META, "powerbi-report")
    assert out["left_out"] == ["Overview: Pick (a slicer needs one field and no measure)",
                               "Overview: Empty card (a card needs a measure)",
                               "Overview: Half a bar (a bar needs a measure and a field)"]
    assert out["plan"]["pages"] == [{"title": "Overview", "components": [
        {"type": "card", "title": "Total", "measure": "Revenue", "field": ""}]}]


def test_a_plan_with_nothing_usable_says_so_with_what_was_left_out():
    plan = {"pages": [{"title": "Only", "components": [{"type": "card", "title": "Nope"}]}]}
    with pytest.raises(SpecError) as raised:
        gen.check_plan(plan, META, "powerbi-report")
    assert raised.value.problems == ["Nothing in the model's plan could be used with this data.",
                                     "Only: Nope (a card needs a measure)",
                                     "Only: the whole page (nothing on it could be used)"]
