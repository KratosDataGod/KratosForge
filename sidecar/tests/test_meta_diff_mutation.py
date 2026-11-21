"""Meaningful diffs of entity settings and notebooks: the wording, logic flags, ordering and cell
positions the mutation run found untested (docs/quality/mutation-testing.md, batch 29)."""

import pytest

from fabric_desktop.meta import diff


def _texts(changes):
    return [c["text"] for c in changes]


# ---------------------------------------------------------------- parse


def test_parse_reads_toml_and_gives_none_for_absent_or_broken_text():
    assert diff.parse(None) is None
    assert diff.parse("[load]\nmode = 'append'\n") == {"load": {"mode": "append"}}
    assert diff.parse("") == {}
    assert diff.parse("[load\n") is None


# ---------------------------------------------------------------- formatting


@pytest.mark.parametrize("value, text", [("a", "a"), (True, "true"), (False, "false"), (3, "3"), (1.5, "1.5"),
                                         ([1, "a", [True]], "[1, a, [true]]"), (None, "None"), ([], "[]")])
def test_values_are_written_the_way_the_file_writes_them(value, text):
    assert diff._fmt(value) == text


# ---------------------------------------------------------------- whole tables


def test_no_table_on_either_side_is_no_change():
    assert diff.entity_changes(None, None) == []


def test_a_new_table_and_a_removed_table_are_logic_changes():
    assert diff.entity_changes(None, {}) == [{"kind": "added", "text": "new table", "logic": True}]
    assert diff.entity_changes({}, None) == [{"kind": "removed", "text": "table removed", "logic": True}]


def test_identical_settings_have_no_changes_and_the_inputs_are_left_alone():
    old = {"contract": {"columns": {"id": "int64"}, "keys": ["id"]}, "check": [{"name": "c1"}], "owner": "a"}
    snapshot = {"contract": {"columns": {"id": "int64"}, "keys": ["id"]}, "check": [{"name": "c1"}], "owner": "a"}
    assert diff.entity_changes(old, snapshot) == []
    new = dict(snapshot, owner="b")
    diff.entity_changes(old, new)
    assert old == snapshot and new["contract"] == {"columns": {"id": "int64"}, "keys": ["id"]}


# ---------------------------------------------------------------- contract


def test_contract_columns_added_removed_and_retyped_are_reported_with_their_types():
    old = {"contract": {"columns": {"id": "int64", "email": "string", "amount": "float64"}}}
    new = {"contract": {"columns": {"id": "int64", "amount": "decimal(18,2)", "region": "string"}}}
    changes = diff.entity_changes(old, new)
    by_kind = {c["kind"]: c for c in changes}
    assert by_kind["column_added"] == {"kind": "column_added", "text": "column region added (string)", "logic": True,
                                       "column": "region", "new": "string"}
    assert by_kind["column_removed"] == {"kind": "column_removed", "text": "column email removed (was string)",
                                         "logic": True, "column": "email", "old": "string"}
    assert by_kind["column_retyped"] == {"kind": "column_retyped",
                                         "text": "column amount changed type from float64 to decimal(18,2)",
                                         "logic": True, "column": "amount", "old": "float64", "new": "decimal(18,2)"}
    assert len(changes) == 3


def test_column_changes_follow_the_column_order_old_columns_first():
    old = {"contract": {"columns": {"b": "int64", "a": "int64"}}}
    new = {"contract": {"columns": {"a": "string", "c": "int64", "b": "string"}}}
    assert [c["column"] for c in diff.entity_changes(old, new)] == ["b", "a", "c"]


def test_contract_keys_changed_say_both_sides_and_an_absent_side_reads_as_empty():
    changes = diff.entity_changes({"contract": {"keys": ["id"]}}, {"contract": {}})
    assert changes == [{"kind": "keys", "text": "contract keys changed from [id] to []", "logic": True,
                        "old": ["id"], "new": None}]
    changes = diff.entity_changes({"contract": {}}, {"contract": {"keys": ["id", "day"]}})
    assert changes[0]["text"] == "contract keys changed from [] to [id, day]"
    assert diff.entity_changes({"contract": {"keys": ["id"]}}, {"contract": {"keys": ["id"]}}) == []


def test_other_contract_settings_read_by_path_and_columns_or_keys_are_not_repeated_as_settings():
    old = {"contract": {"columns": {"id": "int64"}, "keys": ["id"], "drift": "stop"}}
    new = {"contract": {"columns": {"id": "string"}, "keys": ["day"], "drift": "keep"}}
    assert sorted(_texts(diff.entity_changes(old, new))) == [
        "column id changed type from int64 to string", "contract keys changed from [id] to [day]",
        "contract.drift changed from stop to keep"]


