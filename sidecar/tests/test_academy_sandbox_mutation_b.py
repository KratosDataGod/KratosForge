"""More learner-process tests, from a mutation run: the default limits a run reports, the output
cap at its edge, the AppContainer switch and when a run asks for one, the learner library folder
check, and contained() for a trusted program. Scripts run only through sandbox.run, as the lab runs
them; the AppContainer itself is stood in for, so these run on every OS."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from fabric_desktop.academy import sandbox
from fabric_desktop.academy import workspace as ws

SIDECAR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _v1(monkeypatch):
    monkeypatch.setattr(sandbox, "USE_APPCONTAINER", False)


@pytest.fixture
def lab(tmp_path):
    root = tmp_path / "data"
    work = ws.workspace_dir(root, "dp-700", "E37")
    work.mkdir(parents=True)
    (work / "answer.py").write_text("print('hi')\n", encoding="utf-8")
    return root, work


def test_a_run_reports_the_default_limits(lab):
    root, work = lab
    got = sandbox.run(work, "answer.py", root)
    assert got["status"] == "ok" and got["stdout"].strip() == "hi"
    assert got["limits"] == {"seconds": 60.0, "memory_bytes": 1024 ** 3, "cpu_seconds": 120}


def test_a_run_asks_for_an_appcontainer_unless_the_exercise_cant_use_one(lab, monkeypatch):
    root, work = lab
    asked = []
    monkeypatch.setattr(sandbox, "_box", lambda *args: asked.append(args) or None)
    sandbox.run(work, "answer.py", root)
    assert len(asked) == 1
    sandbox.run(work, "answer.py", root, appcontainer_ok=False)
    assert len(asked) == 1


@pytest.mark.parametrize(("value", "on"), [(None, True), ("1", True), ("0", False)])
def test_the_appcontainer_is_on_unless_turned_off(value, on):
    env = {k: v for k, v in os.environ.items() if k != "KRATOSFORGE_ACADEMY_APPCONTAINER"}
    if value is not None:
        env["KRATOSFORGE_ACADEMY_APPCONTAINER"] = value
    out = subprocess.run([sys.executable, "-c", "from fabric_desktop.academy import sandbox; print(sandbox.USE_APPCONTAINER)"],
                         cwd=SIDECAR, env=env, capture_output=True, text=True, timeout=120, check=True).stdout
    assert out.strip() == str(on)


class _Box:
    def __init__(self, *args):
        self.args = args


def test_on_windows_with_appcontainers_a_run_gets_one(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(sandbox, "USE_APPCONTAINER", True)
    monkeypatch.setattr(sandbox.appcontainer, "status", lambda: {"available": True})
    monkeypatch.setattr(sandbox.appcontainer, "Box", _Box)
    box = sandbox._box(tmp_path, ["lib"], tmp_path / "data")
    assert isinstance(box, _Box) and box.args == (tmp_path, sandbox.BOOTSTRAP, ["lib"], tmp_path / "data")
    failed = []

    def refuse(*args):
        raise sandbox.appcontainer.SetupFailed("no profile")
    monkeypatch.setattr(sandbox.appcontainer, "Box", refuse)
    monkeypatch.setattr(sandbox.appcontainer, "_fail", failed.append)
    assert sandbox._box(tmp_path, [], tmp_path / "data") is None and failed == ["no profile"]
    monkeypatch.setattr(sandbox.appcontainer, "status", lambda: {"available": False})
    assert sandbox._box(tmp_path, [], tmp_path / "data") is None


def test_a_learner_library_must_be_a_plain_folder(lab, tmp_path):
    root, work = lab
    (tmp_path / "lib.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ws.Refused, match="^A learner library folder must be a plain folder.$"):
        sandbox._check_lib_root(tmp_path / "lib.txt", work, root)
    with pytest.raises(ws.Refused, match="plain folder"):
        sandbox._check_lib_root(tmp_path / "missing", work, root)
    (tmp_path / "lib").mkdir()
    assert sandbox._check_lib_root(tmp_path / "lib", work, root) == str((tmp_path / "lib").resolve())


@pytest.mark.parametrize(("size", "cut"), [(1024 * 1024 - 1, False), (1024 * 1024, True), (1024 * 1024 + 5, True)])
def test_output_is_kept_up_to_one_mib_and_then_says_it_was_cut(size, cut):
    out = {}
    sandbox._drain(io.BytesIO(b"x" * size), out, "stdout")
    kept = min(size, 1024 * 1024)
    assert out["stdout"] == b"x" * kept + (b"\n[output cut at 1 MB]" if cut else b"")


def test_contained_runs_a_trusted_program_with_the_limits(tmp_path):
    got = sandbox.contained([sys.executable, "-c", "print('ok')"], tmp_path, dict(os.environ), timeout=60,
                            memory_limit=1024 ** 3, cpu_limit=60, processes=1)
    assert got["exit_code"] == 0 and got["stdout"].strip() == b"ok" and got["stderr"] == b""
    assert got["timed_out"] is False and got["memory_hit"] is False


def test_the_posix_group_reports_no_memory_hit():
    assert sandbox._PosixGroup(1, 1).memory_hit() is False


@pytest.mark.skipif(sys.platform == "win32", reason="the resource limits are the Linux and macOS layer")
def test_the_posix_process_runs_under_its_resource_limits(lab):
    root, work = lab
    (work / "answer.py").write_text(
        "import resource as r\n"
        "print([r.getrlimit(getattr(r, n)) for n in ('RLIMIT_DATA', 'RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NOFILE', "
        "'RLIMIT_CORE')])\n", encoding="utf-8")
    got = sandbox.run(work, "answer.py", root, memory_limit=1024 ** 3, cpu_limit=50)
    assert got["status"] == "ok", got["stderr"]
    gib, fsize = 1024 ** 3, 512 * 1024 ** 2
    assert got["stdout"].strip() == str([(gib, gib), (50, 50), (fsize, fsize), (256, 256), (0, 0)])


class _Clock:
    def __init__(self, *times):
        self.times = list(times)

    def monotonic(self):
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]


def test_a_run_reports_its_seconds_to_the_millisecond(lab, monkeypatch):
    root, work = lab
    monkeypatch.setattr(sandbox, "time", _Clock(100.0, 100.12345))
    assert sandbox.run(work, "answer.py", root)["seconds"] == 0.123


class _FakeBox:
    work = None
    closed = 0

    def close(self):
        _FakeBox.closed += 1


def test_a_run_with_an_appcontainer_uses_it_and_closes_it(lab, monkeypatch):
    root, work = lab
    seen, failed = [], []
    _FakeBox.closed = 0
    monkeypatch.setattr(sandbox, "_box", lambda *args: _FakeBox())
    monkeypatch.setattr(sandbox, "_run", lambda *args: seen.append(args[-1]) or {"box": args[-1]})
    got = sandbox.run(work, "answer.py", root)
    assert isinstance(got["box"], _FakeBox) and _FakeBox.closed == 1

    def setup_fails(*args):
        seen.append(args[-1])
        if args[-1] is not None:
            raise sandbox.appcontainer.SetupFailed("no profile")
        return {"box": None}
    monkeypatch.setattr(sandbox, "_run", setup_fails)
    monkeypatch.setattr(sandbox.appcontainer, "_fail", failed.append)
    assert sandbox.run(work, "answer.py", root) == {"box": None}
    assert failed == ["no profile"] and _FakeBox.closed == 2 and seen[-1] is None


def test_the_packages_that_hold_a_sign_in_are_denied_but_the_engine_is_by_its_own_folder(monkeypatch, tmp_path):
    for name in ("fabric_desktop", "msal", "azure"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(sandbox, "read_roots", lambda: [str(tmp_path)])
    got = sandbox.denied_roots(tmp_path / "data")
    assert got[:2] == [str(sandbox.PACKAGE_DIR), str((tmp_path / "data").resolve())]
    assert sorted(got[2:]) == sorted([str(tmp_path / "msal"), str(tmp_path / "azure")])


class _Proc:
    def __init__(self):
        self.calls = []

    def kill(self):
        self.calls.append("kill")

    def close(self):
        self.calls.append("close")


def test_an_appcontainer_process_that_cant_join_its_job_is_ended(tmp_path):
    proc = _Proc()
    box = types.SimpleNamespace(start=lambda argv, env: proc)

    def refuse(_proc):
        raise OSError("not in the job")
    job = types.SimpleNamespace(hold=refuse)
    with pytest.raises(OSError, match="not in the job"):
        sandbox._start(["x"], tmp_path, 1, 1, job, box)
    assert proc.calls == ["kill", "close"]
    ok = types.SimpleNamespace(hold=lambda _proc: None)
    assert sandbox._start(["x"], tmp_path, 1, 1, ok, box) == (proc, ok)


# ---- the Windows Job Object, with kernel32, ntdll and advapi32 stood in for (no real job is made)

class _Api:
    """A callable whose argtypes and restype can be set, as on a ctypes function."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class _Kernel:
    def __init__(self, job=77, last_error=0, set_ok=True, assign_ok=True, in_job=True, in_job_ok=True, peak=0,
                 active=(0,), query_ok=True):
        self.calls, self.job, self.set_ok, self.assign_ok = [], job, set_ok, assign_ok
        self.in_job, self.in_job_ok, self.peak, self.active = in_job, in_job_ok, peak, list(active)
        self.last_error, self.info, self.query_ok = last_error, None, query_ok
        self.CreateJobObjectW = _Api(lambda sa, name: self._log("create", sa is not None, name) or self.job)
        self.SetInformationJobObject = _Api(self._set)
        self.AssignProcessToJobObject = _Api(lambda job, h: self._log("assign", job, h) or self.assign_ok)
        self.IsProcessInJob = _Api(self._is_in)
        self.QueryInformationJobObject = _Api(self._query)
        self.TerminateJobObject = _Api(lambda job, code: self._log("terminate", job, code))
        self.CloseHandle = _Api(lambda h: self._log("close", h))

    def _log(self, *what):
        self.calls.append(what)

    def _set(self, job, cls, ref, size):
        self.info = ref._obj
        self._log("set", job, cls, size)
        return self.set_ok

    def _is_in(self, handle, job, ref):
        self._log("in_job", handle, job)
        ref._obj.value = self.in_job
        return self.in_job_ok

    def _query(self, job, cls, ref, size, _ret):
        self._log("query", job, cls)
        if cls == 9:
            ref._obj.PeakJobMemoryUsed = self.peak
        elif cls == 1:
            ref._obj.ActiveProcesses = self.active.pop(0) if len(self.active) > 1 else self.active[0]
        return self.query_ok


