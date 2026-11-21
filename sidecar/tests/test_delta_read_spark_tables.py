"""Reads of real Delta tables that use deletion vectors or column mapping (delta_read.py, and the local
engine's views in localdata/engine.py).

The tables in tests/fixtures/delta_spark were written by Apache Spark 3.5.5 with Delta Lake 3.3.2
(tools/make_spark_delta_fixtures.py), as Fabric Spark writes them: deleted rows sit in deletion
vector files, and with column mapping the data files name the columns ``col-<uuid>``. Each case
checks the rows Spark itself read back after making the table. delta-rs's pyarrow reader refuses
the first kind and reads every value of the second kind as NULL, so a read that slips back to it
fails here.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pytest
from deltalake import DeltaTable

from fabric_desktop import delta_read, preview
from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as eng
from fabric_desktop.meta import store

FIXTURES = Path(__file__).parent / "fixtures" / "delta_spark"

# What Spark read back from each table, ordered by id: (id, amount, label, p) unless the table renamed them.
_ALL = [(i, i * 10, f"n{i}", "a" if i < 5 else "b") for i in range(10)]
EXPECTED = {
    # DELETE id IN (3, 7), then DELETE id = 8: the second vector replaces the first.
    "dv": {"columns": ["id", "amount", "label", "p"], "rows": [r for r in _ALL if r[0] not in (3, 7, 8)]},
    # Partitioned by p; DELETE id IN (1, 6) puts a vector on a file in each partition.
    "dv_part": {"columns": ["id", "amount", "label", "p"], "rows": [r for r in _ALL if r[0] not in (1, 6)]},
    # Column mapping by name; amount renamed total, label renamed "the label", p dropped.
    "cm_name": {"columns": ["id", "total", "the label"], "rows": [r[:3] for r in _ALL]},
    # Column mapping by ID; amount renamed total.
    "cm_id": {"columns": ["id", "total", "label", "p"], "rows": _ALL},
    # Both: DELETE id IN (2, 5), then amount renamed total.
    "dv_cm": {"columns": ["id", "total", "label", "p"], "rows": [r for r in _ALL if r[0] not in (2, 5)]},
}
NAMES = sorted(EXPECTED)


@pytest.fixture
def tables(tmp_path):
    """A writable copy of the fixtures, so no test can change the checked-in tables."""
    out = tmp_path / "tables"
    shutil.copytree(FIXTURES, out)
    return out


def _sorted(table: pa.Table) -> list[tuple]:
    return sorted(tuple(r.values()) for r in table.to_pylist())


def test_the_fixtures_use_the_features_they_stand_for():
    for name in NAMES:
        reasons = delta_read.needs_kernel(DeltaTable(str(FIXTURES / name)))
        assert ("deletion vectors" in reasons) == name.startswith("dv"), name
        assert ("column mapping" in reasons) == ("cm" in name), name
    assert list((FIXTURES / "dv").glob("deletion_vector_*.bin")), "the deletion vector files are part of the fixture"


@pytest.mark.parametrize("name", NAMES)
def test_arrow_reads_the_rows_spark_reads(name):
    got = delta_read.arrow(DeltaTable(str(FIXTURES / name)))
    assert got.column_names == EXPECTED[name]["columns"]
    assert _sorted(got) == EXPECTED[name]["rows"]
    # No Delta field metadata follows the rows, so a later write can't take the physical names along.
    assert all(not f.metadata for f in got.schema)
    assert not any(pa.types.is_string_view(f.type) for f in got.schema)


@pytest.mark.parametrize("name", NAMES)
def test_count_and_dataset_leave_deleted_rows_out(name):
    table = DeltaTable(str(FIXTURES / name))
    assert delta_read.count(table) == len(EXPECTED[name]["rows"])
    assert _sorted(delta_read.dataset(table).to_table()) == EXPECTED[name]["rows"]
    head = delta_read.head(table, 2)
    assert head.num_rows == 2 and set(_sorted(head)) <= set(EXPECTED[name]["rows"])


def test_a_table_without_either_feature_is_read_by_the_pyarrow_reader_as_before(tmp_path, monkeypatch):
    from deltalake import write_deltalake
    write_deltalake(str(tmp_path / "plain"), pa.table({"id": [1, 2]}))
    table = DeltaTable(str(tmp_path / "plain"))
    assert delta_read.needs_kernel(table) == []
    monkeypatch.setattr(delta_read, "_query", lambda *a, **k: pytest.fail("a plain table went through DataFusion"))
    assert delta_read.arrow(table).column("id").to_pylist() == [1, 2]
    assert delta_read.count(table) == 2 and delta_read.head(table, 1).num_rows == 1
    assert delta_read.dataset(table).count_rows() == 2


def test_an_older_version_is_read_with_the_deletion_vector_it_had_then():
    table = DeltaTable(str(FIXTURES / "dv"), version=1)  # before DELETE id = 8
    assert [r[0] for r in _sorted(delta_read.arrow(table))] == [0, 1, 2, 4, 5, 6, 8, 9]
    sql, info = eng.delta_select(str(FIXTURES / "dv"), version=1)
    assert [r[0] for r in duckdb.connect().sql(sql + " ORDER BY id").fetchall()] == [0, 1, 2, 4, 5, 6, 8, 9]
    assert info["deleted_rows"] == 2


@pytest.mark.parametrize("name", NAMES)
def test_the_local_view_reads_the_rows_spark_reads(name):
    sql, info = eng.delta_select(str(FIXTURES / name))
    rel = duckdb.connect().sql(sql)
    assert rel.columns == EXPECTED[name]["columns"]
    assert sorted(rel.fetchall()) == EXPECTED[name]["rows"]
    deleted = 10 - len(EXPECTED[name]["rows"])
    assert info.get("deleted_rows", 0) == deleted


def test_the_local_view_reads_by_physical_name_only_with_column_mapping():
    sql, _ = eng.delta_select(str(FIXTURES / "cm_name"))
    assert '"col-' in sql and 'AS "total"' in sql and 'AS "the label"' in sql
    plain, _ = eng.delta_select(str(FIXTURES / "dv"))
    assert '"col-' not in plain and " AS " in plain  # only the deleted-rows subquery's aliases


def test_project_queries_and_time_travel_see_the_spark_rows(tmp_path, tables):
    local = LocalData(tmp_path / "app" / "local-data")
    try:
        e = local.engine(local.create("Spark tables")["id"])
        for name in NAMES:
            shutil.copytree(tables / name, e.tables_dir / "dbo" / name)
        for name in NAMES:
            out = e.execute(f"SELECT * FROM dbo.{name} ORDER BY id")
            assert [c["name"] for c in out["columns"]] == EXPECTED[name]["columns"], name
            assert [tuple(r) for r in out["rows"]] == EXPECTED[name]["rows"], name
        old = e.execute("SELECT id FROM dbo.dv VERSION AS OF 1 ORDER BY id", dialect="spark")
        assert [r[0] for r in old["rows"]] == [0, 1, 2, 4, 5, 6, 8, 9]
    finally:
        local.close_all()


def test_a_write_to_a_table_outside_the_profile_is_still_refused_and_changes_nothing(tmp_path, tables, monkeypatch):
    """Reading these tables doesn't open them to writes: that is the profile's choice, not the reader's."""
    from fabric_desktop import delta_profile
    monkeypatch.setattr(delta_profile, "DEFAULT_PROFILE", delta_profile.CONSERVATIVE)
    local = LocalData(tmp_path / "app" / "local-data")
    try:
        e = local.engine(local.create("Spark tables")["id"])
        shutil.copytree(tables / "dv_cm", e.tables_dir / "dbo" / "dv_cm")
        folder = e.tables_dir / "dbo" / "dv_cm"
        before = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
        with pytest.raises(ValueError, match="outside the Fabric-safe Delta profile"):
            e.execute("INSERT INTO dbo.dv_cm SELECT 100, 1, 'x', 'a'")
        assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == before
        assert DeltaTable(str(folder)).version() == 2
    finally:
        local.close_all()


