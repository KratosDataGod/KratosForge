"""A commit on this PC that loses the race for a version on Windows fails as a clean commit conflict.

Elsewhere delta-rs either makes a commit that lost the race for a version on the next one (an append), or
raises CommitFailedError. On Windows the loser can instead be refused opening its staged log file,
``_delta_log/NNN.json#1``, with os error 5 ("Access is denied"): main's Windows CI hit it in
test_maintenance_concurrency. Nothing was committed then. Every commit the app makes to a table on this
PC answers that the same way:

- an append (LocalStore.write) is sent again, and after its tries fails as CommitFailedError;
- an overwrite or a replace (LocalStore.write, write_stream) fails as CommitFailedError at once: sending it
  again could overwrite the winner's rows;
- OPTIMIZE, Z-Order, VACUUM and the retention property (compact_local, a notebook's OPTIMIZE / ZORDER BY /
  VACUUM) run again on the latest version, which changes no rows, and fail as CommitFailedError after their
  tries;
- a notebook's RESTORE fails as CommitFailedError at once: a second RESTORE would undo the winner's commit.

Any other OSError is left as it is. The race is simulated by making delta-rs's call raise the exact error
Windows gives, so this runs on every OS. No network, no Fabric."""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError
from deltalake.table import TableAlterer, TableOptimizer

from fabric_desktop import lakehouse, maintenance, retrying
from fabric_desktop.meta import store as store_mod
from fabric_desktop.meta.store import APPEND_RACE_ATTEMPTS, LocalStore
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_zorder_hardening import allowed, events, ids  # noqa: F401 - allowed is a fixture

STAGED = ("Generic LocalFileSystem error\n  \x1b[31m\u21b3\x1b[0m Unable to open file D:\\a\\_temp\\t\\dbo\\events"
          "\\_delta_log\\00000000000000000031.json#1\n  \x1b[31m\u21b3\x1b[0m Access is denied. (os error 5)\n")
OTHER = "Generic LocalFileSystem error: There is not enough space on the disk. (os error 112)"


def racing(real, losses: int, calls: list, text: str = STAGED):
    """``real``, refused ``losses`` times first with ``text`` as an OSError."""
    def call(*a, **k):
        calls.append(1)
        if len(calls) <= losses:
            raise OSError(text)
        return real(*a, **k)
    return call


# ---- the classification

def test_the_ci_error_is_a_lost_commit_and_other_errors_are_not():
    assert isinstance(retrying.lost_commit(OSError(STAGED), "t"), CommitFailedError)
    assert retrying.lost_commit(OSError(OTHER), "t") is None
    # a log file held while it is read (no #n) may follow a commit that went in: not called a lost commit
    held = STAGED.replace(".json#1", ".json")
    assert retrying.windows_log_race(OSError(held)) and retrying.lost_commit(OSError(held), "t") is None
    said = str(retrying.lost_commit(OSError(STAGED), "dbo.events's next version"))
    assert "dbo.events" in said and "wasn't committed" in said and "Nothing was changed by it" in said


def test_local_commit_runs_again_then_fails_as_a_commit_conflict():
    calls: list = []
    assert retrying.local_commit(racing(lambda: "done", 2, calls), "t", backoff=0) == "done" and len(calls) == 3
    calls.clear()
    with pytest.raises(CommitFailedError, match="wasn't committed") as raised:
        retrying.local_commit(racing(lambda: "done", 99, calls), "t", attempts=4, backoff=0)
    assert len(calls) == 4 and isinstance(raised.value.__cause__, OSError)
    calls.clear()
    with pytest.raises(OSError, match="os error 112"):
        retrying.local_commit(racing(lambda: "done", 99, calls, OTHER), "t", backoff=0)
    assert calls == [1]


# ---- LocalStore: the app's own writes on this PC

def frame(start: int, n: int = 5) -> pl.DataFrame:
    return pl.DataFrame({"id": list(range(start, start + n)), "amount": [0.0] * n})


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod.time, "sleep", lambda _s: None)
    s = LocalStore(tmp_path, "dev")
    s.write("LH_Bronze", "dbo", "events", frame(0), mode="append")
    return s


def table_ids(s: LocalStore) -> list[int]:
    return sorted(DeltaTable(s.uri("LH_Bronze", "dbo", "events")).to_pyarrow_table()["id"].to_pylist())


def test_an_append_that_loses_the_race_is_made_again(store, monkeypatch):
    calls: list = []
    monkeypatch.setattr(store_mod, "write_deltalake", racing(store_mod.write_deltalake, 2, calls))
    store.write("LH_Bronze", "dbo", "events", frame(100), mode="append")
    assert len(calls) == 3 and table_ids(store) == list(range(5)) + list(range(100, 105))


def test_an_append_that_keeps_losing_fails_as_a_commit_conflict_and_changes_nothing(store, monkeypatch):
    before = DeltaTable(store.uri("LH_Bronze", "dbo", "events")).version()
    calls: list = []
    monkeypatch.setattr(store_mod, "write_deltalake", racing(store_mod.write_deltalake, 99, calls))
    with pytest.raises(CommitFailedError, match="dbo.events"):
        store.write("LH_Bronze", "dbo", "events", frame(100), mode="append")
    assert len(calls) == APPEND_RACE_ATTEMPTS
    assert DeltaTable(store.uri("LH_Bronze", "dbo", "events")).version() == before and table_ids(store) == list(range(5))