def test_an_empty_or_missing_contract_or_columns_table_is_no_change():
    assert diff.entity_changes({"contract": None}, {}) == []
    assert diff.entity_changes({"contract": {"columns": None}}, {"contract": {}}) == []


# ---------------------------------------------------------------- settings


@pytest.mark.parametrize("a, b, text", [
    (None, "append", "load.mode set to append"),
    ("append", None, "load.mode removed (was append)"),
    ("append", "merge", "load.mode changed from append to merge"),
])
def test_a_setting_is_set_removed_or_changed(a, b, text):
    old = {"load": {} if a is None else {"mode": a}}
    new = {"load": {} if b is None else {"mode": b}}
    assert diff.entity_changes(old, new) == [{"kind": "setting", "text": text, "logic": True, "setting": "mode",
                                             "path": "load.mode", "old": a, "new": b}]


def test_nested_settings_read_by_their_full_path():
    old = {"source": {"options": {"csv": {"sep": ","}}}}
    new = {"source": {"options": {"csv": {"sep": ";"}}}}
    (c,) = diff.entity_changes(old, new)
    assert c["path"] == "source.options.csv.sep" and c["setting"] == "sep"
    assert c["text"] == "source.options.csv.sep changed from , to ;"


def test_a_table_replaced_by_a_value_is_one_setting_change():
    (c,) = diff.entity_changes({"source": {"options": {"a": 1}}}, {"source": {"options": 5}})
    assert c["text"] == "source.options changed from {'a': 1} to 5"


def test_a_top_level_value_and_a_table_turning_into_a_value_are_compared():
    assert _texts(diff.entity_changes({"owner": "ann"}, {"owner": "bo"})) == ["owner changed from ann to bo"]
    assert _texts(diff.entity_changes({"load": {"mode": "x"}}, {"load": 3})) == ["load.mode removed (was x)"]
    assert _texts(diff.entity_changes({"load": 3}, {"load": {"mode": "x"}})) == ["load.mode set to x"]


def test_logic_settings_come_before_descriptive_ones():
    old = {"owner": "ann", "load": {"mode": "append"}, "description": "a"}
    new = {"owner": "bo", "load": {"mode": "merge"}, "description": "b"}
    changes = diff.entity_changes(old, new)
    assert [(c["path"], c["logic"]) for c in changes] == [("load.mode", True), ("description", False), ("owner", False)]


@pytest.mark.parametrize("section, key", [("source", "new_files_only"), ("load", "memory_guard"), ("load", "batch_by"),
                                          ("load", "lookback"), ("load", "max_deletes")])
def test_operational_settings_are_not_logic_changes(section, key):
    (c,) = diff.entity_changes({section: {key: 1}}, {section: {key: 2}})
    assert c["logic"] is False


def test_an_operational_name_in_another_section_is_still_logic():
    (c,) = diff.entity_changes({"source": {"lookback": 1}}, {"source": {"lookback": 2}})
    assert c["logic"] is True


# ---------------------------------------------------------------- list sections


@pytest.mark.parametrize("section, old, new, text", [
    ("derive", {"column": "total", "expr": "a+b"}, {"column": "total", "expr": "a*b"},
     "derived column total changed expr from a+b to a*b"),
    ("check", {"name": "c1", "on_fail": "stop"}, {"name": "c1", "on_fail": "quarantine"},
     "check c1 changed on_fail from stop to quarantine"),
    ("check", {"column": "id", "rule": "not_null"}, {"column": "id", "rule": "unique"},
     "check id changed rule from not_null to unique"),
    ("check", {"column": "id", "python": "f", "level": 1}, {"column": "id", "python": "f", "level": 2},
     "check id/f changed level from 1 to 2"),
    ("keys", {"table": "dim", "business_key": "id", "x": 1}, {"table": "dim", "business_key": "id", "x": 2},
     "surrogate key dim:id changed x from 1 to 2"),
    ("lookup", {"as": "cust", "dim": "customer", "on": "a"}, {"as": "cust", "dim": "customer", "on": "b"},
     "lookup cust changed on from a to b"),
    ("lookup", {"dim": "customer", "on": "a"}, {"dim": "customer", "on": "b"},
     "lookup customer changed on from a to b"),
    ("clean", {"column": "email", "trim": True}, {"column": "email", "trim": False},
     "cleaning step email changed trim from true to false"),
    ("clean", {"dedupe_keep": "first"}, {"dedupe_keep": "last"}, "cleaning step dedupe changed dedupe_keep from first to last"),
])
def test_an_edited_list_item_reads_as_changed_by_its_identity(section, old, new, text):
    (c,) = diff.entity_changes({section: [old]}, {section: [new]})
    assert c == {"kind": f"{section}_changed", "text": text, "logic": True, "item": c["item"]}