def test_too_many_deleted_runs_are_refused_with_the_reason(monkeypatch):
    monkeypatch.setattr(eng, "MAX_DELETED_RANGES", 1)
    with pytest.raises(ValueError, match=r"2 runs of deleted rows .* Compacting the table in Fabric \(OPTIMIZE\)"):
        eng.delta_select(str(FIXTURES / "dv"))


def test_a_struct_column_under_column_mapping_is_refused_not_read_wrong():
    meta = {b"delta.columnMapping.physicalName": b"col-1"}
    flat = pa.schema([pa.field("a", pa.int64(), metadata=meta)])
    assert eng._physical_names(flat) == {"a": "col-1"}
    nested = pa.schema([pa.field("s", pa.list_(pa.struct([("x", pa.int64())])), metadata=meta)])
    with pytest.raises(ValueError, match="Column s is a struct in a table with column mapping") as refused:
        eng._physical_names(nested)
    assert str(refused.value).endswith("Read it from a notebook on this PC instead, which reads it in full.")


def test_deleted_positions_are_ranges_and_rows_past_a_short_vector_are_kept():
    keep = [True, False, False, True, False]  # rows 5 and on: past the vector, kept
    table = SimpleNamespace(
        protocol=lambda: SimpleNamespace(reader_features=["deletionVectors"], writer_features=["deletionVectors"]),
        deletion_vectors=lambda: pa.table({"filepath": ["file:///t/a.parquet", "file:///t/b.parquet"],
                                           "selection_vector": [keep, [True, True]]}))
    assert delta_read.deleted_positions(table) == {"file:///t/a.parquet": [(1, 2), (4, 4)]}
    assert delta_read.deleted_rows(table) == 3


