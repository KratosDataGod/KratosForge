"""This PC's Spark: session settings that can't be read, a schema mismatch on write, SQL that can't
be parsed, the type checks on a cast, the merge builder's refusals, and the best-effort cleanup
when a connection refuses. Nothing here starts a session's query on a table."""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest
import sqlglot

from fabric_desktop import lakehouse
from fabric_desktop import spark_local as sl


class _Conf:
    def __init__(self, error):
        self.error = error

    def get(self, key, default=None):
        raise self.error


@pytest.mark.parametrize("error", [KeyError("x"), NotImplementedError("x")])
def test_a_conf_that_cant_answer_has_nothing_set(error):
    session = SimpleNamespace(conf=_Conf(error))
    assert sl._refuse_dynamic_overwrite(session) is None
    assert sl._refuse_auto_merge(session) is None


def test_a_schema_mismatch_says_how_to_change_the_schema(monkeypatch):
    from deltalake.exceptions import SchemaMismatchError

    def refuse(*a, **k):
        raise SchemaMismatchError("column b is missing")

    monkeypatch.setattr(lakehouse, "write_table", refuse)
    with pytest.raises(ValueError, match="doesn't match table sales's schema \\(column b is missing\\). Nothing was written"):
        sl._write_table(pa.table({"a": [1]}), "sales", "append", None)


def test_sql_that_cant_be_parsed_isnt_refused_here():
    assert sl.sql_refusal("SELECT (((") is None


def test_the_attached_lakehouses_name_is_dropped_from_a_target_without_schemas(monkeypatch):
    attached = SimpleNamespace(name="Sales", schemas_enabled=lambda: False)
    monkeypatch.setattr(lakehouse, "_attached", attached)
    tree = sqlglot.parse_one("DELETE FROM sales.orders WHERE id = 1", read="spark")
    sl._in_attached(tree)
    assert tree.this.db == "" and tree.this.name == "orders"


def test_an_all_null_column_isnt_a_store_assignment():
    assert sl._store_assigns(pa.null(), pa.int64()) is False


def test_a_cast_that_cant_go_back_doesnt_keep_the_values():
    before = pa.array([[1]], type=pa.list_(pa.int64()))
    after = pa.array([1.0])
    assert sl._same_values(before, after) is False


def test_for_path_reads_only_the_attached_lakehouses_tables(monkeypatch):
    monkeypatch.setattr(lakehouse, "relative_path", lambda p: "Files/raw/x")
    with pytest.raises(NotImplementedError, match="DeltaTable.forPath reads tables of the attached lakehouse"):
        sl.LocalDeltaTable.forPath(None, "abfss://ws@onelake/lh/Files/raw/x")


def test_is_delta_table_takes_a_tables_path_or_a_name(monkeypatch):
    seen = []
    monkeypatch.setattr(lakehouse, "relative_path", lambda p: "Tables/dbo/sales" if "/" in p else None)
    monkeypatch.setattr(lakehouse, "table_exists", lambda name: seen.append(name) or name == "dbo.sales")
    assert sl.LocalDeltaTable.isDeltaTable(None, "abfss://ws@onelake/lh/Tables/dbo/sales") is True
    assert sl.LocalDeltaTable.isDeltaTable(None, "orders") is False
    assert seen == ["dbo.sales", "orders"]


def test_restore_to_a_time_runs_in_fabric():
    with pytest.raises(NotImplementedError):
        sl.LocalDeltaTable("sales").restoreToTimestamp("2026-01-01")


def test_merge_text_must_be_sql_or_a_column():
    with pytest.raises(TypeError, match="Expected SQL text or a Column, got int"):
        sl._sql_text(5)


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default=None: True)
    return sl.LocalDeltaTable("sales", "t").merge(object(), "t.id = s.id")


def test_merge_builder_refusals(builder):
    with pytest.raises(ValueError, match="whenNotMatchedBySourceUpdate needs the columns to set"):
        builder.whenNotMatchedBySourceUpdate(set={})
    with pytest.raises(NotImplementedError, match="MERGE with schema evolution runs in Fabric"):
        builder.withSchemaEvolution()
    with pytest.raises(ValueError, match="has no whenMatched or whenNotMatched clause"):
        builder.execute()
    builder.whenNotMatchedBySourceUpdate("t.active", {"active": "false"}).whenNotMatchedBySourceDelete()
    assert builder._clauses == ["WHEN NOT MATCHED BY SOURCE AND (t.active) THEN UPDATE SET active = false",
                                "WHEN NOT MATCHED BY SOURCE THEN DELETE"]
    with pytest.raises(TypeError, match="needs a spark DataFrame as its source"):
        builder.execute()


def test_update_needs_the_columns_to_set(monkeypatch):
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default=None: True)
    with pytest.raises(ValueError, match="update needs the columns to set"):
        sl.LocalDeltaTable("sales").update("id = 1", set={})


class _Refusing:
    def close(self):
        raise RuntimeError("already closed")

    def query_progress(self):
        raise RuntimeError("closed")

    def interrupt(self):
        raise RuntimeError("nothing running")


def test_reset_progress_and_interrupt_survive_a_connection_that_refuses(monkeypatch):
    monkeypatch.setattr(sl, "_session", SimpleNamespace(conn=_Refusing()))
    assert sl.progress() is None
    sl.interrupt([_Refusing()])  # neither the session's nor the notebook's connection stops Cancel
    sl.reset()
    assert sl._session is None
    assert sl.progress() is None
