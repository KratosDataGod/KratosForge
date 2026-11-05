"""PB-027: the table and column names notebook SQL completion falls back on when Fabric can't be asked."""

from __future__ import annotations

from fabric_desktop import retention, schema_cache
from fabric_desktop.service import Service


def test_tables_and_columns_are_kept_per_lakehouse(tmp_path):
    assert schema_cache.load(tmp_path, "lh1") == {"tables": {}, "columns": {}}
    schema_cache.record_tables(tmp_path, "lh1", {"dbo": ["sales"]})
    schema_cache.record_columns(tmp_path, "lh1", "sales", [{"name": "amount", "type": "double"}])
    schema_cache.record_columns(tmp_path, "lh1", "dbo.customers", [{"name": "id", "type": "long"}])
    schema_cache.record_tables(tmp_path, "lh1", {"dbo": ["sales", "customers"]})  # a new listing keeps the columns
    assert schema_cache.load(tmp_path, "lh1") == {
        "tables": {"dbo": ["sales", "customers"]},
        "columns": {"sales": [{"name": "amount", "type": "double"}], "dbo.customers": [{"name": "id", "type": "long"}]}}
    assert schema_cache.load(tmp_path, "lh2") == {"tables": {}, "columns": {}}


def test_the_cache_stays_inside_its_folder_and_in_the_download_cache(tmp_path):
    schema_cache.record_tables(tmp_path, "../../evil", {"dbo": ["x"]})
    files = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json")]
    assert files == ["onelake-cache/sql-schemas/evil.json"]
    assert schema_cache.FOLDER.parts[0] in retention.CACHE_FOLDERS  # local_retention_days removes it
    schema_cache.record_tables(tmp_path, "", {"dbo": ["x"]})  # nothing to name the file by: nothing kept
    assert schema_cache.load(tmp_path, "") == {"tables": {}, "columns": {}}


def test_a_broken_cache_reads_as_empty_and_a_failed_write_never_fails_the_read(tmp_path, monkeypatch):
    path = tmp_path / schema_cache.FOLDER / "lh1.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert schema_cache.load(tmp_path, "lh1") == {"tables": {}, "columns": {}}
    path.write_text('{"tables": ["wrong shape"], "columns": null}', encoding="utf-8")
    assert schema_cache.load(tmp_path, "lh1") == {"tables": {}, "columns": {}}

    def full(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(schema_cache.jsonfile, "write_json", full)
    schema_cache.record_tables(tmp_path, "lh1", {"dbo": ["sales"]})  # logs, doesn't raise


def test_list_tables_records_what_fabric_listed(tmp_path, monkeypatch):
    class Catalog:
        def __init__(self, _token):
            pass

        def list_schemas(self, ws, lh):
            return ["dbo", "finance"]

        def list_tables(self, ws, lh, schema):
            return {"dbo": ["sales"], "finance": ["budget"]}[schema]

    monkeypatch.setattr("fabric_desktop.service.TableCatalog", Catalog)
    service = Service(client_id="x", root=tmp_path)
    assert service.list_tables("ws1", "lh1") == {"dbo": ["sales"], "finance": ["budget"]}
    assert service.cached_schemas("lh1") == {"tables": {"dbo": ["sales"], "finance": ["budget"]}, "columns": {}}
