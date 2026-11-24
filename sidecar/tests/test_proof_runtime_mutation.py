"""The proof runtime: what mutation testing found unchecked in proof_runtime.py. Which tables are read
through delta-rs's query reader, the default schema in keys and paths, the default seed and storage
options, which writes are seeded from the pinned version, a file's size and the seconds a run took.
Nothing reaches a lakehouse."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from fabric_desktop import proof_runtime as pr


def runtime(**config):
    return pr.ProofRuntime({"id": "p1"} | config, "local")


class Table:
    def __init__(self, reader=None, writer=None, configuration=None):
        self._protocol = SimpleNamespace(reader_features=reader, writer_features=writer)
        self._configuration = configuration

    def protocol(self):
        return self._protocol

    def metadata(self):
        return SimpleNamespace(configuration=self._configuration)

    def to_pyarrow_table(self):
        return "plain"


@pytest.fixture
def queried(monkeypatch):
    import deltalake

    seen = []

    class QueryBuilder:
        def register(self, name, table):
            seen.append(name)
            return self

        def execute(self, sql):
            seen.append(sql)
            return {"id": [1, 2]}

    monkeypatch.setattr(deltalake, "QueryBuilder", QueryBuilder)
    return seen


@pytest.mark.parametrize("table", [
    Table(), Table(configuration={}), Table(reader=[], writer=["appendOnly"]),
    Table(configuration={"delta.columnMapping.mode": "None"}), Table(configuration={"delta.columnMapping.mode": None}),
])
def test_a_plain_table_is_read_through_pyarrow(table, queried):
    assert pr._rows(table) == "plain" and queried == []


@pytest.mark.parametrize("table", [
    Table(reader=["deletionVectors"]), Table(writer=["columnMapping"]),
    Table(configuration={"delta.columnMapping.mode": "Name"}),
])
def test_deletion_vectors_or_column_mapping_are_read_through_the_query_reader(table, queried):
    out = pr._rows(table)
    assert out.to_pydict() == {"id": [1, 2]}
    assert queried == ["kf_table", "SELECT * FROM kf_table"]


def test_a_table_without_a_schema_is_dbos_and_a_schema_is_kept():
    rt = runtime()
    rt.root = "lh"
    assert (rt.key(None, "Orders"), rt.key("Sales", "Orders")) == ("dbo.orders", "sales.orders")
    assert rt.shared_table(None, "Orders") == "lh/Tables/dbo/Orders"
    assert rt.shared_table("sales", "Orders") == "lh/Tables/sales/Orders"


def test_the_seed_is_zero_unless_given():
    assert runtime().seed == 0 and runtime().log["seed"] == 0


def test_storage_options_are_the_sides_own(tmp_path):
    rt = runtime()
    assert rt.options() == {}
    rt.bind(tmp_path, options=lambda: {"bearer_token": "t"})
    rt.restore()
    assert rt.options() == {"bearer_token": "t"}


def test_an_overwrite_isnt_seeded_from_the_pinned_version(tmp_path):
    rt = runtime(pins={"dbo.orders": 3})
    rt.root = str(tmp_path)
    copied = []
    uri = rt.write_location("dbo", "orders", "overwrite", lambda u, rows: copied.append(u))
    assert uri.endswith("proofs/p1/local/tables/dbo/orders") and copied == []
    assert rt.log["writes"] == {"dbo.orders": ["overwrite"]} and rt.log["reads"] == {}


def test_a_files_hash_and_size_are_recorded(tmp_path):
    data = b"x" * 1234
    (tmp_path / "f.csv").write_bytes(data)
    rt = runtime()
    rt.file_read("Files/f.csv", str(tmp_path / "f.csv"))
    assert rt.log["files"]["Files/f.csv"] == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": 1234}


def test_seconds_are_kept_to_the_millisecond(monkeypatch):
    rt = runtime()
    rt._clock = 0.0
    monkeypatch.setattr(pr, "time", SimpleNamespace(monotonic=lambda: 10.123456))
    assert rt.finish(modules=[])["seconds"] == 10.123
