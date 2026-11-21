"""tmdl paths that mutation testing (batch 12, docs/quality/mutation-testing.md) showed no test checked:
the exact TMDL the writer lays out, the reader's edge cases, and the limits on both."""

import pytest

from fabric_desktop import tmdl


def _model():
    return {"name": "M", "model": {
        "tables": [
            {"name": "Sales", "description": "  indented\nsecond",
             "columns": [
                 {"name": "Customer Key", "dataType": "int64"},
                 {"name": "Double", "type": "calculated", "expression": "[Amount] * 2"},
                 {"name": "Odd", "type": "calculatedTableColumn", "expression": "x", "dataType": "string"}],
             "measures": [{"name": "Total", "expression": "SUM(Sales[Amount])",
                           "formatStringDefinition": {"expression": 'VAR x = 1\nRETURN "0.0"', "dataCategory": "fmt"}}],
             "partitions": [{"name": "P", "source": {"type": "m", "expression": "let\n  a = 1\nin\n  a"}}],
             "extendedProperties": [{"name": "E", "type": "json", "value": {"a": 1}}]},
            {"name": "Customer", "columns": [{"name": "Key", "dataType": "int64"}]}],
        "relationships": [{"name": "r1", "fromTable": "Sales", "fromColumn": "Customer Key",
                           "toTable": "Customer", "toColumn": "Key"}],
        "expressions": [{"name": "Src", "kind": "m", "expression": '"x"'}]}}


# ---- the writer's layout, line by line

def test_a_table_is_written_with_each_kind_of_object_at_its_own_depth():
    files, report = tmdl.from_bim(_model())
    assert report == []
    assert files["definition/tables/Sales.tmdl"].split("\n") == [
        "table Sales",
        # a description with edge spaces is a fenced property, fenced one level deeper than the table's body
        "\tdescription = ```",
        "\t\t  indented",
        "\t\tsecond",
        "\t\t```",
        "\tcolumn 'Customer Key'",
        "\t\tdataType: int64",
        # a calculated column says so by its "= expression"; the type isn't written as well
        "\tcolumn Double = [Amount] * 2",
        # any other type with an expression is kept as written
        "\tcolumn Odd = x",
        "\t\ttype: calculatedTableColumn",
        "\t\tdataType: string",
        "\tmeasure Total = SUM(Sales[Amount])",
        # a format string definition: its expression fenced under it, its own properties one level deeper
        "\t\tformatStringDefinition = ```",
        "\t\t\tVAR x = 1",
        '\t\t\tRETURN "0.0"',
        "\t\t\t```",
        "\t\t\tdataCategory: fmt",
        # a partition whose source is only an expression writes it as "source =", not as a nested block
        "\tpartition P = m",
        "\t\tsource = ```",
        "\t\t\tlet",
        "\t\t\t  a = 1",
        "\t\t\tin",
        "\t\t\t  a",
        "\t\t\t```",
        # a JSON extended property is written as compact JSON, not as a Python value
        '\textendedProperty E = {"a":1}',
        "",
    ]


def test_relationships_are_written_at_the_top_level_with_table_dot_column_ends():
    files, _ = tmdl.from_bim(_model())
    assert files["definition/relationships.tmdl"] == (
        "relationship r1\n"
        "\tfromColumn: Sales.'Customer Key'\n"  # a column name with a space is quoted
        "\ttoColumn: Customer.Key\n")           # a bare one is not


def test_an_m_expression_does_not_write_its_default_kind():
    files, _ = tmdl.from_bim(_model())
    assert files["definition/expressions.tmdl"] == 'expression Src = "x"\n'


def test_the_written_model_reads_back_as_the_same_model():
    bim = _model()
    files, _ = tmdl.from_bim(bim)
    back, report = tmdl.to_bim(files, "M")
    assert report == []
    assert tmdl.differences(bim, back) == []  # reading adds only the default compatibility level
    rel = back["model"]["relationships"][0]
    assert (rel["fromTable"], rel["fromColumn"], rel["toTable"], rel["toColumn"]) == ("Sales", "Customer Key", "Customer", "Key")


