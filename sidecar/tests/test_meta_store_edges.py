"""Where a metadata project's tables live (meta/store.py), the paths the main tests don't reach: a
table created by another writer between the read and the write, a failed stream whose files can't be
listed or tidied, the highest value read without statistics, source deletes in an upsert, commits
looked up since a version, Files read as Parquet or JSON, and a OneLake store's layers that aren't set
up. Local tables in tmp_path; the OneLake store is only asked for addresses, so nothing is sent."""

from __future__ import annotations

import json

import polars as pl
import pyarrow as pa
import pytest
from deltalake import write_deltalake
from deltalake.exceptions import CommitFailedError, TableNotFoundError

from fabric_desktop.errors import NotSetUp
from fabric_desktop.meta import store as S
from fabric_desktop.meta.store import LocalStore, OneLakeStore, Store

LH = "LH_Silver"


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "dev", "dev")


def orders(ids=(1, 2)):
    return pl.DataFrame({"id": list(ids), "amount": [10.0 * i for i in ids]})


# ---- the base class


def test_a_store_without_its_own_files_and_addresses_says_so():
    bare = Store()
    for call in (lambda: bare.uri(LH, "dbo", "t"), lambda: bare.list_files(LH, "*"),
                 lambda: bare.fetch_files(LH, []), lambda: bare._data_files(LH, "dbo", "t"),
                 lambda: bare._delete_data_files(LH, "dbo", "t", [])):
        with pytest.raises(NotImplementedError):
            call()
    assert bare.landing_lakehouse({}) == S.LANDING_LAKEHOUSE
    assert bare.landing_lakehouse({"lakehouse_name": "LH_Zone"}) == "LH_Zone"


def test_only_whole_number_and_text_keys_bound_a_merge():
    df = pl.DataFrame({"id": [3, 1], "name": ["b", "o'k"], "price": [1.5, 2.5], "none": [None, None]})
    assert S._key_bounds(df, ["id", "name", "price", "none"]) == [
        "t.`id` >= 1", "t.`id` <= 3", "t.`name` >= 'b'", "t.`name` <= 'o''k'"]


# ---- writes


def test_a_table_another_writer_made_after_the_read_stops_an_overwrite(store):
    store.write(LH, "dbo", "orders", orders())
    with pytest.raises(CommitFailedError, match="orders was created by another writer meanwhile"):
        store.write(LH, "dbo", "orders", orders((3,)), read_version=None)


def test_a_table_another_writer_made_after_the_read_stops_a_stream(store):
    store.write(LH, "dbo", "orders", orders())
    rows = orders((3,)).to_arrow().to_reader()
    with pytest.raises(CommitFailedError, match="created by another writer meanwhile"):
        store.write_stream(LH, "dbo", "orders", rows)
    assert store.read(LH, "dbo", "orders")["id"].to_list() == [1, 2]


def test_a_schema_that_cant_be_kept_after_a_commit_is_read_from_the_log_later(store, monkeypatch):
    said = []
    monkeypatch.setattr(S._log, "debug", lambda msg, *args: said.append(msg % args))

    def broken(*args):
        raise OSError("log moved")

    monkeypatch.setattr(S, "lazy", broken)
    assert store.write(LH, "dbo", "orders", orders()) == 0
    assert said and said[0].startswith("Couldn't keep the schema of")


def _failing_stream():
    def batches():
        yield orders().to_arrow().to_batches()[0]
        raise OSError("the source stopped")

    schema = orders().to_arrow().schema
    return pa.RecordBatchReader.from_batches(schema, batches())


