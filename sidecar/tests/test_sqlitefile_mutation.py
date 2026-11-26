"""sqlitefile's edges, from the mutation run (docs/quality/mutation-testing.md): the defaults of
connect and transaction, a connection whose pragma fails, busy_message's two conditions, a store's
folders made with their parents, -wal and -shm moved with a damaged file, and too_long_named letting
every failure it doesn't explain through as it was."""

from __future__ import annotations

import sqlite3

import pytest

from fabric_desktop import sqlitefile, support_bundle


def _recording(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    real = sqlite3.connect

    def connect(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(sqlitefile.sqlite3, "connect", connect)
    return calls


def test_connect_and_transaction_wait_30_seconds_by_default(tmp_path, monkeypatch):
    calls = _recording(monkeypatch)
    with sqlitefile.connect(tmp_path / "a.sqlite"):
        pass
    with sqlitefile.transaction(tmp_path / "a.sqlite"):
        pass
    assert [c["timeout"] for c in calls] == [30, 30]


def test_transaction_gives_tuples_unless_asked_for_rows(tmp_path):
    with sqlitefile.transaction(tmp_path / "a.sqlite") as db:
        assert type(db.execute("select 1 as a").fetchone()) is tuple
    with sqlitefile.transaction(tmp_path / "a.sqlite", rows=True) as db:
        assert db.execute("select 1 as a").fetchone()["a"] == 1


class _NoPragma:
    closed = False

    def execute(self, sql):
        raise sqlite3.OperationalError("disk I/O error")

    def close(self):
        self.closed = True


@pytest.mark.parametrize("opener", [sqlitefile.connect, sqlitefile.transaction])
def test_a_connection_whose_pragma_fails_is_closed_and_the_failure_raised(opener, tmp_path, monkeypatch):
    fake = _NoPragma()
    monkeypatch.setattr(sqlitefile.sqlite3, "connect", lambda *a, **k: fake)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        with opener(tmp_path / "a.sqlite"):
            pytest.fail("the block ran on a connection without synchronous=full")
    assert fake.closed


def test_busy_message_needs_both_an_operational_error_and_the_locked_wording():
    assert sqlitefile.busy_message(sqlite3.OperationalError("database is locked")) is not None
    assert sqlitefile.busy_message(sqlite3.OperationalError("disk I/O error")) is None
    assert sqlitefile.busy_message(RuntimeError("database is locked")) is None


def test_open_store_makes_the_folders_above_the_file(tmp_path):
    path = tmp_path / "a" / "b" / "c" / "s.sqlite"

    def first_open():
        with sqlitefile.connect(path) as db:
            db.execute("create table if not exists t (x)")
    assert sqlitefile.open_store(path, first_open, then="starting empty") is None
    assert path.exists()


def test_set_aside_moves_the_wal_and_shm_with_the_file(tmp_path):
    path = tmp_path / "s.sqlite"
    for name, text in (("s.sqlite", "main"), ("s.sqlite-wal", "wal"), ("s.sqlite-shm", "shm")):
        (tmp_path / name).write_text(text)
    dest = sqlitefile.set_aside(path, "starting empty")
    assert not path.exists()
    assert dest.read_text() == "main"
    for suffix in ("-wal", "-shm"):
        assert not (tmp_path / f"s.sqlite{suffix}").exists()
        assert dest.with_name(dest.name + suffix).read_text() == suffix[1:]


def test_too_long_named_raises_any_other_sqlite_failure_as_it_was(tmp_path):
    original = sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError) as raised:
        with sqlitefile.too_long_named(tmp_path / "s.sqlite"):
            raise original
    assert raised.value is original


def test_too_long_named_raises_an_os_error_it_doesnt_explain_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    original = PermissionError(13, "Access is denied", str(tmp_path / "s.sqlite"))
    with pytest.raises(PermissionError) as raised:
        with sqlitefile.too_long_named(tmp_path / "s.sqlite"):
            raise original
    assert raised.value is original


def test_too_long_named_names_an_os_error_for_a_path_past_260(tmp_path, monkeypatch):
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    path = tmp_path / ("x" * 300) / "s.sqlite"
    original = FileNotFoundError(2, "The system cannot find the path specified", str(path))
    original.winerror = 3  # type: ignore[attr-defined]
    with pytest.raises(OSError) as raised:
        with sqlitefile.too_long_named(path):
            raise original
    assert raised.value is not original and raised.value.__cause__ is original
    assert "s.sqlite" in str(raised.value) and "long paths" in str(raised.value)


def test_too_long_named_raises_a_busy_file_past_260_as_it_was(tmp_path, monkeypatch):
    # Only "unable to open database file" is SQLite's answer for a path too long; a locked file isn't.
    monkeypatch.setattr(support_bundle, "long_paths_enabled", lambda: False)
    original = sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError) as raised:
        with sqlitefile.too_long_named(tmp_path / ("x" * 300) / "s.sqlite"):
            raise original
    assert raised.value is original


def test_busy_message_follows_an_explicit_cause_without_a_context():
    outer = RuntimeError("the change failed")
    outer.__cause__ = sqlite3.OperationalError("database is locked")
    assert outer.__context__ is None
    assert sqlitefile.busy_message(outer) is not None
