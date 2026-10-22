"""Shared sessions (kernel_sessions.share), like Fabric's high-concurrency sessions: notebooks in
one process share spark, its temp views and settings and run one cell at a time, but keep
variables of their own. Stop ends it for all; Leave gives a notebook its own session again."""

from __future__ import annotations

import pytest
from test_kernel_sessions import finish, out, svc  # noqa: F401
from test_prove import call  # noqa: F401

from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.kernel_sessions import GuestKernel, KernelSessions
from fabric_desktop.notebook import Kernel


def run(s, notebook, code):
    call(s, "start_cell", code=code, notebook=notebook)
    return finish(s, notebook)


def test_a_shared_session_shares_spark_but_not_variables(svc):  # noqa: F811
    call(svc, "save_app_settings", changes={"kernel_process": True})
    run(svc, "A", "x = 'A'\nspark.range(3).createOrReplaceTempView('shared_view')\nspark.conf.set('demo.key', 'set by A')")
    run(svc, "B", "x = 'B before'")
    shared = call(svc, "kernel_session_share", notebook="B", host="A")
    assert shared == {"notebook": "B", "host": "A", "shared_with": ["B"]}
    # B's own session ended: its variables are gone. It sees A's spark, temp view and settings.
    assert out(run(svc, "B", "print('x' in dir())")) == "False\n"
    assert out(run(svc, "B", "print(spark.table('shared_view').count(), spark.conf.get('demo.key'))")) == "3 set by A\n"
    run(svc, "B", "x = 'B'")
    assert out(run(svc, "A", "x")) == "'A'"
    assert [v["value"] for v in call(svc, "kernel_variables", notebook="B") if v["name"] == "x"] == ["'B'"]
    rows = {r["notebook"]: r for r in call(svc, "kernel_sessions")["sessions"]}
    assert set(rows) == {"A"} and rows["A"]["shared_with"] == ["B"]
    # One process; one cell at a time across the notebooks, and each sees only its own cell.
    assert call(svc, "kernel_info", notebook="B")["pid"] == call(svc, "kernel_info", notebook="A")["pid"]
    call(svc, "start_cell", code="import time\ntime.sleep(3)", notebook="A")
    with pytest.raises(Exception, match="A is running a cell in this shared session"):
        call(svc, "start_cell", code="1", notebook="B")
    b = call(svc, "poll_cell", notebook="B")
    assert b["status"] == "ok" and b["session_running"] is True and out(b) == ""  # its own last cell, not A's
    assert call(svc, "interrupt_cell", notebook="B") == {"interrupted": False}  # not B's cell to cancel
    call(svc, "interrupt_cell", notebook="A")
    assert finish(svc, "A")["status"] == "cancelled"
    # Restart in B clears only B's variables.
    call(svc, "restart_kernel", notebook="B")
    assert out(run(svc, "B", "print('x' in dir())")) == "False\n"
    assert out(run(svc, "A", "x")) == "'A'"
    # Leave: B's next cell starts a session of its own, without A's temp view.
    assert call(svc, "kernel_session_leave", notebook="B") == {"notebook": "B", "left": "A"}
    assert out(run(svc, "B", "print('shared_view' in [t.name for t in spark.catalog.listTables()])")) == "False\n"
    assert call(svc, "kernel_info", notebook="B")["pid"] != call(svc, "kernel_info", notebook="A")["pid"]


def test_stop_ends_the_shared_session_for_every_notebook_in_it(svc):  # noqa: F811
    call(svc, "save_app_settings", changes={"kernel_process": True})
    run(svc, "A", "x = 1")
    call(svc, "kernel_session_share", notebook="B", host="A")
    run(svc, "B", "y = 2")
    assert call(svc, "stop_kernel_process", notebook="B") == {"stopped": True}
    assert out(run(svc, "A", "print('x' in dir())")) == "False\n"
    assert out(run(svc, "B", "print('y' in dir())")) == "False\n"
    assert call(svc, "kernel_info", notebook="B")["pid"] == call(svc, "kernel_info", notebook="A")["pid"]


