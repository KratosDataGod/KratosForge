"""App Studio patch operations: the limits, inverses and refusals the mutation run found untested
(docs/quality/mutation-testing.md, batch 29)."""

import pytest

from fabric_desktop.appstudio import patch
from fabric_desktop.appstudio.spec import SpecError
from tests.test_appstudio import sales


def _refused(ops, expected):
    doc = sales()
    with pytest.raises(SpecError, match=expected):
        patch.apply(doc, ops)
    assert doc == sales()


def _comp(doc, page, i):
    return next(p for p in doc["pages"] if p["id"] == page)["components"][i]


# ---------------------------------------------------------------- the change as a whole


@pytest.mark.parametrize("ops", [None, [], {"op": "remove"}, [{"op": "remove", "id": "f_region"}, "remove"]])
def test_a_change_must_be_a_non_empty_list_of_objects(ops):
    _refused(ops, "non-empty list of operations")


def test_a_change_has_at_most_200_operations():
    ops = [{"op": "set", "id": "app", "field": "purpose", "value": f"p{i}"} for i in range(patch.MAX_OPS)]
    assert patch.MAX_OPS == 200
    assert patch.apply(sales(), ops)["spec"]["purpose"] == "p199"
    _refused(ops + [ops[0]], "at most 200 operations")


def test_an_error_names_the_operation_by_its_position_and_nothing_is_applied():
    with pytest.raises(SpecError) as err:
        patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "title", "value": "Sales"},
                              {"op": "remove", "id": "ghost"}])
    assert err.value.problems == ["Operation 2: There's nothing with id 'ghost' in this application."]


def test_the_inverse_runs_last_operation_first_and_each_touched_id_is_listed_once():
    done = patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "title", "value": "A"},
                                 {"op": "set", "id": "c_rev", "field": "title", "value": "B"},
                                 {"op": "set", "id": "app", "field": "purpose", "value": "P"}])
    assert done["changed"] == ["c_rev", "app"]
    assert done["inverse"] == [
        {"op": "set", "id": "app", "field": "purpose", "value": "Revenue, profit and regions for sales leads"},
        {"op": "set", "id": "c_rev", "field": "title", "value": "A"},
        {"op": "set", "id": "c_rev", "field": "title", "value": "Revenue"}]
    assert patch.apply(done["spec"], done["inverse"])["spec"] == sales()


def test_a_result_that_isnt_a_valid_specification_is_refused():
    with pytest.raises(SpecError, match="isn't available"):
        patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "type", "value": "hologram"}])


def test_an_unknown_operation_is_named():
    _refused([{"op": "rename", "id": "c_rev"}], "Unknown operation 'rename'; use add, remove, set or move")
    _refused([{"id": "c_rev"}], "Unknown operation 'None'")


@pytest.mark.parametrize("op, expected", [
    ({"op": "remove"}, "Operation 1: remove needs an id"),
    ({"id": 5}, "Operation 1: The operation needs an id"),
])
def test_an_operation_without_an_id_is_refused(op, expected):
    _refused([op], expected)


# ---------------------------------------------------------------- set


@pytest.mark.parametrize("field", ["props.a.b.c", "layout.x"])
def test_a_field_path_of_up_to_four_parts_can_be_set(field):
    done = patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": field, "value": 1 if field == "layout.x" else "v"}])
    assert done["changed"] == ["c_rev"]


@pytest.mark.parametrize("field", ["props.a.b.c.d", "props.bad name", "props..x", "props.9x"])
def test_a_field_that_isnt_a_path_is_refused(field):
    _refused([{"op": "set", "id": "c_rev", "field": field, "value": 1}], "isn't a field path")


def test_a_field_part_may_be_an_id_with_a_dash():
    done = patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "props.label-color", "value": "red"}])
    assert _comp(done["spec"], "pg_overview", 0)["props"] == {"label-color": "red"}


@pytest.mark.parametrize("id_, field", [("c_rev", "id"), ("app", "format"), ("pg_overview", "components"),
                                        ("c_rev", "components")])
def test_ids_format_and_component_lists_cant_be_set(id_, field):
    _refused([{"op": "set", "id": id_, "field": field, "value": "x"}], f"{field} can't be set; use add, remove or move")


