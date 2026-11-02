"""EA-008: a scheduled run on this PC that was killed (the PC slept, Task Manager, a crash) leaves its
lock behind. The next run takes over a lock whose process has gone, or whose PID now belongs to
another process, and the killed run shows in the history as interrupted."""

import json
import os
import subprocess
import sys
import time


from fabric_desktop import schedule_service
from test_schedules import svc  # noqa: F401 - fixture


def schedule(svc):
    svc.notebooks.save("Nightly", [{"type": "code", "source": "x = 1"}])
    return svc.add_pc_schedule({"kind": "notebook", "name": "Nightly"}, {"kind": "daily", "times": ["06:00"]})["schedule"]["id"]


def killed_mid_run(svc, sid):
    """A run's process that took the schedule's lock and was then killed."""
    code = ("import sys, time\nfrom pathlib import Path\nfrom fabric_desktop.service import Service\n"
            "svc = Service(root=Path(sys.argv[1]))\n"
            "assert svc._lock(sys.argv[2], {'schedule_id': sys.argv[2], 'label': 'Nightly', 'trigger': 'schedule', "
            "'started': '2026-11-02T03:00:00+00:00'})\nprint('locked', flush=True)\ntime.sleep(60)\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(svc.root), sid], stdout=subprocess.PIPE, text=True,
                             env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    assert child.stdout.readline().strip() == "locked"
    child.kill()
    child.wait(10)


def test_a_killed_run_doesnt_block_the_next_one_and_is_recorded(svc):
    sid = schedule(svc)
    killed_mid_run(svc, sid)
    row = svc.run_schedule(sid)
    assert row["status"] == "ok", row
    runs = [r for r in svc.pc_schedule_runs() if r["schedule_id"] == sid]
    assert [r["status"] for r in runs] == ["ok", "interrupted"]
    assert runs[1]["started"] == "2026-11-02T03:00:00+00:00" and "stopped before it finished" in runs[1]["message"]


def test_a_lock_whose_pid_now_belongs_to_another_process_is_taken_over(svc):
    sid = schedule(svc)
    lock = svc._lock(sid, {"schedule_id": sid, "label": "Nightly", "trigger": "schedule", "started": "x"})
    held = json.loads(lock.read_text())
    held["process"] = "another process's start"  # this PID, reused: a different process now
    lock.write_text(json.dumps(held))
    assert svc.run_schedule(sid)["status"] == "ok"


def test_a_run_still_going_in_a_live_process_is_still_skipped(svc):
    sid = schedule(svc)
    assert svc._lock(sid) is not None  # this process, which is alive
    assert svc.run_schedule(sid)["status"] == "skipped"


def test_a_lock_from_before_this_change_with_a_dead_pid_is_taken_over(svc):
    sid = schedule(svc)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(10)
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.lock").write_text(str(child.pid))  # the old format: just the PID
    row = svc.run_schedule(sid)
    assert row["status"] == "ok", row


def test_an_unreadable_lock_still_waits_out_the_old_stale_time(svc, monkeypatch):
    sid = schedule(svc)
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.lock"
    path.write_text("")  # its writer stopped between creating and writing it, or it's damaged
    assert svc.run_schedule(sid)["status"] == "skipped"
    old = time.time() - schedule_service.STALE_LOCK_SECONDS - 1
    os.utime(path, (old, old))
    assert svc.run_schedule(sid)["status"] == "ok"


def test_this_process_is_alive_and_a_finished_one_is_not():
    me = schedule_service.process_identity(os.getpid())
    assert schedule_service.process_alive(os.getpid(), me)
    assert not schedule_service.process_alive(os.getpid(), "someone else")
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(10)
    assert not schedule_service.process_alive(child.pid, None)
