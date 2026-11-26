"""Differential test, second half: the other cases of tests/fixtures/spark_3_5_3_differential.json, in a file
of their own so pytest-xdist runs the two halves at once."""

from __future__ import annotations

import pytest

from tests import spark_differential as diff


@pytest.mark.parametrize("cases", diff.chunks(1, 2))
def test_second_half_answers_as_spark(cases: list[dict]) -> None:
    diff.check(cases)