@pytest.mark.parametrize("read_version", [False, 0])
def test_an_overwrite_that_loses_the_race_fails_as_a_commit_conflict_at_once(store, monkeypatch, read_version):
    calls: list = []
    monkeypatch.setattr(store_mod, "write_deltalake", racing(store_mod.write_deltalake, 1, calls))
    with pytest.raises(CommitFailedError, match="wasn't committed"):
        store.write("LH_Bronze", "dbo", "events", frame(100), mode="overwrite", read_version=read_version)
    assert calls == [1] and table_ids(store) == list(range(5))


def test_a_streamed_replace_that_loses_the_race_fails_as_a_commit_conflict(store, monkeypatch):
    calls: list = []
    monkeypatch.setattr(store_mod, "write_deltalake", racing(store_mod.write_deltalake, 1, calls))
    rows = pa.RecordBatchReader.from_batches(frame(100).to_arrow().schema, frame(100).to_arrow().to_batches())
    with pytest.raises(CommitFailedError, match="wasn't committed"):
        store.write_stream("LH_Bronze", "dbo", "events", rows, read_version=0)
    assert calls == [1] and table_ids(store) == list(range(5))


def test_any_other_write_failure_is_left_as_it_is(store, monkeypatch):
    calls: list = []
    monkeypatch.setattr(store_mod, "write_deltalake", racing(store_mod.write_deltalake, 99, calls, OTHER))
    with pytest.raises(OSError, match="os error 112"):
        store.write("LH_Bronze", "dbo", "events", frame(100), mode="append")
    assert calls == [1]


# ---- maintenance on this PC

@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(retrying.time, "sleep", lambda _s: None)


def small_table(path) -> str:
    uri = str(path / "t")
    for b in range(4):
        write_deltalake(uri, pa.table({"id": list(range(b * 5, b * 5 + 5))}), mode="append")
    return uri


@pytest.mark.parametrize("cls,name", [(TableOptimizer, "compact"), (DeltaTable, "vacuum"),
                                      (TableAlterer, "set_table_properties")])
def test_compact_local_runs_each_commit_again_after_a_lost_race(tmp_path, monkeypatch, no_wait, cls, name):
    uri = small_table(tmp_path)
    calls: list = []
    monkeypatch.setattr(cls, name, racing(getattr(cls, name), 1, calls))
    maintenance.compact_local(uri)
    assert len(calls) >= 2  # refused once, then made (VACUUM is also asked for a dry run elsewhere)
    assert sorted(DeltaTable(uri).to_pyarrow_table()["id"].to_pylist()) == list(range(20))


@pytest.mark.parametrize("cls,name", [(TableOptimizer, "compact"), (DeltaTable, "vacuum"),
                                      (TableAlterer, "set_table_properties")])
def test_compact_local_that_keeps_losing_fails_as_a_commit_conflict(tmp_path, monkeypatch, no_wait, cls, name):
    uri = small_table(tmp_path)
    monkeypatch.setattr(cls, name, racing(getattr(cls, name), 99, []))
    with pytest.raises(CommitFailedError, match="wasn't committed"):
        maintenance.compact_local(uri)
    assert sorted(DeltaTable(uri).to_pyarrow_table()["id"].to_pylist()) == list(range(20))


@pytest.mark.parametrize("op", ["compact", "zorder", "vacuum"])
def test_notebook_maintenance_that_keeps_losing_fails_as_a_commit_conflict(allowed, monkeypatch, no_wait, op):  # noqa: F811
    uri = events(allowed, 3)
    before, version = sorted(ids(uri)), DeltaTable(uri).version()
    cls, name = {"compact": (TableOptimizer, "compact"), "zorder": (TableOptimizer, "z_order"),
                 "vacuum": (DeltaTable, "vacuum")}[op]
    calls: list = []
    monkeypatch.setattr(cls, name, racing(getattr(cls, name), 99, calls))
    with pytest.raises(CommitFailedError, match="dbo.events"):
        if op == "vacuum":
            lakehouse.maintain_table("dbo.events", "vacuum")
        else:
            lakehouse.maintain_table("dbo.events", "optimize", zorder=["id"] if op == "zorder" else None)
    assert len(calls) == 6 and DeltaTable(uri).version() == version and sorted(ids(uri)) == before


def test_notebook_vacuum_runs_again_after_a_lost_race(allowed, monkeypatch, no_wait):  # noqa: F811
    uri = events(allowed, 3)
    before = sorted(ids(uri))
    calls: list = []
    monkeypatch.setattr(DeltaTable, "vacuum", racing(DeltaTable.vacuum, 1, calls))
    lakehouse.maintain_table("dbo.events", "vacuum")
    assert len(calls) == 2 and sorted(ids(uri)) == before


def test_notebook_restore_that_loses_the_race_fails_as_a_commit_conflict_and_isnt_repeated(allowed, monkeypatch):  # noqa: F811
    uri = events(allowed, 3)
    version = DeltaTable(uri).version()
    calls: list = []
    monkeypatch.setattr(DeltaTable, "restore", racing(DeltaTable.restore, 99, calls))
    with pytest.raises(CommitFailedError, match="wasn't committed"):
        lakehouse.maintain_table("dbo.events", "restore", version=0)
    assert calls == [1] and DeltaTable(uri).version() == version
