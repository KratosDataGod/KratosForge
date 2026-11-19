"""App Studio generation (appstudio/generate.py): what the mutation run (batch 127,
docs/quality/mutation-testing.md) showed no test pinned. The plan schema's limits, the instruction, the
grid sizes and how a page wraps, what a plan keeps and cuts, a measure without a formula, a table without
columns, and the sample metadata."""

from __future__ import annotations

import pytest

from fabric_desktop.appstudio import generate
from fabric_desktop.appstudio import spec as specs
from fabric_desktop.redact import one_line

TARGET = "powerbi-report"
META = {"tables": [{"name": "Sales", "columns": [{"name": "Region"}, {"name": "Month"}],
                    "measures": [{"name": "Revenue", "expression": "SUM(Sales[Amount])"}, {"name": "Orders"}]}]}
LONG = "w" * 1000


def card(title="Revenue", measure="Revenue"):
    return {"type": "card", "title": title, "measure": measure}


def test_the_plan_schema_keeps_its_limits():
    assert (generate.MAX_PAGES, generate.MAX_COMPONENTS_PER_PAGE, generate.TITLE_LIMIT) == (10, 12, 120)
    s = generate.PLAN_SCHEMA
    p = s["properties"]
    assert s["additionalProperties"] is False
    assert (p["name"]["maxLength"], p["purpose"]["maxLength"]) == (120, 500)
    assert (p["audience"]["items"]["maxLength"], p["audience"]["maxItems"]) == (120, 5)
    assert (p["questions"]["items"]["maxLength"], p["questions"]["maxItems"]) == (300, 5)
    page = p["pages"]["items"]
    assert p["pages"]["maxItems"] == 10 and page["additionalProperties"] is False
    assert page["properties"]["title"]["maxLength"] == 120
    comp = page["properties"]["components"]
    assert comp["maxItems"] == 12 and comp["items"]["additionalProperties"] is False
    assert comp["items"]["properties"]["title"]["maxLength"] == 200


def test_the_instruction_names_the_limits_and_types():
    text = generate.instruction("kratosforge-app")
    assert "at most 10 pages and 12 components a page" in text and "area, bar, card" in text


def test_sizes_on_the_grid_and_a_full_row_wraps():
    assert generate._SIZE == {"card": (3, 2), "slicer": (3, 2), "text": (12, 1)} and generate._DEFAULT_SIZE == (6, 4)
    plan = {"name": "A", "pages": [{"title": "P", "components": [card() for _ in range(5)]}]}
    doc = generate.to_spec(plan, META, TARGET, {"kind": "semantic-model", "name": "Sales model"})
    layout = [(c["layout"]["x"], c["layout"]["y"], c["layout"]["w"], c["layout"]["h"]) for c in doc["pages"][0]["components"]]
    assert layout == [(0, 0, 3, 2), (3, 0, 3, 2), (6, 0, 3, 2), (9, 0, 3, 2), (0, 2, 3, 2)]


def test_a_measure_without_a_formula_is_referenced_by_name():
    plan = {"name": "A", "pages": [{"title": "P", "components": [card("O", "Orders"), card()]}]}
    doc = generate.to_spec(plan, META, TARGET, {"kind": "semantic-model", "name": "Sales model"})
    assert [(m["name"], m["expression"]) for m in doc["measures"]] == [("Orders", "[Orders]"), ("Revenue", "SUM(Sales[Amount])")]


def test_a_plan_keeps_at_most_ten_pages_of_twelve_and_names_untitled_pages():
    plan = {"pages": [{"components": [card() for _ in range(13)]} for _ in range(11)]}
    out = generate.check_plan(plan, META, TARGET)["plan"]
    assert len(out["pages"]) == 10 and all(len(p["components"]) == 12 for p in out["pages"])
    assert [p["title"] for p in out["pages"]][:2] == ["Page 1", "Page 2"]


def test_a_plans_texts_are_cut_to_their_limits():
    plan = {"name": LONG, "purpose": LONG, "audience": [LONG] * 6, "questions": [LONG] * 6,
            "pages": [{"title": LONG, "components": [card(title=LONG)]}]}
    out = generate.check_plan(plan, META, TARGET)["plan"]
    assert out["name"] == one_line(LONG, 120) and out["purpose"] == one_line(LONG, 500)
    assert out["audience"] == [one_line(LONG, 120)] * 5 and out["questions"] == [one_line(LONG, 300)] * 5
    assert out["pages"][0]["title"] == one_line(LONG, 120)
    assert out["pages"][0]["components"][0]["title"] == one_line(LONG, 200)


def test_what_is_left_out_is_named_within_its_limits():
    long_type, long_measure, long_field = "t" * 100, "m" * 200, "Sales[" + "f" * 200 + "]"
    plan = {"pages": [{"title": "P", "components": [
        {"type": long_type, "title": ""}, card("x", long_measure),
        {"type": "bar", "title": "y", "measure": "Revenue", "field": long_field}, card()]}]}
    left = generate.check_plan(plan, META, TARGET)["left_out"]
    t40 = long_type[:40]
    assert left[0] == f"P: {t40} ({t40} isn't available for a Power BI report)"
    assert left[1] == f"P: x (the model has no measure {long_measure[:80]!r})"
    assert left[2] == f"P: y (the model has no column {long_field[:120]!r})"


def test_nothing_usable_lists_at_most_twenty_reasons():
    plan = {"pages": [{"title": "P", "components": [{"type": "nope", "title": f"c{i}"} for i in range(12)]},
                      {"title": "Q", "components": [{"type": "nope", "title": f"d{i}"} for i in range(12)]}]}
    with pytest.raises(specs.SpecError) as e:
        generate.check_plan(plan, META, TARGET)
    assert len(e.value.problems) == 21


def test_a_table_without_columns_has_none_in_the_context():
    out = generate.context({"tables": [{"name": "Only measures", "measures": [{"name": "M"}]}]})
    assert out["data"]["tables"] == [{"name": "Only measures", "columns": [], "measures": [{"name": "M"}]}]


def test_sample_metadata_lists_each_column_once_and_isnt_truncated():
    meta = generate.sample_metadata()
    assert meta["truncated"] is False
    for t in meta["tables"]:
        names = [c["name"] for c in t["columns"]]
        assert len(names) == len(set(names))
    assert any(t["columns"] for t in meta["tables"])


def test_metadata_without_relationships_has_none_in_the_context():
    assert generate.context({"tables": []})["data"]["relationships"] == []


def test_only_a_text_component_carries_its_title_as_text():
    plan = {"name": "A", "pages": [{"title": "P", "components": [
        {"type": "text", "title": "Read me", "measure": "", "field": ""}, card()]}]}
    comps = generate.to_spec(plan, META, TARGET, {"kind": "semantic-model", "name": "Sales model"})["pages"][0]["components"]
    assert [c["props"] for c in comps] == [{"text": "Read me"}, {}]