def test_a_stream_whose_files_couldnt_be_listed_leaves_them_for_vacuum(store, monkeypatch):
    store.write(LH, "dbo", "orders", orders())
    monkeypatch.setattr(LocalStore, "_data_files", lambda self, *a: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(Exception, match="the source stopped"):
        store.write_stream(LH, "dbo", "orders", _failing_stream(), read_version=0)
    assert store.last_cleanup["note"].startswith("couldn't list the table's files before the write")


def test_files_a_failed_stream_left_that_cant_be_removed_are_left_for_vacuum(store, monkeypatch):
    store.write(LH, "dbo", "orders", orders())

    def refuse(self, *args):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(LocalStore, "_delete_data_files", refuse)
    with pytest.raises(Exception, match="the source stopped"):
        store.write_stream(LH, "dbo", "orders", _failing_stream(), read_version=0)
    assert store.last_cleanup["removed"] == 0
    assert "VACUUM removes them later" in store.last_cleanup["note"]


def test_a_table_folder_that_isnt_there_has_no_data_files(store):
    assert store._data_files(LH, "dbo", "nothing") == set()


# ---- reads


def test_the_highest_value_is_read_from_the_rows_when_the_log_has_no_statistics(store):
    assert store.column_max(LH, "dbo", "orders", "id") is None
    uri = store.uri(LH, "dbo", "orders")
    write_deltalake(uri, pa.table({"id": [4, 9, 2]}), configuration={"delta.dataSkippingNumIndexedCols": "0"})
    assert store.column_max(LH, "dbo", "orders", "id") == 9


def test_commits_since_a_version_are_the_only_ones_looked_at(store):
    assert store.has_commit(LH, "dbo", "orders", "run", "r1") is False
    store.write(LH, "dbo", "orders", orders(), metadata={"run": "r1"})
    store.write(LH, "dbo", "orders", orders((5,)), mode="append", metadata={"run": "r2"})
    assert store.has_commit(LH, "dbo", "orders", "run", "r1") is True
    assert store.has_commit(LH, "dbo", "orders", "run", "r1", since=0) is False
    assert store.has_commit(LH, "dbo", "orders", "run", "r2", since=0) is True
    assert store.has_commit(LH, "dbo", "orders", "run", "r2", since=1) is False


def test_files_are_read_as_parquet_json_and_json_lines_and_other_kinds_are_refused(store):
    files = store.files_dir(LH)
    files.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(files / "a.parquet")
    (files / "b.json").write_text(json.dumps([{"id": 2}]))
    (files / "c.jsonl").write_text('{"id": 3}\n')
    df, names = store.read_files(LH, "*")
    assert sorted(df["id"].to_list()) == [1, 2, 3] and names == ["a.parquet", "b.json", "c.jsonl"]
    assert store.read_files(LH, "*.csv") == (None, [])
    (files / "d.xlsx").write_bytes(b"PK")
    with pytest.raises(ValueError, match="Can't read d.xlsx: use CSV, Parquet or JSON"):
        store.read_files(LH, "d.*")


# ---- merges


def test_a_merge_against_a_version_of_a_table_that_isnt_there_raises(store):
    with pytest.raises(TableNotFoundError):
        store.merge_insert(LH, "dbo", "orders", orders(), ["id"], version=0)


def test_source_deletes_remove_their_rows_in_the_same_commit_and_the_flag_isnt_written(store):
    store.write(LH, "dbo", "orders", orders((1, 2, 3)))
    change = pl.DataFrame({"id": [2, 3, 4], "amount": [0.0, 33.0, 40.0], "gone": [True, None, False]})
    version, metrics = store.upsert(LH, "dbo", "orders", change, ["id"], delete_flag="gone")
    got = store.read(LH, "dbo", "orders").sort("id")
    assert version == 1 and got.columns == ["id", "amount"]
    assert got.rows() == [(1, 10.0), (3, 33.0), (4, 40.0)]
    assert metrics["num_target_rows_deleted"] == 1


# ---- a OneLake store's addresses


def test_a_onelake_layer_that_isnt_set_up_says_how_to_make_it(tmp_path):
    dev = OneLakeStore("dev", "ws1", {}, lambda: "tok", tmp_path)
    with pytest.raises(NotSetUp, match="LH_Silver doesn't exist in the dev workspace yet; run Dev to create it"):
        dev.uri(LH, "dbo", "orders")
    prod = OneLakeStore("prod", "ws2", {}, lambda: "tok", tmp_path)
    with pytest.raises(NotSetUp, match="promote to create it"):
        prod.uri(LH, "dbo", "orders")


def test_a_onelake_table_is_addressed_by_its_layers_workspace_and_signed_with_the_token(tmp_path):
    s = OneLakeStore("dev", "ws1", {LH: "lh1"}, lambda: "tok", tmp_path, lakehouse_workspaces={LH: "ws9"})
    assert s.uri(LH, "dbo", "orders").startswith("abfss://ws9@")
    assert s.options()["bearer_token"] == "tok"


def test_a_landing_zone_without_its_lakehouse_yet_is_refused(tmp_path):
    s = OneLakeStore("dev", "ws1", {}, lambda: "tok", tmp_path)
    with pytest.raises(LookupError, match="the landing zone Sales has no lakehouse yet"):
        s.landing_lakehouse({"name": "Sales", "lakehouse_id": "lh9"})
    assert s.landing_lakehouse({"id": "z1", "lakehouse_id": "lh9", "workspace_id": "wsL"}) == S.LANDING_LAKEHOUSE
    assert s.uri(S.LANDING_LAKEHOUSE, "dbo", "files").startswith("abfss://wsL@")
