"""A write to a project's own Delta table while OneDrive, a virus scanner or the search indexer holds one of
its files on Windows. delta-rs reports that as an OSError ending "(os error 32)" or "(os error 33)"; the
person is told which file and, where Windows says (file_locks.holders), which program, and that nothing
was committed. The fakes raise delta-rs's own Windows wording, since a Linux runner can't hold a file so.
"""

from __future__ import annotations

import deltalake
import pytest
from deltalake import DeltaTable

from fabric_desktop import delta_dml, file_locks
from fabric_desktop.localdata import LocalData

LOG = "C:\\Users\\ann\\OneDrive\\KratosForge\\Sales\\tables\\dbo\\s\\_delta_log\\00000000000000000001.json"
HELD = (f"Generic LocalFileSystem error: Unable to open file {LOG}: The process cannot access the file because "
        "it is being used by another process. (os error 32)")


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Held")["id"])
    e.execute("CREATE TABLE s USING DELTA AS SELECT 1 AS n")
    yield e
    local.close_all()


def _held_write(monkeypatch):
    def write_deltalake(*a, **kw):
        raise OSError(HELD)
    monkeypatch.setattr(deltalake, "write_deltalake", write_deltalake)


def test_an_insert_with_a_held_file_names_the_file_and_says_nothing_was_committed(engine, monkeypatch):
    _held_write(monkeypatch)
    monkeypatch.setattr(file_locks, "holders", lambda p: [])
    with pytest.raises(ValueError) as caught:
        engine.execute("INSERT INTO s SELECT 2")
    assert str(caught.value) == (
        "Writing dbo.s stopped because another program, such as OneDrive, a virus scanner or the search indexer, "
        "has one of the table's files open (00000000000000000001.json). Nothing was committed. Wait a moment and "
        "run it again. If the project is in a folder OneDrive syncs, pause syncing while you work.")
    assert DeltaTable(str(engine.tables_dir / "dbo" / "s")).version() == 0


def test_an_insert_names_the_program_holding_the_file(engine, monkeypatch):
    _held_write(monkeypatch)
    asked = []
    monkeypatch.setattr(file_locks, "holders", lambda p: asked.append(p) or ["Microsoft OneDrive"])
    with pytest.raises(ValueError) as caught:
        engine.execute("INSERT INTO s SELECT 2")
    assert asked == [LOG]
    assert str(caught.value) == (
        "Writing dbo.s stopped because 00000000000000000001.json, one of the table's files, is open in Microsoft "
        "OneDrive. Nothing was committed. Close it there, or wait for it to finish, and run it again.")


def test_a_change_with_a_held_file_says_so(engine, monkeypatch):
    def apply(*a, **kw):
        raise OSError(HELD.replace("(os error 32)", "(os error 33)"))
    monkeypatch.setattr(delta_dml, "apply", apply)
    monkeypatch.setattr(file_locks, "holders", lambda p: ["Windows Search"])
    with pytest.raises(ValueError, match=r"^Changing dbo\.s stopped because 00000000000000000001\.json, one of the "
                                         r"table's files, is open in Windows Search\. Nothing was committed\."):
        engine.execute("UPDATE s SET n = 3 WHERE n = 1")


def test_any_other_write_failure_keeps_its_own_words(engine, monkeypatch):
    def write_deltalake(*a, **kw):
        raise OSError("No space left on device (os error 28)")
    monkeypatch.setattr(deltalake, "write_deltalake", write_deltalake)
    with pytest.raises(ValueError, match="nothing was committed: No space left on device"):
        engine.execute("INSERT INTO s SELECT 2")


def test_a_held_file_after_the_commit_went_in_isnt_said_to_have_committed_nothing(engine, monkeypatch):
    real = deltalake.write_deltalake

    def committed_then_held(*a, **kw):
        real(*a, **kw)  # the commit goes in; then a file is held (a checkpoint, say)
        raise OSError(HELD)
    monkeypatch.setattr(deltalake, "write_deltalake", committed_then_held)
    with pytest.raises(ValueError) as caught:
        engine.execute("INSERT INTO s SELECT 2")
    assert "stopped because" not in str(caught.value)


# ---- a pipeline run's write to a table on this PC (meta.store.LocalStore)

def _store_held(monkeypatch, then=None):
    from fabric_desktop.meta import store as store_mod
    real = store_mod.write_deltalake

    def write(*a, **kw):
        if then is not None:
            then(real, *a, **kw)
        raise OSError(HELD)
    monkeypatch.setattr(store_mod, "write_deltalake", write)


def test_a_run_writing_a_local_table_with_a_held_file_says_so(tmp_path, monkeypatch):
    import polars as pl

    from fabric_desktop.meta.store import LocalStore
    store = LocalStore(tmp_path)
    store.write("LH_Bronze", "dbo", "orders", pl.DataFrame({"id": [1]}))
    _store_held(monkeypatch)
    monkeypatch.setattr(file_locks, "holders", lambda p: ["Microsoft OneDrive"])
    for write in (lambda: store.write("LH_Bronze", "dbo", "orders", pl.DataFrame({"id": [2]}), mode="append"),
                  lambda: store.write_stream("LH_Bronze", "dbo", "orders",
                                             pl.DataFrame({"id": [3]}).to_arrow().to_reader(), read_version=0)):
        with pytest.raises(OSError) as caught:
            write()
        assert str(caught.value) == (
            "Writing dbo.orders stopped because 00000000000000000001.json, one of the table's files, is open in "
            "Microsoft OneDrive. Nothing was committed. Close it there, or wait for it to finish, and run it again.")
        assert "os error 32" in str(caught.value.__cause__)  # delta-rs's own words kept for the log
    assert store.read("LH_Bronze", "dbo", "orders")["id"].to_list() == [1]


def test_a_run_whose_commit_went_in_before_a_file_was_held_keeps_delta_rs_words(tmp_path, monkeypatch):
    import polars as pl

    from fabric_desktop.meta.store import LocalStore
    store = LocalStore(tmp_path)
    store.write("LH_Bronze", "dbo", "orders", pl.DataFrame({"id": [1]}))
    _store_held(monkeypatch, then=lambda real, *a, **kw: real(*a, **kw))
    with pytest.raises(OSError, match=r"os error 32\)$"):
        store.write("LH_Bronze", "dbo", "orders", pl.DataFrame({"id": [2]}), mode="append")