@pytest.fixture
def win(monkeypatch):
    import ctypes

    from fabric_desktop import kernel_proc
    state = types.SimpleNamespace(k=_Kernel(), resume=0, sddl=[], freed=[], convert_ok=True)
    resume = _Api(lambda h: state.resume)

    def convert(sddl, rev, ref, _size):
        state.sddl.append((sddl, rev))
        ref._obj.value = 4242
        return state.convert_ok

    dlls = {"ntdll": types.SimpleNamespace(NtResumeProcess=resume),
            "advapi32": types.SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=_Api(convert))}
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: dlls[name], raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.k.last_error, raising=False)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(
        kernel32=types.SimpleNamespace(LocalFree=lambda p: state.freed.append(p.value))), raising=False)
    monkeypatch.setattr(kernel_proc, "_kernel32", lambda: state.k)
    return state


def test_a_job_sets_its_limits_and_lets_nothing_break_away(win):
    job = sandbox._WinJob(1000, 7, None, processes=3)
    import ctypes
    assert win.k.calls[:2] == [("create", False, None), ("set", 77, 9, ctypes.sizeof(win.k.info))]
    basic = win.k.info.BasicLimitInformation
    assert basic.LimitFlags == 0x2000 | 0x0008 | 0x0200 | 0x0004 | 0x0400
    assert basic.ActiveProcessLimit == 3 and basic.PerJobUserTimeLimit == 7 * 10_000_000
    assert win.k.info.JobMemoryLimit == 1000
    assert (job.job, job.peak, job.emptied) == (77, 0, False)
    sandbox._WinJob(1000, 7, None, processes=0)
    assert win.k.info.BasicLimitInformation.ActiveProcessLimit == 1


