"""Runtime workers (engines/runtimes.py): time limits, how pip and the worker are started, the install
log, the CLI lookup and a session's edges, pinned where mutation testing
(docs/quality/mutation-testing.md, batch 84) found nothing that would notice a change. Stand-in
processes only: no worker or pip starts."""

from __future__ import annotations

import io
import json
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from fabric_desktop.engines import runtimes as rt
from fabric_desktop.engines.contract import CellRequest, SessionClosed, SessionConfig
from tests.test_runtimes import locks, runtimes_for  # noqa: F401 - fixtures

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_the_time_limits():
    assert rt.INSTALL_SECONDS == 1200  # 20 minutes
    assert rt.CELL_SECONDS == 7200  # 2 hours
    assert rt.STOP_SECONDS == 10


# ---------------------------------------------------------------- the install


def test_pip_runs_hidden_with_its_output_captured_as_text_and_never_raises(tmp_path, locks):  # noqa: F811
    seen = []
    runtimes_for(tmp_path, locks, seen).install("1.2", {})
    [(_cmd, kw)] = seen
    assert kw["capture_output"] is True and kw["text"] is True and kw["check"] is False
    assert kw["creationflags"] == NO_WINDOW and kw["timeout"] == 1200


@pytest.mark.parametrize(("stdout", "stderr", "log"), [(None, "only errors", "only errors"),
                                                        ("only output", None, "only output"),
                                                        ("out ", "err", "out err")])
def test_the_install_log_is_whatever_pip_printed(tmp_path, locks, stdout, stderr, log):  # noqa: F811
    done = runtimes_for(tmp_path, locks, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr))
    assert done.install("1.2", {})["log"] == log


def test_the_install_log_keeps_its_last_twenty_thousand_characters(tmp_path, locks):  # noqa: F811
    printed = "a" * 5 + "b" * 20000
    done = runtimes_for(tmp_path, locks, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=printed, stderr=""))
    assert done.install("1.2", {})["log"] == "b" * 20000


@pytest.mark.skipif(sys.platform == "win32", reason="the execute bit is how POSIX marks a program")
def test_a_cli_file_that_cant_be_run_isnt_used(tmp_path):
    (tmp_path / "thing").write_text("")
    (tmp_path / "thing").chmod(0o644)
    assert rt.trusted_cli("thing", {"PATH": str(tmp_path)}) is None


# ---------------------------------------------------------------- a session around a stand-in worker


