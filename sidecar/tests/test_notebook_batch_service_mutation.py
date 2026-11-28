"""Mutation-testing kill tests for notebook_batch_service (batch 86): the limits a run of several notebooks
keeps, when a quiet batch counts as lost, how Stop ends a notebook that doesn't end itself, the heartbeat,
the times each run logs, and how the background processes are started. No process is started here."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import types

import pytest

from fabric_desktop import notebook_batch_service as nbs
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service

from test_schedules import FakeScheduler

CELLS = [{"type": "code", "source": "a = 1"}]
_REAL_THREAD = threading.Thread


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    s = Service(root=tmp_path / "app")
    s.notebooks.save("A", CELLS)
    s.notebooks.save("B", CELLS)
    return s


@pytest.fixture
def popen(monkeypatch):
    """notebook_batch_start's background process, recorded instead of started."""
    started = []
    monkeypatch.setattr(nbs.subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    return started


def _clock(monkeypatch, now: float = 1_000_000.0, monotonic=None, sleep=None):
    """The module's time, frozen (time()) and scripted (monotonic())."""
    fake = types.SimpleNamespace(time=lambda: now, monotonic=monotonic or time.monotonic, sleep=sleep or time.sleep)
    monkeypatch.setattr(nbs, "time", fake)
    return fake


def _steps(start: float, step: float):
    count = iter(range(10_000))
    return lambda: start + step * next(count)


# ---- limits

def test_up_to_fifty_notebooks_run_at_once(svc, popen):
    for i in range(51):
        svc.notebooks.save(f"N{i}", CELLS)
    assert len(svc.notebook_batch_start([f"N{i}" for i in range(50)])["items"]) == 50
    with pytest.raises(ValueError, match="Up to 50 notebooks"):
        svc.notebook_batch_start([f"N{i}" for i in range(51)])


def test_parallel_is_two_by_default_and_kept_between_one_and_four(svc, popen):
    assert svc.notebook_batch_start(["A"])["parallel"] == 2
    assert svc.notebook_batch_start(["A"], parallel=9)["parallel"] == 4
    assert svc.notebook_batch_start(["A"], parallel=-3)["parallel"] == 1
    assert svc.notebook_batch_start(["A"], parallel=0)["parallel"] == 1


def test_the_background_run_is_started_with_no_window_and_no_console(svc, popen):
    batch = svc.notebook_batch_start(["A"])
    args, kw = popen[-1]
    assert args == [nbs.sys.executable, "-m", "fabric_desktop.cli", "run-notebooks", batch["id"]]
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert kw["cwd"] == str(svc.root)
    assert kw["stdin"] is kw["stdout"] is kw["stderr"] is subprocess.DEVNULL


# ---- a batch that went quiet

def _beat(svc, batch, beat, state="running"):
    path = svc._batch_file(batch["id"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data |= {"beat": beat, "state": state}
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_batch_is_lost_only_after_ninety_quiet_seconds(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"])
    now = 1_000_000.0
    _clock(monkeypatch, now)
    _beat(svc, batch, now - 90)
    assert svc.notebook_batch(batch["id"])["state"] == "running"  # exactly 90 s: not yet
    _beat(svc, batch, now - 50)
    assert svc.notebook_batch(batch["id"])["state"] == "running"
    _beat(svc, batch, now - 90.5)
    lost = svc.notebook_batch(batch["id"])
    assert lost["state"] == "lost" and lost["items"][0]["status"] == "failed"


def test_a_finished_batch_is_never_lost(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"])
    _clock(monkeypatch)
    _beat(svc, batch, 0, state="done")
    done = svc.notebook_batch(batch["id"])
    assert done["state"] == "done" and done["items"][0]["status"] == "waiting"


# ---- one notebook's process, and Stop

class _Proc:
    """A notebook process that only ends when it is ended from outside."""

    def __init__(self, args, **kw):
        self.args, self.kw = args, kw
        self.ended = False
        self.waits: list[float] = []
        self.returncode = None

    def communicate(self, timeout=None):
        self.waits.append(timeout)
        if self.ended:
            self.returncode = -9
            return "last words\n", ""
        raise subprocess.TimeoutExpired(self.args, timeout)


def test_a_notebook_that_ignores_stop_is_ended_after_thirty_seconds(tmp_path, monkeypatch):
    procs = []
    monkeypatch.setattr(nbs.subprocess, "Popen", lambda args, **kw: procs.append(_Proc(args, **kw)) or procs[-1])
    monkeypatch.setattr(nbs.shell, "end", lambda proc: setattr(proc, "ended", True) or True)
    readings = iter([0.0, 30.0, 30.5])  # Stop seen at 0 s; still running at 30 s; ended at 30.5 s
    _clock(monkeypatch, monotonic=lambda: next(readings))
    stop = tmp_path / "x.stop"
    stop.write_text("now", encoding="utf-8")
    out, code, ended = nbs._run_notebook_process(["python", "-m", "x"], tmp_path, stop)
    assert (out, code, ended) == ("last words\n", -9, True)
    proc = procs[0]
    # two waits of a second each before it is ended (not one: 30 s is not yet past the grace), then 5 s for its output
    assert proc.waits == [nbs.STOP_CHECK_SECONDS, nbs.STOP_CHECK_SECONDS, 5]
    kw = proc.kw
    assert kw["text"] is True and kw["encoding"] == "utf-8" and kw["errors"] == "replace"
    assert kw["stdout"] is kw["stderr"] is subprocess.PIPE and kw["stdin"] is subprocess.DEVNULL
    assert kw["env"]["PYTHONIOENCODING"] == "utf-8" and kw["cwd"] == str(tmp_path)
    assert kw["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_a_notebook_ended_after_stop_is_logged_with_the_grace_it_had(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"], parallel=1)
    monkeypatch.setattr(nbs, "_run_notebook_process", lambda args, root, stop: ("", None, True))
    _clock(monkeypatch, now=time.time(), monotonic=_steps(100.0, 1.234))
    out = svc.run_notebook_batch(batch["id"])
    item = out["items"][0]
    assert item["status"] == "stopped" and item["message"] == "You stopped it while it was running; it was ended 30 s later"
    assert item["seconds"] == 2.5  # read twice after the start: once for the log, once here
    logged = svc.pc_schedules.runs()[0]
    assert (logged["status"], logged["trigger"], logged["seconds"]) == ("stopped", "home", 1.2)


# ---- the batch's own run

def test_the_last_result_line_is_the_notebooks_result(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"])
    out_text = '{"status": "failed", "message": "an earlier line"}\nprint\n{"status": "ok", "message": "1 cell ran"}\n'
    monkeypatch.setattr(nbs, "_run_notebook_process", lambda args, root, stop: (out_text, 0, False))
    item = svc.run_notebook_batch(batch["id"])["items"][0]
    assert (item["status"], item["message"]) == ("ok", "1 cell ran")


def test_each_notebooks_seconds_are_its_own_to_a_tenth(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"], parallel=1)
    monkeypatch.setattr(nbs, "_run_notebook_process", lambda args, root, stop: ('{"status": "ok"}\n', 0, False))
    _clock(monkeypatch, now=time.time(), monotonic=_steps(100.0, 1.234))
    assert svc.run_notebook_batch(batch["id"])["items"][0]["seconds"] == 1.2


def test_notebooks_of_a_batch_run_side_by_side(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A", "B"], parallel=2)
    both = threading.Barrier(2, timeout=10)  # each waits for the other: one at a time would never get past it

    def run(args, root, stop):
        both.wait()
        return '{"status": "ok", "message": "ran"}\n', 0, False

    monkeypatch.setattr(nbs, "_run_notebook_process", run)
    assert [i["status"] for i in svc.run_notebook_batch(batch["id"])["items"]] == ["ok", "ok"]


def test_a_batch_file_without_parallel_runs_one_at_a_time(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"])
    path = svc._batch_file(batch["id"])
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["parallel"]
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(nbs, "_run_notebook_process", lambda args, root, stop: ('{"status": "ok"}\n', 0, False))
    assert svc.run_notebook_batch(batch["id"])["items"][0]["status"] == "ok"


def test_the_heartbeat_waits_ten_seconds_and_saves_only_while_running(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"])
    waits: list[float] = []
    finished = threading.Event()
    beats: list[threading.Thread] = []

    def sleep(seconds):
        waits.append(seconds)
        beats.append(threading.current_thread())
        if len(waits) > 4:
            # Hold the heartbeat's next wake-up until the batch is done, so that it wakes to a done
            # batch whatever the machine's load: it must then save nothing.
            finished.wait(10)
        else:
            time.sleep(0.01)

    _clock(monkeypatch, now=time.time(), sleep=sleep)
    saved: list[str] = []
    real = nbs.jsonfile.write_json
    monkeypatch.setattr(nbs.jsonfile, "write_json", lambda path, data, *a, **kw: (saved.append(data.get("state")),
                                                                                  real(path, data, *a, **kw))[1])

    def slow(args, root, stop):
        deadline = time.monotonic() + 5
        while len(waits) < 5 and time.monotonic() < deadline:  # four beats saved, the fifth held
            time.sleep(0.01)
        return '{"status": "ok"}\n', 0, False

    monkeypatch.setattr(nbs, "_run_notebook_process", slow)
    try:
        svc.run_notebook_batch(batch["id"])
        assert len(waits) >= 4 and set(waits) == {10}
        # running, the item running, beats, the item done, then done
        assert saved.count("running") >= 5 and saved[-1] == "done"
        settled = len(saved)
    finally:
        finished.set()  # the heartbeat wakes, now to a done batch
    beats[-1].join(5)  # the heartbeat thread, which sees the batch is done and ends
    assert not beats[-1].is_alive()
    assert len(saved) == settled  # nothing saved once the batch is done


def test_background_threads_never_hold_the_process_open(svc, popen, monkeypatch):
    made: list[tuple[str | None, bool]] = []

    class Thread(_REAL_THREAD):
        def __init__(self, *a, **kw):
            made.append((kw.get("name"), bool(kw.get("daemon"))))
            super().__init__(*a, **kw)

    monkeypatch.setattr(nbs.threading, "Thread", Thread)
    batch = svc.notebook_batch_start(["A"])
    monkeypatch.setattr(nbs, "_run_notebook_process", lambda args, root, stop: ('{"status": "ok"}\n', 0, False))
    svc.run_notebook_batch(batch["id"])
    monkeypatch.setattr(svc, "_run_notebook_here", lambda name, progress: {"status": "ok", "message": "ran"})
    svc.run_batch_notebook(batch["id"], 0)
    own = [t for t in made if not (t[0] or "").startswith("ThreadPoolExecutor")]  # the pool's threads are joined
    assert own == [(None, True), ("batch-stop", True)]  # the heartbeat, then the Stop watcher


def test_one_notebooks_own_run_logs_its_seconds_to_a_tenth(svc, popen, monkeypatch):
    batch = svc.notebook_batch_start(["A"])
    monkeypatch.setattr(svc, "_run_notebook_here", lambda name, progress: {"status": "ok", "message": "ran"})
    _clock(monkeypatch, now=time.time(), monotonic=_steps(100.0, 1.234))
    row = svc.run_batch_notebook(batch["id"], 0)
    assert (row["status"], row["seconds"]) == ("ok", 1.2)


# ---- old batches

def test_batches_go_after_seven_days(svc, popen, monkeypatch):
    svc.notebook_batch_start(["A"])
    d = svc._batch_dir()
    now = 2_000_000_000
    _clock(monkeypatch, now=float(now))
    files = {name: d / f"{name}.json" for name in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc", "dddddddddddd")}
    ages = {"aaaaaaaaaaaa": 7 * 86400 + 4, "bbbbbbbbbbbb": 7 * 86400, "cccccccccccc": 6 * 86400, "dddddddddddd": 8 * 86400 - 60}
    for name, path in files.items():
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (now - ages[name], now - ages[name]))
    svc._forget_old_batches()
    kept = {name for name, path in files.items() if path.exists()}
    assert kept == {"bbbbbbbbbbbb", "cccccccccccc"}  # exactly seven days old is kept; a few seconds more is not


# ---- Run in Fabric from Home

def test_run_in_fabric_from_home_keeps_fabric_changes_unless_asked(svc, monkeypatch):
    seen = []
    monkeypatch.setattr(svc, "run_notebook_in_fabric", lambda *a, **kw: seen.append(kw) or {"status": "started"})
    svc.notebook_batch_fabric("A")
    assert seen == [{"replace_fabric_changes": False}]