def test_a_named_job_can_only_be_queried_and_must_be_new(win):
    sandbox._WinJob(1, 1, "Local\\x")
    assert win.k.calls[0] == ("create", True, "Local\\x")
    assert win.sddl == [("D:P(A;;0x0004;;;OW)", 1)] and win.freed == [4242]
    win.k.last_error = 183   # ERROR_ALREADY_EXISTS: someone else's job of that name
    win.k.calls.clear()
    with pytest.raises(OSError, match="^Windows wouldn't make a job for the exercise"):
        sandbox._WinJob(1, 1, "Local\\x")
    assert ("close", 77) in win.k.calls


def test_the_query_only_rules_are_inherited_by_no_one(win):
    sa = sandbox._query_only_attributes()
    assert (sa.lpSecurityDescriptor, bool(sa.bInheritHandle)) == (4242, False)
    win.convert_ok = False
    with pytest.raises(OSError, match="^Windows wouldn't make the exercise job's access rules"):
        sandbox._query_only_attributes()


@pytest.mark.parametrize(("change", "said"), [
    ({"job": 0}, "^Windows wouldn't make a job for the exercise"),
    ({"set_ok": False}, "^Windows wouldn't set the exercise's limits"),
])
def test_a_job_windows_wont_make_is_an_error(win, change, said):
    for k, v in change.items():
        setattr(win.k, k, v)
    with pytest.raises(OSError, match=said):
        sandbox._WinJob(1, 1, None)
    if change.get("set_ok") is False:
        assert ("close", 77) in win.k.calls


