"""Where a metadata project's tables live (meta/store.py): what mutation testing (batch 14,
docs/quality/mutation-testing.md) showed no test checked. Local tables in tmp_path; nothing is sent."""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable
from deltalake.exceptions import CommitFailedError

from fabric_desktop.leases import LeaseLost
from fabric_desktop.meta import store as S
from fabric_desktop.meta.store import LocalStore

LH = "LH_Silver"


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "dev", "dev")


def orders(ids=(1, 2)):
    return pl.DataFrame({"id": list(ids), "amount": [10.0 * i for i in ids]})


def last_commit(store, table):
    return DeltaTable(store.uri(LH, "dbo", table)).history(1)[0]


@pytest.mark.parametrize("run, where", [
    ({"batch_id": "fabric-123", "worker": "w1"}, "Fabric"),
    ({"batch_id": "b1", "worker": "w1", "host": "pc7"}, "Agent w1 on pc7"),
    ({"batch_id": "b1", "worker": "w1"}, "Agent w1"),
    ({"batch_id": None, "worker": "", "host": "pc7"}, "This PC"),
    ({}, "This PC"),
])
def test_where_a_run_ran(run, where):
    assert S.ran_on(run) == where


def test_create_if_absent_says_whether_it_made_the_table(store):
    cols = {"id": pl.Int64}
    assert store.create_if_absent(LH, "dbo", "t", cols) is True
    assert store.create_if_absent(LH, "dbo", "t", cols) is False
    assert store.count(LH, "dbo", "t") == 0


def test_the_kept_schema_is_for_the_version_this_store_wrote_only(store):
    v = store.write(LH, "dbo", "t", orders())
    assert store.written_schema(LH, "dbo", "t", v) == pl.Schema({"id": pl.Int64, "amount": pl.Float64})
    assert store.written_schema(LH, "dbo", "t", v + 1) is None
    assert store.written_schema(LH, "dbo", "other", v) is None


def test_a_merge_into_a_table_with_a_narrower_type_is_refused(store):
    store.write(LH, "dbo", "t", pl.DataFrame({"id": [1], "n": [1]}))
    big = pl.DataFrame({"id": [2], "n": [2.75]})
    with pytest.raises(ValueError, match=r"Writing to dbo\.t would change values to fit its column types"):
        store.merge_insert(LH, "dbo", "t", big, ["id"])
    assert store.count(LH, "dbo", "t") == 1


def test_an_upsert_on_a_key_with_no_range_still_merges(store):
    df = pl.DataFrame({"k": [1.5, 2.5], "v": ["a", "b"]})  # a float key bounds nothing
    store.write(LH, "dbo", "t", df)
    store.upsert(LH, "dbo", "t", pl.DataFrame({"k": [2.5, 3.5], "v": ["B", "c"]}), ["k"])
    got = store.read(LH, "dbo", "t").sort("k")
    assert got.to_dicts() == [{"k": 1.5, "v": "a"}, {"k": 2.5, "v": "B"}, {"k": 3.5, "v": "c"}]


def test_an_upsert_keeps_its_commit_metadata(store):
    store.write(LH, "dbo", "t", orders())
    store.upsert(LH, "dbo", "t", orders((2, 3)), ["id"], metadata={"run_id": "r9"})
    assert last_commit(store, "t").get("run_id") == "r9"
    assert store.has_commit(LH, "dbo", "t", "run_id", "r9")


def test_a_stream_keeps_its_commit_metadata(store):
    rows = orders().to_arrow()
    v = store.write_stream(LH, "dbo", "t", pa.RecordBatchReader.from_batches(rows.schema, rows.to_batches()),
                           metadata={"run_id": "r1"})
    assert v == 0 and last_commit(store, "t").get("run_id") == "r1"
    assert store.read(LH, "dbo", "t").sort("id").equals(orders())


def test_a_stream_that_loses_its_lease_says_so_and_isnt_called_a_conflict(store, monkeypatch):
    def lost():
        raise LeaseLost("the lease on dbo.t is gone")

    monkeypatch.setattr(S, "check_held", lost)
    rows = orders().to_arrow()
    with pytest.raises(LeaseLost):
        store.write_stream(LH, "dbo", "t", pa.RecordBatchReader.from_batches(rows.schema, rows.to_batches()))
    assert store.last_cleanup == S._LEASE_LOST
    assert store.last_cleanup is not S._LEASE_LOST  # a copy, so the module's own can't be changed


