"""tmdl paths that the uncapped mutation run (batch 182, docs/quality/mutation-testing.md) showed no test
checked: the size limits, indents of spaces, the canonical form of a model and how two models are compared."""

import pytest

from fabric_desktop import tmdl


# ---- limits

def test_tmdl_one_character_over_20_mb_is_refused_before_it_is_read():
    files = {"notes.txt": "a" * tmdl.MAX_CHARS, "definition/model.tmdl": "x"}
    with pytest.raises(ValueError, match="larger than 20 MB"):
        tmdl.to_bim(files)


def test_tmdl_of_exactly_20_mb_is_read():
    files = {"notes.txt": "a" * (tmdl.MAX_CHARS - len("model Model")), "definition/model.tmdl": "model Model"}
    bim, _ = tmdl.to_bim(files)
    assert bim["name"] == "Model"


def test_the_limit_is_20_million_characters():
    assert tmdl.MAX_CHARS == 20_000_000


# ---- indents

def test_fewer_than_four_spaces_are_no_indent():
    assert tmdl._indent("   x") == 0
    assert tmdl._indent("    x") == 1
    assert tmdl._indent("       x") == 1
    assert tmdl._indent("\t   x") == 1


# ---- the canonical form

def test_numbers_become_text_and_booleans_stay_booleans():
    assert tmdl.canonical(5) == "5"
    assert tmdl.canonical(1.5) == "1.5"
    assert tmdl.canonical(True) is True
    assert tmdl.canonical(False) is False
    assert tmdl.canonical({"a": 1, "b": True, "c": None}) == {"a": "1", "b": True}


# ---- comparing

def test_an_object_that_became_text_is_a_difference_at_its_path():
    before = {"model": {"annotations": {"x": "1"}}}
    after = {"model": {"annotations": "x"}}
    assert tmdl.differences(before, after) == ["annotations"]
    assert tmdl.differences(after, before) == ["annotations"]


def test_a_model_without_a_compatibility_level_gets_1604():
    bim, _ = tmdl.to_bim({"definition/model.tmdl": "model Model"})
    assert bim["compatibilityLevel"] == 1604


def test_files_other_than_the_known_ones_are_read_last():
    assert tmdl._file_order("definition/roles/r.tmdl") == (30, "definition/roles/r", "definition/roles/r.tmdl")
    assert tmdl._file_order("definition/relationships.tmdl") == (20, "definition/relationships", "definition/relationships.tmdl")
    assert tmdl._file_order("definition/tables/T.tmdl") == (10, "definition/tables/t", "definition/tables/T.tmdl")
    assert tmdl._file_order("definition/model.tmdl") == (1, "definition/model", "definition/model.tmdl")


# ---- writing

def test_a_value_with_a_quote_at_only_one_end_is_written_as_it_is():
    assert tmdl._scalar('"abc') == '"abc'
    assert tmdl._scalar('abc"') == 'abc"'
    assert tmdl._scalar('"a"') == '"""a"""'


@pytest.mark.parametrize("name", [5, ""])
def test_an_object_whose_name_is_not_text_or_is_empty_is_left_out_by_name(name):
    files, report = tmdl.from_bim({"name": "M", "model": {"tables": [{"name": name, "columns": []}, {"name": "T"}]}})
    assert sorted(files) == ["definition/model.tmdl", "definition/tables/T.tmdl"]
    assert report == [{"object": "table", "kind": "lost", "message": "an object with no name was left out"}]


def test_a_description_that_is_not_text_is_not_written_and_is_reported():
    files, report = tmdl.from_bim({"name": "M", "model": {"tables": [{"name": "T", "description": 5}]}})
    assert files["definition/tables/T.tmdl"] == "table T\n"
    assert report == [{"object": "table 'T' description (left out)", "kind": "lost",
                       "message": "wouldn't read back from TMDL as it was"}]


def test_unquote_needs_a_quote_at_both_ends():
    assert tmdl.unquote("'abc") == "'abc"
    assert tmdl.unquote("abc'") == "abc'"
    assert tmdl.unquote("'a''b'") == "a'b"


def test_database_and_model_properties_are_one_tab_deep():
    files, report = tmdl.from_bim({"name": "M", "compatibilityLevel": 1604, "model": {"culture": "en-US"}})
    assert report == []
    assert files["definition/database.tmdl"] == "database\n\tcompatibilityLevel: 1604\n"
    assert files["definition/model.tmdl"] == "model Model\n\tculture: en-US\n"


