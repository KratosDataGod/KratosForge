"""The notebook kernel's own process (kernel_proc.py), the paths the main tests don't reach: a process
that never says it's ready or stops answering, a line that isn't a message, a call the engine doesn't
know, an interrupt the cell ignores past the time limit, every request made after the process ended,
and the Windows Job Object and memory calls, with kernel32 faked so they run on any OS.

The child process here is a short script that speaks the kernel's protocol, so nothing runs a cell."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from ctypes import wintypes

from fabric_desktop import kernel_proc
from fabric_desktop.frames import FrameGone
from fabric_desktop.kernel_proc import KernelGone, ProcessKernel

CHILD = r'''
import json, os, sys

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

sys.stdout.write("this line isn't a message\n")
send({"id": "ready", "result": {"pid": os.getpid()}})
for line in sys.stdin:
    message = json.loads(line)
    op = message.get("op")
    if op is None or op == "silent":
        continue
    if op == "ask":  # ask the engine for something, and answer with what it said
        send({"id": "c1", "call": message["args"]["call"], "args": message["args"]["args"]})
        send({"id": message["id"], "result": json.loads(sys.stdin.readline())})
    elif op == "interrupt":
        send({"id": message["id"], "error": {"type": "RuntimeError", "message": "the cell ignores it"}})
    elif op in ("start", "poll"):
        send({"id": message["id"], "result": {"status": "running", "execution_count": 1, "version": 1,
                                              "seconds": 0, "outputs": []}})
    else:
        send({"id": message["id"], "result": {"op": op}})
'''


@pytest.fixture
def child(tmp_path):
    """A ProcessKernel whose process is CHILD."""
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    k = ProcessKernel(command=[sys.executable, str(script)])
    yield k
    k.close()


class Alive:
    """A process that is running, as far as the kernel can tell."""
    pid = 4242

    def poll(self):
        return None


def test_a_line_that_isnt_a_message_is_skipped_and_an_unknown_call_gets_an_error(child):
    assert child._request("ask", call="no_such_call", args=[]) == {
        "id": "c1", "error": {"type": "ValueError", "message": "Unknown call 'no_such_call'"}}
    assert child._request("ask", call="token", args=["storage"])["error"] == {
        "type": "RuntimeError", "message": "tokens aren't available to this kernel."}
    assert child.pid == child._proc.pid  # the ready message came after the line that wasn't one


def test_a_process_that_stops_answering_times_out(child):
    with pytest.raises(KernelGone, match="isn't answering"):
        child._request("silent", timeout=0.2)
    assert child.alive()  # still running: Stop is the person's choice


def test_a_process_that_never_says_its_ready_is_ended(tmp_path, monkeypatch):
    k = ProcessKernel(command=[sys.executable, "-c", "import time; time.sleep(60)"])

    class Quick(threading.Event):
        def wait(self, timeout=None):
            return super().wait(min(timeout or 0.5, 0.5))
    monkeypatch.setattr(kernel_proc, "threading", SimpleNamespace(Event=Quick, Thread=threading.Thread,
                                                                  Lock=threading.Lock, local=threading.local))
    with pytest.raises(KernelGone, match="didn't start"):
        k._ensure()
    proc = k._proc
    assert proc.wait(10) is not None
    k.close()


def test_starting_while_a_cell_runs_is_refused(child):
    assert child.start("while True: pass")["status"] == "running"
    with pytest.raises(RuntimeError, match="already running"):
        child.start("x = 1")
    assert child.stop() == {"stopped": True}


def test_a_cell_that_ignores_the_interrupt_past_the_time_limit_is_ended(child, monkeypatch):
    monkeypatch.setattr(kernel_proc, "WATCH_SECONDS", 0.05)
    monkeypatch.setattr(kernel_proc, "TIME_GRACE_SECONDS", 0.3)
    child.time_limit = 0.2
    child.start("while True: pass")
    proc = child._proc
    proc.wait(15)  # the limit's watcher ends it; the interrupt's error didn't stop the watcher
    deadline = time.monotonic() + 10
    while child._proc is proc and time.monotonic() < deadline:
        time.sleep(0.02)
    snap = child.poll()
    assert child.limit_hit == "time" and snap["status"] == "error"
    assert "didn't stop when interrupted" in snap["outputs"][-1]["evalue"]


def test_a_start_whose_process_ended_mid_answer_waits_for_the_reader_to_say_why(monkeypatch):
    k = ProcessKernel()

    def request(op, **kw):
        if op == "start":
            raise KernelGone("The notebook's Python process stopped.")
    monkeypatch.setattr(k, "_request", request)
    waited = []

    def sleep(seconds):  # the reader thread marks the process gone while start waits
        waited.append(seconds)
        k._gone("The notebook's Python process stopped (exit code 3).")
    monkeypatch.setattr(kernel_proc, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleep))
    snap = k.start("import os; os._exit(3)")
    assert waited == [0.02] and snap["status"] == "error"
    assert "exit code 3" in snap["outputs"][-1]["evalue"]


def test_a_guest_polling_a_session_whose_process_just_ended_sees_it_idle(monkeypatch):
    k = ProcessKernel()
    k._proc = Alive()

    def gone(op, **kw):
        raise KernelGone("The notebook's Python process stopped.")
    monkeypatch.setattr(k, "_request", gone)
    with k.as_guest("Other notebook"):
        assert k.poll(-1, 0.0) == {"status": "idle", "execution_count": 0, "version": 0, "seconds": 0, "outputs": []}
    assert k.interrupt() == {"interrupted": True, "stopped": False}


def test_a_guest_is_forgotten_by_the_running_process_only(monkeypatch):
    k = ProcessKernel()
    assert k.forget_guest("Other") == {"forgotten": False}
    asked = []
    k._proc = Alive()
    monkeypatch.setattr(k, "_request", lambda op, spawn=True, **kw: asked.append((op, spawn, kw, k._guest_name()))
                        or {"forgotten": True})
    with k.as_guest("Other"):
        assert k.forget_guest("Other") == {"forgotten": True}
    assert asked == [("forget_guest", False, {"notebook": "Other"}, None)]  # asked as the session, not the guest


def test_an_ended_cell_polled_at_its_version_sends_no_outputs_again():
    k = ProcessKernel()
    k._last = {"status": "ok", "execution_count": 2, "version": 3, "seconds": 1, "outputs": [{"type": "text"}]}
    assert k.poll(3)["outputs"] is None
    assert k.poll(2)["outputs"] == [{"type": "text"}]


def test_requests_after_the_process_ended_start_nothing():
    k = ProcessKernel()
    assert k.interrupt() == {"interrupted": False}
    assert k.stop() == {"stopped": False}
    assert k.variables() == [] and k.inspect("x") is None and k.reads() == {"tables": {}, "files": {}}
    assert k.variable_detail("df") == {"name": "df", "missing": True}
    assert k.ipywidget_event("m1", {"value": 1})["gone"] is True
    for ask in (lambda: k.frame_page("f"), lambda: k.frame_chart("f", kind="bar"), lambda: k.frame_profile("f"),
                lambda: k.frame_query_profile("f"), lambda: k.frame_wrangle("f", []),
                lambda: k.frame_download("f", "csv", "out.csv")):
        with pytest.raises(FrameGone):
            ask()
    k.unbind_proof()
    assert k._proc is None  # none of them started a process


def test_ending_a_process_that_isnt_the_kernels_does_nothing():
    k = ProcessKernel()
    k._proc = Alive()
    k._end(Alive(), "Stopped: over the limit.")
    assert k._ended == ""


class _Pipe:
    def write(self, text):
        raise OSError("The pipe has been ended")

    def flush(self):
        pass


class _Closed:
    stdin = _Pipe()


def test_a_write_to_an_ended_process_says_its_gone():
    with pytest.raises(KernelGone, match="stopped"):
        ProcessKernel()._send(_Closed(), {"id": "p1"})  # type: ignore[arg-type]


@pytest.mark.parametrize("seconds, said", [(7200, "2 hour"), (90, "1.5 minute"), (30, "30 second")])
def test_durations_read_as_in_the_limit_message(seconds, said):
    assert kernel_proc._duration(seconds) == said


def test_sizes_read_in_mb_or_gb():
    assert kernel_proc._size(512 * 1024 ** 2) == "512 MB" and kernel_proc._size(2 * 1024 ** 3) == "2 GB"


# ---- the process tree


class _Proc:
    pid = 11
    _handle = 9

    def __init__(self):
        self.killed = 0

    def kill(self):
        self.killed += 1


class FakeDLL:
    """kernel32 for any OS: each call records its arguments and returns what ``returns`` says."""

    def __init__(self, **returns):
        self.calls = []
        self.returns = returns

    def __getattr__(self, name):
        if name.startswith("_") or name in ("calls", "returns"):
            raise AttributeError(name)
        fn = _Fn(self, name)
        setattr(self, name, fn)
        return fn


class _Fn:
    """One kernel32 function: typed by the code under test, called with ctypes arguments."""

    def __init__(self, dll, name):
        self.dll, self.name, self.restype, self.argtypes = dll, name, None, None

    def __call__(self, *args):
        self.dll.calls.append((self.name, args))
        value = self.dll.returns.get(self.name, 1)
        return value(*args) if callable(value) else value


def _platform(monkeypatch, name):
    monkeypatch.setattr(kernel_proc, "sys", SimpleNamespace(platform=name))


def test_on_windows_the_tree_is_the_job_and_ending_it_closes_the_job(monkeypatch):
    _platform(monkeypatch, "win32")
    dll = FakeDLL()
    monkeypatch.setattr(kernel_proc, "_kernel32", lambda: dll)
    monkeypatch.setattr(kernel_proc, "_job_for", lambda proc: 7)
    monkeypatch.setattr(kernel_proc, "_job_pids", lambda job: [11, 12] if job == 7 else None)
    proc = _Proc()
    tree = kernel_proc._Tree(proc)  # type: ignore[arg-type]
    assert tree.pids() == [11, 12]
    tree.kill()
    assert [name for name, _ in dll.calls] == ["TerminateJobObject", "CloseHandle"] and proc.killed == 1
    assert tree.job is None and tree.pids() == [11]
    tree.release()
    assert len(dll.calls) == 2  # closed once


def test_without_a_job_or_on_another_os_the_tree_is_the_kernel_process(monkeypatch):
    _platform(monkeypatch, "win32")
    monkeypatch.setattr(kernel_proc, "_job_for", lambda proc: None)
    assert kernel_proc._Tree(_Proc()).pids() == [11]  # type: ignore[arg-type]
    _platform(monkeypatch, "darwin")
    assert kernel_proc._Tree(_Proc()).pids() == [11]  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform == "win32", reason="signals are the Linux and macOS way to end the tree")
def test_ending_a_tree_whose_processes_are_already_gone_still_ends_the_kernel(monkeypatch):
    _platform(monkeypatch, "linux")

    def gone(*a):
        raise ProcessLookupError

    def refused(*a):
        raise PermissionError
    monkeypatch.setattr(kernel_proc, "os", SimpleNamespace(kill=gone, killpg=refused))
    monkeypatch.setattr(kernel_proc, "_descendants", lambda root: [])
    proc = _Proc()
    kernel_proc._Tree(proc).kill()  # type: ignore[arg-type]
    assert proc.killed == 1


def test_without_proc_the_parent_links_come_from_ps(monkeypatch):
    monkeypatch.setattr(kernel_proc, "Path", lambda p: SimpleNamespace(is_dir=lambda: False))
    ran = []

    def run(args, **kw):
        ran.append(args)
        return SimpleNamespace(stdout="    1     0\n   42     1\n   43    42\nnot a pair of ids at all\n")
    monkeypatch.setattr(kernel_proc, "subprocess", SimpleNamespace(run=run, SubprocessError=subprocess.SubprocessError))
    assert kernel_proc._parents() == [(1, 0), (42, 1), (43, 42)]
    assert kernel_proc._descendants(1) == [42, 43]
    assert ran[0] == ["/bin/ps", "-A", "-o", "pid=,ppid="]

    def missing(args, **kw):
        raise FileNotFoundError("ps")
    monkeypatch.setattr(kernel_proc, "subprocess", SimpleNamespace(run=missing, SubprocessError=subprocess.SubprocessError))
    assert kernel_proc._parents() == []


# ---- kernel32 itself, faked


def test_kernel32_is_typed_so_handles_pass_as_pointers(monkeypatch):
    dll = FakeDLL()
    loaded = []
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: loaded.append((name, use_last_error)) or dll,
                        raising=False)
    assert kernel_proc._kernel32.__wrapped__() is dll
    assert dll.CreateJobObjectW.restype is wintypes.HANDLE
    assert dll.AssignProcessToJobObject.argtypes == [wintypes.HANDLE, wintypes.HANDLE]
    assert kernel_proc.win_kernel32.__wrapped__() is dll
    assert dll.OpenProcess.restype is wintypes.HANDLE and dll.CloseHandle.restype is wintypes.BOOL
    assert loaded == [("kernel32", True)] * 2


def test_a_job_kills_on_close_and_holds_the_process_or_is_closed(monkeypatch):
    flags = []

    def set_info(job, kind, info, size):
        flags.append((kind, info._obj.BasicLimitInformation.LimitFlags))
        return True
    dll = FakeDLL(CreateJobObjectW=5, SetInformationJobObject=set_info)
    monkeypatch.setattr(kernel_proc, "_kernel32", lambda: dll)
    assert kernel_proc._job_for(_Proc()) == 5  # type: ignore[arg-type]
    assert flags == [(9, 0x2000)] and ("AssignProcessToJobObject", (5, 9)) in dll.calls

    dll.calls.clear()
    dll.returns["AssignProcessToJobObject"] = 0  # an older Windows that doesn't allow nested jobs
    assert kernel_proc._job_for(_Proc()) is None  # type: ignore[arg-type]
    assert dll.calls[-1] == ("CloseHandle", (5,))

    dll.returns["CreateJobObjectW"] = 0
    assert kernel_proc._job_for(_Proc()) is None  # type: ignore[arg-type]


def test_a_job_list_that_fails_for_another_reason_or_keeps_growing_is_unknown(monkeypatch):
    def other_failure(job, kind, buffer, size, returned):
        buffer._obj.NumberOfAssignedProcesses = 3  # fits: so not ERROR_MORE_DATA
        return False
    monkeypatch.setattr(kernel_proc, "_kernel32", lambda: FakeDLL(QueryInformationJobObject=other_failure))
    assert kernel_proc._job_pids(1) is None
    sizes = []

    def growing(job, kind, buffer, size, returned):
        listed = buffer._obj
        sizes.append(len(listed.ProcessIdList))
        listed.NumberOfAssignedProcesses = len(listed.ProcessIdList) + 1  # always one more than fits
        return False
    monkeypatch.setattr(kernel_proc, "_kernel32", lambda: FakeDLL(QueryInformationJobObject=growing))
    assert kernel_proc._job_pids(1) is None and len(sizes) == 3


def test_windows_memory_is_the_private_bytes_and_the_handle_is_always_closed(monkeypatch):
    _platform(monkeypatch, "win32")

    def counters(handle, buffer, size):
        buffer._obj.PrivateUsage = 123_456_789
        return True
    dll = FakeDLL(OpenProcess=7, K32GetProcessMemoryInfo=counters)
    monkeypatch.setattr(kernel_proc, "win_kernel32", lambda: dll)
    assert kernel_proc.memory_used(11) == 123_456_789
    assert dll.calls[-1] == ("CloseHandle", (7,))
    dll.returns["K32GetProcessMemoryInfo"] = 0
    assert kernel_proc.memory_used(11) is None and dll.calls[-1] == ("CloseHandle", (7,))
    dll.returns["OpenProcess"] = 0  # the process is gone, or this person can't open it
    dll.calls.clear()
    assert kernel_proc.memory_used(11) is None and [n for n, _ in dll.calls] == ["OpenProcess"]


def test_linux_memory_of_a_process_that_isnt_there_is_unknown(monkeypatch):
    _platform(monkeypatch, "linux")
    monkeypatch.setattr(kernel_proc, "Path", lambda p: SimpleNamespace(read_text=lambda: "Name:\tpython\nVmRSS:\tlots kB\n"))
    assert kernel_proc.memory_used(11) is None
