"""Fault injection on the workload and incident stores: a damaged or cut-short file, a full disk, a
write that fails partway, two writers at once, and a crash between writing a copy and renaming it
into place. Each must recover cleanly or fail loudly with a clear message, never lose or silently
change a record."""

import json
import multiprocessing
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from fabric_desktop import leases, sqlitefile
from fabric_desktop.leases import LocalBackend
from fabric_desktop.workloads.incidents import KEY, IncidentLog
from fabric_desktop.workloads.notify import Outbox
from fabric_desktop.workloads.store import ManagementStore

SIGNAL = {"kind": "run_failed", "workload": "silver.orders", "title": "silver.orders failed",
          "evidence_id": "r1", "detail": "boom"}


def _signal(n: int) -> dict:
    return SIGNAL | {"workload": f"t{n}", "title": f"t{n} failed", "evidence_id": f"r{n}"}


def _full_disk(monkeypatch):
    """Every connection the stores open can't grow the file past its size now: SQLite's own
    "database or disk is full", as on a full disk."""
    real = sqlitefile.sqlite3.connect

    def connect(path, *a, **k):
        db = real(path, *a, **k)
        pages = db.execute("pragma page_count").fetchone()[0]
        db.execute(f"pragma max_page_count = {max(pages, 1)}")
        return db
    monkeypatch.setattr(sqlitefile.sqlite3, "connect", connect)


# ---- workloads.sqlite (who owns what, history, actions, audit lines not yet sent)

def test_a_damaged_workloads_file_is_refused_with_a_clear_message_and_kept_as_it_is(tmp_path):
    path = tmp_path / "workloads.sqlite"
    store = ManagementStore(path)
    store.assign("nb:a", {"team": "Sales"}, by="chris")
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 3])  # cut short
    kept = path.read_bytes()
    with pytest.raises(ValueError, match="workloads file on this PC .* can't be read.*doesn't replace it"):
        ManagementStore(path)
    assert path.read_bytes() == kept and not list(tmp_path.glob("*.corrupt-*"))


def test_a_full_disk_fails_the_change_loudly_and_keeps_what_was_there(tmp_path, monkeypatch):
    store = ManagementStore(tmp_path / "workloads.sqlite")
    store.assign("nb:a", {"team": "Sales"}, by="chris")
    _full_disk(monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="full"):
        for n in range(500):  # enough history to need a new page
            store.assign("nb:a", {"technical_owner": f"owner-{n}@contoso.com" + "x" * 200}, by="chris")
    monkeypatch.undo()
    again = ManagementStore(tmp_path / "workloads.sqlite")
    assert again.assignments(["nb:a"])["nb:a"]["team"]["value"] == "Sales"
    # every change that was kept has its history line, and no line without its change
    owner = again.assignments(["nb:a"])["nb:a"].get("technical_owner", {}).get("value", "")
    hist = [h for h in again.history("nb:a", 1000) if h["field"] == "technical_owner"]
    assert (hist[0]["after"] if hist else "") == owner


def test_a_change_that_fails_partway_leaves_nothing_of_it(tmp_path):
    store = ManagementStore(tmp_path / "workloads.sqlite")
    store.assign("nb:a", {"team": "Sales"}, by="chris")

    class Torn(Exception):
        pass
    with pytest.raises(Torn):
        with store._tx() as db:  # one change of two fields, as assign() makes it; it stops after the first
            db.execute("INSERT OR REPLACE INTO assignment VALUES ('nb:a', 'team', 'Finance', 'c', 't')")
            db.execute("INSERT INTO history (wid, field, before, after, by, at) VALUES "
                       "('nb:a', 'team', 'Sales', 'Finance', 'c', 't')")
            db.execute("INSERT OR REPLACE INTO assignment VALUES ('nb:a', 'criticality', 'high', 'c', 't')")
            raise Torn("the PC lost power before the criticality's history line")
    got = store.assignments(["nb:a"])["nb:a"]
    assert got["team"]["value"] == "Sales" and "criticality" not in got
    assert [h["after"] for h in store.history("nb:a")] == ["Sales"]


def test_two_writers_at_once_keep_every_change_and_its_history(tmp_path):
    path = tmp_path / "workloads.sqlite"
    ManagementStore(path)
    errors = []

    def writer(n):
        try:
            mine = ManagementStore(path)  # its own connection, as a second app window or process has
            for i in range(25):
                mine.assign(f"nb:{n}", {"technical_owner": f"o{i}"}, by=f"w{n}")
                mine.start_action(f"nb:{n}", "run", by=f"w{n}")
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    store = ManagementStore(path)
    for n in range(4):
        assert len(store.history(f"nb:{n}", 1000)) == 25 and len(store.actions(f"nb:{n}", 1000)) == 25
        assert store.assignments([f"nb:{n}"])[f"nb:{n}"]["technical_owner"]["value"] == "o24"


def _kill_mid_transaction(path: str) -> None:  # runs in a child process that dies holding a write
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("begin immediate")
    db.execute("insert into assignment values ('nb:a', 'team', 'Lost', 'x', 't')")
    os._exit(1)  # no commit, no close: as when the process is killed