def test_a_table_entry_that_is_not_an_object_is_left_out_and_reported():
    files, report = tmdl.from_bim({"name": "M", "model": {"tables": ["x", {"name": "T"}]}})
    assert files["definition/model.tmdl"] == "model Model\nref table T\n"
    assert report == [{"object": "tables", "kind": "lost", "message": "an entry that isn't an object was left out"}]


def test_a_partition_source_with_more_than_an_expression_is_written_as_a_block():
    bim = {"name": "M", "model": {"tables": [{"name": "T", "partitions": [
        {"name": "P", "source": {"type": "m", "expression": "x", "dataView": "full"}}]}]}}
    files, report = tmdl.from_bim(bim)
    assert report == []
    assert files["definition/tables/T.tmdl"].split("\n") == [
        "table T", "\tpartition P = m", "\t\tsource", "\t\t\tdataView: full", "\t\t\texpression = x", ""]


def test_a_relationship_end_without_a_column_keeps_its_table():
    files, report = tmdl.from_bim({"name": "M", "model": {"relationships": [
        {"name": "r", "fromTable": "A", "toTable": "B", "toColumn": "k"}]}})
    assert report == []
    assert files["definition/relationships.tmdl"].split("\n") == ["relationship r", "\ttoColumn: B.k", "\tfromTable: A", ""]


def test_a_relationship_table_that_is_not_text_is_kept_as_a_property():
    files, report = tmdl.from_bim({"name": "M", "model": {"relationships": [
        {"name": "r", "fromTable": 5, "fromColumn": "k", "toTable": "B", "toColumn": "k"}]}})
    assert report == []
    assert files["definition/relationships.tmdl"].split("\n") == [
        "relationship r", "\tfromColumn: k", "\ttoColumn: B.k", "\tfromTable: 5", ""]


def test_a_format_string_definition_on_one_line_keeps_its_properties_apart():
    src = "table T\n\tmeasure M = 1\n\t\tformatStringDefinition = x\n\t\t\tdataCategory: fmt\n"
    bim, report = tmdl.to_bim({"definition/tables/T.tmdl": src})
    assert report == []
    assert bim["model"]["tables"][0]["measures"][0]["formatStringDefinition"] == {"expression": "x", "dataCategory": "fmt"}


def test_a_line_not_understood_is_reported_by_its_first_80_characters():
    _, report = tmdl.to_bim({"definition/model.tmdl": "model Model\n\t" + "x" * 100 + " ! y\n"})
    assert report == [{"object": "x" * 80, "kind": "lost", "message": "not understood, left out (definition/model.tmdl:2)"}]


def test_a_default_that_is_not_text_is_written_as_text():
    files, _ = tmdl.from_bim({"name": "M", "model": {"tables": [{"name": "T", "columns": [{"name": "C", "expression": 5}]}]}})
    assert files["definition/tables/T.tmdl"] == "table T\n\tcolumn C = 5\n"


def test_only_a_plain_one_line_changed_property_is_written():
    props = [{"property": "Name"}, {"property": "a "}, {"property": ""}, {"property": 5}, {"property": "a\nb"}]
    files, report = tmdl.from_bim({"name": "M", "model": {"tables": [{"name": "T", "changedProperties": props}]}})
    assert files["definition/tables/T.tmdl"] == "table T\n\tchangedProperty = Name\n"
    lost = [r for r in report if r["message"] == "this changed property can't be written as TMDL"]
    assert len(lost) == 4


def test_a_key_only_in_the_second_model_is_added():
    assert tmdl.differences({"model": {}}, {"model": {"x": "1"}}) == ["x (added)"]


def test_a_format_string_expression_with_a_fence_line_is_refused_by_name():
    bim = {"name": "M", "model": {"tables": [{"name": "T", "measures": [
        {"name": "m", "expression": "1", "formatStringDefinition": {"expression": "a\n```\nb"}}]}]}}
    with pytest.raises(ValueError, match="table 'T' measure 'm' formatStringDefinition: its expression has a line of "
                                         "three backticks"):
        tmdl.from_bim(bim)


def test_a_relationship_table_with_a_line_break_is_refused_by_name():
    bim = {"name": "M", "model": {"relationships": [{"name": "r", "fromTable": "A\nB", "fromColumn": "k"}]}}
    with pytest.raises(ValueError, match="relationship 'r': its name has a line break"):
        tmdl.from_bim(bim)


def test_a_description_right_after_the_three_slashes_is_read_whole():
    bim, _ = tmdl.to_bim({"definition/tables/T.tmdl": "///Sales\ntable T\n"})
    assert bim["model"]["tables"][0]["description"] == "Sales"
