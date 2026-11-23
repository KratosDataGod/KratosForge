"""Spark's number formats: the case batch 231 (docs/quality/mutation-testing.md) found untested.
A format with a character that isn't a format character names that character in its error."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_number_format as nf


@pytest.mark.parametrize(("fmt", "char"), [("99x9", "x"), ("9.9Q", "Q")])
def test_a_character_that_isnt_a_format_character_is_named_in_the_error(fmt, char):
    with pytest.raises(ValueError) as caught:
        nf.tokens(fmt)
    assert str(caught.value) == f"The number format {fmt!r} isn't valid: {char!r} isn't a format character."
