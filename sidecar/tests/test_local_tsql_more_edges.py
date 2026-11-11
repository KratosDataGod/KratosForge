"""T-SQL for the local engine (localdata/tsql.py), the edges the contract tests don't reach: statements
refused by name, types that can't be told (a column of a table not given, a type sqlglot can't read,
a typing pass that fails), and PATINDEX and IN shapes the main tests skip. Translation only; nothing runs."""

import pytest
from sqlglot.errors import OptimizeError

from fabric_desktop.localdata import tsql

COLUMNS = {"dbo": {"t": {"x": "NOT A TYPE ((", "y": "INTEGER", "s": "VARCHAR"}}}


@pytest.mark.parametrize("query, message", [
    ("PRINT 'x'", "doesn't run T-SQL PRINT yet"),
    ("SET LANGUAGE us_english", "doesn't run T-SQL SET yet"),
    ("SET @x = 1", "doesn't run T-SQL SET yet"),
    (";", "The query is empty"),
    ("SELECT PATINDEX('%b%') FROM t", "PATINDEX takes a pattern and a text"),
    ("SELECT PATINDEX(s, 'abc') FROM t", "PATINDEX needs its pattern written out as text"),
    ("SELECT STRING_SPLIT('a,b', ',')", "STRING_SPLIT runs here only in FROM or CROSS APPLY"),
    ("SELECT FORMAT(z, 'd') FROM u", "FORMAT needs to know its value's type"),
    ("SELECT CONVERT(INT, '1', 99)", "can't be run on the local engine"),
])
def test_what_the_local_engine_doesnt_run_is_refused_by_name(query, message):
    with pytest.raises(ValueError, match=message):
        tsql.translate(query, columns=COLUMNS)


def test_a_column_type_sqlglot_cant_read_stays_untyped():
    texts, notes = tsql.translate("SELECT x + 1 FROM t", columns=COLUMNS)
    assert texts == ["SELECT x + 1 FROM t"]


@pytest.mark.parametrize("query, note", [
    ("SELECT 'a' + z FROM u", "Couldn't tell the types in 'a' + z"),
    ("SELECT POWER(z, 2) FROM u", "Couldn't tell the type in POWER(z, 2)"),
    ("SELECT CEILING(z) FROM u", "Couldn't tell the type in CEILING(z)"),
    ("SELECT 1 FROM u WHERE z IN ('a', 'b')", "Couldn't tell the types in some comparisons"),
])
def test_a_column_of_a_table_not_given_gets_a_note_not_a_guess(query, note):
    _texts, notes = tsql.translate(query, columns=COLUMNS)
    assert any(n.startswith(note) for n in notes), notes


def test_patindex_with_only_a_wildcard_finds_the_first_character():
    (text,), _ = tsql.translate("SELECT PATINDEX('%', 'abc')")
    assert text == "SELECT CASE WHEN 'abc' IS NULL THEN NULL ELSE 1 END"


def test_patindex_with_an_unclosed_bracket_matches_it_as_text():
    (text,), _ = tsql.translate("SELECT PATINDEX('%[a', 'abc')")
    assert "\\[a" in text


def test_in_a_subquery_isnt_trimmed():
    (text,), notes = tsql.translate("SELECT 1 FROM t WHERE s IN (SELECT s FROM t)", columns=COLUMNS)
    assert text == "SELECT 1 FROM t WHERE s IN (SELECT s FROM t)"


def test_typing_that_fails_falls_back_to_duckdb_rules_with_a_note(monkeypatch):
    import sqlglot.optimizer.annotate_types as at
    import sqlglot.optimizer.qualify as q

    def no_qualify(*a, **k):
        raise OptimizeError("unknown table")

    def no_types(*a, **k):
        raise OptimizeError("can't annotate")
    monkeypatch.setattr(q, "qualify", no_qualify)
    monkeypatch.setattr(at, "annotate_types", no_types)
    texts, notes = tsql.translate("SELECT CAST(y AS VARCHAR(3)), y / 2 FROM t", columns=COLUMNS)
    assert len(texts) == 1
    assert any(n.startswith("Couldn't tell the column types") for n in notes)


def test_typing_that_loses_nodes_says_it_couldnt_match_the_columns(monkeypatch):
    import sqlglot
    import sqlglot.optimizer.annotate_types as at

    monkeypatch.setattr(at, "annotate_types", lambda tree, **k: sqlglot.parse_one("SELECT 1 = 1", read="duckdb"))
    texts, notes = tsql.translate("SELECT CAST(y AS VARCHAR(3)), y / 2 FROM t WHERE s = 'a'", columns=COLUMNS)
    assert len(texts) == 1
    assert any(n.startswith("Couldn't match the query's columns to their types") for n in notes)


def test_a_number_literal_that_isnt_a_number_reads_as_none():
    from sqlglot import exp
    assert tsql._number(exp.Literal(this="1.2.3", is_string=False)) is None
    assert tsql._number(exp.Neg(this=exp.Literal.number("0.5"))) == -0.5
    assert tsql._number(exp.Literal.string("2")) is None