def test_a_process_killed_mid_write_leaves_the_file_as_it_was_and_writable(tmp_path):
    path = tmp_path / "workloads.sqlite"
    ManagementStore(path).assign("nb:a", {"team": "Sales"}, by="chris")
    p = multiprocessing.get_context("spawn").Process(target=_kill_mid_transaction, args=(str(path),))
    p.start()
    p.join(30)
    assert p.exitcode == 1
    store = ManagementStore(path)  # opens: the unfinished write is rolled back
    assert store.assignments(["nb:a"])["nb:a"]["team"]["value"] == "Sales"
    store.assign("nb:a", {"team": "Finance"}, by="chris")
    assert store.assignments(["nb:a"])["nb:a"]["team"]["value"] == "Finance"


# ---- the incident list (one JSON document per environment, changed by conditional writes)

@pytest.fixture
def log(tmp_path):
    return IncidentLog(LocalBackend(tmp_path / "control"), "dev")


def _file(log) -> Path:
    return log.backend._path(KEY)


@pytest.mark.parametrize("damage", [b"", b'{"incidents": [{"id": "inc-1", "st', b"[1, 2]", b'{"incidents": 3}'])
def test_a_damaged_incident_list_is_refused_with_a_clear_message_and_never_written_over(log, damage):
    log.sync([SIGNAL], evaluated=True, by="test")
    _file(log).write_bytes(damage)
    with pytest.raises(ValueError, match="Dev incident list .* can't be read.*left as it is"):
        log.list()
    with pytest.raises(ValueError, match="can't be read"):
        log.sync([SIGNAL], evaluated=True, by="test")
    assert _file(log).read_bytes() == damage  # an empty list was written over a cut-short one before


def test_a_full_disk_while_writing_the_incident_list_keeps_the_old_one_and_no_copy(log, monkeypatch):
    log.sync([SIGNAL], evaluated=True, by="test")
    before = _file(log).read_bytes()
    real = Path.write_bytes

    def full(self, data):
        if self.suffix == ".tmp":
            real(self, data[: len(data) // 2])  # part of it reached the disk
            raise OSError(28, "No space left on device")
        return real(self, data)
    monkeypatch.setattr(Path, "write_bytes", full)
    with pytest.raises(OSError, match="No space left"):
        log.sync([SIGNAL, _signal(2)], evaluated=True, by="test")
    monkeypatch.undo()
    assert _file(log).read_bytes() == before
    assert not list(_file(log).parent.glob("*.tmp"))  # the half-written copy was left behind before
    assert [c["change"] for c in log.sync([SIGNAL, _signal(2)], evaluated=True, by="test")] == ["opened"]


def test_a_crash_between_writing_the_copy_and_renaming_it_keeps_the_old_list(log, monkeypatch):
    log.sync([SIGNAL], evaluated=True, by="test")
    before = _file(log).read_bytes()

    def crash(src, dst):
        raise KeyboardInterrupt  # the process stops before the rename
    monkeypatch.setattr(leases.os, "replace", crash)
    with pytest.raises(KeyboardInterrupt):
        log.sync([SIGNAL, _signal(2)], evaluated=True, by="test")
    monkeypatch.undo()
    assert _file(log).read_bytes() == before and not list(_file(log).parent.glob("*.tmp"))
    assert json.loads(before)["incidents"][0]["title"] == "silver.orders failed"
    assert [c["change"] for c in log.sync([SIGNAL, _signal(2)], evaluated=True, by="test")] == ["opened"]


def test_two_writers_at_once_each_land_every_change(tmp_path):
    backend_dir = tmp_path / "control"
    errors, opened = [], []

    def writer(n):
        try:
            mine = IncidentLog(LocalBackend(backend_dir), "dev")  # its own backend, as another app or agent
            for i in range(5):
                opened.extend(c["incident"]["title"] for c in mine.sync([_signal(n * 100 + i)], evaluated={"run_failed"}, by=f"w{n}")
                              if c["change"] == "opened")
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    titles = {i["title"] for i in IncidentLog(LocalBackend(backend_dir), "dev").list()}
    assert len(opened) == len(set(opened)) and set(opened) <= titles


# ---- the Teams outbox (messages waiting for Teams)

def test_a_damaged_outbox_is_set_aside_and_new_messages_still_wait(tmp_path, caplog):
    path = tmp_path / "alerts.sqlite3"
    Outbox(path).add("t", ["l"], "Teams down")
    path.write_bytes(b"not a database" * 100)
    with caplog.at_level("WARNING"):
        box = Outbox(path)
    assert box.set_aside is not None and box.set_aside.exists()  # the old one is kept beside it
    assert box.pending() == []
    box.add("new", ["line"], "Teams down")
    assert [m["title"] for m in box.pending()] == ["new"]


def test_a_full_disk_when_a_message_waits_fails_loudly_and_keeps_the_others(tmp_path, monkeypatch):
    box = Outbox(tmp_path / "alerts.sqlite3")
    box.add("first", ["l"], "Teams down")
    _full_disk(monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="full"):
        for n in range(500):
            box.add(f"m{n}", ["x" * 2000], "Teams down")
    monkeypatch.undo()
    assert Outbox(tmp_path / "alerts.sqlite3").pending()[0]["title"] == "first"