def test_what_reads_back_with_a_note_is_in_the_writers_report(monkeypatch):
    # The reader's notes on what the writer wrote reach the report from_bim returns.
    note = {"object": "table 'T'", "kind": "lost", "message": "a note from the reader"}
    real = tmdl.to_bim
    monkeypatch.setattr(tmdl, "to_bim", lambda files, name="": (real(files, name)[0], [note]))
    _, report = tmdl.from_bim({"name": "M", "model": {"culture": True}})
    assert report == [note]


def test_a_long_bad_property_name_is_reported_cut_to_80_characters():
    key = "bad name " + "x" * 100
    _, report = tmdl.from_bim({"name": "M", "model": {key: "v"}})
    lost = [r for r in report if "can't be written as TMDL" in r["message"]]
    assert lost[0]["object"] == f"model property {key[:80]}"


def test_a_model_too_large_to_write_is_refused_before_it_is_read_back(monkeypatch):
    monkeypatch.setattr(tmdl, "MAX_CHARS", 200)
    with pytest.raises(ValueError, match="would be larger than 20 MB, so it isn't written"):
        tmdl.from_bim({"name": "M", "model": {"tables": [{"name": "T", "description": "d" * 300}]}})


# ---- the reader

def test_files_are_read_tables_then_relationships_then_the_rest():
    paths = ["definition/roles/Readers.tmdl", "definition/relationships.tmdl", "definition/tables/Sales.tmdl",
             "definition/model.tmdl", "definition/database.tmdl", "definition/expressions.tmdl"]
    assert sorted(paths, key=tmdl._file_order) == [
        "definition/database.tmdl", "definition/model.tmdl", "definition/expressions.tmdl",
        "definition/tables/Sales.tmdl", "definition/relationships.tmdl", "definition/roles/Readers.tmdl"]


def test_a_relationship_with_one_end_missing_still_reads():
    bim, report = tmdl.to_bim({"definition/relationships.tmdl": "relationship r\n\tfromColumn: Sales.Key\n"})
    assert report == []
    assert bim["model"]["relationships"] == [{"name": "r", "fromTable": "Sales", "fromColumn": "Key"}]


def test_a_format_string_definition_ends_where_its_siblings_begin():
    text = ("table T\n"
            "\tmeasure M = 1\n"
            "\t\tformatStringDefinition = \"0.0\"\n"
            "\t\tdisplayFolder: Money\n")
    bim, _ = tmdl.to_bim({"definition/tables/T.tmdl": text})
    measure = bim["model"]["tables"][0]["measures"][0]
    assert measure["formatStringDefinition"] == {"expression": '"0.0"'}
    assert measure["displayFolder"] == "Money"


def test_column_ref_limits_and_bad_forms():
    ref = "T." + "c" * 1022  # 1,024 characters: the most that is read
    assert tmdl.column_ref(ref) == ("T", "c" * 1022)
    with pytest.raises(ValueError, match="longer than 1,024"):
        tmdl.column_ref(ref + "c")
    with pytest.raises(ValueError, match="not a column reference"):
        tmdl.column_ref("'Sales'Amount")  # a quoted table with no dot after it
    for empty in (".Amount", "Sales.", "'Sales'.", " .Amount"):
        with pytest.raises(ValueError, match="not a column reference"):
            tmdl.column_ref(empty)  # a table or column part with nothing in it


def test_unquote_and_values_need_both_quotes():
    assert tmdl.unquote("''") == ""
    assert tmdl.unquote("'it''s'") == "it's"
    assert tmdl._value("formatString", '"abc') == '"abc'  # only one quote: kept as written
    assert tmdl._value("formatString", '"a""b"') == 'a"b'