def test_the_application_can_only_set_its_own_fields():
    _refused([{"op": "set", "id": "app", "field": "colour", "value": "x"}], "The application has no field 'colour'")
    done = patch.apply(sales(), [{"op": "set", "id": "app", "field": "audience", "value": ["Sales leads"]}])
    assert done["spec"]["audience"] == ["Sales leads"]
    # A component may have a field with an app-only name and vice versa: only the app is limited.
    done = patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "props.colour", "value": "x"}])
    assert _comp(done["spec"], "pg_overview", 0)["props"]["colour"] == "x"


def test_setting_an_existing_field_records_its_old_value_and_a_missing_one_records_none():
    done = patch.apply(sales(), [{"op": "set", "id": "c_region", "field": "props.sort", "value": "asc"}])
    assert done["inverse"] == [{"op": "set", "id": "c_region", "field": "props.sort", "value": "desc"}]
    done = patch.apply(sales(), [{"op": "set", "id": "c_region", "field": "props.size"}])
    assert done["inverse"] == [{"op": "set", "id": "c_region", "field": "props.size"}]
    assert "size" not in _comp(done["spec"], "pg_overview", 1)["props"]


def test_a_path_through_a_missing_object_records_no_old_value_and_through_a_value_is_refused():
    done = patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "props.a.b", "value": 1}])
    assert done["inverse"] == [{"op": "set", "id": "c_rev", "field": "props.a.b"}]
    _refused([{"op": "set", "id": "c_rev", "field": "title.x", "value": 1}], "title.x goes through a value")


def test_removing_a_value_keeps_the_rest_of_its_object_and_a_value_is_copied_not_shared():
    value = {"color": "#000000"}
    done = patch.apply(sales(), [{"op": "set", "id": "c_region", "field": "props.label", "value": value}])
    value["color"] = "#FFFFFF"
    assert _comp(done["spec"], "pg_overview", 1)["props"] == {"sort": "desc", "label": {"color": "#000000"}}
    back = patch.apply(done["spec"], [{"op": "set", "id": "c_region", "field": "props.label"}])["spec"]
    assert _comp(back, "pg_overview", 1)["props"] == {"sort": "desc"}


def test_setting_none_is_a_value_not_a_removal():
    done = patch.apply(sales(), [{"op": "set", "id": "c_region", "field": "props.sort", "value": None}])
    assert _comp(done["spec"], "pg_overview", 1)["props"] == {"sort": None}


# ---------------------------------------------------------------- add


def test_a_component_is_added_at_its_index_on_its_page_and_the_inverse_names_the_page():
    new = {"id": "c_new", "type": "card", "title": "New", "measures": ["m_rev"], "fields": [],
           "layout": {"x": 0, "y": 6, "w": 3, "h": 2}, "props": {}}
    done = patch.apply(sales(), [{"op": "add", "kind": "component", "page": "pg_customers", "index": 1, "value": new}])
    assert [c["id"] for c in done["spec"]["pages"][1]["components"]] == ["c_cust", "c_new"]
    assert done["inverse"] == [{"op": "remove", "id": "c_new", "page": "pg_customers"}]
    assert done["changed"] == ["c_new"]
    done = patch.apply(sales(), [{"op": "add", "kind": "component", "page": "pg_customers", "index": 0, "value": new}])
    assert [c["id"] for c in done["spec"]["pages"][1]["components"]] == ["c_new", "c_cust"]


def test_a_collection_entry_is_appended_without_an_index_and_its_inverse_names_no_page():
    done = patch.apply(sales(), [{"op": "add", "kind": "filter", "value": {"id": "f_new", "field": "Region[Name]"}}])
    assert [f["id"] for f in done["spec"]["filters"]] == ["f_region", "f_new"]
    assert done["inverse"] == [{"op": "remove", "id": "f_new"}]
    assert patch.apply(done["spec"], done["inverse"])["spec"] == sales()


