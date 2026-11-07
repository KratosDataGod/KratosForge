"""A number column whose type changed between decimal and float still gets a total's diff."""
from decimal import Decimal
from pathlib import Path

import polars as pl

from fabric_desktop.meta import compare
from fabric_desktop.meta.project import Entity


def _entity() -> Entity:
    return Entity("silver.orders", {"contract": {"keys": ["order_key"]}}, Path("orders.toml"))


def test_a_column_that_went_from_decimal_to_float_reports_its_total_diff() -> None:
    left = pl.DataFrame({"order_key": [1, 2], "amount": [Decimal("1.50"), Decimal("2.25")]},
                        schema={"order_key": pl.Int64, "amount": pl.Decimal(10, 2)})
    right = pl.DataFrame({"order_key": [1, 2], "amount": [1.5, 3.25]})
    out = compare.compare(_entity(), left, right)
    total = next(t for t in out["totals"] if t["column"] == "amount")
    assert total["diff"] == 1.0
    assert out["columns"]["type_changed"] == [{"column": "amount", "left": "Decimal(precision=10, scale=2)",
                                               "right": "Float64"}]


def test_totals_of_the_same_type_subtract_as_before() -> None:
    assert compare._minus(Decimal("3.50"), Decimal("1.25")) == Decimal("2.25")
    assert compare._minus(5, None) == 5
    assert compare._minus(None, 2.5) == -2.5
    assert compare._diff(Decimal("1.5"), 2.0) == 0.5
