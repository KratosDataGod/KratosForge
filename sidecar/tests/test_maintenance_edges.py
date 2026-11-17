"""Table maintenance (maintenance.py), the paths the main tests don't reach: V-Order read from the
files' footers, a table already at the retention asked for, a Fabric OPTIMIZE that still counts as
V-Order, and files that vanish while a table folder is walked, sized or cleaned. On Delta tables in
tmp_path."""

from __future__ import annotations

import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import maintenance as M


@pytest.fixture
def table(tmp_path):
    uri = str(tmp_path / "orders")
    write_deltalake(uri, pa.table({"id": [1, 2]}))
    write_deltalake(uri, pa.table({"id": [3]}), mode="append")
    return uri


def test_v_order_is_read_from_the_newest_files_footers(table, monkeypatch):
    first = M.vorder_files(table)
    assert first == {"files": 2, "checked": 2, "v_ordered": 0}
    real = pq.read_metadata

    class Stamped:
        def __init__(self, meta):
            self.metadata = {**(meta.metadata or {}), M.VORDER_KEY: b" TRUE "}

    monkeypatch.setattr(pq, "read_metadata", lambda f: Stamped(real(f)))
    assert M.vorder_files(table, limit=1) == {"files": 2, "checked": 1, "v_ordered": 1}


def test_an_empty_table_has_no_files_to_check(tmp_path):
    uri = str(tmp_path / "empty")
    write_deltalake(uri, pa.table({"id": pa.array([], pa.int64())}))
    assert M.vorder_files(uri) == {"files": 0, "checked": 0, "v_ordered": 0}


def test_a_table_already_at_the_retention_asked_for_isnt_changed(table):
    assert M.set_retention(table, 7) is True
    version = DeltaTable(table).version()
    assert M.set_retention(table, 7) is False and DeltaTable(table).version() == version


def test_a_fabric_optimize_counts_as_v_order_unless_it_says_otherwise():
    fabric = {"engineInfo": "Apache-Spark/3.5 Delta-Lake/3.2", "operationParameters": {"vorder": "true"}}
    assert M._vorder_optimize(fabric) is True
    assert M._vorder_optimize(fabric | {"operationParameters": {"vorder": "false"}}) is False
    assert M._vorder_optimize({"clientVersion": "delta-rs.0.25", "engineInfo": "x"}) is False
    assert M._when(None) is None and M._when(0) == "1970-01-01T00:00:00+00:00"


class Gone:
    """A directory entry whose file went away meanwhile."""
    path = "gone"

    def is_symlink(self):
        return False

    def stat(self, follow_symlinks=True):
        raise FileNotFoundError(2, "gone")


def test_entries_that_vanish_while_walked_are_skipped_and_not_old():
    assert M._is_link(Gone()) is True
    assert M._older_than(Gone(), 1) is False


def test_files_removed_or_gone_meanwhile_arent_counted(table):
    data = sorted(n for n in os.listdir(table) if n.endswith(".parquet"))
    size = os.path.getsize(os.path.join(table, data[0]))
    assert M._size(table, [data[0], "part-gone.parquet"]) == size
    assert M._remove(table, [data[0], "part-gone.parquet"]) == 1
    assert not os.path.exists(os.path.join(table, data[0]))