@pytest.mark.parametrize("index", [-1, 2, True, "0", 1.0])
def test_an_index_outside_the_list_or_not_a_whole_number_is_refused(index):
    _refused([{"op": "add", "kind": "filter", "index": index, "value": {"id": "f_new", "field": "Region[Name]"}}],
             "index must be between 0 and 1")


def test_an_index_equal_to_the_length_appends():
    done = patch.apply(sales(), [{"op": "add", "kind": "filter", "index": 1, "value": {"id": "f_new", "field": "Region[Name]"}}])
    assert [f["id"] for f in done["spec"]["filters"]] == ["f_region", "f_new"]


@pytest.mark.parametrize("kind", ["app", "widget", None])
def test_add_kind_must_be_a_component_or_a_collection(kind):
    _refused([{"op": "add", "kind": kind, "value": {"id": "x_new"}}],
             "add kind must be one of: component, filter, interaction, measure, page, source")


@pytest.mark.parametrize("value", [None, "c_new", {"id": 5}])
def test_add_needs_a_value_object_with_a_text_id(value):
    _refused([{"op": "add", "kind": "filter", "value": value}], "add needs a value object with an id")


@pytest.mark.parametrize("id_", ["c_rev", "pg_customers", "m_rev", "app"])
def test_an_id_already_in_use_cant_be_added(id_):
    _refused([{"op": "add", "kind": "filter", "value": {"id": id_, "field": "Region[Name]"}}], "is already used")


def test_a_component_can_only_be_added_to_a_page():
    _refused([{"op": "add", "kind": "component", "page": "c_rev", "value": {"id": "c_new"}}], "'c_rev' isn't a page")


# ---------------------------------------------------------------- remove


def test_the_application_cant_be_removed():
    _refused([{"op": "remove", "id": "app"}], "The application itself can't be removed")


def test_an_entry_in_use_cant_be_removed_and_the_users_are_named_once():
    _refused([{"op": "remove", "id": "m_rev"}], r"m_rev is used by c_rev, c_region, c_cust; change or remove those first")


def test_a_page_whose_component_is_used_from_outside_it_cant_be_removed():
    doc = sales()
    doc["filters"] = []  # nothing names pg_overview itself now
    with pytest.raises(SpecError, match="pg_overview is used by i_drill"):
        patch.apply(doc, [{"op": "remove", "id": "pg_overview"}])


def test_a_page_whose_components_are_only_used_inside_it_can_be_removed():
    doc = sales()
    doc["interactions"] = []
    done = patch.apply(doc, [{"op": "remove", "id": "pg_customers"}])
    assert [p["id"] for p in done["spec"]["pages"]] == ["pg_overview"]
    assert done["inverse"][0]["op"] == "add" and done["inverse"][0]["kind"] == "page" and done["inverse"][0]["index"] == 1
    assert "page" not in done["inverse"][0]
    assert patch.apply(done["spec"], done["inverse"])["spec"] == doc


def test_removing_a_component_records_its_index_and_page_for_undo():
    doc = sales()
    done = patch.apply(doc, [{"op": "remove", "id": "c_slicer"}])
    assert done["inverse"] == [{"op": "add", "kind": "component", "index": 2, "value": _comp(doc, "pg_overview", 2),
                                "page": "pg_overview"}]
    assert patch.apply(done["spec"], done["inverse"])["spec"] == doc
    done = patch.apply(doc, [{"op": "remove", "id": "c_slicer", "page": "pg_named"}])
    assert done["inverse"][0]["page"] == "pg_named"


def test_removing_an_unused_entry_records_its_index():
    doc = sales()
    doc["measures"].append({"id": "m_spare", "name": "Spare", "source": "src_sales", "expression": "1"})
    done = patch.apply(doc, [{"op": "remove", "id": "m_spare"}])
    assert done["inverse"] == [{"op": "add", "kind": "measure", "index": 2, "value": doc["measures"][2]}]


# ---------------------------------------------------------------- move


def test_a_component_moves_to_another_page_and_the_inverse_moves_it_back():
    doc = sales()
    done = patch.apply(doc, [{"op": "move", "id": "c_slicer", "page": "pg_customers", "index": 0}])
    assert [c["id"] for c in done["spec"]["pages"][1]["components"]] == ["c_slicer", "c_cust"]
    assert [c["id"] for c in done["spec"]["pages"][0]["components"]] == ["c_rev", "c_region"]
    assert done["inverse"] == [{"op": "move", "id": "c_slicer", "index": 2, "page": "pg_overview"}]
    assert patch.apply(done["spec"], done["inverse"])["spec"] == doc


