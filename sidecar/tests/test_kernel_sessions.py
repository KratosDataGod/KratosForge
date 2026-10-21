"""A session per notebook (kernel_sessions): own variables, several notebooks running at once
with notebooks in their own process, and one shared kernel inside the engine."""

from __future__ import annotations

import time

import pytest
from test_prove import call  # noqa: F401

from fabric_desktop import app_settings
from fabric_desktop.kernel_sessions import KernelSessions
from fabric_desktop.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    yield s
    for k in s.sessions.kernels():
        close = getattr(k, "close", None)
        if close:
            close()


def finish(s, notebook, timeout=30):
    deadline = time.monotonic() + timeout
    snap = call(s, "poll_cell", version=-1, wait=0.5, notebook=notebook)
    while snap["status"] == "running" and time.monotonic() < deadline:
        snap = call(s, "poll_cell", version=-1, wait=0.5, notebook=notebook)
    return snap


def out(snap):
    return "".join(o.get("text", "") for o in snap.get("outputs") or [])


def test_in_engine_notebooks_share_one_kernel_one_cell_at_a_time(svc):
    call(svc, "start_cell", code="x = 1", notebook="A")
    finish(svc, "A")
    assert out(finish(svc, "B")) == ""  # B asks the same kernel
    call(svc, "start_cell", code="import time\ntime.sleep(5)", notebook="A")
    with pytest.raises(Exception, match="already running in A.*own process"):
        call(svc, "start_cell", code="1", notebook="B")
    call(svc, "interrupt_cell", notebook="A")
    rows = call(svc, "kernel_sessions")
    assert rows["process"] is False and len(rows["sessions"]) == 1 and rows["sessions"][0]["notebook"] is None


def test_own_process_gives_each_notebook_its_variables_and_runs_them_together(svc):
    call(svc, "save_app_settings", changes={"kernel_process": True})
    call(svc, "start_cell", code="x = 'from A'", notebook="A")
    call(svc, "start_cell", code="x = 'from B'", notebook="B")
    finish(svc, "A"), finish(svc, "B")
    call(svc, "start_cell", code="x", notebook="A")
    assert out(finish(svc, "A")) == "'from A'"
    assert [v["value"] for v in call(svc, "kernel_variables", notebook="B") if v["name"] == "x"] == ["'from B'"]
    # Both run at the same time: each sleeps 2 s, together they take well under 4 s.
    started = time.monotonic()
    call(svc, "start_cell", code="import time\ntime.sleep(2)", notebook="A")
    call(svc, "start_cell", code="import time\ntime.sleep(2)", notebook="B")
    assert finish(svc, "A")["status"] == finish(svc, "B")["status"] == "ok"
    assert time.monotonic() - started < 3.5
    pids = {r["notebook"]: r["pid"] for r in call(svc, "kernel_sessions")["sessions"]}
    assert set(pids) == {"A", "B"} and pids["A"] != pids["B"]
    # Stop ends one notebook's process only.
    assert call(svc, "stop_kernel_process", notebook="A") == {"stopped": True}
    call(svc, "start_cell", code="x", notebook="B")
    assert out(finish(svc, "B")) == "'from B'"


def test_rename_keeps_the_session_and_delete_ends_it(svc):
    call(svc, "save_app_settings", changes={"kernel_process": True})
    nb = call(svc, "new_notebook")
    call(svc, "start_cell", code="kept = 7", notebook=nb["name"])
    finish(svc, nb["name"])
    call(svc, "rename_notebook", name=nb["name"], new_name="Renamed")
    call(svc, "start_cell", code="kept", notebook="Renamed")
    assert out(finish(svc, "Renamed")) == "7"
    kernel = svc.sessions.kernel("Renamed")
    call(svc, "delete_notebook", name="Renamed")
    assert not kernel.alive()
    assert all(r["notebook"] != "Renamed" for r in call(svc, "kernel_sessions")["sessions"])


class FakeKernel:
    def __init__(self):
        self.running = False
        self.closed = False
        self.count = 0

    def poll(self, *_a):
        return {"status": "running" if self.running else "idle"}

    def alive(self):
        return not self.closed

    def close(self):
        self.closed = True

    def stop(self):
        self.closed = True
        return {"stopped": True}


def test_the_least_recently_used_idle_session_makes_room_never_a_running_one():
    made = []
    sessions = KernelSessions(lambda _p: made.append(FakeKernel()) or made[-1], lambda: True, max_sessions=lambda: 2)
    a = sessions.kernel("A")
    b = sessions.kernel("B")
    a.running = True
    sessions.session("A").last_used = 0  # A is the oldest, but running
    sessions.kernel("C")
    assert b.closed and not a.closed
    assert "B" in sessions.take_notes()[0]
    a.running = False
    sessions.session("C")  # already there: nothing stopped
    assert sessions.take_notes() == []


def test_idle_sessions_stop_after_the_settings_minutes_except_the_open_one():
    sessions = KernelSessions(lambda _p: FakeKernel(), lambda: True, idle_minutes=lambda: 10)
    old, opened = sessions.kernel("old"), sessions.kernel("open")
    sessions.session("old").last_used = sessions.session("open").last_used = time.time() - 3600
    sessions.active = "open"
    sessions.reap_idle()
    assert old.closed and not opened.closed
    assert "idle for more than 10 minutes" in sessions.take_notes()[0]


def test_settings_limits_are_checked(svc):
    with pytest.raises(Exception, match="between 0 and 20"):
        call(svc, "save_app_settings", changes={"kernel_max_sessions": 50})
    call(svc, "save_app_settings", changes={"kernel_max_sessions": 3, "kernel_idle_minutes": 30})
    assert app_settings.value("kernel_max_sessions") == 3 and app_settings.value("kernel_idle_minutes") == 30
