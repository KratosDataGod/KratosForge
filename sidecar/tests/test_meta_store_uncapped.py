"""Mutation testing batch 180 (docs/quality/mutation-testing.md): where a metadata project's tables
live (meta/store.py), every mutant. Each test pins a behavior a surviving mutant changed without any
other test noticing. Local tables in tmp_path; nothing is sent."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable
from deltalake.exceptions import CommitFailedError

from fabric_desktop.leases import LeaseLost
from fabric_desktop.meta import store as S
from fabric_desktop.meta.store import LocalStore, OneLakeStore

LH = "LH_Silver"


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "dev", "dev")


def orders(ids=(1, 2)):
    return pl.DataFrame({"id": list(ids), "amount": [10.0 * i for i in ids]})


# ---- writes


def _capture_writes(monkeypatch):
    sent = []
    monkeypatch.setattr(S, "write_deltalake", lambda target, data, **kw: sent.append(kw))
    return sent


def test_only_an_append_may_move_up_100_versions_past_other_writers(store, monkeypatch):
    store.write(LH, "dbo", "t", orders())
    sent = _capture_writes(monkeypatch)
    monkeypatch.setattr(LocalStore, "_committed", lambda self, uri, table: 0)
    store.write(LH, "dbo", "t", orders([3]), mode="append")
    store.write(LH, "dbo", "t", orders([4]))
    assert sent[0]["commit_properties"].max_commit_retries == 100
    assert sent[1]["commit_properties"] is None


def test_an_overwrite_against_a_version_another_writer_moved_on_from_is_a_conflict(store):
    store.write(LH, "dbo", "t", orders())
    store.write(LH, "dbo", "t", orders([3]), mode="append")
    with pytest.raises(CommitFailedError):
        store.write(LH, "dbo", "t", orders([9]), read_version=0)
    assert sorted(store.read(LH, "dbo", "t")["id"]) == [1, 2, 3]


def test_a_first_write_that_loses_its_lease_says_so_and_isnt_called_a_conflict(store, monkeypatch):
    def lost():
        raise LeaseLost("the lease is gone")

    monkeypatch.setattr(S, "check_held", lost)
    with pytest.raises(LeaseLost):
        store.write(LH, "dbo", "t", orders(), read_version=None)


class _Remote(S.Store):
    """A store that isn't on this PC, with its tables at local paths so nothing is sent."""

    env = "test"

    def __init__(self, root):
        self.root = root

    def uri(self, lakehouse, schema, table):
        return str(self.root / lakehouse / schema / table)


def test_only_a_store_on_this_pc_makes_the_table_folder_first(tmp_path, monkeypatch):
    import pyarrow as pa

    made = []

    class Recorded(type(Path())):
        def mkdir(self, *a, **kw):
            made.append(str(self))
            return super().mkdir(*a, **kw)

    monkeypatch.setattr(S, "Path", Recorded)
    remote, local = _Remote(tmp_path / "remote"), LocalStore(tmp_path / "local")
    rows = orders().to_arrow()
    for st in (remote, local):
        st.write(LH, "dbo", "w", orders())
        st.write_stream(LH, "dbo", "s", pa.RecordBatchReader.from_batches(rows.schema, rows.to_batches()))
        assert st.create_if_absent(LH, "dbo", "c", {"id": pl.Int64}) is True
        assert st.read(LH, "dbo", "w")["id"].to_list() == [1, 2] and st.count(LH, "dbo", "s") == 2
    assert not [m for m in made if "remote" in m]
    assert {local.uri(LH, "dbo", n) for n in ("w", "s", "c")} <= set(made)


