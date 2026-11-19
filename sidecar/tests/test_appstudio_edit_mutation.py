"""App Studio conversational edits (appstudio/edit.py): what the mutation run (batch 128,
docs/quality/mutation-testing.md) showed no test pinned. The change schema's limits, the instruction, a
component without measures or fields as the model sees it, where an added component goes and its index,
titles and names cut to their limits, the summary, questions and reasons kept, and metadata without tables."""

from __future__ import annotations

import pytest

from fabric_desktop.appstudio import edit, generate
from fabric_desktop.appstudio import spec as specs
from fabric_desktop.redact import one_line

META = {"tables": [{"name": "Sales", "columns": [{"name": "Region"}, {"name": "Month"}],
                    "measures": [{"name": "Revenue", "expression": "SUM(Sales[Amount])"}, {"name": "Orders"}]}]}
LONG = "w" * 1000


def card(title="Revenue"):
    return {"type": "card", "title": title, "measure": "Revenue"}


@pytest.fixture
def spec():
    plan = {"name": "A", "pages": [
        {"title": "One", "components": [card(), {"type": "bar", "title": "By region", "measure": "Revenue",
                                                 "field": "Sales[Region]"}]},
        {"title": "Two", "components": [card()]}]}
    return generate.to_spec(plan, META, "powerbi-report", {"kind": "semantic-model", "name": "Sales model"})


def ops(spec, *changes, **reply):
    return edit.to_ops({"changes": list(changes), **reply}, spec, META)


def test_the_change_schema_keeps_its_limits():
    assert edit.MAX_CHANGES == 30 and edit.WIDTHS == (2, 3, 4, 6, 8, 9, 12)
    s = edit.CHANGE_SCHEMA
    assert s["additionalProperties"] is False and s["properties"]["summary"]["maxLength"] == 300
    changes = s["properties"]["changes"]
    assert changes["maxItems"] == 30 and changes["items"]["additionalProperties"] is False
    assert changes["items"]["properties"]["title"]["maxLength"] == 200
    q = s["properties"]["questions"]
    assert (q["items"]["maxLength"], q["maxItems"]) == (300, 3)


def test_the_instruction_names_the_widths_and_types():
    text = edit.instruction("kratosforge-app")
    assert "width of 2, 3, 4, 6, 8, 9, 12 columns" in text and "area, bar, card" in text


def test_a_component_without_measures_or_fields_is_shown_with_none():
    doc = {"name": "A", "target": "powerbi-report", "measures": [], "pages": [
        {"id": "pg_1", "title": "P", "components": [{"id": "c_1", "type": "text", "layout": {"w": 12}}]}]}
    comp = edit.app_view(doc)["pages"][0]["components"][0]
    assert (comp["measure"], comp["field"], comp["title"]) == ("", "", "")


def test_only_30_changes_are_read(spec):
    out = ops(spec, *[{"action": "set_title", "id": "c_1", "title": f"T{i}"} for i in range(31)])
    assert len(out["ops"]) == 30


def test_an_added_component_goes_under_its_own_pages_components(spec):
    out = ops(spec, {"action": "add_component", "page": "pg_2", "type": "bar", "title": "Month", "measure": "Revenue",
                     "field": "Sales[Month]"},
              {"action": "add_component", "page": "pg_2", "type": "text"})
    first, second = (o for o in out["ops"] if o["op"] == "add" and o["kind"] == "component")
    assert first["index"] == 1 and second["index"] == 2
    assert first["value"]["fields"] == ["Sales[Month]"] and first["value"]["layout"] == {"x": 0, "y": 2, "w": 6, "h": 4}
    # Not pinned: the second one's y. It is placed under the page as it was, not under the first one (a bug,
    # batch 128 in docs/quality/mutation-testing.md), so the two overlap.
    assert (second["value"]["layout"]["w"], second["value"]["layout"]["h"]) == (12, 1)
    assert second["value"]["title"] == "text" and second["value"]["props"] == {"text": "text"}
    assert first["value"]["props"] == {}


def test_a_component_added_to_a_new_page_starts_at_the_top(spec):
    out = ops(spec, {"action": "add_page", "title": "Three"},
              {"action": "add_component", "page": "pg_3", "type": "card", "measure": "Revenue"})
    comp = next(o for o in out["ops"] if o.get("kind") == "component")
    assert comp["index"] == 0 and comp["value"]["layout"]["y"] == 0


def test_titles_and_names_are_cut_to_their_limits(spec):
    out = ops(spec, {"action": "set_title", "id": "c_1", "title": LONG}, {"action": "add_page", "title": LONG},
              {"action": "rename_app", "title": LONG},
              {"action": "add_component", "page": "pg_1", "type": "card", "title": LONG, "measure": "Revenue"})
    values = [o["value"] for o in out["ops"]]
    assert values[0] == one_line(LONG, 200) and values[1]["title"] == one_line(LONG, 120)
    assert values[2] == one_line(LONG, 120) and values[3]["title"] == one_line(LONG, 200)


def test_what_is_left_out_is_named_within_its_limits(spec):
    long_id = "i" * 100
    out = ops(spec, {"action": "remove", "id": long_id}, {"action": "set_type", "id": "c_1", "type": "t" * 100},
              {"action": "set_measure", "id": "c_1", "measure": "m" * 200},
              {"action": "set_field", "id": "c_1", "field": "f" * 200}, {"action": "remove", "id": "c_2"})
    left = out["left_out"]
    assert left[0] == f"{('remove ' + long_id)[:80]}: there's no component or page with that id"
    assert left[1] == f"set_type c_1: {'t' * 40} isn't available for a Power BI report"
    assert left[2] == f"set_measure c_1: the model has no measure {'m' * 80!r}"
    assert left[3] == f"set_field c_1: the model has no column {'f' * 120!r}"


def test_a_measure_name_is_read_to_128_characters(spec):
    # one_line cuts to 128 with an ellipsis, so a model measure of that shape is what a long name reads as.
    cut = "R" * 127 + "\u2026"
    meta = {"tables": [dict(META["tables"][0], measures=[*META["tables"][0]["measures"], {"name": cut}])]}
    out = edit.to_ops({"changes": [{"action": "set_measure", "id": "c_1", "measure": "R" * 300},
                                   {"action": "add_component", "page": "pg_1", "type": "card", "measure": "R" * 300}]},
                      spec, meta)
    assert out["left_out"] == [] and out["done"][0] == f"Show {cut} in c_1"


def test_nothing_usable_lists_at_most_ten_reasons(spec):
    with pytest.raises(specs.SpecError) as e:
        ops(spec, *[{"action": "remove", "id": f"x{i}"} for i in range(12)])
    assert len(e.value.problems) == 11


def test_summary_and_questions_are_cut_and_kept(spec):
    out = ops(spec, {"action": "set_title", "id": "c_1", "title": "T"}, summary=LONG, questions=[LONG] * 4)
    assert out["summary"] == one_line(LONG, 300) and out["questions"] == [one_line(LONG, 300)] * 3
    many = ops(spec, *[{"action": "set_title", "id": "c_1", "title": "x" * 40} for _ in range(10)])
    assert many["summary"] == "; ".join(many["done"])[:300] and len(many["summary"]) == 300


@pytest.mark.parametrize("meta", [{}, {"tables": [{"name": "Sales", "columns": []}]}])
def test_metadata_without_tables_or_measures_still_allows_a_change(spec, meta):
    out = edit.to_ops({"changes": [{"action": "remove", "id": "c_2"}]}, spec, meta)
    assert out["ops"] == [{"op": "remove", "id": "c_2"}]
