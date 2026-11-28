"""Comparing a table in two places (meta/compare.py): the edges mutation testing found unpinned.
Frames and in-memory datasets only; nothing reads a lakehouse."""

from decimal import Decimal
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.dataset as pads
import pytest

from fabric_desktop.meta import compare
from fabric_desktop.meta.project import Entity


def entity(spec=None):
    return Entity("gold.orders", spec or {}, Path("orders.toml"))


KEYED = {"contract": {"keys": ["order_key"]}}


def ds(**cols):
    return pads.dataset(pa.table(cols))


def test_a_missing_table_names_the_side_it_is_missing_on_and_has_nothing_else():
    e = entity(KEYED)
    assert compare.missing(e, None, None)["note"] == "The table doesn't exist on either side yet."
    assert compare.missing(e, None, 3)["note"] == "The table doesn't exist on the left yet."
    assert compare.missing(e, 3, None)["note"] == "The table doesn't exist on the right yet."
    out = compare.missing(e, None, 3)
    assert out["rows"] == {"left": None, "right": 3}
    assert out["only_left"] == {"count": 0, "rows": []} and out["only_right"] == {"count": 0, "rows": []}
    assert out["changed"] == {"count": 0, "by_column": {}, "rows": []}
    assert out["compared_on_keys"] is False and out["method"] == "full" and out["totals"] == []


def test_columns_only_on_one_side_are_listed_on_that_side():
    e = entity(KEYED)
    left = pl.DataFrame({"order_key": [1], "a": [1], "b": ["x"]})
    right = pl.DataFrame({"order_key": [1], "b": ["x"], "c": [2]})
    out = compare.compare(e, left, right)
    assert out["columns"]["only_left"] == ["a"] and out["columns"]["only_right"] == ["c"]


def test_a_column_numeric_on_one_side_only_gets_no_total():
    e = entity(KEYED)
    left = pl.DataFrame({"order_key": [1, 2], "amount": [1, 2]})
    right = pl.DataFrame({"order_key": [1, 2], "amount": ["1", "2"]})
    assert compare.compare(e, left, right)["totals"] == []
    assert compare.compare(e, right, left)["totals"] == []


def test_samples_stop_at_twenty_rows_but_counts_do_not():
    e = entity(KEYED)
    left = pl.DataFrame({"order_key": list(range(25)), "v": [1] * 25})
    right = pl.DataFrame({"order_key": list(range(100, 125)), "v": [1] * 25})
    out = compare.compare(e, left, right)
    assert compare.SAMPLE == 20
    assert out["only_left"]["count"] == 25 and len(out["only_left"]["rows"]) == 20
    assert out["only_right"]["count"] == 25 and len(out["only_right"]["rows"]) == 20


def test_a_keys_only_table_with_the_same_keys_has_nothing_changed():
    e = entity(KEYED)
    out = compare.compare(e, pl.DataFrame({"order_key": [1, 2]}), pl.DataFrame({"order_key": [1, 2]}))
    assert out["compared_on_keys"] is True
    assert out["changed"] == {"count": 0, "by_column": {}, "rows": []}


# ---- tables larger than memory


def test_a_hashed_compare_lists_columns_by_side_and_starts_empty():
    e = entity(KEYED)
    out = compare.hashed(e, ds(order_key=[1], a=[1]), ds(order_key=[1], c=[2]))
    assert out["columns"]["only_left"] == ["a"] and out["columns"]["only_right"] == ["c"]
    assert out["only_left"] == {"count": 0, "rows": []} and out["only_right"] == {"count": 0, "rows": []}
    assert out["changed"] == {"count": 0, "by_column": {}, "rows": []}
    assert out["method"] == "hashed" and out["compared_on_keys"] is False


def test_integer_totals_stay_exact_integers():
    e = entity(KEYED)
    big = 2 ** 53 + 1   # a double can't hold this exactly
    out = compare.hashed(e, ds(order_key=[1], qty=[big]), ds(order_key=[1], qty=[big + 2]))
    total = out["totals"][0]
    assert total["column"] == "qty"
    assert type(total["left"]) is int and total["left"] == big
    assert total["diff"] == 2


def test_two_integer_columns_hash_as_integers_not_rounded_numbers():
    # Rounding to nine significant digits applies only to an integer matched against a decimal.
    e = entity(KEYED)
    out = compare.hashed(e, ds(order_key=[1], code=[12345678901]), ds(order_key=[1], code=[12345678902]))
    assert out["checksum"]["match"] is False


@pytest.mark.parametrize("n", range(8))
def test_a_checksum_is_sixteen_hex_digits(n):
    e = entity(KEYED)
    out = compare.hashed(e, ds(order_key=list(range(n + 1)), v=[f"r{n}"] * (n + 1)), ds(order_key=[1], v=["x"]))
    for side in ("left", "right"):
        text = out["checksum"][side]
        assert len(text) == 16 and int(text, 16) < 1 << 64


def test_float_totals_equal_within_tolerance_show_no_change():
    assert compare._diff(1e12, 1e12 + 1e-4) == 0.0           # relative to the totals
    assert compare._diff(1e12, 1e12 + 1e4) == 1e4
    assert compare._diff(0.0, 1e-10) == 0.0                   # near zero, the absolute floor
    assert compare._diff(0.0, 1e-8) == 1e-8
    assert compare._diff(None, 1e-10) == 0.0
    assert compare._diff(-1e12, -1e12 - 1e-4) == 0.0          # by size, whatever the sign
    assert compare._diff(1e12, 0.0) == -1e12                  # the larger of the two totals sets the scale
    assert compare._diff(0.0, 1e12) == 1e12
    assert compare._diff(1e12, 1e12 - 1000) == 0.0            # the scale is the larger total, left
    assert compare._diff(1e12 - 1000, 1e12) == 0.0            # or right
    # Exactly at the bound still counts as the same total.
    assert compare._diff(0.0, compare.FLOAT_FLOOR) == 0.0
    assert compare._diff(Decimal("1"), Decimal("1")) == Decimal("0")