def test_a_stream_into_a_new_table_another_writer_made_is_a_conflict(store):
    rows = orders().to_arrow()
    store.write(LH, "dbo", "t", orders())  # made by "another writer" before this stream
    with pytest.raises(CommitFailedError, match="t was created by another writer meanwhile"):
        store.write_stream(LH, "dbo", "t", pa.RecordBatchReader.from_batches(rows.schema, rows.to_batches()))


# ---- appends that lose a race for the next version


@pytest.fixture
def sleeps(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(S.time, "sleep", slept.append)
    return slept


def test_an_append_that_keeps_losing_the_windows_commit_race_gives_up_after_ten_tries(store, monkeypatch, sleeps):
    tries = []

    def race(*a, **k):
        tries.append(1)
        raise OSError("Unable to open file _delta_log/00000000000000000001.json#1 (os error 5)")

    monkeypatch.setattr(S, "write_deltalake", race)
    monkeypatch.setattr(S, "staged_commit_race", lambda exc: True)
    with pytest.raises(OSError, match="Unable to open file"):
        store.write(LH, "dbo", "t", orders(), mode="append")
    assert len(tries) == S.APPEND_RACE_ATTEMPTS == 10
    assert sleeps == pytest.approx([0.05 * n for n in range(1, 10)])


def test_an_overwrite_is_never_sent_again_after_that_race(store, monkeypatch, sleeps):
    def race(*a, **k):
        raise OSError("Unable to open file _delta_log/00000000000000000001.json#1 (os error 5)")

    monkeypatch.setattr(S, "write_deltalake", race)
    monkeypatch.setattr(S, "staged_commit_race", lambda exc: True)
    with pytest.raises(OSError):
        store.write(LH, "dbo", "t", orders())
    assert sleeps == []


def test_a_first_append_that_lost_to_another_writer_s_create_is_made_again(store, monkeypatch, sleeps):
    real = S.write_deltalake
    store.write(LH, "dbo", "t", orders())
    calls = []

    def lose_once(target, data, **kw):
        calls.append(target)
        if len(calls) == 1:
            raise CommitFailedError("Protocol changed")
        return real(target, data, **kw)

    monkeypatch.setattr(S, "write_deltalake", lose_once)
    store.write(LH, "dbo", "t", orders((3,)), mode="append")
    assert len(calls) == 2 and isinstance(calls[1], DeltaTable)
    assert sleeps == [pytest.approx(0.05)]
    assert sorted(store.read(LH, "dbo", "t")["id"].to_list()) == [1, 2, 3]


def test_an_append_that_keeps_losing_to_other_creates_gives_up_after_ten_tries(store, monkeypatch, sleeps):
    store.write(LH, "dbo", "t", orders())
    tries = []

    def lose(*a, **k):
        tries.append(1)
        raise CommitFailedError("Metadata changed since the read")

    monkeypatch.setattr(S, "write_deltalake", lose)
    with pytest.raises(CommitFailedError, match="Metadata changed"):
        store.write(LH, "dbo", "t", orders((3,)), mode="append")
    assert len(tries) == 10
    assert sleeps == pytest.approx([0.05 * n for n in range(1, 10)])


# ---- the files a stream that didn't commit left


@pytest.mark.parametrize("left, note", [(["a.parquet"], "removed 1 data file this write left"),
                                        (["a.parquet", "b.parquet"], "removed 2 data files this write left")])
def test_the_files_a_failed_stream_left_are_counted(store, left, note):
    store.write(LH, "dbo", "t", orders())
    folder = S.Path(store.uri(LH, "dbo", "t"))
    before = store._data_files(LH, "dbo", "t")
    for name in left:
        (folder / name).write_bytes(b"x")
    got = store._remove_unwritten(LH, "dbo", "t", before, read_version=0)
    assert got == {"removed": len(left), "note": note}
    assert store._data_files(LH, "dbo", "t") == before


def test_a_failed_stream_whose_files_couldnt_be_listed_removes_none(store, monkeypatch):
    def unlistable(*a):
        raise OSError("can't list")

    def refused():
        raise RuntimeError("refused")

    monkeypatch.setattr(store, "_data_files", unlistable)
    monkeypatch.setattr(S, "check_held", refused)
    rows = orders().to_arrow()
    with pytest.raises(RuntimeError, match="refused"):
        store.write_stream(LH, "dbo", "t", pa.RecordBatchReader.from_batches(rows.schema, rows.to_batches()))
    assert store.last_cleanup == {"removed": 0, "note": "couldn't list the table's files before the write, so any "
                                                        "it left are left for VACUUM"}