def test_a_failed_stream_in_a_store_that_cant_tell_its_files_apart_removes_none(tmp_path, monkeypatch):
    import pyarrow as pa

    def refuse(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(S, "write_deltalake", refuse)
    st = _Remote(tmp_path / "remote")
    rows = orders().to_arrow()
    with pytest.raises(OSError):
        st.write_stream(LH, "dbo", "s", pa.RecordBatchReader.from_batches(rows.schema, rows.to_batches()))
    assert st.last_cleanup["removed"] == 0 and "left for VACUUM" in st.last_cleanup["note"]


def test_merge_insert_returns_the_new_version_and_refuses_a_stale_one(store):
    assert store.merge_insert(LH, "dbo", "t", orders(), ["id"]) == 1  # created at 0, merged at 1
    assert store.merge_insert(LH, "dbo", "t", orders([3]), ["id"], version=1) == 2
    with pytest.raises(CommitFailedError, match="changed since version 1"):
        store.merge_insert(LH, "dbo", "t", orders([4]), ["id"], version=1)
    assert sorted(store.read(LH, "dbo", "t")["id"]) == [1, 2, 3]


# ---- reads


def test_the_version_is_the_tables_latest(store):
    assert store.version(LH, "dbo", "t") is None
    store.write(LH, "dbo", "t", orders())
    store.write(LH, "dbo", "t", orders([3]), mode="append")
    assert store.version(LH, "dbo", "t") == 1


def test_count_and_the_highest_value_come_from_the_log_without_reading_rows(store, monkeypatch):
    store.write(LH, "dbo", "t", orders((1, 5, 3)))
    monkeypatch.setattr(S, "lazy", lambda *a, **kw: pytest.fail("the rows were read"))
    assert store.count(LH, "dbo", "t") == 3
    assert store.column_max(LH, "dbo", "t", "id") == 5


def test_history_gives_the_last_20_commits_by_default(store, monkeypatch):
    store.write(LH, "dbo", "t", orders())
    asked = []
    monkeypatch.setattr(S.DeltaTable, "history", lambda self, limit=None: asked.append(limit) or [])
    store.history(LH, "dbo", "t")
    assert asked == [20]


def test_a_commit_at_or_before_since_isnt_looked_at(store):
    store.write(LH, "dbo", "t", orders(), metadata={"run_id": "r0"})
    store.write(LH, "dbo", "t", orders([3]), mode="append", metadata={"run_id": "r1"})
    store.write(LH, "dbo", "t", orders([4]), mode="append", metadata={"run_id": "r2"})
    assert store.has_commit(LH, "dbo", "t", "run_id", "r2", since=1) is True
    assert store.has_commit(LH, "dbo", "t", "run_id", "r1", since=1) is False
    assert store.has_commit(LH, "dbo", "t", "run_id", "r1", since=0) is True
    assert store.has_commit(LH, "dbo", "t", "run_id", "r2", since=2) is False


def test_csv_files_parse_dates_and_infer_types_from_10000_rows(store):
    folder = store.files_dir(LH) / "in"
    folder.mkdir(parents=True)
    (folder / "d.csv").write_text("id,day\n1,2024-05-06\n", encoding="utf-8")
    df, names = store.read_files(LH, "in/d.csv")
    assert names == ["d.csv"] and df.schema["day"] == pl.Date
    # The 10,001st row is past what is looked at, so its other type isn't read as part of the column.
    (folder / "late.csv").write_text("v\n" + "1\n" * 10000 + "1.5\n", encoding="utf-8")
    with pytest.raises(pl.exceptions.ComputeError):
        store.read_files(LH, "in/late.csv")


# ---- the files a failed write left


def test_removing_what_a_failed_write_left_stops_when_the_lease_is_gone(store, monkeypatch):
    def lost():
        raise LeaseLost("gone")

    monkeypatch.setattr(S, "check_held", lost)
    assert store._remove_unwritten(LH, "dbo", "t", set(), None) == S._LEASE_LOST


def test_nothing_is_removed_when_another_writer_committed_meanwhile(store):
    store.write(LH, "dbo", "t", orders())
    store.write(LH, "dbo", "t", orders([3]), mode="append")
    got = store._remove_unwritten(LH, "dbo", "t", set(), 0)
    assert got == {"removed": 0, "note": "another writer committed to the table meanwhile, so the files this "
                                         "write left are left for VACUUM"}


def test_a_first_write_that_failed_has_its_files_removed(store):
    root = Path(store.uri(LH, "dbo", "t"))
    root.mkdir(parents=True)
    (root / "part-0.parquet").write_bytes(b"x")
    assert store._remove_unwritten(LH, "dbo", "t", set(), None)["removed"] == 1
    assert not (root / "part-0.parquet").exists()


def test_data_files_are_the_files_outside_the_log(store):
    store.write(LH, "dbo", "t", orders())
    files = store._data_files(LH, "dbo", "t")
    assert files and all(f.endswith(".parquet") and not f.startswith("_delta_log") for f in files)


def test_a_data_file_already_gone_is_no_error(store):
    store.write(LH, "dbo", "t", orders())
    store._delete_data_files(LH, "dbo", "t", ["gone.parquet"])


# ---- sync conflicts


def test_commits_a_sync_client_set_aside_are_found_in_version_order(store):
    log = Path(store.uri(LH, "dbo", "t")) / "_delta_log"
    assert store.sync_conflicts(LH, "dbo", "t") == []
    log.mkdir(parents=True)
    for name in ("00000000000000000005-PC2.json", "00000000000000000003.json", "00000000000000000003.crc",
                 "00000000000000000002 (conflicted copy).json", "_last_checkpoint", "notes.txt"):
        (log / name).write_text("{}", encoding="utf-8")
    assert store.sync_conflicts(LH, "dbo", "t") == [(2, "00000000000000000002 (conflicted copy).json"),
                                                    (5, "00000000000000000005-PC2.json")]


# ---- meta tables


def test_a_meta_table_is_compacted_only_at_every_100th_commit(store, monkeypatch):
    asked = []

    def lease(self, lakehouse, schema, table, wait):
        asked.append(wait)
        raise S.LeaseBusy("busy")

    monkeypatch.setattr(LocalStore, "compaction_lease", lease)
    for version in (0, 1, 99, 100, 101, 200):
        asked.append(version)
        store._tidy_meta("run_log", version)
    assert asked == [0, 1, 99, 100, 0, 101, 200, 0]


def test_a_meta_compaction_rewrites_the_files_and_syncs_its_commit(store, monkeypatch):
    for i in range(3):
        store.write(S.META_LAKEHOUSE, "meta", "watermarks", orders([i]), mode="append")
    synced = []
    monkeypatch.setattr(S.durable, "sync_commits", lambda uri, first, last: synced.append((first, last)))
    store._tidy_meta("watermarks", 100)
    t = DeltaTable(store.uri(S.META_LAKEHOUSE, "meta", "watermarks"))
    assert t.version() == 3 and t.history(1)[0]["operation"] == "OPTIMIZE"
    assert synced == [(101, 3)]


def test_the_compaction_lease_lasts_120_seconds(store, monkeypatch):
    made = []

    class Leases:
        def __init__(self, backend, owner, ttl, wait):
            made.append((owner, ttl, wait))

        def hold(self, key):
            return key

    monkeypatch.setattr(S, "Leases", Leases)
    assert store.compaction_lease(LH, "dbo", "t", wait=5) == "compact/" + S.table_key(LH, "dbo", "t")
    assert made == [("KratosForge compaction", 120, 5)]


# ---- OneLake


class _Files:
    def __init__(self):
        self.downloads = []

    def list(self, ws, lh, directory):
        return [{"name": "b.csv", "path": "Files/in/b.csv", "is_dir": False, "size": 2, "modified": "m2"},
                {"name": "a.csv", "path": "Files/in/a.csv", "is_dir": False, "size": 1, "modified": "m1"},
                {"name": "sub", "path": "Files/in/sub", "is_dir": True, "size": 0, "modified": "m0"},
                {"name": "c.txt", "path": "Files/in/c.txt", "is_dir": False, "size": 3, "modified": "m3"}]

    def download(self, ws, lh, path, dest):
        self.downloads.append((ws, lh, path, dest))
        return dest


def test_onelake_files_are_listed_by_pattern_in_order_and_fetched_to_the_cache(tmp_path):
    files = _Files()
    st = OneLakeStore("test", "ws1", {LH: "lh1"}, lambda: "tok", tmp_path, files=files)
    assert st._files is files
    listed = st.list_files(LH, "in/*.csv")
    assert listed == [{"path": "in/a.csv", "size": 1, "modified": "m1"}, {"path": "in/b.csv", "size": 2, "modified": "m2"}]
    got = st.fetch_files(LH, listed[:1])
    assert got == [tmp_path / "test" / LH / "Files" / "in" / "a.csv"]
    assert files.downloads == [("ws1", "lh1", "Files/in/a.csv", got[0])]


# ---- a first append that lost to another writer's create


def _lose_first_to(monkeypatch, store, other: pl.DataFrame):
    real = S.write_deltalake
    calls = []
    monkeypatch.setattr(S.time, "sleep", lambda s: None)

    def lose_once(target, data, **kw):
        calls.append(target)
        if len(calls) == 1:
            store.write(LH, "dbo", "t", other)  # another writer creates the table first
            raise CommitFailedError("Protocol changed")
        return real(target, data, **kw)

    monkeypatch.setattr(S, "write_deltalake", lose_once)
    return calls


def test_an_append_made_again_is_still_refused_when_it_would_change_values(store, monkeypatch):
    _lose_first_to(monkeypatch, store, pl.DataFrame({"id": [1], "amount": [7]}))
    with pytest.raises(ValueError, match="would change values to fit its column types"):
        store.write(LH, "dbo", "t", pl.DataFrame({"id": [3], "amount": [1.5]}), mode="append")
    assert store.read(LH, "dbo", "t")["id"].to_list() == [1]


def test_a_table_is_created_in_a_folder_that_is_already_there(store):
    Path(store.uri(LH, "dbo", "t")).mkdir(parents=True)
    assert store.create_if_absent(LH, "dbo", "t", {"id": pl.Int64}) is True
    assert store.count(LH, "dbo", "t") == 0


def test_an_append_made_again_after_the_table_went_away_raises_the_conflict(store, monkeypatch):
    monkeypatch.setattr(S.time, "sleep", lambda s: None)

    def lose(target, data, **kw):
        raise CommitFailedError("Protocol changed")

    monkeypatch.setattr(S, "write_deltalake", lose)
    with pytest.raises(CommitFailedError, match="Protocol changed"):
        store.write(LH, "dbo", "t", orders(), mode="append")
