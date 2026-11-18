"""Mutation batch 117: optimize/lakehouse.py (docs/quality/mutation-testing.md). The one survivor was the
list of columns that adding columns returns: the ones the table didn't have yet, and none when it had them."""

from __future__ import annotations

import polars as pl

from fabric_desktop.optimize.lakehouse import LocalLakehouse


def test_adding_columns_returns_only_the_new_ones(tmp_path):
    lh = LocalLakehouse(tmp_path / "lake")
    assert lh.create("fdopt_mutation_probe", {"record_id": pl.Utf8, "a": pl.Int64}) is True
    assert lh.add_columns("fdopt_mutation_probe", {"a": pl.Int64, "b": pl.Utf8, "c": pl.Float64}) == ["b", "c"]
    assert set(lh.columns_of("fdopt_mutation_probe")) == {"record_id", "a", "b", "c"}
    assert lh.add_columns("fdopt_mutation_probe", {"b": pl.Utf8}) == []