def test_a_component_without_a_page_moves_within_its_own_page():
    done = patch.apply(sales(), [{"op": "move", "id": "c_rev", "index": 2}])
    assert [c["id"] for c in done["spec"]["pages"][0]["components"]] == ["c_region", "c_slicer", "c_rev"]
    assert done["inverse"] == [{"op": "move", "id": "c_rev", "index": 0, "page": "pg_overview"}]


def test_a_list_entry_moves_within_its_list_and_the_inverse_names_no_page():
    doc = sales()
    done = patch.apply(doc, [{"op": "move", "id": "m_profit", "index": 0}])
    assert [m["id"] for m in done["spec"]["measures"]] == ["m_profit", "m_rev"]
    assert done["inverse"] == [{"op": "move", "id": "m_profit", "index": 1}]
    done = patch.apply(doc, [{"op": "move", "id": "pg_customers", "index": 0, "page": "pg_overview"}])
    assert [p["id"] for p in done["spec"]["pages"]] == ["pg_customers", "pg_overview"]


def test_a_move_to_a_bad_index_puts_the_entry_back_before_refusing():
    with pytest.raises(SpecError, match="index must be between 0 and 1"):
        patch.apply(sales(), [{"op": "move", "id": "m_profit", "index": 2}])
    doc = sales()
    with pytest.raises(SpecError, match="index must be between 0 and 1"):
        patch._one(doc, {"op": "move", "id": "m_profit", "index": 5})
    assert doc == sales()


def test_a_move_to_a_page_that_isnt_one_is_refused():
    _refused([{"op": "move", "id": "c_rev", "page": "m_rev", "index": 0}], "'m_rev' isn't a page")


def test_owner_refuses_a_list_that_isnt_on_a_page():
    with pytest.raises(SpecError, match="isn't on a page"):
        patch._owner(sales(), [])


# ---------------------------------------------------------------- exact messages


@pytest.mark.parametrize("field", ["", 5])
def test_an_empty_or_non_text_field_needs_a_field(field):
    _refused([{"op": "set", "id": "c_rev", "field": field, "value": 1}], "set needs a field")


def test_a_page_can_set_its_own_fields():
    done = patch.apply(sales(), [{"op": "set", "id": "pg_overview", "field": "title", "value": "Home"}])
    assert done["spec"]["pages"][0]["title"] == "Home"


@pytest.mark.parametrize("id_", ["c_rev", "app"])
def test_an_id_in_use_is_refused_by_the_operation_itself(id_):
    with pytest.raises(SpecError) as err:
        patch.apply(sales(), [{"op": "add", "kind": "filter", "value": {"id": id_, "field": "Region[Name]"}}])
    assert err.value.problems == [f"Operation 1: id '{id_}' is already used."]


def test_long_names_are_cut_in_messages():
    long = "x" * 100
    with pytest.raises(SpecError) as err:
        patch.apply(sales(), [{"op": long, "id": "c_rev"}])
    assert err.value.problems == [f"Operation 1: Unknown operation '{'x' * 20}'; use add, remove, set or move."]
    with pytest.raises(SpecError) as err:
        patch.apply(sales(), [{"op": "set", "id": "c_rev", "field": "9" * 100, "value": 1}])
    assert err.value.problems == [f"Operation 1: set field {('9' * 100)[:80]!r} isn't a field path."]
    with pytest.raises(SpecError) as err:
        patch.apply(sales(), [{"op": "move", "id": "c_rev", "page": "p" * 100, "index": 0}])
    assert err.value.problems == [f"Operation 1: '{'p' * 64}' isn't a page in this application."]
    with pytest.raises(SpecError) as err:
        patch.apply(sales(), [{"op": "remove", "id": "z" * 100}])
    assert err.value.problems == [f"Operation 1: There's nothing with id '{'z' * 64}' in this application."]
