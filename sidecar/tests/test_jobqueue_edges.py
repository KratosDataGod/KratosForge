"""The agent's job queue: an older queue file upgraded while another process upgrades it too, a damaged
file moved aside with its write-ahead log, and the list filtered by state."""

import sqlite3

import pytest

from fabric_desktop import jobqueue
from fabric_desktop.jobqueue import JobQueue


class _Racing:
    """A connection where another process adds the same column a moment before this one does."""

    def __init__(self, db, error):
        object.__setattr__(self, "_db", db)
        object.__setattr__(self, "_error", error)

    def execute(self, sql, *args):
        if sql.startswith("alter table"):
            if "duplicate" in self._error:
                self._db.execute(sql, *args)  # the other process's change
            raise sqlite3.OperationalError(self._error)
        return self._db.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._db, name)

    def __setattr__(self, name, value):
        setattr(self._db, name, value)


def _old_queue_file(path, columns=("jobs.claim_id", "jobs.requeued_by", "schedule_state.wall")):
    JobQueue(path)
    with sqlite3.connect(path) as db:
        for column in columns:
            table, name = column.split(".")
            db.execute(f"alter table {table} drop column {name}")
    db.close()


def _racing(monkeypatch, error):
    real = sqlite3.connect
    monkeypatch.setattr(jobqueue.sqlite3, "connect", lambda *a, **k: _Racing(real(*a, **k), error))


def test_columns_another_process_added_first_are_taken_as_added(tmp_path, monkeypatch):
    path = tmp_path / "queue.db"
    _old_queue_file(path)
    _racing(monkeypatch, "duplicate column name: claim_id")
    q = JobQueue(path)
    assert q.set_aside is None
    monkeypatch.undo()
    with sqlite3.connect(path) as db:
        jobs = {r[1] for r in db.execute("pragma table_info(jobs)")}
        state = {r[1] for r in db.execute("pragma table_info(schedule_state)")}
    db.close()
    assert {"claim_id", "requeued_by"} <= jobs and "wall" in state


@pytest.mark.parametrize("columns", [("jobs.claim_id",), ("schedule_state.wall",)])
def test_any_other_failure_adding_a_column_is_raised_and_the_file_kept(tmp_path, monkeypatch, columns):
    path = tmp_path / "queue.db"
    _old_queue_file(path, columns)
    _racing(monkeypatch, "disk I/O error")
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        JobQueue(path)
    assert path.exists() and not list(tmp_path.glob("queue.db.corrupt*"))


def test_a_damaged_queue_moves_aside_with_its_write_ahead_log(tmp_path, monkeypatch):
    path = tmp_path / "queue.db"
    path.write_bytes(b"damaged")
    (tmp_path / "queue.db-wal").write_bytes(b"stale log")
    (tmp_path / "queue.db-shm").write_bytes(b"stale index")
    real, opens = JobQueue._open, []

    def damaged_once(self):
        opens.append(1)
        if len(opens) == 1:  # the damage shows before SQLite reads (or clears) the log
            raise sqlite3.DatabaseError("database disk image is malformed")
        real(self)

    monkeypatch.setattr(JobQueue, "_open", damaged_once)
    q = JobQueue(path)
    assert q.set_aside is not None and q.set_aside.read_bytes() == b"damaged"
    assert q.set_aside.with_name(q.set_aside.name + "-wal").read_bytes() == b"stale log"
    assert q.set_aside.with_name(q.set_aside.name + "-shm").read_bytes() == b"stale index"
    assert q.list() == []


def test_the_list_can_be_filtered_by_state(tmp_path):
    q = JobQueue(tmp_path / "queue.db")
    first = q.enqueue("run", {"n": 1})
    q.enqueue("run", {"n": 2})
    q.cancel(first["id"], "not needed")
    assert [j["payload"]["n"] for j in q.list(state="queued")] == [2]
    assert [j["id"] for j in q.list(state="cancelled")] == [first["id"]]
