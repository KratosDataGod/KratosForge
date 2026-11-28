"""proof_runtime's smaller rules, read directly (mutation testing, batch 341).

test_prove.py and test_proof_runtime_edges.py run proofs; these pin which pinned tables are read through
DataFusion, the defaults (seed 0, schema dbo, no seeding on an overwrite), the storage options handed out,
a file's size, and the seconds a side took.
"""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest

from fabric_desktop import proof_runtime as pr

PLAIN = pa.table({"a": [1]})
QUERIED = pa.table({"a": [2]})


def table(features=(), mode=None):
    config = {"delta.columnMapping.mode": mode} if mode is not None else None
    return SimpleNamespace(protocol=lambda: SimpleNamespace(reader_features=list(features), writer_features=None),
                           metadata=lambda: SimpleNamespace(configuration=config),
                           to_pyarrow_table=lambda: PLAIN)


@pytest.fixture
def datafusion(monkeypatch):
    import deltalake

    class QueryBuilder:
        def register(self, name, t):
            return self

        def execute(self, sql):
            return QUERIED

    monkeypatch.setattr(deltalake, "QueryBuilder", QueryBuilder)


@pytest.mark.parametrize(("features", "mode", "expected"), [
    ((), None, PLAIN), ((), "none", PLAIN), ((), "", PLAIN),
    (("deletionVectors",), None, QUERIED), ((), "name", QUERIED), ((), "ID", QUERIED),
])
def test_a_table_with_deletion_vectors_or_column_mapping_is_read_through_datafusion(datafusion, features, mode,
                                                                                    expected):
    assert pr._rows(table(features, mode)).equals(expected)


def runtime(**config):
    return pr.ProofRuntime({"id": "p1"} | config, "local")


def test_the_defaults_are_seed_zero_and_schema_dbo(tmp_path):
    rt = runtime()
    assert rt.seed == 0 and rt.log["seed"] == 0
    rt.root = str(tmp_path)
    assert rt.key(None, "Orders") == "dbo.orders"
    assert rt.shared_table(None, "Orders") == f"{tmp_path}/Tables/dbo/Orders"


def test_the_storage_options_are_the_sides_own(tmp_path):
    rt = runtime().bind(tmp_path, options=lambda: {"token": "t"})
    rt.restore()
    assert rt.options() == {"token": "t"}


def test_an_overwrite_of_a_pinned_table_starts_empty(tmp_path):
    rt = runtime(pins={"dbo.t": 3})
    rt.root = str(tmp_path)
    copies = []
    uri = rt.write_location("dbo", "t", "overwrite", lambda u, data: copies.append(u))
    assert copies == [] and rt.log["reads"] == {} and uri.endswith("tables/dbo/t")
    rt.write_location("dbo", "t", "append", lambda u, data: copies.append(u))
    assert copies == [] and rt.log["writes"] == {"dbo.t": ["overwrite", "append"]}


def test_a_file_read_records_its_size(tmp_path):
    path = tmp_path / "a.csv"
    path.write_bytes(b"abc")
    rt = runtime()
    rt.file_read("Files/a.csv", str(path))
    assert rt.log["files"]["Files/a.csv"]["bytes"] == 3


def test_the_seconds_are_kept_to_a_thousandth(monkeypatch):
    rt = runtime()
    rt._clock = 100.0
    monkeypatch.setattr(pr.time, "monotonic", lambda: 101.23456)
    monkeypatch.setattr(rt, "environment", lambda modules=None: {})
    assert rt.finish()["seconds"] == 1.235
