"""Differential test, second half: the other cases of tests/fixtures/spark_3_5_3_differential.json, in a file
of their own so pytest-xdist runs the two halves at once."""

from __future__ import annotations

from tests import spark_differential as diff


def test_second_half_answers_as_spark() -> None:
    diff.check(diff.part(1, 2))
