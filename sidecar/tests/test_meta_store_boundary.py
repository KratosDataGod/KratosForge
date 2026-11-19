"""Boundary kill test for meta/store (mutation testing, batch 168)."""
from __future__ import annotations

from deltalake.table import TableOptimizer

from fabric_desktop.meta.store import LocalStore


def test_version_zero_of_an_existing_meta_table_doesnt_compact(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    store.meta_append("watermarks", [{"entity": "bronze.events", "value": "1", "run_id": "r1"}])
    calls = []
    monkeypatch.setattr(TableOptimizer, "compact", lambda self, *a, **kw: calls.append(1))
    store._tidy_meta("watermarks", 0)
    assert calls == []