@pytest.mark.parametrize(("change", "said"), [
    ({"assign_ok": False}, "^Windows wouldn't put the exercise in its job"),
    ({"in_job_ok": False}, "^The exercise's process isn't in its job"),
    ({"in_job": False}, "^The exercise's process isn't in its job"),
    ({"resume": 5}, "^The exercise's process couldn't be started"),
])
def test_a_process_is_resumed_only_once_it_is_in_its_job(win, change, said):
    job = sandbox._WinJob(1, 1, None)
    proc = types.SimpleNamespace(_handle=55)
    job.hold(proc)
    assert ("assign", 77, 55) in win.k.calls and ("in_job", 55, 77) in win.k.calls
    for k, v in change.items():
        setattr(win if k == "resume" else win.k, k, v)
    with pytest.raises(OSError, match=said):
        job.hold(proc)


def test_closing_a_job_ends_it_waits_for_it_and_reads_its_peak(win):
    win.k.peak, win.k.active = 980, [2, 1, 0]
    job = sandbox._WinJob(1000, 1, None)
    job.close()   # waits while two, then one, process is still in the job
    assert job.peak == 980 and job.memory_hit() is True and job.emptied is True and job.job is None
    assert ("terminate", 77, 1) in win.k.calls and ("close", 77) in win.k.calls
    assert ("query", 77, 9) in win.k.calls
    job.close()   # once closed, nothing more is asked of Windows
    assert job._peak() == 0


def test_a_job_under_98_percent_of_its_memory_wasnt_hit(win):
    win.k.peak = 979
    job = sandbox._WinJob(1000, 1, None)
    job.close()
    assert job.memory_hit() is False


def test_a_job_that_wont_empty_says_so(win, monkeypatch):
    win.k.active = [1]
    clock = iter([0.0, 0.0, 20.0])
    monkeypatch.setattr(sandbox, "time", types.SimpleNamespace(monotonic=lambda: next(clock, 20.0), sleep=lambda s: None))
    job = sandbox._WinJob(1000, 1, None)
    job.close()
    assert job.emptied is False


def test_the_job_structures_have_windows_layout():
    import ctypes
    ext = sandbox._extended()
    assert ext.IoInfo.size == 6 * 8
    assert ext.ProcessMemoryLimit.offset == ext.IoInfo.offset + 48
    assert ext.JobMemoryLimit.offset == ext.ProcessMemoryLimit.offset + ctypes.sizeof(ctypes.c_size_t)


# ---- what a run reports

@pytest.mark.parametrize(("code", "text", "status"), [
    (3, "isn't in its job", "refused"), (3, "isn't in its AppContainer", "refused"), (3, "other", "error"),
    (1, "isn't in its job", "error"), (0, "isn't in its job", "ok")])