class Worker:
    """A stand-in worker process: answers start and exec, ends on quit."""

    def __init__(self, command, **kw):
        self.command, self.kw, self.sent, self.returncode = command, kw, [], None
        self._out: queue.Queue[str | None] = queue.Queue()
        self.stdin = SimpleNamespace(write=self._write, flush=lambda: None)
        self.stdout = iter(self._out.get, None)

    def _write(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        if msg["op"] == "start":
            self._out.put(json.dumps({"op": "started", "client": "c"}) + "\n")
        elif msg["op"] == "exec":
            self._out.put(json.dumps({"op": "result", "id": msg["id"], "status": "ok", "outputs": []}) + "\n")
        elif msg["op"] == "quit":
            self.returncode = 0
            self._out.put(None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def started(monkeypatch):
    made = []

    def popen(command, **kw):
        made.append(Worker(command, **kw))
        return made[-1]

    monkeypatch.setattr(rt.subprocess, "Popen", popen)
    s = rt.WorkerSession(SessionConfig(notebook="nb"), ["py", "-I", "worker"], {"A": "1"}, start_seconds=5)
    yield s, made[0]
    s.close()


def test_the_worker_starts_hidden_with_text_pipes_and_in_its_own_session(started):
    s, worker = started
    kw = worker.kw
    assert kw["text"] is True and kw["encoding"] == "utf-8" and kw["stderr"] is subprocess.DEVNULL
    assert kw["start_new_session"] is (sys.platform != "win32") and kw["creationflags"] == NO_WINDOW
    assert s.client == "c"


def test_the_reader_thread_never_keeps_the_app_open(started):
    [reader] = [t for t in threading.enumerate() if t.name == "-nb"]
    assert reader.daemon is True


def test_cells_are_numbered_from_one(started):
    s, worker = started
    s.execute(CellRequest(code="1", language="python"))
    s.execute(CellRequest(code="2", language="python"))
    assert [m["id"] for m in worker.sent if m["op"] == "exec"] == [1, 2]


# ---------------------------------------------------------------- a session without a process


class Proc:
    def __init__(self, exited=None):
        self.stdin, self.exited = io.StringIO(), exited

    def poll(self):
        return self.exited

    def wait(self, timeout=None):
        return 0


def session(proc=None, replies=(), cell_seconds=60.0):
    s = object.__new__(rt.WorkerSession)
    s.notebook, s.engine, s.proc, s.client = "nb", "thing", proc or Proc(), ""
    s._cell_seconds, s._closed, s._running, s._n = cell_seconds, False, False, 0
    s._lock, s._lines = threading.Lock(), queue.Queue()
    for r in replies:
        s._lines.put(r)
    return s


def sent(s):
    return [json.loads(line)["op"] for line in s.proc.stdin.getvalue().splitlines()]


def test_a_closed_session_refuses_a_cell():
    s = session(replies=[{"op": "result", "id": 1, "status": "ok", "outputs": []}])
    s._closed = True
    with pytest.raises(SessionClosed, match="was closed"):
        s.execute(CellRequest(code="1", language="python"))
    assert sent(s) == []


def test_only_this_cells_result_or_error_answers_it():
    s = session(replies=[{"op": "result", "id": 7, "status": "error", "outputs": [{"type": "text", "text": "other"}]},
                         {"op": "progress", "id": 1},
                         {"op": "result", "id": 1, "status": "ok", "outputs": [{"type": "text", "text": "mine"}]}])
    result = s.execute(CellRequest(code="1", language="python"))
    assert result.status == "ok" and result.outputs == [{"type": "text", "text": "mine"}]


def test_after_a_cell_there_is_nothing_to_cancel():
    s = session(replies=[{"op": "result", "id": 1, "status": "ok", "outputs": []}])
    s.execute(CellRequest(code="1", language="python"))
    assert s.cancel() is False and sent(s) == ["exec"]


def test_a_cell_takes_the_time_between_its_start_and_its_answer(monkeypatch):
    ticks = iter([100.0, 103.5])
    monkeypatch.setattr(rt, "time", SimpleNamespace(perf_counter=lambda: next(ticks), monotonic=rt.time.monotonic))
    s = session(replies=[{"op": "result", "id": 1, "status": "ok", "outputs": []}])
    assert s.execute(CellRequest(code="1", language="python")).seconds == 3.5


def no_time_left(monkeypatch):
    ticks = iter(range(0, 10 ** 6, 1000))
    monkeypatch.setattr(rt, "time", SimpleNamespace(perf_counter=rt.time.perf_counter,
                                                    monotonic=lambda: float(next(ticks))))


def test_a_wait_that_runs_out_says_how_many_minutes_it_waited(monkeypatch):
    no_time_left(monkeypatch)
    assert session()._wait(lambda m: True, 90) == {"op": "error", "evalue": "no answer within 2 minutes"}


def test_a_cell_past_its_limit_says_the_limit_in_minutes(monkeypatch):
    no_time_left(monkeypatch)
    s = session(cell_seconds=90)
    with pytest.raises(SessionClosed, match="still running after 2 minutes"):
        s.execute(CellRequest(code="1", language="python"))
    assert sent(s) == ["exec", "interrupt", "quit"]


def test_closing_a_closed_session_whose_process_still_runs_asks_it_to_quit():
    s = session(Proc(exited=None))
    s._closed = True
    s.close()
    assert sent(s) == ["quit"]


def test_closing_a_closed_session_whose_process_has_ended_does_nothing():
    s = session(Proc(exited=0))
    s._closed = True
    s.close()
    assert sent(s) == []


def test_closing_an_open_session_whose_process_has_ended_still_marks_it_closed():
    s = session(Proc(exited=0))
    s.close()
    assert s.closed and sent(s) == ["quit"]
