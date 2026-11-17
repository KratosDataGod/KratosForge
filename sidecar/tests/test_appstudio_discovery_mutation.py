"""App Studio discovery: the caps at their exact values, the text limits at their edges, and which texts are
cleaned for support (descriptions and measure expressions). Mutation batch 56."""

from __future__ import annotations

from fabric_desktop.appstudio import discovery


def _bim(tables, relationships=()):
    return {"model": {"tables": tables, "relationships": list(relationships)}}


def test_the_caps_are_the_documented_numbers():
    assert (discovery.MAX_TABLES, discovery.MAX_COLUMNS, discovery.MAX_MEASURES, discovery.MAX_RELATIONSHIPS) == (
        300, 4000, 2000, 1000)
    assert (discovery.NAME_LIMIT, discovery.DESCRIPTION_LIMIT, discovery.EXPRESSION_LIMIT) == (128, 300, 2000)


def test_exactly_the_table_cap_is_read_whole_and_one_more_is_cut():
    tables = [{"name": f"T{i}"} for i in range(300)]
    got = discovery.from_bim(_bim(tables))
    assert len(got["tables"]) == 300 and not got["truncated"]
    got = discovery.from_bim(_bim(tables + [{"name": "T300"}]))
    assert len(got["tables"]) == 300 and got["truncated"]


def test_exactly_the_column_and_measure_caps_are_read_whole():
    columns = [{"name": f"c{i}"} for i in range(4000)]
    measures = [{"name": f"m{i}"} for i in range(2000)]
    got = discovery.from_bim(_bim([{"name": "T", "columns": columns, "measures": measures}]))
    assert len(got["tables"][0]["columns"]) == 4000 and len(got["tables"][0]["measures"]) == 2000
    assert not got["truncated"]
    got = discovery.from_bim(_bim([{"name": "T", "columns": columns + [{"name": "x"}], "measures": measures}]))
    assert len(got["tables"][0]["columns"]) == 4000 and got["truncated"]
    got = discovery.from_bim(_bim([{"name": "T", "measures": measures + [{"name": "x"}]}]))
    assert len(got["tables"][0]["measures"]) == 2000 and got["truncated"]


def test_exactly_the_relationship_cap_is_read_whole():
    rels = [{"fromTable": "T", "fromColumn": f"a{i}", "toTable": "U", "toColumn": "b"} for i in range(1000)]
    got = discovery.from_bim(_bim([{"name": "T"}], rels))
    assert len(got["relationships"]) == 1000 and not got["truncated"]
    got = discovery.from_bim(_bim([{"name": "T"}], rels + [rels[0]]))
    assert len(got["relationships"]) == 1000 and got["truncated"]


def test_a_name_is_kept_up_to_128_characters():
    assert discovery._name("n" * 128) == "n" * 128
    assert len(discovery._name("n" * 129)) == 128 and discovery._name("n" * 129).endswith("…")


def test_text_of_exactly_the_limit_is_kept_whole_and_longer_text_is_cut_to_it():
    assert discovery._text("d" * 300, 300) == "d" * 300
    cut = discovery._text("d" * 301, 300)
    assert len(cut) == 300 and cut == "d" * 299 + "…"
    got = discovery.from_bim(_bim([{"name": "T", "description": "d" * 301,
                                    "columns": [{"name": "c", "description": "e" * 301}]}]))
    assert len(got["tables"][0]["description"]) == 300
    assert len(got["tables"][0]["columns"][0]["description"]) == 300


def test_an_expression_is_kept_up_to_2000_characters():
    got = discovery.from_bim(_bim([{"name": "T", "measures": [
        {"name": "a", "expression": "1" * 2000}, {"name": "b", "expression": "2" * 2001}]}]),
        include_expressions=True)
    a, b = got["tables"][0]["measures"]
    assert a["expression"] == "1" * 2000 and len(b["expression"]) == 2000 and b["expression"].endswith("…")


def test_table_and_column_descriptions_leave_out_emails_and_user_names():
    got = discovery.from_bim(_bim([{
        "name": "T", "description": "Owner jane.doe@contoso.com",
        "columns": [{"name": "c", "description": r"From C:\Users\jdoe\sales.csv"}],
        "measures": [{"name": "m", "description": "Ask jane.doe@contoso.com"}]}]))
    table = got["tables"][0]
    assert table["description"] == "Owner [email]"
    assert table["columns"][0]["description"] == r"From C:\Users\[user]\sales.csv"
    assert table["measures"][0]["description"] == "Ask [email]"


def test_an_expressions_emails_and_user_paths_are_removed_too():
    # DAX references and literals stay; only emails and user names in paths are left out.
    got = discovery.from_bim(_bim([{"name": "T", "measures": [
        {"name": "m", "expression": "CALCULATE([Sales], 'Owners'[Email] = \"jane.doe@contoso.com\")"},
        {"name": "p", "expression": "\"C:\\Users\\jdoe\\x.csv\" & 'T'[Col] & 42"}]}]),
        include_expressions=True)
    m, p = got["tables"][0]["measures"]
    assert m["expression"] == "CALCULATE([Sales], 'Owners'[Email] = \"[email]\")"
    assert p["expression"] == "\"C:\\Users\\[user]\\x.csv\" & 'T'[Col] & 42"
    assert discovery._text("jane.doe@contoso.com", 100) == "jane.doe@contoso.com"
