"""Mutation-kill tests for kernel_sessions.py, with stand-in kernels: no engine, no processes."""

from __future__ import annotations

import contextlib

import pytest

from fabric_desktop import kernel_sessions
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.kernel_sessions import DEFAULT_MAX_SESSIONS, SHARED, GuestKernel, KernelSessions, Session


class FakeKernel:
    def __init__(self, process=True):
        self.process = process
        self.closed = False
        self.status = "idle"
        self.calls = []
        self.guest = None
        self.forgot = []
        self.count = 0
        self.extra = {}

    def poll(self, *args, **kwargs):
        return {"status": self.status} | self.extra

    def alive(self):
        return not self.closed

    def close(self):
        self.closed = True

    def stop(self):
        return {"stopped": True, "kernel": self}

    @contextlib.contextmanager
    def as_guest(self, notebook):
        before, self.guest = self.guest, notebook
        try:
            yield
        finally:
            self.guest = before

    def run(self, code):
        self.calls.append((self.guest, code))
        return code

    def forget_guest(self, notebook):
        self.forgot.append(notebook)


def make_sessions(**kw):
    made = []

    def make(process):
        made.append(FakeKernel(process))
        return made[-1]
    s = KernelSessions(make, lambda: True, **kw)
    s.made = made
    return s


def open_sessions(sessions, n):
    made = []
    for i in range(n):
        made.append(sessions.kernel(f"nb{i}"))
    return made


def test_the_default_cap_is_five():
    assert DEFAULT_MAX_SESSIONS == 5


@pytest.mark.parametrize("max_sessions", [None, lambda: 0, lambda: None])
def test_five_sessions_are_kept_by_default_and_a_sixth_stops_the_oldest(max_sessions):
    kw = {} if max_sessions is None else {"max_sessions": max_sessions}
    sessions = KernelSessions(lambda process: FakeKernel(), lambda: True, **kw)
    sessions.active = "open"
    kernels = open_sessions(sessions, 5)
    assert not any(k.closed for k in kernels)
    assert sessions.take_notes() == []
    open_sessions(sessions, 6)  # nb0..nb4 are looked up again, nb5 is new
    assert [k.closed for k in kernels] == [True, False, False, False, False]
    notes = sessions.take_notes()
    assert len(notes) == 1 and "nb0" in notes[0] and "at most 5 notebooks" in notes[0]


def test_any_running_is_true_or_false_and_skips_the_notebook_named():
    sessions = KernelSessions(lambda process: FakeKernel(), lambda: True)
    a, b = sessions.kernel("A"), sessions.kernel("B")
    assert sessions.any_running() is False
    a.status = "running"
    assert sessions.any_running() is True
    assert sessions.any_running(other_than="B") is True
    assert sessions.any_running(other_than="A") is False
    b.status = "running"
    assert sessions.any_running(other_than="A") is True


# ---- GuestKernel and Session

def test_a_guest_kernel_calls_as_its_notebook_and_forwards_attributes():
    k = FakeKernel()
    g = GuestKernel(k, "B", "A")
    assert isinstance(g, FakeKernel) and g.guest == "B" and g.host == "A"
    assert g.run("x") == "x" and k.calls == [("B", "x")]
    assert k.guest is None
    assert g.alive == k.alive  # alive is passed through, not wrapped
    g.notebook_name = "B"
    assert k.notebook_name == "B"
    assert g.count == 0
    g.restart()
    g.close()
    assert k.forgot == ["B", "B"] and not k.closed
    for name in ("bind_proof", "unbind_proof", "finish_proof"):
        with pytest.raises(RuntimeError, match="B shares A's session"):
            getattr(g, name)()


def test_session_running_and_alive():
    k = FakeKernel()
    s = Session("A", k)
    assert s.running() is False and s.alive() is True
    k.status = "running"
    assert s.running() is True
    k.status, k.extra = "idle", {"session_running": 1}
    assert s.running() is True
    k.closed = True
    assert s.alive() is False

    class NoAlive:
        pass
    assert Session("B", NoAlive()).alive() is True


def test_touch_moves_last_used(monkeypatch):
    s = Session("A", FakeKernel())
    monkeypatch.setattr(kernel_sessions.time, "time", lambda: 1e10)
    s.touch()
    assert s.last_used == 1e10


# ---- lookups

def test_in_engine_every_notebook_shares_one_kernel():
    made = []
    s = KernelSessions(lambda process: made.append(FakeKernel(process)) or made[-1], lambda: False)
    assert s.in_process is False
    assert s.kernel("A") is s.kernel("B") is s.kernel(None)
    assert made[0].process is False and len(made) == 1
    assert s.session("A").notebook == SHARED