def test_a_changed_item_lists_fields_set_removed_and_changed_in_name_order():
    old = {"check": [{"name": "c1", "b": 1, "c": 2}]}
    new = {"check": [{"name": "c1", "a": 5, "b": 2}]}
    (c,) = diff.entity_changes(old, new)
    assert c["text"] == "check c1 changed a set to 5, b from 1 to 2, c removed"


def test_items_without_an_identity_are_numbered_and_non_tables_are_skipped():
    old = {"clean": [{"trim": True}, "junk"]}
    new = {"clean": [{"trim": True}, {"upper": True}]}
    changes = diff.entity_changes(old, new)
    assert [(c["kind"], c["item"]) for c in changes] == [("clean_added", "#2")]
    assert changes[0]["text"] == "cleaning step #2 added"


def test_added_items_come_before_removed_ones_and_name_their_noun():
    old = {"derive": [{"column": "a"}, {"column": "b"}]}
    new = {"derive": [{"column": "c"}, {"column": "a"}]}
    assert _texts(diff.entity_changes(old, new)) == ["derived column c added", "derived column b removed"]


def test_a_reorder_alone_is_reported_once_and_with_another_change_not_at_all():
    old = {"check": [{"name": "a"}, {"name": "b"}]}
    assert diff.entity_changes(old, {"check": [{"name": "b"}, {"name": "a"}]}) == [
        {"kind": "check_reordered", "text": "checks reordered", "logic": True}]
    changes = diff.entity_changes(old, {"check": [{"name": "b"}, {"name": "a", "x": 1}]})
    assert [c["kind"] for c in changes] == ["check_changed"]


def test_a_missing_list_section_is_an_empty_list():
    assert _texts(diff.entity_changes({}, {"keys": [{"table": "t", "business_key": "k"}]})) == ["surrogate key t:k added"]
    assert _texts(diff.entity_changes({"lookup": None}, {"lookup": [{"dim": "d"}]})) == ["lookup d added"]


def test_a_check_named_by_column_python_and_expr_joins_what_it_has():
    ident = diff._IDENTITY["check"]
    assert ident({"column": "a", "python": "p", "expr": "e"}) == "a/p/e"
    assert ident({"expr": "e"}) == "e"
    assert ident({"name": "n", "column": "a"}) == "n"
    assert ident({}) == ""


# ---------------------------------------------------------------- project


def test_project_changes_skip_unchanged_entities_and_flag_logic():
    old = {"a": "[load]\nmode = 'append'\n", "b": "owner = 'x'\n", "c": "owner = 'x'\n"}
    new = {"a": "[load]\nmode = 'append'\n", "b": "owner = 'y'\n", "c": "[load]\nmode = 'merge'\n"}
    out = diff.project_changes(old, new)
    assert [(e["entity"], e["state"], e["logic"]) for e in out] == [("b", "changed", False), ("c", "changed", True)]


def test_project_changes_name_new_removed_and_unreadable_entities_in_name_order():
    out = diff.project_changes({"z": "owner = 'x'\n", "m": "owner = 'x'\n", "k": None},
                               {"a": "owner = 'x'\n", "m": "owner = [\n", "k": "bad = \n"})
    assert [(e["entity"], e["state"]) for e in out] == [("a", "new"), ("k", "unreadable"), ("m", "unreadable"),
                                                        ("z", "removed")]
    assert out[1] == {"entity": "k", "state": "unreadable", "changes": [],
                      "problem": "the file isn't valid TOML, so it can't be compared by meaning"}


def test_an_entity_absent_on_both_sides_is_left_out():
    assert diff.project_changes({"a": None}, {"a": None}) == []


# ---------------------------------------------------------------- notebooks


def _nb(*cells):
    return {"cells": [dict(c) for c in cells]}


def test_cells_take_their_id_or_position_their_type_and_their_joined_source():
    nb = {"cells": [{"id": "a", "cell_type": "markdown", "source": ["# t\n", "x"]}, {"source": 5}, {"id": ""}]}
    assert diff._cells(nb) == [("a", "markdown", "# t\nx"), ("#2", "code", "5"), ("#3", "code", "")]
    assert diff._cells(None) == [] and diff._cells({"cells": None}) == []


