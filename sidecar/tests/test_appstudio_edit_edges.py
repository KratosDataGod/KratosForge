"""App Studio's conversational edits (appstudio/edit.py), the paths the main tests don't reach: an answer
with no list of changes, a measure the app already has, a width change that keeps the component on the
grid, and each change that's left out because something it needs is missing."""

from __future__ import annotations

import pytest

from fabric_desktop.appstudio import discovery, edit, generate
from fabric_desktop.appstudio import spec as specs

from test_appstudio_generate import BIM, PLAN


@pytest.fixture
def app():
    """The Sales overview app: page pg_1 with c_1 card, c_2 slicer, c_3 bar (x 6, w 6) and c_4 line;
    measure m_1 is Revenue. The model also has a Big deals measure and Sales[Amount], Date[Month]."""
    meta = discovery.from_bim(BIM)
    doc = generate.to_spec(generate.check_plan(PLAN, meta, "powerbi-report")["plan"], meta, "powerbi-report",
                           {"kind": "sample", "name": "S"})
    return doc, meta


def _ops(app, *changes, **reply):
    doc, meta = app
    return edit.to_ops({"summary": "", "changes": list(changes), **reply}, doc, meta)


@pytest.mark.parametrize("reply", [None, "rename it", {"summary": "s"}, {"changes": "set_title c_1"}])
def test_an_answer_without_a_list_of_changes_is_refused(app, reply):
    doc, meta = app
    with pytest.raises(specs.SpecError, match="no list of changes"):
        edit.to_ops(reply, doc, meta)


def test_a_measure_the_app_already_has_is_reused_not_added_again(app):
    made = _ops(app, {"action": "set_measure", "id": "c_4", "measure": "Revenue"})
    assert made["ops"] == [{"op": "set", "id": "c_4", "field": "measures", "value": ["m_1"]}]
    assert made["done"] == ["Show Revenue in c_4"]


def test_a_model_measure_the_app_lacks_is_added_once_for_two_uses(app):
    made = _ops(app, {"action": "set_measure", "id": "c_1", "measure": "Big deals"},
                {"action": "set_measure", "id": "c_4", "measure": "Big deals"})
    adds = [o for o in made["ops"] if o["op"] == "add"]
    assert len(adds) == 1 and adds[0]["value"]["id"] == "m_2" and adds[0]["value"]["expression"] == "[Big deals]"
    assert [o["value"] for o in made["ops"] if o["op"] == "set"] == [["m_2"], ["m_2"]]


def test_a_wider_component_moves_left_to_stay_on_the_grid(app):
    doc, _ = app
    made = _ops(app, {"action": "set_width", "id": "c_3", "width": 9},
                {"action": "set_width", "id": "c_3", "width": 12})
    assert [o["value"] for o in made["ops"]] == [{"x": 3, "y": 0, "w": 9, "h": 4}, {"x": 0, "y": 0, "w": 12, "h": 4}]
    assert made["done"] == ["Make c_3 9 columns wide", "Make c_3 12 columns wide"]
    out = edit.preview(doc, made["ops"])
    assert out["changed"] == ["c_3"]


def test_entries_that_arent_changes_are_skipped(app):
    made = _ops(app, "rename c_1", ["set_title"], None, {"action": "set_title", "id": "c_1", "title": "Total"})
    assert made["done"] == ["Rename c_1 to 'Total'"] and made["left_out"] == []


@pytest.mark.parametrize("change, why", [
    ({"action": "set_title", "id": "c_1", "title": "   "}, "set_title c_1: no title given"),
    ({"action": "add_component", "page": "c_1", "type": "card"}, "needs a page id from the app"),
    ({"action": "add_component", "page": "pg_1", "type": "hologram"}, "needs a page id from the app"),
    ({"action": "add_component", "page": "pg_1", "type": "card", "measure": "Margin"}, "measure or field isn't in the model"),
    ({"action": "add_component", "page": "pg_1", "type": "bar", "field": "Sales[Secret2]"}, "measure or field isn't in the model"),
    ({"action": "add_page", "title": ""}, "add_page: no title given"),
    ({"action": "rename_app"}, "rename_app: no name given"),
])
def test_a_change_missing_what_it_needs_is_left_out(app, change, why):
    made = _ops(app, change, {"action": "set_title", "id": "c_2", "title": "Region"})
    assert len(made["left_out"]) == 1 and why in made["left_out"][0]
    assert made["ops"] == [{"op": "set", "id": "c_2", "field": "title", "value": "Region"}]


def test_questions_are_trimmed_kept_to_three_and_the_summary_falls_back_to_whats_done(app):
    made = _ops(app, {"action": "rename_app", "title": "Sales 2"},
                questions=["Which region?", 7, "  ", "Monthly?", "Weekly?", "Daily?"])
    assert made["questions"] == ["Which region?", "Monthly?", "Weekly?"]
    assert made["summary"] == "Rename the app to 'Sales 2'"
