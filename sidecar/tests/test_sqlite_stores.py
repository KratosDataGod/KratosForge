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
