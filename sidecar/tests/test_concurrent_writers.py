"""Writers at once on one local table: one wins cleanly, the other is told plainly, nothing is damaged.

Measured with processes on one PC (the app, scheduled runs and the agent): two runs of one table
take turns under its lease, and runs appending to the run log at once all land. Two PCs writing a
project in a synced folder can't be made to take turns (each PC's leases and commits reach the
other only when the folder syncs), so the commit the sync client sets aside is reported instead.
"""

import shutil
import threading
from contextlib import contextmanager

import polars as pl
from test_large_tables import DAY1, DAY2, Setup, rows
from test_write_failures import ids

from fabric_desktop.leases import Leases, LocalBackend, table_key
from fabric_desktop.meta import store as store_mod
from fabric_desktop.meta.store import META_SCHEMAS, LocalStore, sync_conflicts


def locked(s: Setup, owner: str, wait: float):
    """The table lock meta_service gives a run on this PC."""
    leases = Leases(LocalBackend(s.store.root / "_leases"), owner=owner, ttl=60, wait=wait)

    @contextmanager
    def lock(e):
        with leases.hold(table_key("LH_Bronze", e.schema, e.table)):
            yield
    return lock


def test_a_run_of_a_table_another_writer_holds_writes_nothing_and_says_who(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    s.source(rows([10, 11], DAY2))
    other = Leases(LocalBackend(s.store.root / "_leases"), "the agent", ttl=60)
    etag = other.try_acquire(table_key("LH_Bronze", "dbo", "events"))
    engine = s.engine(DAY2)
    engine.table_lock = locked(s, "KratosForge", wait=0.3)
    res = engine.run("bronze.events")
    assert res.status == "failed" and res.retryable and res.rows_written == 0
    assert res.message.startswith("LH_Bronze/dbo.events is being written by the agent on ")
    assert res.message.endswith("so nothing was written. Run it again once that write has finished")
    assert ids(s) == [0, 1, 2]
    other.backend.delete(table_key("LH_Bronze", "dbo", "events"), etag)  # the agent finishes
    again = s.engine(DAY2)
    again.table_lock = locked(s, "KratosForge", wait=0.3)
    assert again.run("bronze.events").status == "ok"
    assert ids(s) == [0, 1, 2, 10, 11]


def test_two_runs_at_once_take_turns_and_each_row_is_written_once(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    s.source(rows([10, 11, 12], DAY2))
    results = []

    def run(owner):
        engine = s.engine(DAY2)
        engine.table_lock = locked(s, owner, wait=60)
        results.append(engine.run("bronze.events"))
    threads = [threading.Thread(target=run, args=(f"run {i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted((r.status, r.rows_written) for r in results) == [("ok", 0), ("ok", 3)]
    assert ids(s) == [0, 1, 2, 10, 11, 12]


def test_appends_to_the_run_log_at_once_all_land(tmp_path):
    store = LocalStore(tmp_path, "dev")

    def row(run_id):
        return {c: None for c in META_SCHEMAS["run_log"]} | {"run_id": run_id, "entity": "e", "status": "ok"}
    store.meta_append("run_log", [row("seed")])
    errors = []

    def append(who):
        for i in range(15):
            try:
                store.meta_append("run_log", [row(f"{who}-{i}")])
            except Exception as exc:  # noqa: BLE001 - counted below
                errors.append(exc)
    threads = [threading.Thread(target=append, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    log = store.meta("run_log")
    assert log.height == 61 and log["run_id"].n_unique() == 61


def test_an_append_may_move_past_many_other_commits(tmp_path, monkeypatch):
    seen = []
    real = store_mod.write_deltalake

    def spy(*a, **kw):
        seen.append((kw.get("mode"), kw["commit_properties"]))
        return real(*a, **kw)
    monkeypatch.setattr(store_mod, "write_deltalake", spy)
    store = LocalStore(tmp_path, "dev")
    store.write("LH_Bronze", "dbo", "t", pl.DataFrame({"id": [1]}))
    store.write("LH_Bronze", "dbo", "t", pl.DataFrame({"id": [2]}), mode="append")
    assert seen[0] == ("overwrite", None)  # an overwrite still fails on any commit made since it read
    assert seen[1][0] == "append" and seen[1][1].max_commit_retries == store_mod.APPEND_COMMIT_RETRIES


def test_only_files_delta_doesnt_name_are_taken_for_set_aside_commits(tmp_path):
    v = "00000000000000000005"
    delta = [f"{v}.json", f"{v}.crc", f"{v}.checkpoint.parquet", f"{v}.checkpoint.0000000001.0000000002.parquet",
             f"{v}.checkpoint.80a083e8-7026-4e79-81be-64bd76c43a11.parquet", f"{v}.00000000000000000009.compacted.json",
             f"{v}.json#1", "_last_checkpoint", "_commit_1f2e.json.tmp"]
    synced = [f"{v}-PC2.json", f"{v} (Ann's conflicted copy 2026-11-07).json", f"{v}(1).json"]
    for name in delta + synced:
        (tmp_path / name).write_text("{}")
    (tmp_path / "_sidecars").mkdir()
    assert sync_conflicts(tmp_path) == [(5, name) for name in sorted(synced)]
    assert sync_conflicts(tmp_path / "missing") == []


def test_a_commit_a_sync_client_set_aside_is_reported_until_its_file_is_deleted(tmp_path):
    s = Setup(tmp_path, True)
    s.source(rows(range(3), DAY1), mode="overwrite")
    s.run(day=DAY1)
    log = tmp_path / "project" / ".local" / "dev" / "LH_Bronze" / "Tables" / "dbo" / "events" / "_delta_log"
    newest = max(log.glob("*.json"))
    # Another PC wrote the same version at once; the sync kept this PC's and set the other's aside.
    aside = log / f"{newest.stem}-PC2.json"
    shutil.copy(newest, aside)
    s.source(rows([10], DAY2))
    res = s.run(day=DAY2)
    assert "another PC's write to this table was lost" in res.message and aside.name in res.message
    assert "Run this project on one PC only" in res.message
    assert any(step.name == "sync" for step in res.steps)
    assert ids(s) == [0, 1, 2, 10]  # the run itself goes ahead
    aside.unlink()
    assert "another PC" not in s.run(day=DAY2).message
