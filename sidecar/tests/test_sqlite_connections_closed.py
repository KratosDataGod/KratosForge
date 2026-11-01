"""QA-022: the Teams outbox and the platform ledger close every SQLite connection they open.

``with sqlite3.connect(...)`` commits or rolls back but never closes; on Windows the open handle
keeps the file locked."""

import gc
import sqlite3
import warnings

import pytest

from fabric_desktop.workloads import notify, platform_notify


class Tracked(sqlite3.Connection):
    opened: list["Tracked"] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False
        Tracked.opened.append(self)

    def close(self):
        self.closed = True
        super().close()


@pytest.fixture
def tracked(monkeypatch):
    real = sqlite3.connect
    Tracked.opened = []
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real(*a, factory=Tracked, **k))
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        yield Tracked.opened
        gc.collect()
    Tracked.opened = []


def assert_all_closed(opened):
    assert opened, "expected the store to open connections"
    still_open = [i for i, c in enumerate(opened) if not c.closed]
    assert not still_open, f"{len(still_open)} of {len(opened)} connections left open"


def test_outbox_closes_every_connection(tmp_path, tracked):
    box = notify.Outbox(tmp_path / "o.sqlite")
    box.add("t", ["a"], "boom")
    [item] = box.pending()
    box.failed(item["id"], "again")
    box.done(item["id"])
    assert box.pending() == []
    assert_all_closed(tracked)


def test_ledger_closes_every_connection(tmp_path, tracked):
    ledger = platform_notify.Ledger(tmp_path / "o.sqlite")
    ledger.mark("k", "request.waiting", True)
    assert ledger.get("k") == ("request.waiting", True)
    assert [r["key"] for r in ledger.open_keys()] == ["k"]
    ledger.forget("k")
    assert ledger.get("k") is None
    assert_all_closed(tracked)


def test_a_failed_write_rolls_back_and_still_closes(tmp_path, tracked):
    box = notify.Outbox(tmp_path / "o.sqlite")
    with pytest.raises(RuntimeError):
        with box._db() as db:
            db.execute("INSERT INTO outbox (created, title, lines) VALUES (0, 't', '[]')")
            raise RuntimeError("stop")
    assert box.pending() == []
    assert_all_closed(tracked)


def test_job_queue_closes_every_connection_from_any_thread(tmp_path, tracked):
    import threading

    from fabric_desktop.jobqueue import JobQueue
    q = JobQueue(tmp_path / "queue.sqlite3", backoff_seconds=0)
    done = []

    def work(n):  # the agent and its mailbox read and change the queue from several threads
        job = q.enqueue("meta", {"env": "dev", "n": n})
        claimed = q.claim(f"etl-{n}", 60)
        assert claimed and q.succeed(claimed["id"], f"etl-{n}")
        assert q.get(job["id"]) and q.list() and q.counts("dev") and q.for_env("dev")
        q.set_schedule_next(f"s{n}", 1.0)
        assert q.schedule_next(f"s{n}") == 1.0
        done.append(n)
    threads = [threading.Thread(target=work, args=(n,)) for n in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(done) == [0, 1, 2]
    assert_all_closed(tracked)