def test_process_notebooks_each_get_a_prepared_kernel():
    prepared = []
    s = make_sessions(prepare=lambda nb, k: prepared.append((nb, k)))
    a, b = s.kernel("A"), s.kernel("B")
    assert a is not b and a.process is True
    assert prepared == [("A", a), ("B", b)]
    s.active = "B"
    assert s.kernel(None) is b
    assert s.session("C", create=False) is None and "C" not in [x.notebook for x in s._sessions.values()]


def test_a_guest_session_touches_its_host_and_takes_its_times(monkeypatch):
    s = make_sessions()
    host = s.session("A")
    s.share("B", "A")
    host.last_used = 5.0
    guest = s.session("B")
    assert isinstance(guest.kernel, GuestKernel) and guest.notebook == "B"
    assert host.last_used > 5.0
    assert (guest.created, guest.last_used) == (host.created, host.last_used)
    s.forget("A")
    assert s.session("B", create=False) is None


def test_put_sets_the_kind_from_the_kernel():
    s = make_sessions()
    s.put("A", FakeKernel())
    assert s.in_process is False and s.kernel("Z").closed is False and s.session("Z").notebook == SHARED


# ---- the cap and idle sessions

def test_a_running_or_open_session_is_never_stopped_to_make_room():
    s = make_sessions(max_sessions=lambda: 2)
    s.active = "A"
    a, b = s.kernel("A"), s.kernel("B")
    b.status = "running"
    c = s.kernel("C")
    assert not a.closed and not b.closed and not c.closed
    assert s.take_notes() == []
    b.status = "idle"
    s.kernel("D")  # three live, at most two: the two idle ones go, oldest first; A is open
    assert b.closed and c.closed and not a.closed
    assert s.take_notes() == [f"Stopped the session for {nb} to make room: at most 2 notebooks keep a session "
                              "(Settings). Its variables were cleared." for nb in ("B", "C")]
    assert sorted(s._sessions) == ["A", "D"]


def test_a_dead_session_does_not_count_against_the_cap():
    s = make_sessions(max_sessions=lambda: 2)
    a = s.kernel("A")
    a.closed = True
    s.kernel("B")
    b = s.session("B").kernel
    s.kernel("C")
    assert not b.closed  # A was dead: B and C fit


@pytest.mark.parametrize("cap", [lambda: -3, lambda: 1])
def test_the_cap_is_at_least_one(cap):
    s = make_sessions(max_sessions=cap)
    a = s.kernel("A")
    s.kernel("B")
    assert a.closed
    assert s.take_notes() == [("Stopped the session for A to make room: at most 1 notebooks keep a session "
                               "(Settings). Its variables were cleared.")]