def test_what_sharing_refuses(svc):  # noqa: F811
    with pytest.raises(Exception, match="needs notebooks in their own process"):
        call(svc, "kernel_session_share", notebook="B", host="A")
    call(svc, "save_app_settings", changes={"kernel_process": True})
    with pytest.raises(Exception, match="Pick another notebook"):
        call(svc, "kernel_session_share", notebook="A", host="A")
    call(svc, "kernel_session_share", notebook="B", host="A")
    with pytest.raises(Exception, match="Other notebooks share B's session|Pick another"):
        call(svc, "kernel_session_share", notebook="A", host="B")  # B's host is A: A can't join itself
    # Joining a guest joins its host: one session, never a chain.
    assert call(svc, "kernel_session_share", notebook="C", host="B")["host"] == "A"
    with pytest.raises(Exception, match="Other notebooks share A's session"):
        call(svc, "kernel_session_share", notebook="A", host="D")
    # A proof needs the notebook's own session.
    with pytest.raises(RuntimeError, match="shares A's session"):
        svc.sessions.kernel("B").bind_proof({}, "a", "")
    # Different environments are refused, as in Fabric.
    svc.environments.create("pandas2")
    svc.environments.attach("D", "pandas2")
    with pytest.raises(Exception, match="different environments"):
        call(svc, "kernel_session_share", notebook="D", host="A")


class ClosingKernel(Kernel):
    def close(self) -> None:
        self.restart()


def test_rename_delete_and_switching_off_keep_the_shares_right():
    made = []
    sessions = KernelSessions(lambda _p: made.append(ClosingKernel()) or made[-1], lambda: True)
    try:
        sessions.share("B", "A")
        sessions.rename("A", "A2")
        assert sessions.host_of("B") == "A2" and sessions.guests("A2") == ["B"]
        sessions.rename("B", "B2")
        assert sessions.host_of("B2") == "A2"
        guest = sessions.kernel("B2")
        assert isinstance(guest, GuestKernel) and isinstance(guest, Kernel) and not isinstance(guest, ProcessKernel)
        guest.run("v = 1")
        sessions.forget("A2")  # the host is deleted: its guests get sessions of their own
        assert sessions.host_of("B2") is None
        sessions.share("C", "B2")
        sessions.forget("C")
        assert sessions.guests("B2") == []
        sessions.share("C", "B2")
        sessions.switch(False)
        assert sessions.host_of("C") is None
    finally:
        for k in made:
            k.restart()


def test_a_notebook_still_sees_its_last_cell_after_another_starts_one(svc):  # noqa: F811
    call(svc, "save_app_settings", changes={"kernel_process": True})
    run(svc, "A", "1")
    call(svc, "kernel_session_share", notebook="B", host="A")
    call(svc, "start_cell", code="print('from B')", notebook="B")
    deadline = __import__("time").monotonic() + 30
    while call(svc, "poll_cell", notebook="A").get("session_running") and __import__("time").monotonic() < deadline:
        pass
    # A's cell starts before B's UI has polled B's end: B still gets its own output, not A's.
    call(svc, "start_cell", code="import time\ntime.sleep(2)\nprint('from A')", notebook="A")
    snap = call(svc, "poll_cell", notebook="B")
    assert snap["status"] == "ok" and out(snap) == "from B\n" and snap.get("session_running") is True
    assert out(finish(svc, "A")) == "from A\n"
    with pytest.raises(Exception, match="shares A's session.*environment"):
        call(svc, "environment_attach", notebook="B", name=None)


def test_a_guest_keeps_to_its_hosts_environment_lakehouse_and_session(svc):  # noqa: F811
    call(svc, "save_app_settings", changes={"kernel_process": True})
    run(svc, "A", "1")
    call(svc, "kernel_session_share", notebook="B", host="A")
    snap = run(svc, "B", "%pip install six")
    assert snap["status"] == "error" and "uses that notebook's environment" in str(snap["outputs"])
    with pytest.raises(Exception, match=r"B shares A.s session, which uses its environment. They leave the shared session \(Sessions\) first"):
        call(svc, "environment_attach", notebook="A", name=None)
    ref = {"workspace_id": "w", "lakehouse_id": "other", "name": "Other"}
    with pytest.raises(Exception, match="B shares A's session but now has another default lakehouse"):
        call(svc, "start_cell", code="1", notebook="B", lakehouse_ref=ref)
    # A guest's use keeps the host's session in use, so idle reaping doesn't stop it.
    host = svc.sessions._sessions["A"]
    host.last_used = 0
    call(svc, "poll_cell", notebook="B")
    assert host.last_used > 0
