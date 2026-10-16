"""Incremental loads from files read only the files they haven't loaded (meta.loaded_files)."""

import pytest

from fabric_desktop.meta.store import OneLakeStore
from test_meta import DAY1, DAY2, ORDER_COLS, Repo, edit_entity, landing

DAY3 = DAY2.replace(day=DAY2.day + 1)


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "project")
    edit_entity(r.root, "bronze.orders", 'mode = "full"', 'mode = "incremental"')
    r.reload()
    first = r.engine().run("bronze.orders")
    assert first.status == "ok", first.message
    return r


def read_step(res) -> str:
    return next(s.detail for s in res.steps if s.name == "read")


def test_a_run_reads_only_files_it_has_not_loaded(repo):
    rows_before = repo.table("bronze.orders").height
    landing(repo.root, "landing/orders/orders_2026-10-02.csv", f"{ORDER_COLS}\n106,2,2026-10-02,30.00,,shipped,2026-10-02\n")
    res = repo.engine(DAY2).run("bronze.orders")
    assert res.status == "ok" and res.rows_read == 1
    assert read_step(res).startswith("1 new file of 2")
    assert repo.table("bronze.orders").height == rows_before + 1
    loaded = repo.dev.meta("loaded_files")
    assert sorted(loaded["path"].to_list()) == ["landing/orders/orders_2026-10-01.csv", "landing/orders/orders_2026-10-02.csv"]
    assert set(loaded["lakehouse"]) == {"LH_Bronze"}


def test_nothing_new_is_a_successful_run_that_writes_nothing(repo):
    versions = len(repo.dev.history("LH_Bronze", "dbo", "orders"))
    res = repo.engine(DAY2).run("bronze.orders")
    assert res.status == "ok" and res.rows_written == 0
    assert "no new files: all 1 file matching landing/orders/*.csv are loaded" in read_step(res)
    assert len(repo.dev.history("LH_Bronze", "dbo", "orders")) == versions
    assert repo.engine(DAY2).run("silver.orders").status == "ok"  # downstream carries on


def test_a_file_replaced_in_place_is_read_again(repo):
    path = repo.root / ".local/dev/LH_Bronze/Files/landing/orders/orders_2026-10-01.csv"
    path.write_text(path.read_text(encoding="utf-8") + "107,1,2026-10-01,5.00,,shipped,2026-10-01\n", encoding="utf-8")
    res = repo.engine(DAY2).run("bronze.orders")
    assert res.status == "ok" and read_step(res).startswith("1 new file of 1")
    assert 107 in repo.table("bronze.orders")["order_id"].to_list()


def test_a_full_backfill_reads_every_file_and_records_them(repo):
    landing(repo.root, "landing/orders/orders_2026-10-02.csv", f"{ORDER_COLS}\n106,2,2026-10-02,30.00,,shipped,2026-10-02\n")
    res = repo.engine(DAY2).run("bronze.orders", backfill="full")
    assert res.status == "ok" and read_step(res).startswith("2 files")
    assert "no new files" in read_step(repo.engine(DAY3).run("bronze.orders"))


def test_new_files_only_can_be_turned_off(repo):
    edit_entity(repo.root, "bronze.orders", 'files = "landing/orders/*.csv"',
                'files = "landing/orders/*.csv"\nnew_files_only = false')
    repo.reload()
    res = repo.engine(DAY2).run("bronze.orders")
    assert res.status == "ok" and read_step(res).startswith("1 file")


def test_full_loads_still_read_every_file(tmp_path):
    r = Repo(tmp_path / "p")
    assert r.engine().run("bronze.customers").status == "ok"
    res = r.engine(DAY2).run("bronze.customers")
    assert res.status == "ok" and read_step(res).startswith("1 file") and r.dev.meta("loaded_files").height == 0


def test_a_failed_run_records_no_files(repo, monkeypatch):
    landing(repo.root, "landing/orders/orders_2026-10-02.csv", f"{ORDER_COLS}\n106,2,2026-10-02,30.00,,shipped,2026-10-02\n")
    engine = repo.engine(DAY2)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(engine, "_write", broken)
    assert engine.run("bronze.orders").status == "failed"
    assert repo.dev.meta("loaded_files").height == 1  # only the first run's file
    res = repo.engine(DAY2).run("bronze.orders")
    assert read_step(res).startswith("1 new file of 2")


def test_if_the_list_cannot_be_recorded_the_table_is_still_written(repo, monkeypatch):
    landing(repo.root, "landing/orders/orders_2026-10-02.csv", f"{ORDER_COLS}\n106,2,2026-10-02,30.00,,shipped,2026-10-02\n")
    real = repo.dev.meta_append

    def no_manifest(table, rows):
        if table == "loaded_files":
            raise OSError("OneLake said no")
        return real(table, rows)
    monkeypatch.setattr(repo.dev, "meta_append", no_manifest)
    res = repo.engine(DAY2).run("bronze.orders")
    assert res.status == "ok" and "the next run reads them again" in res.steps[-1].detail
    assert 106 in repo.table("bronze.orders")["order_id"].to_list()


class FakeFiles:
    def __init__(self):
        self.downloaded = []

    def list(self, ws, lh, directory):
        assert directory == "Files/landing/orders"
        return [{"name": "a.csv", "path": "Files/landing/orders/a.csv", "is_dir": False, "size": 10, "modified": "m1"},
                {"name": "notes.txt", "path": "Files/landing/orders/notes.txt", "is_dir": False, "size": 1, "modified": "m"},
                {"name": "old", "path": "Files/landing/orders/old", "is_dir": True, "size": 0, "modified": ""}]

    def download(self, ws, lh, path, dest):
        self.downloaded.append(path)
        return dest


def test_onelake_lists_without_downloading_and_fetches_only_what_is_read(tmp_path):
    files = FakeFiles()
    store = OneLakeStore("test", "ws", {"LH_Bronze": "lh"}, lambda: "tok", tmp_path, files=files)
    listed = store.list_files("LH_Bronze", "landing/orders/*.csv")
    assert listed == [{"path": "landing/orders/a.csv", "size": 10, "modified": "m1"}] and files.downloaded == []
    assert store.fetch_files("LH_Bronze", listed) == [tmp_path / "test" / "LH_Bronze" / "Files" / "landing/orders/a.csv"]
    assert files.downloaded == ["Files/landing/orders/a.csv"]


def test_read_settings_are_not_logic_changes(repo):
    before = repo.p.fingerprint(repo.p.entities["bronze.orders"])
    edit_entity(repo.root, "bronze.orders", 'mode = "incremental"', 'mode = "incremental"\nmemory_guard = false')
    edit_entity(repo.root, "bronze.orders", 'files = "landing/orders/*.csv"',
                'files = "landing/orders/*.csv"\nnew_files_only = false')
    repo.reload()
    assert repo.p.fingerprint(repo.p.entities["bronze.orders"]) == before
