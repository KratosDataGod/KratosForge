"""WR-256: Spark 3.5's substring, substr, left, right, instr, locate and position on this PC at their edges,
value-checked against Spark 3.5.3 (see spark_values.py).

Zero, negative, NULL and past-the-end positions and lengths, empty text, numbers, binary, and characters past
ASCII (an emoji counts as one character, as Spark counts code points): the queries in
tools/spark_value_cases/substrings.txt, substring_forms.txt and substring_locate.txt (three files, so
each test stays well inside the time limit), each answered by Spark 3.5.3 in tests/fixtures/spark_3_5_3_<name>.json."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("binary text", r"X'0102", r"reads text here, and on this PC a binary",
            "`substring`, `substr`, `left` and `right` of binary"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_substring_positions_answer_as_spark_3_5(spark):
    check(spark, load("substrings"), [])


def test_spark_substring_forms_and_other_functions_answer_as_spark_3_5(spark):
    check(spark, load("substring_forms"), REFUSED)


@pytest.mark.parametrize("half", [0, 1])
def test_spark_locate_answers_as_spark_3_5(spark, half):
    """In two halves: locate is the slowest of these to run here."""
    cases = load("substring_locate")
    middle = len(cases) // 2
    check(spark, cases[:middle] if half == 0 else cases[middle:], [])
