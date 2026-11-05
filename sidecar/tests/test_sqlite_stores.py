"""SQLite stores open through sqlitefile.open_store: a file SQLite can't read is moved aside, with its
bytes kept, and the store starts empty (or refuses, where its comment says so); a busy file is never moved."""

import sqlite3

import pytest

from fabric_desktop import sqlitefile
from fabric_desktop.integrations.store import IntegrationStore


def test_open_store_refuses_with_its_message_and_moves_nothing(tmp_path):
    path = tmp_path / "s.sqlite"
    path.write_bytes(b"not a database" * 100)

    def first_open():
        with sqlitefile.connect(path) as db:
            sqlitefile.check(db)

    with pytest.raises(ValueError, match="s.sqlite is damaged"):
        sqlitefile.open_store(path, first_open, refuse="{path} is damaged: {reason}")
    assert path.read_bytes() == b"not a database" * 100 and not list(tmp_path.glob("*.corrupt-*"))
    with pytest.raises(TypeError):
        sqlitefile.open_store(path, first_open)


def test_an_unreadable_integrations_file_is_set_aside_and_integrations_start_off(tmp_path, caplog):
    path = tmp_path / "integrations.sqlite"
    path.write_bytes(b"garbage" * 1000)
    store = IntegrationStore(path)
    assert store.set_aside is not None and store.set_aside.name.startswith("integrations.sqlite.corrupt-")
    assert store.set_aside.read_bytes() == b"garbage" * 1000  # kept for a person to look at
    assert store.settings()["enabled"] is False and store.clients() == []
    assert "connect again" in caplog.text
    assert IntegrationStore(path).set_aside is None  # the new file is fine


def test_an_integrations_file_damaged_inside_is_set_aside_too(tmp_path):
    path = tmp_path / "integrations.sqlite"
    store = IntegrationStore(path)
    with store._tx() as db:
        db.executemany("insert into settings (key, value) values (?, ?)", [(f"k{i}", '"' + "x" * 500 + '"') for i in range(200)])
    with sqlitefile.connect(path) as db:  # closed after, so Windows can move the file aside
        db.execute("pragma wal_checkpoint(truncate)")
    data = bytearray(path.read_bytes())
    data[8192:12288] = b"\xff" * 4096  # a page in the middle, past the header
    path.write_bytes(bytes(data))
    assert IntegrationStore(path).set_aside is not None


def test_a_healthy_integrations_file_keeps_its_rows(tmp_path):
    path = tmp_path / "integrations.sqlite"
    IntegrationStore(path).set_setting("enabled", True)
    store = IntegrationStore(path)
    assert store.set_aside is None and store.settings()["enabled"] is True


