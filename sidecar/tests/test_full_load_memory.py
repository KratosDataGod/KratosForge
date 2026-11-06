"""A full load that replaces a table reads only the old table's schema and Delta version, never its
rows: a rerun of a large full load held the old table and the new rows in memory at once."""

import datetime as dt
import decimal

import polars as pl
import pytest
from deltalake import DeltaTable
from test_meta import Repo

from fabric_desktop.meta.store import LocalStore, Store, lazy


def test_a_full_load_over_an_existing_table_reads_none_of_its_rows(tmp_path, monkeypatch):
    r = Repo(tmp_path / "project")
    assert r.engine().run("bronze.orders").status == "ok"
    first = r.table("bronze.orders").drop("_run_id", "_loaded_at", strict=False)
    whole, read_at = [], Store.read_at
    monkeypatch.setattr(Store, "read_at", lambda self, lh, schema, table: whole.append(table) or read_at(self, lh, schema, table))
    res = r.engine(dt.date(2026, 10, 2)).run("bronze.orders")
    assert res.status == "ok", res.message
    assert "orders" not in whole
    again = r.table("bronze.orders").drop("_run_id", "_loaded_at", strict=False)
    assert again.sort(again.columns).equals(first.sort(first.columns))  # replaced with the same rows


TYPES = pl.DataFrame({
    "i": [1], "f": [1.5], "s": ["a"], "b": [True], "d": [dt.date(2026, 1, 1)],
    "ts": [dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)],
    "dec": [decimal.Decimal("1.25")], "bin": [b"x"], "l": [["p", "q"]], "st": [{"k": 1, "v": "w"}],
}, schema_overrides={"dec": pl.Decimal(10, 2)})


@pytest.mark.parametrize("onelake", [False, True], ids=["this PC", "dataset read OneLake uses"])
def test_schema_at_gives_the_schema_and_version_read_at_gives(tmp_path, onelake):
    """The type check before a replace compares the new rows with this schema, so it must be the
    one a full read gives, type for type."""
    store = LocalStore(tmp_path, "dev")
    store.write("LH", "dbo", "t", TYPES)
    store.write("LH", "dbo", "t", TYPES, mode="append")
    rows, version = store.read_at("LH", "dbo", "t")
    if onelake:
        uri = store.uri("LH", "dbo", "t")
        schema = lazy(DeltaTable(uri), uri, {}).collect_schema()
    else:
        schema, at = store.schema_at("LH", "dbo", "t")
        assert at == version == 1
    assert rows is not None and schema == rows.schema
    assert store.schema_at("LH", "dbo", "missing") == (None, None)
