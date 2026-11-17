"""Line magics and Spark SQL statements: pins what mutation testing (batch 33,
docs/quality/mutation-testing.md) showed no test held. Strings and brackets that keep a line from
starting a statement, spaces between a magic's words, and how SQL is split at its semicolons."""

from __future__ import annotations

import pytest
from fabric_desktop import magics


@pytest.fixture(autouse=True)
def _own_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def _names(code: str) -> list[str]:
    return [m.name for m in magics.find(code)]


# ---- where a line starts a statement

@pytest.mark.parametrize("opener", ['x = """', "x = '''", 'x = f("""'])
def test_a_magic_inside_a_triple_quoted_string_is_left_alone(opener):
    code = f'{opener}\n%time inside\n"""\n%who' if '"' in opener else f"{opener}\n%time inside\n'''\n%who"
    assert _names(code) == ([] if opener.endswith("(\"\"\"") else ["who"])


@pytest.mark.parametrize("first, magic", [
    ('print("x")', True),        # the string ends where it ends, so the bracket closes
    ('print("(")', True),        # a bracket inside a string doesn't count
    ('s = "a\\"b("', True),      # \" doesn't end the string
    ('s = "\\"" + "("', True),   # nor does it run past the string's own end
    ('s = "it\'s" + (1', False),  # a bracket still open: the next line continues this one
])
def test_strings_on_the_line_before_decide_whether_a_magic_starts_a_statement(first, magic):
    assert _names(f"{first}\n%time y") == (["time"] if magic else [])


@pytest.mark.parametrize("quote", ['"""', "'''"])
def test_a_triple_quote_with_text_after_it_on_the_line_still_opens_a_string(quote):
    assert _names(f"x = {quote}text\n%time inside\n{quote}\n%who") == ["who"]


def test_extra_spaces_between_words_make_no_empty_words():
    assert magics.words("a  b") == ["a", "b"]
    assert magics.words("  pip   install  x  ") == ["pip", "install", "x"]
    assert magics.words('"a b"  c') == ["a b", "c"]


# ---- Spark SQL

def test_nothing_to_run_returns_no_rows():
    assert magics.returns_rows("") is False
    assert magics.returns_rows("-- only a comment") is False
    assert magics.returns_rows("INSERT INTO t VALUES (1)") is False


def test_it_is_the_last_statement_that_decides():
    assert magics.returns_rows("SELECT 1; WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x") is False
    assert magics.returns_rows("INSERT INTO t VALUES (1); SELECT * FROM t") is True


def test_a_quoted_name_before_the_verb_is_not_a_verb():
    assert magics.returns_rows("WITH `x insert` AS (SELECT 1) SELECT * FROM `x insert`") is True
    assert magics.returns_rows("WITH `x select` AS (SELECT 1) INSERT INTO t SELECT * FROM `x select`") is False


def test_a_backslash_escapes_inside_quotes_but_not_inside_backticks():
    assert magics._statements("SELECT 'it\\'s; ok'") == ["SELECT 'it\\'s; ok'"]
    assert magics._statements('SELECT "a\\"; b"') == ['SELECT "a\\"; b"']
    assert magics._statements("SELECT `a\\`; SELECT 1") == ["SELECT `a\\`", " SELECT 1"]


def test_a_semicolon_after_a_closed_string_splits():
    assert magics._statements("SELECT 'a'; SELECT 1") == ["SELECT 'a'", " SELECT 1"]
    assert magics._statements('SELECT "ab";SELECT 1') == ['SELECT "ab"', "SELECT 1"]


def test_a_backslash_at_the_very_end_of_an_open_quote_is_kept():
    assert magics._statements("SELECT 'a\\") == ["SELECT 'a\\"]


def test_a_block_comment_is_taken_out_and_what_follows_it_kept():
    assert magics._statements("SELECT 1/*c*/;SELECT 2") == ["SELECT 1 ", "SELECT 2"]
    assert magics._statements("SELECT 1 /* never closed; SELECT 2") == ["SELECT 1  "]
