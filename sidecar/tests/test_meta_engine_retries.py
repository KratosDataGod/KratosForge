"""EA-028: the engine's two retry loops, pinned before they moved onto retrying.retry_call."""

from __future__ import annotations

import pytest
from deltalake.exceptions import CommitFailedError

from fabric_desktop.meta import engine as engine_mod
from fabric_desktop.meta.engine import AFTER_COMMIT_ATTEMPTS, WRITE_ATTEMPTS, Engine, RunResult


@pytest.fixture
def waits(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("fabric_desktop.meta.engine.time.sleep", slept.append)
    return slept


class _Writer:
    def __init__(self, lose: int, error: type[BaseException] = CommitFailedError, committed: bool = True):
        self.lose, self.error, self.committed, self.tries, self.advanced = lose, error, committed, 0, 0

    def _write_once(self, e, df, res, backfill):
        self.tries += 1
        if self.tries <= self.lose:
            raise self.error("another writer committed first")
        return self.committed

    def _advance_watermark(self, e, df, backfill, res):
        self.advanced += 1


def test_a_lost_commit_race_is_written_again_with_a_growing_wait(waits):
    assert WRITE_ATTEMPTS == 3
    w = _Writer(lose=2)
    Engine._write(w, None, None, None, None)
    assert (w.tries, w.advanced, waits) == (3, 1, [0.5, 1.0])
    w = _Writer(lose=0, committed=False)
    Engine._write(w, None, None, None, None)
    assert (w.tries, w.advanced) == (1, 0)  # nothing committed: the watermark stays


def test_the_last_lost_race_and_any_other_error_are_raised(waits):
    w = _Writer(lose=3)
    with pytest.raises(CommitFailedError):
        Engine._write(w, None, None, None, None)
    assert (w.tries, w.advanced, waits) == (3, 0, [0.5, 1.0])
    w = _Writer(lose=1, error=PermissionError)
    with pytest.raises(PermissionError):
        Engine._write(w, None, None, None, None)
    assert (w.tries, waits) == (1, [0.5, 1.0])


class _Store:
    def __init__(self, committed_before_failing: bool = False):
        self.committed_before_failing, self.checks = committed_before_failing, []

    def version(self, *where):
        return 7

    def has_commit(self, *args):
        self.checks.append(args)
        return self.committed_before_failing


class _Recorder:
    def __init__(self, store: _Store):
        self.store, self.run_id = store, "run1"


def _failing(times: int, writes: list):
    def write(metadata):
        writes.append(metadata)
        if len(writes) <= times:
            raise OSError("lost response")
    return write


def test_an_append_after_the_commit_is_tried_three_times_then_noted(waits):
    assert AFTER_COMMIT_ATTEMPTS == 3
    store, writes, res = _Store(), [], RunResult("run1", "E", "ok", message="done")
    assert Engine._after_commit(_Recorder(store), res, "the run log", ("lh", "meta", "runs"), _failing(9, writes)) is False
    assert len(writes) == 3 and waits == [0.2, 0.4]
    assert len({w[engine_mod.APPEND_KEY] for w in writes}) == 1  # one token names the one append
    assert len(store.checks) == 2 and all(c[-1] == 7 for c in store.checks)  # looked before each retry
    assert res.message == "done; couldn't save the run log (OSError: lost response)"
    assert (res.steps[-1].name, res.steps[-1].detail) == ("record", "couldn't save the run log (OSError: lost response)")


def test_an_append_that_committed_before_failing_is_not_written_twice(waits):
    store, writes, res = _Store(committed_before_failing=True), [], RunResult("run1", "E", "ok")
    assert Engine._after_commit(_Recorder(store), res, "the run log", ("lh", "meta", "runs"), _failing(1, writes)) is True
    assert len(writes) == 1 and waits == [0.2] and res.message == ""
    store, writes = _Store(), []
    assert Engine._after_commit(_Recorder(store), res, "the run log", ("lh", "meta", "runs"), _failing(0, writes)) is True
    assert len(writes) == 1 and store.checks == []