def test_an_added_cell_has_its_position_and_every_line_as_added():
    old = _nb({"id": "a", "source": "x"})
    new = _nb({"id": "a", "source": "x"}, {"id": "b", "source": "l1\nl2", "cell_type": "markdown"})
    assert diff.notebook_changes(old, new) == [{"cell": "b", "kind": "added", "type": "markdown", "position": 2,
                                                "diff": ["+l1", "+l2"]}]


def test_a_removed_cell_has_its_old_position_and_every_line_as_removed():
    old = _nb({"id": "a", "source": "x"}, {"id": "b", "source": "l1\nl2"})
    assert diff.notebook_changes(old, _nb({"id": "a", "source": "x"})) == [
        {"cell": "b", "kind": "removed", "type": "code", "position": 2, "diff": ["-l1", "-l2"]}]


def test_a_changed_cell_has_a_one_line_context_diff_without_file_headers():
    old = _nb({"id": "a", "source": "l1\nl2\nl3\nl4"})
    new = _nb({"id": "a", "source": "l1\nl2\nL3\nl4"})
    (c,) = diff.notebook_changes(old, new)
    assert c == {"cell": "a", "kind": "changed", "type": "code", "position": 1,
                 "diff": ["@@ -2,3 +2,3 @@", " l2", "-l3", "+L3", " l4"]}


def test_a_cell_whose_type_changed_says_what_it_was():
    (c,) = diff.notebook_changes(_nb({"id": "a", "source": "x"}), _nb({"id": "a", "source": "x", "cell_type": "markdown"}))
    assert c["kind"] == "changed" and c["type"] == "markdown" and c["was"] == "code" and c["diff"] == []


def test_an_unchanged_notebook_has_no_changes_and_outputs_are_ignored():
    old = _nb({"id": "a", "source": "x", "outputs": [1]})
    assert diff.notebook_changes(old, _nb({"id": "a", "source": "x", "outputs": [2]})) == []


def test_a_reorder_is_reported_once_after_the_edits_and_before_the_removals():
    old = _nb({"id": "a", "source": "1"}, {"id": "b", "source": "2"}, {"id": "c", "source": "3"})
    new = _nb({"id": "b", "source": "2!"}, {"id": "a", "source": "1"})
    assert [c["kind"] for c in diff.notebook_changes(old, new)] == ["changed", "reordered", "removed"]
    reordered = diff.notebook_changes(old, new)[1]
    assert reordered == {"cell": None, "kind": "reordered", "type": None, "position": None, "diff": []}


def test_removing_a_cell_alone_is_not_a_reorder():
    old = _nb({"id": "a", "source": "1"}, {"id": "b", "source": "2"}, {"id": "c", "source": "3"})
    new = _nb({"id": "a", "source": "1"}, {"id": "c", "source": "3"})
    assert [c["kind"] for c in diff.notebook_changes(old, new)] == ["removed"]


def test_nested_settings_are_compared_depth_first_in_key_order():
    old = {"options": {"b": {"y": 1, "x": {"deep": 1}}, "a": 1, "c": {"k": [1]}}, "z": 1}
    new = {"options": {"b": {"y": 2, "x": {"deep": 2, "more": "m"}}, "a": 1, "c": 5}, "z": 2}
    assert [(c["path"], c["old"], c["new"]) for c in diff.entity_changes(old, new)] == [
        ("options.b.x.deep", 1, 2), ("options.b.x.more", None, "m"), ("options.b.y", 1, 2),
        ("options.c", {"k": [1]}, 5), ("z", 1, 2)]


def test_settings_nested_deep_are_compared_and_unreadable_nesting_is_reported():
    """A dotted key nearly a thousand parts long (no nesting in the text) is compared rather than raising
    RecursionError; one far longer, or an array nested past what Python's TOML reader takes, is a file
    that can't be compared."""
    key = "o." + ".".join(["a"] * 990)
    old, new = f'[source]\nentity = "x"\n[options]\n{key} = 1\n', f'[source]\nentity = "x"\n[options]\n{key} = 2\n'
    [entity] = diff.project_changes({"e": old}, {"e": new})
    [change] = entity["changes"]
    assert change["path"] == "options." + key and (change["old"], change["new"]) == (1, 2)
    nested = "x = " + "[" * 2000 + "]" * 2000 + "\n"
    assert diff.project_changes({"e": old}, {"e": nested})[0]["state"] == "unreadable"
    deeper = "o." + ".".join(["a"] * 20000) + " = 1\n"  # past what == can compare
    assert diff.project_changes({"e": deeper}, {"e": deeper.replace("1", "2")})[0]["state"] == "unreadable"
    assert diff.parse("o." + ".".join(["a"] * (diff.MAX_NESTING - 1)) + " = 1") is not None
