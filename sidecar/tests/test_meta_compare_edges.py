"""Comparing a table between environments: the edges the compare tests don't reach. Frames only;
nothing reads a lakehouse."""

from pathlib import Path

import polars as pl

from fabric_desktop.meta import compare
from fabric_desktop.meta.project import Entity


def entity(spec):
    return Entity("gold.dim_customer", spec, Path("dim_customer.toml"))


def test_a_surrogate_key_and_looked_up_keys_are_ignored_by_design():
    e = entity({"contract": {"keys": ["customer_id"]}, "scd": {"surrogate_key": "customer_sk"},
                "lookup": [{"as": "region_sk"}]})
    assert {"customer_sk", "region_sk"} <= compare.ignored_columns(e)


def test_only_current_rows_are_compared_and_a_missing_side_is_named():
    e = entity({"contract": {"keys": ["customer_id"]}})
    left = pl.DataFrame({"customer_id": [1, 1], "name": ["old", "new"], "is_current": [False, True]})
    out = compare.compare(e, left, None)
    assert out["rows"] == {"left": 1, "right": None}
    assert out["note"] == "The table doesn't exist on the right yet."


def test_without_contract_keys_only_counts_and_totals_are_compared():
    e = entity({})
    out = compare.compare(e, pl.DataFrame({"amount": [1, 2]}), pl.DataFrame({"amount": [4]}))
    assert out["compared_on_keys"] is False and "no contract keys" in out["note"]
    assert out["totals"] == [{"column": "amount", "left": 3, "right": 4, "diff": 1}]


def test_keys_only_or_no_rows_in_common_has_nothing_changed():
    e = entity({"contract": {"keys": ["customer_id"]}})
    out = compare.compare(e, pl.DataFrame({"customer_id": [1]}), pl.DataFrame({"customer_id": [2]}))
    assert out["compared_on_keys"] is True and out["changed"]["count"] == 0
    assert out["only_left"]["count"] == 1 and out["only_right"]["count"] == 1
