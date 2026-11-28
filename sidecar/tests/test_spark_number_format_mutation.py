"""Spark's number formats for to_number and to_char: the cases mutation testing found untested
(batch 27, docs/quality/mutation-testing.md). An empty format, a separator at the end of a digit run,
the 38-digit limit, a format that starts with its decimal point, and a sign moved past more than one
space."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_number_format as nf
from fabric_desktop import sql


def one(query: str):
    return next(iter(sql.run(query).to_pylist()[0].values()))


def to_number(fmt: str, text: str):
    return one(f"SELECT to_number('{text}', '{fmt}')")


def to_char(fmt: str, value: int):
    return one(f"SELECT to_char({value}, '{fmt}')")


def test_an_empty_format_says_it_is_empty():
    with pytest.raises(ValueError, match="it is empty"):
        nf.tokens("")


@pytest.mark.parametrize("fmt", ["99,", "99,.9", "$9,", "9,9,"])
def test_a_separator_at_the_end_of_a_digit_run_is_refused(fmt):
    with pytest.raises(ValueError, match="each thousands separator must have digits on both sides"):
        nf.tokens(fmt)


def test_38_digits_is_the_most_a_format_can_have():
    assert nf.parse_sql("9" * 38, "NULL")[1:] == (38, 0)
    assert nf.parse_sql("9" * 20 + "." + "9" * 18, "NULL")[1:] == (38, 18)
    with pytest.raises(ValueError, match="more than 38 digits"):
        nf.parse_sql("9" * 39, "NULL")
    with pytest.raises(ValueError, match="more than 38 digits"):
        nf.parse_sql("9" * 20 + "." + "9" * 19, "NULL")


@pytest.mark.parametrize("fmt, text, want", [(".9", ".5", "0.5"), (".99", ".25", "0.25"), ("D99", ".07", "0.07")])
def test_a_format_that_starts_with_its_decimal_point_reads_the_fraction(fmt, text, want):
    assert str(to_number(fmt, text)) == want


def test_a_sign_moves_left_past_every_space_before_it():
    assert to_char("99S", 0) == "+  "
    assert to_char("999MI", -5) == "  5-"
    assert to_char("999S", 5) == "  5+"