def test_reap_idle(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(kernel_sessions.time, "time", lambda: now[0])
    s = make_sessions(idle_minutes=lambda: 1)
    s.active = "open"
    old, fresh, run, opened = s.kernel("old"), s.kernel("fresh"), s.kernel("run"), s.kernel("open")
    run.status = "running"
    now[0] += 61
    s.session("fresh")
    s.reap_idle()
    assert old.closed and not fresh.closed and not run.closed and not opened.closed
    assert s.take_notes() == ["Stopped the session for old: idle for more than 1 minutes. Its variables were cleared."]
    assert sorted(s._sessions) == ["fresh", "open", "run"]
    assert s.take_notes() == []


def test_reap_idle_needs_minutes_and_processes(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(kernel_sessions.time, "time", lambda: now[0])
    for minutes in (0, -1, None):
        s = make_sessions(idle_minutes=lambda m=minutes: m)
        k = s.kernel("A")
        now[0] += 10_000
        s.reap_idle()
        assert not k.closed
    s = make_sessions(idle_minutes=lambda: 0.5)
    k = s.kernel("A")
    now[0] += 29
    s.reap_idle()
    assert not k.closed
    now[0] += 2
    s.reap_idle()
    assert k.closed and "more than 0.5 minutes" in s.take_notes()[0]


def test_reap_idle_puts_back_a_session_that_started_running(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(kernel_sessions.time, "time", lambda: now[0])
    s = make_sessions(idle_minutes=lambda: 1)
    k = s.kernel("A")
    polls = []

    def poll(*a, **kw):
        polls.append(1)
        return {"status": "idle" if len(polls) == 1 else "running"}
    k.poll = poll
    now[0] += 120
    s.reap_idle()
    assert not k.closed and s._sessions["A"].kernel is k and s._reaping == {}
    assert s.take_notes() == []


# ---- changes

def test_switch_returns_every_kernel_and_starts_over():
    s = make_sessions()
    a, b = s.kernel("A"), s.kernel("B")
    s.share("C", "A")
    s._reaping["R"] = s._sessions.pop("B")
    assert s.switch(False) == [a, b]
    assert s._sessions == {} and s._reaping == {} and s.joined() == {} and s.in_process is False


def test_rename_moves_sessions_shares_and_the_open_notebook():
    s = make_sessions()
    a = s.kernel("A")
    s.share("G", "A")
    s.active = "A"
    s.rename("A", "A2")
    assert s.session("A2", create=False).kernel is a and s.session("A2").notebook == "A2"
    assert "A" not in s._sessions and s.joined() == {"G": "A2"} and s.active == "A2"
    s.rename("G", "G2")
    assert s.joined() == {"G2": "A2"}
    r = s.kernel("R")
    s._reaping["R"] = s._sessions.pop("R")
    s.rename("R", "R2")
    assert s._reaping["R2"].kernel is r and s._reaping["R2"].notebook == "R2"
    s.active = "X"
    s.rename("Y", "Z")
    assert s.active == "X"


def test_rename_onto_an_existing_session_keeps_both():
    s = make_sessions()
    a, b = s.kernel("A"), s.kernel("B")
    s.rename("A", "B")
    assert s._sessions["A"].kernel is a and s._sessions["B"].kernel is b


def test_stop_asks_the_session_and_a_guest_stops_its_host():
    s = make_sessions()
    assert s.stop("A") == {"stopped": False}
    a = s.kernel("A")
    assert s.stop("A") == {"stopped": True, "kernel": a}
    s.share("G", "A")
    assert s.stop("G")["kernel"] is a


def test_forget():
    s = make_sessions()
    a, b = s.kernel("A"), s.kernel("B")
    s.share("G", "A")
    s.forget("G")
    assert s.joined() == {} and a.forgot == ["G"] and not a.closed
    s.share("G", "A")
    s.forget("A")
    assert a.closed and s.joined() == {} and "A" not in s._sessions
    s._reaping["B"] = s._sessions.pop("B")
    s.forget("B")
    assert b.closed and s._reaping == {}
    s.forget("nothing")


def test_forget_in_engine_does_nothing():
    k = FakeKernel(False)
    s = KernelSessions(lambda p: k, lambda: False)
    s.kernel("A")
    s.forget("A")
    assert not k.closed and s.kernels() == [k]


def test_what_share_refuses_and_what_it_does():
    s = KernelSessions(lambda p: FakeKernel(p), lambda: False)
    with pytest.raises(RuntimeError, match="needs notebooks in their own process"):
        s.share("B", "A")
    s = make_sessions()
    for nb, host in (("", "A"), ("B", ""), ("A", "A")):
        with pytest.raises(ValueError, match="Pick another notebook"):
            s.share(nb, host)
    b = s.kernel("B")
    b.status = "running"
    with pytest.raises(RuntimeError, match="A cell is running in B"):
        s.share("B", "A")
    b.status = "idle"
    assert s.share("B", "A") == {"notebook": "B", "host": "A", "shared_with": ["B"]}
    assert b.closed and "B" not in s._sessions
    with pytest.raises(RuntimeError, match="Other notebooks share A's session"):
        s.share("A", "C")
    assert s.share("D", "B") == {"notebook": "D", "host": "A", "shared_with": ["B", "D"]}  # never a chain


def test_share_moves_a_guest_from_one_host_to_another():
    s = make_sessions()
    a, c = s.kernel("A"), s.kernel("C")
    s.share("B", "A")
    s.share("B", "C")
    assert a.forgot == ["B"] and s.joined() == {"B": "C"}
    s.share("B", "C")
    assert a.forgot == ["B"] and c.forgot == []


def test_share_closes_a_session_reap_idle_has_out():
    s = make_sessions()
    b = s.kernel("B")
    s._reaping["B"] = s._sessions.pop("B")
    s.share("B", "A")
    assert b.closed and s._reaping == {}


def test_leave():
    s = make_sessions()
    a = s.kernel("A")
    s.share("B", "A")
    assert s.leave("B") == {"notebook": "B", "left": "A"} and a.forgot == ["B"]
    assert s.leave("B") == {"notebook": "B", "left": None}
    assert s.host_of("B") is None


def test_host_of_and_guests():
    s = make_sessions()
    s.kernel("A")
    s.share("C", "A")
    s.share("B", "A")
    assert s.host_of("B") == "A" and s.host_of("A") is None
    s.active = "C"
    assert s.host_of(None) == "A"
    assert s.guests("A") == ["B", "C"] and s.guests("B") == []
    assert s._in_use() == {"C", "A"}
    j = s.joined()
    j["X"] = "Y"
    assert "X" not in s.joined()


# ---- the Sessions list

def test_describe_in_engine(monkeypatch):
    k = FakeKernel(False)
    s = KernelSessions(lambda p: k, lambda: False)
    s.kernel(None)
    rows = s.describe()
    assert rows == [{"notebook": None, "process": False, "status": "idle", "pid": None, "memory_bytes": None,
                     "cells_run": 0, "started": rows[0]["started"], "last_used": rows[0]["last_used"],
                     "active": True, "shared_with": []}]
    k.status = "running"
    assert s.describe()[0]["status"] == "running"


def test_describe_orders_by_last_use_and_says_who_shares(monkeypatch):
    s = make_sessions()
    _, b = s.kernel("A"), s.kernel("B")
    s._sessions["A"].last_used, s._sessions["B"].last_used = 1.0, 2.0
    s.share("G", "A")
    s.active = "G"
    rows = s.describe()
    assert [r["notebook"] for r in rows] == ["B", "A"]
    assert [r["active"] for r in rows] == [False, True]
    assert rows[1]["shared_with"] == ["G"] and rows[0]["shared_with"] == []
    assert [r["process"] for r in rows] == [False, False]  # stand-ins, not ProcessKernel
    b.closed = True
    b.count = 0
    assert s.describe()[0]["status"] == "ready"
    b.count = 3
    assert s.describe()[0]["status"] == "stopped" and s.describe()[0]["cells_run"] == 3


class FakeProcess(FakeKernel):
    """Passes isinstance(k, ProcessKernel) without starting a process."""
    __class__ = property(lambda self: ProcessKernel)  # type: ignore[assignment]
    pid = 4242


def test_put_of_a_process_kernel_keeps_notebooks_apart():
    s = KernelSessions(lambda p: FakeKernel(p), lambda: False)
    k = FakeProcess()
    s.put("A", k)
    assert s.in_process is True and s.kernel("A") is k and s.session("A").notebook == "A"


def test_describe_process_sessions(monkeypatch):
    monkeypatch.setattr(kernel_sessions, "memory_used", lambda pid: pid * 2)
    s = KernelSessions(lambda p: FakeProcess(), lambda: True)
    a, unnamed = s.kernel("A"), s.kernel("")
    row = s.describe()
    assert sorted(r["notebook"] or "" for r in row) == ["", "A"]
    a_row = next(r for r in row if r["notebook"] == "A")
    assert a_row["process"] is True and a_row["pid"] == 4242 and a_row["memory_bytes"] == 8484
    assert a_row["status"] == "idle" and a_row["active"] is False
    unnamed.closed = True
    assert [r["notebook"] for r in s.describe()] == ["A"]  # made for no notebook and never started
    a.closed = True
    a.poll = lambda *x, **kw: pytest.fail("a stopped process isn't polled")
    a_row = s.describe()[0]
    assert a_row["status"] == "ready" and a_row["pid"] is None and a_row["memory_bytes"] is None
    s.active = "A"
    assert s.describe()[0]["active"] is True


def test_sessions_are_not_reaped_unless_settings_ask(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(kernel_sessions.time, "time", lambda: now[0])
    s = make_sessions()
    k = s.kernel("A")
    now[0] += 10_000
    s.reap_idle()
    assert not k.closed


def test_a_session_idle_for_exactly_the_limit_is_kept(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(kernel_sessions.time, "time", lambda: now[0])
    s = make_sessions(idle_minutes=lambda: 1)
    k = s.kernel("A")
    now[0] += 60
    s.reap_idle()
    assert not k.closed
    now[0] += 0.5
    s.reap_idle()
    assert k.closed


def test_describe_polls_a_live_process_and_counts_cells_only_if_known(monkeypatch):
    monkeypatch.setattr(kernel_sessions, "memory_used", lambda pid: 1)
    s = KernelSessions(lambda p: FakeProcess(), lambda: True)
    k = s.kernel("A")
    k.status = "running"
    assert s.describe()[0]["status"] == "running"

    class Bare:
        def poll(self, *a, **kw):
            return {"status": "idle"}

        def alive(self):
            return False
    s = make_sessions()
    s.put("B", Bare())
    row = s.describe()[0]
    assert row["status"] == "ready" and row["cells_run"] == 0