def test_the_bootstraps_own_refusal_is_reported_as_refused(lab, code, text, status):
    root, work = lab
    (work / "answer.py").write_text(f"import sys\nsys.stderr.write({text!r})\nsys.exit({code})\n", encoding="utf-8")
    assert sandbox.run(work, "answer.py", root)["status"] == status


class _Started(Exception):
    pass


class _NoJob:
    def __init__(self, *args):
        self.job = "job"

    def close(self):
        pass


def _argv(monkeypatch, work, root, box):
    seen = []

    def start(argv, *args):
        seen.append(argv)
        raise _Started
    monkeypatch.setattr(sandbox, "_start", start)
    # On Windows _run makes its job first; a stand-in keeps these 1-byte limits from reaching Windows.
    monkeypatch.setattr(sandbox, "_WinJob", _NoJob)
    with pytest.raises(_Started):
        sandbox._run(work, work / "answer.py", root, [], 1, 1, 1, box)
    return seen[0]


def test_the_command_line_compiles_no_bytecode_only_in_an_appcontainer(lab, monkeypatch):
    import json
    root, work = lab
    plain = _argv(monkeypatch, work, root, None)
    assert plain[:4] == [sys.executable, "-I", "-X", "utf8"] and "-B" not in plain
    assert "appcontainer" not in json.loads(plain[-1])
    box = types.SimpleNamespace(work=work, bootstrap=work / "boot.py", share=lambda job: ("shared", job))
    boxed = _argv(monkeypatch, work, root, box)
    assert boxed[:5] == [sys.executable, "-I", "-X", "utf8", "-B"] and str(work / "boot.py") in boxed[-2]
    cfg = json.loads(boxed[-1])
    assert cfg["appcontainer"] is True and cfg["job_handle"][0] == "shared"


def test_read_roots_take_only_site_packages_from_the_path(monkeypatch, tmp_path):
    (tmp_path / "site-packages").mkdir()
    (tmp_path / "other").mkdir()
    monkeypatch.setattr(sys, "path", [str(tmp_path / "site-packages"), str(tmp_path / "other"), "", str(tmp_path / "gone-site-packages")])
    got = sandbox.read_roots()
    assert str(tmp_path / "site-packages") in got and str(tmp_path / "other") not in got
    assert str(tmp_path / "gone-site-packages") not in got and "" not in got


class _Chunks:
    def __init__(self, size, total):
        self.size, self.left = size, total

    def read(self, _n):
        n = min(self.size, self.left)
        self.left -= n
        return b"y" * n


def test_output_in_odd_sized_chunks_is_cut_at_exactly_one_mib():
    out = {}
    sandbox._drain(_Chunks(1000, 1_100_000), out, "stderr")
    assert out["stderr"] == b"y" * (1024 * 1024) + b"\n[output cut at 1 MB]"


@pytest.mark.skipif(sys.platform == "win32", reason="the process group is the Linux and macOS layer")
def test_contained_ends_what_the_program_left_running(tmp_path):
    import time
    code = ("import subprocess, sys\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(p.pid)\n")
    got = sandbox.contained([sys.executable, "-c", code], tmp_path, dict(os.environ), timeout=60,
                            memory_limit=1024 ** 3, cpu_limit=60, processes=2)
    pid = int(got["stdout"].strip())
    deadline = time.monotonic() + 10
    alive = True
    while alive and time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except ProcessLookupError:
            alive = False
    if alive:
        os.kill(pid, 9)
    assert not alive, "a process the program started was still running"


def test_a_job_windows_wont_describe_counts_as_empty_and_unpeaked(win):
    win.k.active, win.k.peak = [3], 999
    job = sandbox._WinJob(1000, 1, None)
    win.k.query_ok = False
    assert job.active() == 0 and job._peak() == 0


class _Popen:
    made: list = []
    procs: list = []

    def __init__(self, argv, **kw):
        _Popen.made.append(kw)
        _Popen.procs.append(self)
        self.calls = []

    def kill(self):
        self.calls.append("kill")