def test_a_vector_for_a_file_the_view_doesnt_read_is_refused(monkeypatch):
    monkeypatch.setattr(delta_read, "deleted_positions", lambda _t: {"file:///elsewhere/x.parquet": [(0, 0)]})
    with pytest.raises(ValueError, match="deletion vector for a file that isn't in its file list") as refused:
        eng.delta_select(str(FIXTURES / "dv"))
    assert str(refused.value).endswith("Run OPTIMIZE on it in Fabric, then refresh.")


def test_previews_count_rows_without_the_deleted_ones():
    for name in NAMES:
        out = preview.table_preview(DeltaTable(str(FIXTURES / name)), limit=3)
        assert out["row_count"] == len(EXPECTED[name]["rows"]), name
        assert [c["name"] for c in out["columns"]] == EXPECTED[name]["columns"]
        assert len(out["rows"]) == 3 and all(None not in row for row in out["rows"]), name


def test_the_stores_reads_see_the_spark_rows(tables):
    for name in NAMES:
        uri = str(tables / name)
        frame = store.read_table_uri(uri, None)
        assert frame is not None and frame.columns == EXPECTED[name]["columns"]
        assert sorted(frame.rows()) == EXPECTED[name]["rows"], name
        lazy = store.lazy(DeltaTable(uri), uri, None).collect()
        assert sorted(lazy.rows()) == EXPECTED[name]["rows"], name


def test_a_whole_read_past_the_size_limit_is_refused_with_the_reason(monkeypatch):
    table = DeltaTable(str(FIXTURES / "cm_name"))
    size = delta_read.data_bytes(table)
    assert size > 0
    monkeypatch.setattr(delta_read, "MAX_WHOLE_READ_BYTES", size - 1)
    for read in (delta_read.arrow, delta_read.dataset):
        with pytest.raises(ValueError, match=r"uses column mapping, so it is read whole into this PC's memory"):
            read(table)
    assert delta_read.head(table, 2).num_rows == 2  # a first-rows read stops early, so it has no limit
    assert delta_read.count(table) == 10
    monkeypatch.setattr(delta_read, "MAX_WHOLE_READ_BYTES", size)
    assert delta_read.arrow(table).num_rows == 10
