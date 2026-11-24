"""Schema drift paths (meta/drift.py) that the uncapped mutation run (batch 216,
docs/quality/mutation-testing.md) showed no test checked: a new [contract.columns] section goes right
after an existing [contract] section and before the next one, and a uint16 widens to a decimal only
with room for its 10 digits."""

from __future__ import annotations

import polars as pl
import pytest

from fabric_desktop.meta import drift


def test_a_new_columns_section_follows_the_contract_section():
    text = '[contract]\nkeys = ["id"]\n\n[source]\nfrom = "x.csv"\n'
    assert drift.apply(text, add={"amount": "float64"}) == (
        '[contract]\nkeys = ["id"]\n\n[contract.columns]\namount = "float64"\n\n[source]\nfrom = "x.csv"\n')


@pytest.mark.parametrize("new,fits", [(pl.Decimal(10, 0), True), (pl.Decimal(12, 2), True), (pl.Decimal(12, 3), False)])
def test_a_uint16_widens_to_a_decimal_with_ten_digits_left_of_the_point(new, fits):
    assert drift.widens(pl.UInt16, new) is fits
