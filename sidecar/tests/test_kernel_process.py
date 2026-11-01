"""The notebook kernel in a process of its own (Prove stage 4): the same cells, but Stop and
crashes end only that process, and the engine keeps answering."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from test_prove import LAKEHOUSE, CELLS, FakeFabric, call, payload, run_cell, shared_version, svc, wait  # noqa: F401

from fabric_desktop import app_settings, kernel_proc, lakehouse
from fabric_desktop.kernel_proc import STOPPED_TEXT, ProcessKernel
from fabric_desktop.notebook import NotebookStore
from fabric_desktop.server import handle
from fabric_desktop.service import Service

BOOTSTRAP = str(Path(__file__).with_name("kernel_bootstrap.py"))


@pytest.fixture
def kernel():
    k = ProcessKernel(token=lambda audience: f"token-for-{audience}")
    yield k
    k.close()


def until_done(k, timeout=30):
    deadline = time.monotonic() + timeout
    snap = k.poll(-1, 0.5)
    while snap["status"] == "running" and time.monotonic() < deadline:
        snap = k.poll(-1, 0.5)
    return snap


def text(snap):
    return "".join(o.get("text", "") + o.get("evalue", "") for o in snap.get("outputs") or [])


def test_cells_run_in_another_process_with_the_same_kernel(kernel):
    out = kernel.run("import os, polars as pl\nx = 41\nprint(os.getpid())\npl.DataFrame({'a': [1, 2]})")
    assert out["status"] == "ok", out
    assert int(out["outputs"][0]["text"]) == kernel.pid != os.getpid()
    assert out["outputs"][1]["type"] == "table"
    assert kernel.run("x + 1")["outputs"] == [{"type": "text", "text": "42"}]
    assert any(v["name"] == "x" for v in kernel.variables())
    kernel.run("xylophone = 1")
    assert {"label": "xylophone", "type": "variable"} in kernel.complete("xylo", 4)["items"]
    # notebookutils asks the engine for tokens
    out = kernel.run("import notebookutils\nnotebookutils.credentials.getToken('storage')")
    assert out["outputs"] == [{"type": "text", "text": "'token-for-storage'"}]


def test_the_persons_own_kernel_keeps_their_environment(monkeypatch):
    """Only external-tool runs get the allowlisted environment (HD-012); the person's own
    notebooks see the variables they set, as they expect."""
    monkeypatch.setenv("FABRIC_DESKTOP_TEST_SECRET", "theirs")
    k = ProcessKernel()
    try:
        out = k.run("import os\nos.environ.get('FABRIC_DESKTOP_TEST_SECRET')")
    finally:
        k.close()
    assert out["outputs"] == [{"type": "text", "text": "'theirs'"}]


def test_a_process_that_ends_leaves_no_pipe_open(kernel):
    """Popen leaves its pipes to the garbage collector; the app starts a process per restart, so
    each one that ended kept two handles open until a collection happened to run."""
    def closed(proc, timeout=10):
        deadline = time.monotonic() + timeout
        while not (proc.stdin.closed and proc.stdout.closed) and time.monotonic() < deadline:
            time.sleep(0.02)
        return proc.stdin.closed and proc.stdout.closed

    kernel.run("x = 1")
    stopped = kernel._proc
    assert kernel.stop() == {"stopped": True}
    assert closed(stopped)
    kernel.run("y = 1")  # a fresh process
    crashed = kernel._proc
    assert crashed is not stopped
    kernel.start("import os\nos._exit(3)")
    assert closed(crashed)
    assert kernel.run("1 + 1")["outputs"] == [{"type": "text", "text": "2"}]  # and the next one still starts


def test_a_write_stuck_holding_the_send_lock_doesnt_stop_the_process_being_marked_gone(kernel, monkeypatch):
    """If a request's write never returns (something the process started holds the pipe open and
    doesn't read), the reader still closes stdout and marks the process gone, leaving stdin."""
    monkeypatch.setattr(kernel_proc, "CLOSE_WAIT_SECONDS", 0.2)
    kernel.run("x = 1")
    proc = kernel._proc
    kernel.start("import os, time\ntime.sleep(0.5)\nos._exit(3)")
    assert kernel._send_lock.acquire(timeout=5)  # as a _send stuck mid-write would hold it
    try:
        deadline = time.monotonic() + 10
        while kernel._proc is proc and time.monotonic() < deadline:
            time.sleep(0.02)
        assert kernel._proc is not proc  # marked gone without the lock
        assert proc.stdout.closed and not proc.stdin.closed
    finally:
        kernel._send_lock.release()
    assert kernel.run("1 + 1")["outputs"] == [{"type": "text", "text": "2"}]


