"""Boundary kill test for meta/keys (mutation testing, batch 168)."""
from __future__ import annotations

import polars as pl

from fabric_desktop.meta import keys as K
from fabric_desktop.meta.store import LocalStore


def test_exactly_the_lookup_limit_of_keys_is_looked_up_not_read_whole(tmp_path, monkeypatch):
    store = LocalStore(tmp_path, "dev")
    kt = K.KeyTable("customer", {}, tmp_path / "customer.toml")
    K.merge_keys(store, kt, pl.Series(["a", "b", "c", "d"]))
    monkeypatch.setattr(K, "KEY_LOOKUP_LIMIT", 3)
    assert K._matching(store, kt, ["a", "b", "c"])[2] is False
    assert K._matching(store, kt, ["a", "b", "c", "d"])[2] is True
