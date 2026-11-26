"""magics.py paths the uncapped mutation run (batch 227, docs/quality/mutation-testing.md) showed no test
checked: an escape just before a triple quote's end, an empty triple-quoted string, and an empty block
comment between SQL statements."""

from __future__ import annotations

from fabric_desktop import magics


def test_an_escaped_backslash_just_before_a_triple_quotes_end_still_closes_it():
    assert magics._scan('s = """a\\\\"""', "", 0) == ("", 0, False)
    assert magics._scan('a\\\\"""', '"""', 0) == ("", 0, False)  # the string opened on an earlier line


def test_an_empty_triple_quoted_string_opens_and_closes_on_its_line():
    assert magics._scan('x = """"""', "", 0) == ("", 0, False)
    assert magics._scan("x = ''''''", "", 0) == ("", 0, False)
    assert magics._scan('x = """', "", 0) == ('"""', 0, False)


def test_an_empty_block_comment_ends_where_it_closes():
    assert [s.strip() for s in magics._statements("SELECT 1 /**/; SELECT 2")] == ["SELECT 1", "SELECT 2"]


def test_a_magic_after_an_empty_triple_quoted_string_is_still_found():
    code = 'x = """"""\n%sql SELECT 1'
    assert [m.name for m in magics.find(code)] == ["sql"]
