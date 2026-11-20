"""meta/drift.py at its exact edge (mutation batch 165, a boundary re-score of batch 29,
docs/quality/mutation-testing.md): an unsigned integer to a signed one of the same width doesn't widen."""

import polars as pl

from fabric_desktop.meta import drift


def test_an_unsigned_integer_to_a_signed_one_of_the_same_width_is_not_widening():
    assert drift.widens(pl.UInt32, pl.Int32) is False  # 4,294,967,295 doesn't fit
    assert drift.widens(pl.UInt32, pl.Int64) is True
    assert drift.widens(pl.UInt8, pl.Int8) is False
