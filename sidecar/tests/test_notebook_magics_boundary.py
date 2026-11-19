"""magics.py at its exact edges (mutation batch 164, a boundary re-score of batch 33,
docs/quality/mutation-testing.md): a string that runs to the end of its line, and a word that ends
at the end of its line."""

from fabric_desktop import magics


def test_a_string_left_open_at_the_end_of_its_line_ends_with_the_line():
    assert magics._scan("x = 'abc", "", 0) == ("", 0, False)


def test_a_word_that_ends_at_the_end_of_its_line_ends_there():
    assert magics._ends_word("abc", 3) is True
    assert magics._ends_word("abc def", 3) is True
    assert magics._ends_word("abcdef", 3) is False
