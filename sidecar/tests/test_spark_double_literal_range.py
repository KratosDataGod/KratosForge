"""A number with an exponent is a double in Spark 3.5, and one past a double's range is refused by its parser
([INVALID_NUMERIC_LITERAL_RANGE]), where DuckDB reads it as infinity."""
from __future__ import annotations

import re
import time

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


@pytest.mark.parametrize("query, shown", [
    ("SELECT 1e309 AS x", "1e309"),
    ("SELECT -1e400 AS x", "-1e400"),
    ("SELECT 1e30000000", "1e30000000"),              # with no alias too: its column name is never worked out
    ("SELECT 2E+308 AS x", "2E+308"),
    ("SELECT 1.7976931348623158e308 AS x", "1.7976931348623158e308"),  # past MaxValue, though a float rounds it
    ("SELECT x FROM VALUES (1) t(x) WHERE x < 1e999", "1e999"),
])
def test_a_double_literal_past_its_range_is_refused_as_spark_refuses_it(spark, query, shown):
    started = time.monotonic()
    with pytest.raises(ValueError, match=rf"\[INVALID_NUMERIC_LITERAL_RANGE\] Numeric literal {re.escape(shown)} is outside "
                                         r"the valid range for double"):
        spark.sql(query).collect()
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("query, value", [
    ("SELECT 1e308 AS x", 1e308),
    ("SELECT 1.7976931348623157E308 AS x", 1.7976931348623157e308),
    ("SELECT 1e-400 AS x", 0.0),                       # below the smallest double: inside the range, 0.0
    ("SELECT 1e-30000000 AS x", 0.0),
    ("SELECT -1.5E3 AS x", -1500.0),
])
def test_a_double_literal_inside_its_range_and_other_numbers_answer_as_before(spark, query, value):
    assert spark.sql(query).collect()[0].x == value


def test_text_and_names_that_look_like_a_huge_number_are_left_alone(spark):
    row = spark.sql("SELECT '1e400' AS s, `1e400` FROM VALUES (7) t(`1e400`)").collect()[0]
    assert (row.s, row["1e400"]) == ("1e400", 7)