def test_a_busy_integrations_file_is_not_set_aside(tmp_path, monkeypatch):
    path = tmp_path / "integrations.sqlite"
    IntegrationStore(path)
    monkeypatch.setattr(IntegrationStore, "_first_open",
                        lambda self: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    with pytest.raises(sqlite3.OperationalError):
        IntegrationStore(path)
    assert path.exists() and not list(tmp_path.glob("*.corrupt-*"))


def test_an_unreadable_workloads_file_is_refused_and_kept(tmp_path):
    from fabric_desktop.workloads.store import ManagementStore
    path = tmp_path / "workloads.sqlite"
    path.write_bytes(b"garbage" * 1000)
    with pytest.raises(ValueError, match="holds who owns each workload"):
        ManagementStore(path)
    assert path.read_bytes() == b"garbage" * 1000 and not list(tmp_path.glob("*.corrupt-*"))


def test_a_healthy_workloads_file_keeps_its_owners(tmp_path):
    from fabric_desktop.workloads.store import ManagementStore
    path = tmp_path / "workloads.sqlite"
    ManagementStore(path).assign("w1", {"criticality": "high"}, by="me")
    assert ManagementStore(path).assignments()["w1"]["criticality"]["value"] == "high"


def test_an_unreadable_outbox_and_ledger_are_set_aside_and_start_empty(tmp_path, caplog):
    from fabric_desktop.workloads.notify import Outbox
    from fabric_desktop.workloads.platform_notify import Ledger
    box = tmp_path / "platform_outbox.sqlite"  # one file for both, as platform_notify_service opens them
    box.write_bytes(b"garbage" * 1000)
    outbox = Outbox(box)
    assert outbox.set_aside is not None and outbox.set_aside.read_bytes() == b"garbage" * 1000
    assert outbox.pending() == [] and "new ones are sent as usual" in caplog.text
    ledger = Ledger(box)
    assert ledger.set_aside is None and ledger.get("k") is None  # the file Outbox made is fine
    alone = tmp_path / "ledger.sqlite"
    alone.write_bytes(b"garbage" * 1000)
    assert Ledger(alone).set_aside is not None and "sent once more" in caplog.text


def test_an_unreadable_local_platform_file_is_refused_and_kept_as_it_is(tmp_path):
    from fabric_desktop.central.client import LocalPlatform
    path = tmp_path / "platform.sqlite"
    path.write_bytes(b"garbage" * 1000)
    with pytest.raises(ValueError, match="holds the keys and rules"):
        LocalPlatform(path, user=lambda: "me@contoso.com")
    assert path.read_bytes() == b"garbage" * 1000 and not list(tmp_path.glob("*.corrupt-*"))


def test_a_healthy_local_platform_file_keeps_its_admin(tmp_path):
    from fabric_desktop.central import core
    from fabric_desktop.central.client import LocalPlatform
    path = tmp_path / "platform.sqlite"
    LocalPlatform(path, user=lambda: "me@contoso.com")
    platform = LocalPlatform(path, user=lambda: "someone@contoso.com")
    with platform._db() as db:
        assert [a["user_name"] for a in core.list_admins(db)] == ["me@contoso.com"]


# sqlitefile.transaction: one helper for a group of changes, used by the workloads file, the outbox and the ledger.

def test_transaction_commits_rolls_back_and_always_closes(tmp_path, monkeypatch):
    path = tmp_path / "t.sqlite"
    opened = []
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: opened.append(real(*a, **k)) or opened[-1])
    with sqlitefile.transaction(path) as db:
        db.execute("CREATE TABLE t (v TEXT)")
        db.execute("INSERT INTO t VALUES ('kept')")
    with pytest.raises(RuntimeError), sqlitefile.transaction(path) as db:
        db.execute("INSERT INTO t VALUES ('dropped')")
        raise RuntimeError("boom")
    with sqlitefile.transaction(path, rows=True) as db:
        rows = db.execute("SELECT v FROM t").fetchall()
    assert [r["v"] for r in rows] == ["kept"]
    for db in opened:  # each connection was closed, success or not
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")


def test_a_workloads_change_that_fails_part_way_leaves_nothing(tmp_path):
    from fabric_desktop.workloads.store import ManagementStore
    store = ManagementStore(tmp_path / "workloads.sqlite")
    store.assign("w1", {"criticality": "high"}, by="me")
    with pytest.raises(RuntimeError), store._tx() as db:
        db.execute("DELETE FROM assignment")
        raise RuntimeError("boom")
    assert store.assignments()["w1"]["criticality"]["value"] == "high"
    assert not store._lock.locked()  # the lock is let go too


def test_a_healthy_outbox_and_ledger_keep_their_rows(tmp_path):
    from fabric_desktop.workloads.notify import Outbox
    from fabric_desktop.workloads.platform_notify import Ledger
    box = tmp_path / "outbox.sqlite"
    Outbox(box).add("New: orders failed", ["boom"], "Teams didn't answer")
    again = Outbox(box)
    assert again.set_aside is None and [r["title"] for r in again.pending()] == ["New: orders failed"]
    assert Ledger(box).set_aside is None
