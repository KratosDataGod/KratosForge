"""Runtime workers (engines/runtimes.py): the install log behind a proxy, and the session's edges the
worker tests don't reach. A session here is built around a stand-in process, so no worker starts."""

from __future__ import annotations

import queue
import subprocess
import threading
from types import SimpleNamespace

import pytest

from fabric_desktop.engines import runtimes as rt
from fabric_desktop.engines.contract import CellRequest, SessionClosed
from tests.test_runtimes import locks, runtimes_for  # noqa: F401 - fixtures


def test_a_refused_install_behind_a_proxy_says_how_to_fix_it(tmp_path, locks):  # noqa: F811
    proxied = runtimes_for(tmp_path, locks, run=lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="ProxyError('Cannot connect to proxy.')"))
    out = proxied.install("1.2", {})
    assert not out["ok"] and "The proxy refused pip's connection" in out["log"]
    assert "dbconnect_index_url in the policy file" in out["log"]


class Pipe:
    def __init__(self, broken=False):
        self.broken, self.sent = broken, []

    def write(self, text):
        if self.broken:
            raise BrokenPipeError
        self.sent.append(text)

    def flush(self):
        pass


class Proc:
    def __init__(self, broken=False, hangs=False):
        self.stdin, self.hangs, self.waited, self.ended = Pipe(broken), hangs, [], False

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.waited.append(timeout)
        if self.hangs:
            raise subprocess.TimeoutExpired("worker", timeout)
        return 0


def session(proc, replies=(), cell_seconds=60.0):
    s = object.__new__(rt.WorkerSession)  # no worker process: the stand-in answers
    s.notebook, s.engine, s.proc, s.client = "nb", "thing", proc, ""
    s._cell_seconds, s._closed, s._running, s._n = cell_seconds, False, False, 0
    s._lock, s._lines = threading.Lock(), queue.Queue()
    for r in replies:
        s._lines.put(r)
    return s


def test_the_base_session_waits_its_own_stop_time():
    assert session(Proc()).stop_seconds() == rt.STOP_SECONDS


def test_a_message_to_a_worker_that_has_ended_closes_the_session():
    with pytest.raises(SessionClosed, match="The runtime process has ended"):
        session(Proc(broken=True))._send({"op": "exec"})


def test_a_wait_keeps_going_past_an_empty_moment(monkeypatch):
    s = session(Proc())
    answers = iter([queue.Empty(), {"op": "result", "id": 1}])

    def get(timeout=None):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(s._lines, "get", get)
    assert s._wait(lambda m: m.get("op") == "result", None) == {"op": "result", "id": 1}


def test_a_second_cell_while_one_runs_is_refused():
    s = session(Proc())
    s._lock.acquire()
    with pytest.raises(RuntimeError, match="already running"):
        s.execute(CellRequest(code="1", language="python"))


def test_each_output_reaches_the_page_as_it_is_returned():
    s = session(Proc(), [{"op": "result", "id": 1, "status": "ok",
                          "outputs": [{"type": "text", "text": "hi"}, {"type": "frame", "rows": [[1], [2]]}]}])
    shown = []
    result = s.execute(CellRequest(code="1", language="python"), on_output=shown.append)
    assert [o["type"] for o in shown] == ["text", "frame"] and result.rows == 2


def test_a_worker_that_wont_quit_is_ended(monkeypatch):
    ended = []
    from fabric_desktop import shell
    monkeypatch.setattr(shell, "end", lambda proc: ended.append(proc))
    proc = Proc(hangs=True)
    session(proc).close()
    assert ended == [proc] and proc.waited == [5]
    assert '"op": "quit"' in proc.stdin.sent[0]


def test_a_worker_that_ends_while_a_cell_runs_closes_the_session():
    s = session(Proc(), [None])  # the reader's end-of-output mark
    with pytest.raises(SessionClosed, match="ended while the cell ran"):
        s.execute(CellRequest(code="1", language="python"))
    assert s.closed
