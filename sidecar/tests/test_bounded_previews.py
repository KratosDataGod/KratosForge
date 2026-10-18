"""Previews and quick SQL results read only the rows they show (HD-033, finding DI-5)."""

from pathlib import Path

import pytest

from fabric_desktop import sql
from fabric_desktop.meta.store import Store
from fabric_desktop.service import Service

from test_notebook import LH, _sql_service


def test_head_keeps_the_first_rows_and_counts_the_rest_a_batch_at_a_time():
    seen = []
    with sql.Stream("SELECT * FROM range(120000) t(n)", batch_rows=10_000) as result:
        for batch in result:
            seen.append(batch.num_rows)
    assert max(seen) <= 10_000 and sum(seen) == 120_000  # never more than one batch on this side
    rows, total = sql.head("SELECT * FROM range(120000) t(n)", limit=5)
    assert rows.num_rows == 5 and total == 120_000 and rows.column("n").to_pylist() == [0, 1, 2, 3, 4]


def test_an_empty_result_keeps_its_columns():
    rows, total = sql.head("SELECT 1 AS a, 'x' AS b WHERE false")
    assert rows.column_names == ["a", "b"] and total == 0


@pytest.fixture
def no_full_reads(monkeypatch):
    def whole(*a, **kw):
        raise AssertionError("the whole result was read")
    monkeypatch.setattr(sql, "run", whole)


def test_quick_sql_never_reads_the_whole_result(tmp_path, monkeypatch, no_full_reads):
    service, _ = _sql_service(tmp_path, monkeypatch)
    out = service.run_sql("SELECT Country FROM sales ORDER BY Country", LH, limit=2)
    assert out["rows"] == [["DE"], ["UK"]] and out["row_count"] == 4


def test_csv_export_writes_every_row_in_batches(tmp_path, monkeypatch, no_full_reads):
    service, _ = _sql_service(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    out = service.export_sql_csv("SELECT * FROM regions ORDER BY Country", LH)
    assert out["rows"] == 2 and Path(out["path"]).read_text().splitlines() == ['"Country","Region"', '"UK","Europe"', '"US","Americas"']


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    assert [r["status"] for r in s.meta_run()] == ["ok"] * 6
    return s


def test_entity_previews_read_only_the_rows_they_show(svc, monkeypatch):
    project = svc._project()
    name = next(n for n, e in project.entities.items() if e.layer in project.layer_names())
    e = project.entities[name]
    full = svc._store(project, "dev").read(project.layer(e.layer).lakehouse, e.schema, e.table)
    assert full.height > 2
    reads = []
    real = Store.read

    def read(self, lakehouse, schema, table, version=None):
        reads.append(table)
        return real(self, lakehouse, schema, table, version)

    monkeypatch.setattr(Store, "read", read)
    out = svc.meta_preview(name, limit=2)
    assert len(out["rows"]) == 2 and out["total"] == full.height and out["columns"] == full.columns
    preview = svc.meta_entity(name)["preview"]
    assert preview["total"] == full.height and len(preview["rows"]) == min(50, full.height)
    assert e.table not in reads  # the table itself is scanned for the first rows, never read whole
