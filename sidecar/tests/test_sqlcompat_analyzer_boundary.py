"""sqlcompat/analyzer.py at its exact edges (mutation batch 164, a boundary re-score of batch 32,
docs/quality/mutation-testing.md): a parse error at the very start of the text is placed there, and one
just past its end isn't placed."""

from fabric_desktop.sqlcompat import analyzer


def test_an_error_at_the_first_character_is_placed_and_one_past_the_end_is_not():
    assert analyzer._error_offset("abc", {"line": 1, "col": 1, "highlight": "a"}) == 0
    assert analyzer._error_offset("abc", {"line": 1, "col": 3, "highlight": "c"}) == 2
    assert analyzer._error_offset("abc", {"line": 1, "col": 3, "highlight": ""}) is None  # just past the end
