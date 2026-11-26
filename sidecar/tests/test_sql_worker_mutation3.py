"""sql_worker.py: the mutants that test_sql_worker_mutation.py and the module's other tests leave alive. Progress
is said once the interval has passed and not before, a statement event without a number means none is running,
the engine is given ten minutes to answer, and a failed run says why in at most 4000 characters and ends
cleanly."""

from __future__ import annotations

import io
import json
import time
import types

import pytest

from fabric_desktop import sql_worker
from tests.test_sql_worker_inprocess import fresh, job, said  # noqa: F401 - fresh is an autouse fixture


def _clock(*values):
    """A monotonic clock that gives ``values`` in turn, then the last one for good."""
    seq = list(values)
    return types.SimpleNamespace(monotonic=lambda: seq.pop(0) if len(seq) > 1 else seq[0], sleep=time.sleep)


def test_progress_is_said_once_the_interval_has_passed_and_not_before(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sql_worker, "time", _clock(5.0))
    monkeypatch.setattr(sql_worker, "PROGRESS_SECONDS", 0)
    sql_worker.run(job(tmp_path, "SELECT 1 AS n"))
    assert [e["event"] for e in said(capsys)] == ["started", "progress", "done"]
    monkeypatch.setattr(sql_worker, "PROGRESS_SECONDS", 6)
    sql_worker.run(job(tmp_path, "SELECT 1 AS n"))
    assert [e["event"] for e in said(capsys)] == ["started", "done"]


def test_a_statement_event_without_a_number_means_none_is_running(capsys):
    sql_worker._say("statement", statement=None)
    assert sql_worker._statement_now == 0


def test_the_engine_is_given_ten_minutes_to_answer(monkeypatch):
    waited = []

    class Replies:
        def get(self, timeout):
            waited.append(timeout)
            return {"value": "tok"}

    monkeypatch.setattr(sql_worker, "_replies", Replies())
    assert sql_worker._storage_token() == "tok"
    assert waited == [600]


@pytest.fixture
def main_with(monkeypatch, capsys):
    """main() with its stdin, its listening thread and its exit replaced: the events it said, its exit
    code and the thread it started."""
    def go(job_, fails, statement_now=0):
        threads, codes = [], []

        class Thread:
            def __init__(self, **kw):
                threads.append(kw)

            def start(self):
                pass

        def exit_(code):
            codes.append(code)
            raise SystemExit(code)

        monkeypatch.setattr(sql_worker, "_statement_now", statement_now)
        monkeypatch.setattr(sql_worker, "_take_stdin", lambda: io.StringIO(json.dumps(job_) + "\n"))
        monkeypatch.setattr(sql_worker, "threading", types.SimpleNamespace(Thread=Thread))
        monkeypatch.setattr(sql_worker.os, "_exit", exit_)
        monkeypatch.setattr(sql_worker, "run", fails)
        with pytest.raises(SystemExit):
            sql_worker.main()
        return said(capsys), codes, threads
    return go


def _raise(exc):
    def run(job_):
        raise exc
    return run


def test_a_failed_run_says_why_in_at_most_4000_characters_and_ends_cleanly(main_with):
    events, codes, threads = main_with({"query": "x"}, _raise(RuntimeError("y" * 5000)), statement_now=4)
    assert events == [{"event": "error", "kind": "sql", "message": "y" * 4000}]
    assert codes == [0]
    assert [(t["daemon"], t["name"]) for t in threads] == [(True, "engine-listen")]