def test_cancel_keeps_variables_and_stop_ends_only_the_process(kernel):
    kernel.run("kept = 1")
    kernel.start("import time\nwhile True:\n    time.sleep(0.05)")
    time.sleep(0.5)
    assert kernel.interrupt()["interrupted"] is True
    assert until_done(kernel)["status"] == "cancelled"
    assert kernel.run("kept")["outputs"] == [{"type": "text", "text": "1"}]
    # A cell stuck in one long call can't be interrupted; Stop ends its process, not the engine.
    pid = kernel.pid
    kernel.start("import time\ntime.sleep(600)")
    time.sleep(0.3)
    started = time.monotonic()
    assert kernel.stop() == {"stopped": True}
    assert time.monotonic() - started < 10
    snap = kernel.poll(-1, 0)
    assert snap["status"] == "cancelled" and STOPPED_TEXT in text(snap)
    out = kernel.run("'kept' in dir()")
    assert out["outputs"] == [{"type": "text", "text": "False"}] and kernel.pid != pid


def described(pid):
    """What a process that should have ended is doing, for a failure message."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return f"{pid}: no /proc entry"
    keep = ("Name", "State", "PPid", "SigPnd", "ShdPnd", "SigBlk")
    return f"{pid}: pgid {os.getpgid(pid)}; " + "; ".join(line for line in status.splitlines() if line.split(":")[0] in keep)


def alive(pid):
    """Whether process ``pid`` is still running. A zombie waiting to be reaped counts as ended, and so
    does one being reaped ("X (dead)"), which comes after "Z": treating it as running let the check
    see the process end and then come back."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if not Path("/proc").is_dir():
        return True  # macOS: the signal is all there is to go on
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
        state = next((line for line in lines if line.startswith("State:")), "State:\tX")
        return state.split()[1] not in ("Z", "X")
    except (FileNotFoundError, ProcessLookupError):
        return False  # a zombie reaped between the signal and the read: it ended


