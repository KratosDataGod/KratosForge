"""Stop on Home's run of several notebooks ends the ones running too, with every process their cells
started, and each ends as "stopped" in the batch and in the notebook's runs. A notebook that hangs is
the case: it would otherwise hold the batch forever."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from fabric_desktop import notebook_batch_service as nbs
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service

from test_prove import call
from test_schedules import FakeScheduler


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))  # the process kernel setting is saved there
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    return Service(root=tmp_path / "app")


def _start(svc, names, **kw):
    """notebook_batch_start without its background process (the test runs that part itself)."""
    with pytest.MonkeyPatch.context() as m:
        m.setattr(nbs.subprocess, "Popen", lambda args, **kw: None)
        return svc.notebook_batch_start(names, **kw)


def _gone(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value != 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:  # a zombie that nobody waited for has ended too
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


def _until(check, seconds=20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def _read_pid(path) -> int:
    assert _until(lambda: path.exists() and path.read_text(encoding="ascii").strip())
    return int(path.read_text(encoding="ascii"))


# ---- the notebook's own process: Stop ends its kernel and what the cell started


def test_stop_ends_a_hung_notebook_and_the_process_its_cell_started(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(nbs, "STOP_CHECK_SECONDS", 0.1)
    call(svc, "save_app_settings", changes={"kernel_process": True})
    assert isinstance(svc.kernel, ProcessKernel)
    pid_file = tmp_path / "child.pid"
    hang = (f"import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
            f"time.sleep(600)")
    svc.notebooks.save("Hangs", [{"type": "code", "source": "a = 1"}, {"type": "code", "source": hang}])
    batch = _start(svc, ["Hangs"])
    got: list[dict] = []
    try:
        runner = threading.Thread(target=lambda: got.append(svc.run_batch_notebook(batch["id"], 0)))
        t0 = time.monotonic()
        runner.start()
        assert _until(lambda: pid_file.exists() or bool(got))
        assert not got, got[0]["message"]  # hung on the second cell
        child = _read_pid(pid_file)
        svc.notebook_batch_stop(batch["id"])
        runner.join(30)
        assert got and time.monotonic() - t0 < 30
        assert (got[0]["status"], got[0]["message"]) == ("stopped", nbs.STOPPED)
        assert _until(lambda: _gone(child))
        assert svc.notebook_runs("Hangs")[0]["status"] == "stopped"
    finally:
        svc.kernel.close()


def test_a_notebook_that_finished_before_stop_keeps_its_result(svc, monkeypatch):
    monkeypatch.setattr(nbs, "STOP_CHECK_SECONDS", 0.05)
    svc.notebooks.save("Quick", [{"type": "code", "source": "a = 1"}])
    batch = _start(svc, ["Quick"])

    def ran_then_stopped(name, progress):
        svc.notebook_batch_stop(batch["id"])
        time.sleep(0.2)  # the watcher sees Stop while the run is still being reported
        return {"status": "ok", "message": "1 cell ran", "cells": 1}
    monkeypatch.setattr(svc, "_run_notebook_here", ran_then_stopped)
    assert svc.run_batch_notebook(batch["id"], 0)["status"] == "ok"


# ---- the batch: a notebook process that doesn't end after Stop is ended from outside


HUNG_PROCESS = """
import subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open(sys.argv[1], "w").write(f"{p.pid}")
time.sleep(600)
"""


def test_a_notebook_process_that_ignores_stop_is_ended_with_what_it_started(tmp_path, monkeypatch):
    monkeypatch.setattr(nbs, "STOP_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(nbs, "STOP_GRACE_SECONDS", 0.5)
    script, pid_file, stop = tmp_path / "hung.py", tmp_path / "child.pid", tmp_path / "x.stop"
    script.write_text(HUNG_PROCESS, encoding="utf-8")
    got: list = []
    runner = threading.Thread(target=lambda: got.append(
        nbs._run_notebook_process([sys.executable, str(script), str(pid_file)], tmp_path, stop)))
    runner.start()
    child = _read_pid(pid_file)
    stop.write_text("now", encoding="utf-8")
    runner.join(60)
    assert got and got[0][2] is True
    assert _until(lambda: _gone(child))


def test_a_notebook_process_that_ends_on_its_own_after_stop_isnt_ended_here(tmp_path, monkeypatch):
    monkeypatch.setattr(nbs, "STOP_CHECK_SECONDS", 0.05)
    stop = tmp_path / "x.stop"
    stop.write_text("now", encoding="utf-8")
    code = 'import time; time.sleep(0.3); print(\'{"status": "stopped", "message": "x"}\')'
    out, rc, ended = nbs._run_notebook_process([sys.executable, "-c", code], tmp_path, stop)
    assert (rc, ended) == (0, False) and '"stopped"' in out


def test_a_notebook_ended_after_stop_is_stopped_in_the_batch_and_its_runs(svc, monkeypatch):
    svc.notebooks.save("Stuck", [{"type": "code", "source": "a = 1"}])
    svc.notebooks.save("Next", [{"type": "code", "source": "a = 1"}])
    batch = _start(svc, ["Stuck", "Next"], parallel=1)

    def stuck(args, root, stop):
        svc.notebook_batch_stop(batch["id"])  # pressed while it runs
        return "", None, True
    monkeypatch.setattr(nbs, "_run_notebook_process", stuck)
    out = svc.run_notebook_batch(batch["id"])
    first, second = out["items"]
    assert out["state"] == "done" and first["status"] == "stopped" and nbs.STOPPED in first["message"]
    assert second["status"] == "skipped"
    run = svc.notebook_runs("Stuck")[0]
    assert (run["status"], run["trigger"]) == ("stopped", "home") and run["ended"]
    assert not (svc.root / nbs.BATCHES / f"{batch['id']}.stop").exists()
