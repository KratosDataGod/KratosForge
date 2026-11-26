"""Mutation batch 360: the proof runtime's choice of Delta reader, its storage options, flat lakehouse
paths and when a write is seeded from the pinned table (proof_runtime.py). Stand-in tables only;
nothing reaches a lakehouse."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop import proof_runtime as pr


class Protocol:
    def __init__(self, reader=None, writer=None):
        self.reader_features, self.writer_features = reader, writer


class Metadata:
    def __init__(self, configuration):
        self.configuration = configuration


class Table:
    def __init__(self, reader=None, writer=None, configuration=None):
        self._p, self._m = Protocol(reader, writer), Metadata(configuration)

    def protocol(self):
        return self._p

    def metadata(self):
        return self._m

    def to_pyarrow_table(self):
        return "pyarrow reader"


@pytest.fixture
def datafusion(monkeypatch):
    import deltalake

    class QueryBuilder:
        def register(self, name, table):
            assert name == "kf_table"
            return self

        def execute(self, sql):
            assert sql == "SELECT * FROM kf_table"
            return {"via": ["datafusion"]}
    monkeypatch.setattr(deltalake, "QueryBuilder", QueryBuilder)


def test_a_plain_table_is_read_with_the_pyarrow_reader(datafusion):
    assert pr._rows(Table()) == "pyarrow reader"
    assert pr._rows(Table(configuration={"delta.columnMapping.mode": "None"})) == "pyarrow reader"


@pytest.mark.parametrize("table", [Table(reader=["deletionVectors"]), Table(writer=["columnMapping"]),
                                   Table(configuration={"delta.columnMapping.mode": "name"})])
def test_deletion_vectors_or_column_mapping_are_read_through_datafusion(datafusion, table):
    assert pr._rows(table) == pa.table({"via": ["datafusion"]})


def test_options_come_from_the_bound_side():
    rt = pr.ProofRuntime({"id": "p1"}, "local")
    assert rt.options() == {}
    rt.bind("/lh", options=lambda: {"token": "t"})  # pragma: allowlist secret (test value)
    try:
        assert rt.options() == {"token": "t"}  # pragma: allowlist secret (test value)
    finally:
        rt.restore()


def test_a_flat_lakehouse_keeps_tables_without_a_schema_folder():
    rt = pr.ProofRuntime({"id": "p1", "flat": True}, "local")
    rt.root = "/lh"
    assert rt.shared_table("sales", "Orders") == "/lh/Tables/Orders"


def test_an_overwrite_isnt_seeded_from_the_pinned_table_unless_it_keeps_rows():
    rt = pr.ProofRuntime({"id": "p1", "pins": {"dbo.orders": 3}}, "local")
    rt.root = "/lh"
    written = []
    uri = rt.write_location("dbo", "orders", "overwrite", lambda u, rows: written.append(u))
    assert written == [] and uri == "/lh/Files/_fabric_desktop/proofs/p1/local/tables/dbo/orders"
    assert rt.log["writes"] == {"dbo.orders": ["overwrite"]}
    assert rt.log["reads"] == {}


def test_the_seed_defaults_to_zero():
    assert pr.ProofRuntime({"id": "p1"}, "local").log["seed"] == 0


def test_keys_and_shared_paths_default_to_dbo():
    assert pr.ProofRuntime.key(None, "Orders") == "dbo.orders"
    assert pr.ProofRuntime.key("Sales", "Orders") == "sales.orders"
    rt = pr.ProofRuntime({"id": "p1"}, "local")
    rt.root = "/lh"
    assert rt.shared_table(None, "Orders") == "/lh/Tables/dbo/Orders"
    assert rt.shared_table("sales", "Orders") == "/lh/Tables/sales/Orders"


def test_seconds_are_rounded_to_milliseconds(monkeypatch):
    clock = iter([100.0, 101.23456])
    monkeypatch.setattr(pr.time, "monotonic", lambda: next(clock))
    rt = pr.ProofRuntime({"id": "p1"}, "local")
    assert rt.finish(modules=[])["seconds"] == 1.235


def test_a_file_read_records_its_hash_and_size(tmp_path):
    f = tmp_path / "in.csv"
    f.write_bytes(b"a,b\n1,2\n")
    rt = pr.ProofRuntime({"id": "p1"}, "local")
    rt.file_read("Files/in.csv", str(f))
    assert rt.log["files"] == {"Files/in.csv": {
        "sha256": "492d5ea496056f1a6a6592241032fab764c321596317930b4fa0e1e8bc3b7470",  # pragma: allowlist secret (test value)
        "bytes": 8}}
