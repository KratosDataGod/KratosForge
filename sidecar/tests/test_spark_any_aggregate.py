"""Spark's any(x) aggregate on the left of a comparison (any(x) = true) is the aggregate, not ANY's quantifier,
which is always on the right (x = ANY (subquery), s LIKE ANY ('a%', 'b%'))."""

from __future__ import annotations

import pytest

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    session = Kernel().namespace["spark"]
    session.sql("CREATE OR REPLACE TEMP VIEW kf_any AS SELECT * FROM VALUES (true), (false) AS v(x)")
    return session


@pytest.mark.parametrize(("query", "want"), [
    ("SELECT any(x) = true FROM kf_any", True),
    ("SELECT any(x) = false FROM kf_any", False),
    ("SELECT some(x) = true FROM kf_any WHERE NOT x", False),
    ("SELECT any(x) FROM kf_any", True),
])
def test_any_on_the_left_of_a_comparison_is_the_aggregate(spark, query: str, want: bool) -> None:
    assert spark.sql(query).collect()[0][0] is want


def test_any_on_the_right_stays_a_quantifier() -> None:
    written = sql.parse("SELECT x = ANY (SELECT y FROM u) FROM t")[0].sql(dialect=sql.KfDuckDB)
    assert "ANY (SELECT" in written and "BOOL_OR" not in written
