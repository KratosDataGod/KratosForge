"""Mutation batch 92: delta_read.py (docs/quality/mutation-testing.md). Column mapping found from either
the setting or the protocol, a read of some columns of a table that needs DataFusion, the memory cap and
its message, and no deleted positions on a table without deletion vectors."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from deltalake import DeltaTable

from fabric_desktop import delta_read

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"


def _fake(config: dict | None = None, readers=None, writers=None):
    meta = SimpleNamespace(configuration=config or {})
    proto = SimpleNamespace(reader_features=readers, writer_features=writers)
    return SimpleNamespace(metadata=lambda: meta, protocol=lambda: proto)


def test_column_mapping_is_found_from_the_setting_alone():
    assert delta_read.needs_kernel(_fake({"delta.columnMapping.mode": "name"})) == ["column mapping"]


def test_column_mapping_is_found_from_the_protocol_alone():
    assert delta_read.needs_kernel(_fake(writers=["columnMapping"])) == ["column mapping"]
    assert delta_read.needs_kernel(_fake({"delta.columnMapping.mode": "none"})) == []


def test_the_whole_read_cap_is_two_gibibytes():
    assert delta_read.MAX_WHOLE_READ_BYTES == 2 * 1024 ** 3 == 2_147_483_648


def test_a_read_too_big_for_memory_says_both_sizes(monkeypatch):
    table = _fake(readers=["deletionVectors"], writers=["deletionVectors"])
    monkeypatch.setattr(delta_read, "MAX_WHOLE_READ_BYTES", 1000 * 1024 ** 3)
    monkeypatch.setattr(delta_read, "data_bytes", lambda t: 2000 * 1024 ** 3)
    with pytest.raises(ValueError) as err:
        delta_read.arrow(table)
    assert "uses deletion vectors, so it is read whole" in str(err.value)
    assert "files hold 2000.00 GB, more than 1000 GB." in str(err.value)


def test_some_columns_of_a_table_with_deletion_vectors(tmp_path):
    shutil.copytree(FIXTURES / "dv", tmp_path / "dv")
    table = DeltaTable(str(tmp_path / "dv"))
    got = delta_read.arrow(table, columns=["label", "id"])
    assert got.column_names == ["label", "id"]
    assert sorted(got.column("id").to_pylist()) == [0, 1, 2, 4, 5, 6, 9]
    assert delta_read.head(table, 2, columns=["id"]).column_names == ["id"]


def test_no_deleted_positions_without_deletion_vectors():
    got = delta_read.deleted_positions(_fake())
    assert got == {} and isinstance(got, dict)