def test_on_windows_the_v1_process_starts_suspended_and_joins_its_job(lab, monkeypatch):
    root, work = lab
    _Popen.made = []
    monkeypatch.setattr(sandbox.subprocess, "Popen", _Popen)
    held = []
    job = types.SimpleNamespace(hold=held.append)
    proc, limiter = sandbox._start(["x"], work, 1, 1, job)
    assert limiter is job and held == [proc] and isinstance(proc, _Popen)
    assert _Popen.made[0]["creationflags"] == 0x00000004 | getattr(sandbox.subprocess, "CREATE_NO_WINDOW", 0)
    assert _Popen.made[0]["stdin"] == sandbox.subprocess.DEVNULL and _Popen.made[0]["cwd"] == str(work)

    def refuse(_proc):
        raise OSError("not in the job")
    with pytest.raises(OSError, match="not in the job"):
        sandbox._start(["x"], work, 1, 1, types.SimpleNamespace(hold=refuse))
    assert _Popen.procs[-1].calls == ["kill"]


@pytest.mark.parametrize(("times", "sleeps"), [([0.0, 10.5], 0), ([0.0, 10.0], 0), ([0.0, 9.9, 10.5], 1)])
def test_closing_waits_up_to_ten_seconds_for_the_job_to_empty(win, monkeypatch, times, sleeps):
    win.k.active = [1]
    clock, slept = list(times), []
    monkeypatch.setattr(sandbox, "time", types.SimpleNamespace(
        monotonic=lambda: clock.pop(0) if len(clock) > 1 else clock[0], sleep=slept.append))
    job = sandbox._WinJob(1000, 1, None)
    job.close()
    assert len(slept) == sleeps and job.emptied is False


def test_output_readers_never_keep_the_app_from_exiting(lab, monkeypatch, tmp_path):
    import threading
    made = []

    def thread(*args, **kw):
        made.append(kw.get("daemon"))
        return threading.Thread(*args, **kw)
    monkeypatch.setattr(sandbox, "threading", types.SimpleNamespace(Thread=thread))
    root, work = lab
    assert sandbox.run(work, "answer.py", root)["status"] == "ok"
    sandbox.contained([sys.executable, "-c", "pass"], tmp_path, dict(os.environ), timeout=60,
                      memory_limit=1024 ** 3, cpu_limit=60, processes=1)
    assert made == [True, True, True, True]


class _WinProc:
    procs: list = []
    fail = False

    def __init__(self, argv, **kw):
        if _WinProc.fail:
            raise OSError("no such program")
        self.kw, self.calls, self.returncode, self._handle = kw, [], 0, 55
        self.stdout, self.stderr = io.BytesIO(b"out"), io.BytesIO(b"")
        _WinProc.procs.append(self)

    def wait(self, timeout=None):
        self.calls.append("wait")
        return 0

    def kill(self):
        self.calls.append("kill")


def test_contained_on_windows_runs_suspended_in_a_job_of_its_own(win, monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(sandbox.subprocess, "Popen", _WinProc)
    _WinProc.procs, _WinProc.fail = [], False
    got = sandbox.contained(["git"], tmp_path, {}, timeout=5, memory_limit=1000, cpu_limit=2, processes=4)
    assert got == {"exit_code": 0, "stdout": b"out", "stderr": b"", "timed_out": False, "memory_hit": False}
    proc = _WinProc.procs[-1]
    assert proc.kw["creationflags"] == 0x00000004 | getattr(sandbox.subprocess, "CREATE_NO_WINDOW", 0)
    assert win.k.info.BasicLimitInformation.ActiveProcessLimit == 4 and ("create", False, None) in win.k.calls
    assert ("terminate", 77, 1) in win.k.calls
    win.k.calls.clear()
    win.k.assign_ok = False
    with pytest.raises(OSError, match="wouldn't put the exercise in its job"):
        sandbox.contained(["git"], tmp_path, {}, timeout=5, memory_limit=1000, cpu_limit=2, processes=4)
    assert _WinProc.procs[-1].calls == ["kill", "wait"] and ("terminate", 77, 1) in win.k.calls
    win.k.calls.clear()
    _WinProc.fail = True
    with pytest.raises(OSError, match="no such program"):
        sandbox.contained(["git"], tmp_path, {}, timeout=5, memory_limit=1000, cpu_limit=2, processes=4)
    assert ("terminate", 77, 1) in win.k.calls