def test_stop_ends_the_processes_a_cell_started_too(kernel):
    """HD-037 (EN-8): Stop used to end only the kernel, leaving a process its cell started running."""
    kernel.start("import subprocess, sys, time\n"
                 "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                 "print(child.pid, flush=True)\ntime.sleep(120)")
    deadline = time.monotonic() + 30
    pid = None
    while pid is None and time.monotonic() < deadline:
        out = text(kernel.poll(-1, 0.5)).strip()
        pid = int(out) if out.isdigit() else None
    assert pid and alive(pid)
    assert kernel.stop() == {"stopped": True}
    deadline = time.monotonic() + 10
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid), described(pid)


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows the Job Object holds every process the kernel starts")
@pytest.mark.parametrize("how", ["start_new_session=True", "process_group=0"])
def test_stop_ends_a_process_that_left_the_kernels_group(kernel, how):
    """A cell's process in its own session or group (a server it started) outlived Stop."""
    kernel.start("import subprocess, sys, time\n"
                 f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], {how})\n"
                 "grand = subprocess.Popen([sys.executable, '-c', 'import subprocess, sys, time; "
                 "p = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(120)\"], start_new_session=True); "
                 "print(p.pid, flush=True); time.sleep(120)'], stdout=subprocess.PIPE, text=True)\n"
                 "print(child.pid, grand.pid, grand.stdout.readline().strip(), flush=True)\ntime.sleep(120)")
    deadline = time.monotonic() + 30
    pids = []
    while not pids and time.monotonic() < deadline:
        words = text(kernel.poll(-1, 0.5)).split()
        pids = [int(w) for w in words] if len(words) == 3 and all(w.isdigit() for w in words) else []
    assert pids and all(alive(p) for p in pids)
    assert os.getpgid(pids[0]) != os.getpgid(kernel.pid), "the child left the kernel's group"
    assert kernel.stop() == {"stopped": True}
    deadline = time.monotonic() + 10
    while any(alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(alive(p) for p in pids), [described(p) for p in pids]


def test_a_crash_ends_the_cell_with_a_reason_and_the_next_cell_gets_a_fresh_kernel(kernel):
    kernel.start("import os\nos._exit(3)")
    snap = until_done(kernel)
    assert snap["status"] == "error" and "exit code 3" in text(snap)
    assert kernel.run("1 + 1")["outputs"] == [{"type": "text", "text": "2"}]


def test_other_notebooks_and_the_managed_table_check_come_from_the_engine(tmp_path, monkeypatch):
    store = NotebookStore(tmp_path / "nb")
    store.save("helpers", [{"type": "code", "source": "def double(x):\n    return 2 * x"}])
    k = ProcessKernel(load_notebook=lambda name: store.load(name)["cells"])
    try:
        assert k.run("%run helpers\ndouble(21)")["outputs"][-1] == {"type": "text", "text": "42"}
        lakehouse.attach(lakehouse.Attached(workspace_id="ws", lakehouse_id="lh", name="LH", storage_token=lambda: "t",
                                            work_dir=tmp_path))
        monkeypatch.setattr(lakehouse, "write_guard", lambda name, schema, table: f"{name}.{schema}.{table} is managed"
                            if table == "managed" else None)
        out = k.run("import polars as pl\nfrom fabric_desktop import lakehouse\n"
                    "lakehouse.write_table(pl.DataFrame({'a': [1]}), 'dbo.managed')")
        assert out["status"] == "error" and "LH.dbo.managed is managed" in json.dumps(out["outputs"])
    finally:
        lakehouse.attach(None)
        k.close()


def test_the_setting_switches_the_kernel_and_stop_uses_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    assert s.kernel_info() == {"process": False, "pid": os.getpid()}
    assert call(s, "stop_kernel_process") == {"stopped": False, "in_engine": True}
    call(s, "save_app_settings", changes={"kernel_process": True})
    assert app_settings.value("kernel_process") is True
    try:
        call(s, "start_cell", code="import time\ntime.sleep(600)")
        info = s.kernel_info()
        assert info["process"] is True and info["pid"] != os.getpid()
        out = handle(s, json.dumps({"id": 1, "method": "save_app_settings", "params": {"changes": {"kernel_process": False}}}))
        assert "running" in out["error"]["message"] if isinstance(out.get("error"), dict) else "running" in str(out.get("error"))
        assert call(s, "stop_kernel_process") == {"stopped": True}
        assert call(s, "poll_cell", version=-1)["status"] == "cancelled"
        assert run_cell(s, "2 * 21")["outputs"] == [{"type": "text", "text": "42"}]
    finally:
        s.kernel.close()


def test_a_proof_runs_the_pc_side_in_the_kernel_process(svc):  # noqa: F811
    svc.fake = FakeFabric(svc)
    tz_before = os.environ.get("TZ")
    kernel = ProcessKernel(token=svc._token_for, command=[sys.executable, BOOTSTRAP, str(svc.lh_root)])
    svc.sessions.put(payload()["name"], kernel)  # the proved notebook's own session
    try:
        before = shared_version(svc)
        out = wait(svc, call(svc, "prove_start", **payload())["id"])
        assert out["state"] == "done", out["message"]
        assert out["verdict"]["verdict"] == "same", out["verdict"]
        assert shared_version(svc) == before
        # The PC side ran in the kernel's process, with the proof bound there.
        assert out["inputs"]["files"][0]["path"] == "Files/raw/rates.csv"
        assert out["pc"]["cells"]["4"]["outputs"][0]["text"].strip() == "5"
        # The time zone was fixed in the kernel's process only; the engine's is as it was.
        assert os.environ.get("TZ") == tz_before
        # PB-094: the PC side's CPU and memory are the kernel process's, not the engine's.
        assert out["pc"]["measured"] == "the notebook's own kernel process on this PC"
        assert "kernel process" in " ".join(out["scorecard"]["notes"])
    finally:
        kernel.close()


@pytest.fixture
def quick_limits(monkeypatch):
    from fabric_desktop import kernel_proc
    monkeypatch.setattr(kernel_proc, "WATCH_SECONDS", 0.1)
    monkeypatch.setattr(kernel_proc, "TIME_GRACE_SECONDS", 1.0)


def test_a_cell_past_the_time_limit_is_interrupted_and_keeps_variables(kernel, quick_limits):
    kernel.run("kept = 1")
    kernel.time_limit = 1.0
    kernel.start("import time\nwhile True:\n    time.sleep(0.05)")
    snap = until_done(kernel)
    assert snap["status"] == "error" and "ran longer than the 1 second limit" in text(snap)
    assert "ran longer" in text(kernel.poll(-1, 0)), "every later look at the cell still says why it stopped"
    assert kernel.limit_hit == "time"
    assert kernel.run("kept")["outputs"] == [{"type": "text", "text": "1"}]
    # Run again without the limit: this one cell isn't stopped.
    kernel.start("import time\ntime.sleep(1.6)\n'done'", time_limit=False)
    snap = until_done(kernel)
    assert snap["status"] == "ok" and kernel.limit_hit == ""


def test_a_cell_that_ignores_the_interrupt_has_its_process_ended(kernel, quick_limits):
    kernel.run("kept = 1")
    pid = kernel.pid
    # The cell swallows the interrupt wherever it lands once it has printed, so the outcome doesn't
    # depend on timing. The limit is set only once the cell is inside its try, or a slow machine
    # could interrupt it before, and the cell would just stop.
    kernel.time_limit = 0
    kernel.start("import time\nwhile True:\n    try:\n        print('in', flush=True)\n        time.sleep(600)\n"
                 "    except KeyboardInterrupt:\n        pass")
    deadline = time.monotonic() + 30
    while "in" not in text(kernel.poll(-1, 0.2)) and time.monotonic() < deadline:
        pass
    kernel.time_limit = 0.5
    snap = until_done(kernel)
    assert snap["status"] == "error" and "didn't stop when interrupted" in text(snap)
    kernel.time_limit = None  # the check runs in a fresh process, whose start a slow runner can take past 0.5 s
    out = kernel.run("'kept' in dir()")
    assert out["outputs"] == [{"type": "text", "text": "False"}] and kernel.pid != pid


def test_a_poll_as_the_process_ends_reports_the_ended_cell_not_a_fresh_process(kernel):
    kernel.run("kept = 1")
    kernel.start("import time\ntime.sleep(600)")
    proc, pid = kernel._proc, kernel.pid
    kernel._end(proc, "Stopped: over the limit.")  # what the limits watcher does
    proc.wait()
    real, looks = proc.poll, iter([None])
    proc.poll = lambda: next(looks, real())  # the first check still sees it running: it ends just after
    snap = until_done(kernel)
    assert snap["status"] == "error" and "over the limit" in text(snap)
    assert kernel.pid == pid, "a poll doesn't start a process; the next cell does"


@pytest.mark.skipif(not sys.platform.startswith(("linux", "win")), reason="memory is read on Windows and Linux")
def test_a_notebook_over_the_memory_limit_has_its_process_ended(kernel, quick_limits):
    from fabric_desktop.kernel_proc import memory_used
    assert memory_used(os.getpid()) > 10 * 1024 ** 2
    kernel.run("kept = 1")
    kernel.memory_limit = (memory_used(kernel.pid) or 0) + 200 * 1024 ** 2
    kernel.start("import time\nblock = bytearray(400 * 1024 ** 2)\ntime.sleep(30)")
    snap = until_done(kernel)
    assert snap["status"] == "error" and "the memory limit" in text(snap) and kernel.limit_hit == "memory"
    assert kernel.run("'kept' in dir()")["outputs"] == [{"type": "text", "text": "False"}]


@pytest.mark.skipif(not sys.platform.startswith(("linux", "win")), reason="memory is read on Windows and Linux")
def test_the_memory_limit_counts_the_processes_a_cell_starts(kernel, quick_limits):
    from fabric_desktop.kernel_proc import memory_used
    kernel.run("kept = 1")
    kernel.memory_limit = (memory_used(kernel.pid) or 0) + 200 * 1024 ** 2
    child = "import time; block = b'x' * (400 * 1024 ** 2); time.sleep(60)"
    kernel.start(f"import subprocess, sys\nsubprocess.run([sys.executable, '-c', {child!r}])")
    snap = until_done(kernel)
    assert snap["status"] == "error" and "the memory limit" in text(snap) and kernel.limit_hit == "memory"
    assert kernel.run("'kept' in dir()")["outputs"] == [{"type": "text", "text": "False"}]


def test_the_memory_limit_reads_the_interpreter_the_process_reported(kernel, quick_limits, monkeypatch):
    """From a Windows venv, Popen's pid is the venv launcher (a few MB) and the interpreter is its
    child: the limit must read the pid the kernel reported, not proc.pid (#896's Windows trial)."""
    from fabric_desktop import kernel_proc
    kernel.run("kept = 1")
    launcher = kernel._proc.pid
    kernel.pid = interpreter = launcher + 100_000  # what a venv launcher's child would report
    monkeypatch.setattr(kernel_proc, "memory_used", lambda pid: 10 * 1024 ** 3 if pid == interpreter else 5 * 1024 ** 2)
    # The limit goes on once the cell is running: set before, the watcher could end the process before
    # start, which would then run the cell in a fresh process under its real pid and never hit the limit.
    kernel.start("import time\ntime.sleep(30)")
    kernel.memory_limit = 1024 ** 3
    snap = until_done(kernel)
    assert snap["status"] == "error" and "the memory limit" in text(snap) and kernel.limit_hit == "memory"


def test_the_limits_come_from_settings_or_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    call(s, "save_app_settings", changes={"kernel_process": True, "cell_time_limit_minutes": 60, "kernel_memory_limit_gb": 8})
    try:
        assert (s.kernel.time_limit, s.kernel.memory_limit) == (3600, 8 * 1024 ** 3)
        call(s, "save_app_settings", changes={"cell_time_limit_minutes": 0})
        assert s.kernel.time_limit is None and app_settings.load()["cell_time_limit_minutes"]["source"] == "default"
        for bad in (-1, "lots", 10 ** 9):
            with pytest.raises(ValueError):
                app_settings.save({"kernel_memory_limit_gb": bad})
        policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(json.dumps({"kernel_memory_limit_gb": 4}))
        with pytest.raises(ValueError, match="Your organization sets"):
            app_settings.save({"kernel_memory_limit_gb": 16})
        s.kernel.close()
        s.kernel = s._new_kernel()
        assert s.kernel.memory_limit == 4 * 1024 ** 3
        # A policy value out of range or of the wrong kind is ignored, and Help and about says why.
        for bad in (10 ** 9, True):
            policy.write_text(json.dumps({"kernel_memory_limit_gb": bad}))
            assert app_settings.load()["kernel_memory_limit_gb"]["source"] != "policy"
            assert any("kernel_memory_limit_gb" in p for p in app_settings.policy_problems())
    finally:
        s.kernel.close()


def test_a_proof_stopped_by_a_limit_says_so():
    from fabric_desktop.prove_service import _verdict
    record = {"pc": {"status": "failed", "stopped_by": "the time limit"}, "fabric": {"status": "ok"}}
    assert _verdict(record, [], [])["text"] == ("The run failed on this PC (stopped by the time limit), "
                                               "so the results can't be compared.")


def test_hover_and_signature_help_come_from_the_notebook_process(kernel):
    assert kernel.inspect("x", 1) is None  # no process is started just to hover
    kernel.start("def area(w, h=1):\n    'Width times height.'\n    return w * h")
    assert until_done(kernel)["status"] == "ok"
    hover = kernel.inspect("area(2)", 1)
    assert hover["signature"] == "(w, h=1)" and hover["doc"] == "Width times height."
    sig = kernel.inspect("area(2, ", call=True)
    assert sig["params"] == ["w", "h=1"] and sig["active"] == 1


def test_variable_detail_comes_from_the_kernel_process(kernel):
    assert kernel.variable_detail("x") == {"name": "x", "missing": True}  # no process yet
    out = kernel.run("import polars as pl\nframe = pl.DataFrame({'a': [1]})\nitems = [1, 'two']")
    assert out["status"] == "ok", out
    assert kernel.variable_detail("frame")["columns"] == [{"name": "a", "type": "Int64"}]
    assert [i["value"] for i in kernel.variable_detail("items")["items"]] == ["1", "'two'"]


def test_stop_restart_and_close_delete_the_processs_output_files(kernel):
    big = "print('x' * 1_100_000)"
    assert kernel.run(big)["status"] == "ok"
    folder = kernel.output_dir
    assert folder is not None and len(list(folder.glob("kratosforge-output-*.txt"))) == 1
    kernel.stop()
    assert not list(folder.glob("*"))
    kernel.run(big)
    assert len(list(folder.glob("*"))) == 1
    kernel.restart()
    assert not list(folder.glob("*"))
    kernel.run(big)
    kernel.close()
    assert not folder.exists() and kernel.output_dir is None


def test_the_run_meter_measures_the_kernel_process_not_the_engine(kernel):
    """PB-094: with notebooks in their own process, a run's CPU and memory are that process's."""
    from fabric_desktop.measure import RunMeter, cpu_seconds
    kernel.run("x = 1")  # started, so its start-up isn't part of this run
    before = cpu_seconds(kernel.pid)
    engine_before = time.process_time()
    with RunMeter(interval=0.05, process=lambda: kernel.live_pid) as meter:
        out = kernel.run("import time\nheld = bytearray(300 * 1024 * 1024)\n"
                         "end = time.process_time() + 1.5\nwhile time.process_time() < end:\n    pass\nprint('ok')")
    after = cpu_seconds(kernel.pid)
    assert text(out).strip() == "ok"
    result = meter.result()
    # The kernel's own CPU time, as the process reports it, and none of the engine's.
    assert result["cpu_seconds"] >= 1.4 and abs(result["cpu_seconds"] - (after - before)) < 0.2
    assert time.process_time() - engine_before < 1.0
    assert result["peak_memory_mb"] >= 300 and result["measured"] == "the notebook's own kernel process on this PC"


def test_a_kernel_that_starts_during_the_run_counts_from_its_start(kernel):
    from fabric_desktop.measure import RunMeter, cpu_seconds
    kernel.restart()
    with RunMeter(interval=0.05, process=lambda: kernel.live_pid) as meter:
        kernel.run("end = __import__('time').process_time() + 0.5\nwhile __import__('time').process_time() < end:\n    pass")
    assert meter.processes == 1 and meter.cpu >= 0.5 and meter.cpu <= cpu_seconds(kernel.pid) + 0.05


def test_a_stopped_or_restarted_kernel_isnt_measured_before_its_next_start(kernel):
    """The id of an ended process (which the OS may give another process) is never measured."""
    from fabric_desktop.measure import RunMeter
    kernel.run("x = 1")
    pid = kernel.pid
    assert kernel.live_pid == pid
    for end in (kernel.stop, kernel.restart):
        end()
        assert kernel.live_pid is None and kernel.pid == pid  # the last id is still there, not measured
        with RunMeter(interval=0.05, process=lambda: kernel.live_pid) as meter:
            time.sleep(0.2)
        assert meter.processes == 0 and meter.cpu == 0 and meter.result()["peak_memory_mb"] is None
    kernel.run("x = 2")
    assert kernel.live_pid == kernel.pid != pid


def test_a_job_with_more_processes_than_the_list_holds_is_read_again_bigger(monkeypatch):
    """QueryInformationJobObject fails with ERROR_MORE_DATA when the list is too small, but says how
    many processes the job has: the list is read again that big rather than given up on."""
    from fabric_desktop import kernel_proc
    pids = list(range(1, kernel_proc.JOB_PIDS + 51))
    calls = []

    class Kernel32:
        @staticmethod
        def QueryInformationJobObject(job, kind, buffer, size, _returned):  # noqa: N802
            listed = buffer._obj  # the JOBOBJECT_BASIC_PROCESS_ID_LIST passed by reference
            room = len(listed.ProcessIdList)
            calls.append(room)
            listed.NumberOfAssignedProcesses = len(pids)
            if room < len(pids):
                return False  # ERROR_MORE_DATA
            listed.NumberOfProcessIdsInList = len(pids)
            for i, pid in enumerate(pids):
                listed.ProcessIdList[i] = pid
            return True

    monkeypatch.setattr(kernel_proc, "_kernel32", lambda: Kernel32)
    assert kernel_proc._job_pids(1) == pids
    assert calls[0] == kernel_proc.JOB_PIDS and calls[1] >= len(pids)
