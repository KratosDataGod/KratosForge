"""Measuring a run (measure.py): what the meter reads from /proc, and what it reports when it can't read
something, a kernel restarts mid-run, or a sample fails. The kernel processes are stood in for, so the
numbers are exact."""

import io
import os
import subprocess
import sys
import threading

import pytest

from fabric_desktop import kernel_proc, measure

linux_only = pytest.mark.skipif(not os.path.exists("/proc/self/statm"), reason="reads /proc")


# --------------------------------------------------------------------------- reading this PC

@linux_only
def test_this_processs_memory_is_its_resident_pages():
    with open("/proc/self/statm", encoding="ascii") as f:
        pages = int(f.read().split()[1])
    assert abs(measure.rss_bytes() - pages * os.sysconf("SC_PAGE_SIZE")) < 64 * 1024 ** 2


@linux_only
def test_memory_that_cant_be_read_is_none(monkeypatch):
    def refuse(*a, **kw):
        raise PermissionError("no /proc here")
    monkeypatch.setattr(measure, "open", refuse, raising=False)
    assert measure.rss_bytes() is None


@linux_only
def test_cpu_time_is_user_plus_system_and_survives_a_name_with_spaces_and_brackets(monkeypatch):
    fields = ["S"] + ["0"] * 10 + ["250", "150"] + ["0"] * 30  # state, then utime and stime at 11 and 12
    stat = "4242 (my (odd) kernel) " + " ".join(fields)
    monkeypatch.setattr(measure, "open", lambda path, encoding=None: io.StringIO(stat), raising=False)
    assert measure.cpu_seconds(4242) == pytest.approx(400 / os.sysconf("SC_CLK_TCK"))


@linux_only
def test_cpu_time_of_a_process_that_ended_is_none():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert measure.cpu_seconds(child.pid) is None


@linux_only
def test_cpu_time_of_a_live_process_is_read():
    assert measure.cpu_seconds(os.getpid()) >= 0


# --------------------------------------------------------------------------- the meter with a kernel process

class Kernels:
    """Which kernel process runs, and the CPU seconds and memory each has used."""

    def __init__(self, monkeypatch, pid=None):
        self.pid, self.cpu, self.memory = pid, {}, {}
        monkeypatch.setattr(measure, "cpu_seconds", lambda pid: self.cpu.get(pid))
        monkeypatch.setattr(kernel_proc, "memory_used", lambda pid: self.memory.get(pid))

    def process(self):
        return self.pid


def test_a_kernel_restart_counts_the_first_from_the_runs_start_and_the_second_whole(monkeypatch):
    k = Kernels(monkeypatch, pid=100)
    k.cpu, k.memory = {100: 10.0}, {100: 200 * 1024 ** 2}
    meter = measure.RunMeter(interval=3600, process=k.process)
    with meter:
        k.cpu[100] = 12.5
        meter._read()
        k.pid = 200  # the kernel restarted
        k.cpu[200], k.memory[200] = 3.0, 500 * 1024 ** 2
        meter._read()
        k.cpu[200] = 4.0
    out = meter.result()
    assert out["cpu_seconds"] == 6.5 and meter.processes == 2  # 2.5 of the first, all 4 of the second
    assert out["start_memory_mb"] == 200.0 and out["peak_memory_mb"] == 500.0
    assert out["measured"] == "the notebook's own kernel process on this PC"


def test_a_kernel_that_never_starts_used_no_cpu_and_no_memory(monkeypatch):
    k = Kernels(monkeypatch, pid=None)
    meter = measure.RunMeter(interval=3600, process=k.process)
    with meter:
        pass
    out = meter.result()
    assert out["cpu_seconds"] == 0 and out["peak_memory_mb"] is None and out["start_memory_mb"] is None
    assert meter.processes == 0


def test_a_kernel_that_starts_mid_run_counts_all_its_cpu(monkeypatch):
    k = Kernels(monkeypatch, pid=None)
    meter = measure.RunMeter(interval=3600, process=k.process)
    with meter:
        k.pid, k.cpu[7] = 7, 1.25
        meter._read()
        k.cpu[7] = 2.0
    assert meter.result()["cpu_seconds"] == 2.0


def test_a_kernel_whose_cpu_cant_be_read_keeps_its_last_reading(monkeypatch):
    k = Kernels(monkeypatch, pid=5)
    k.cpu = {5: 1.0}
    meter = measure.RunMeter(interval=3600, process=k.process)
    with meter:
        k.cpu[5] = 3.0
        meter._read()
        del k.cpu[5]  # it ended before the last sample
    assert meter.result()["cpu_seconds"] == 2.0


def test_a_process_lookup_that_fails_measures_nothing_rather_than_the_engine(monkeypatch):
    k = Kernels(monkeypatch)

    def broken():
        raise RuntimeError("kernel table locked")
    monkeypatch.setattr(measure, "rss_bytes", lambda: 999 * 1024 ** 2)  # the engine's: never reported for a kernel run
    meter = measure.RunMeter(interval=3600, process=broken)
    with meter:
        pass
    out = meter.result()
    assert out["peak_memory_mb"] is None and out["cpu_seconds"] == 0
    assert k.cpu == {}


# --------------------------------------------------------------------------- the meter on the engine

def test_without_a_kernel_the_engine_process_is_measured(monkeypatch):
    readings = iter([100, 300, 200, 150])
    monkeypatch.setattr(measure, "rss_bytes", lambda: next(readings, 150) * 1024 ** 2)
    meter = measure.RunMeter(interval=3600)
    with meter:
        meter._read()
        meter._read()
    out = meter.result()
    assert out["start_memory_mb"] == 100.0 and out["peak_memory_mb"] == 300.0
    assert out["measured"].startswith("the engine process") and out["cpu_seconds"] >= 0 and meter.processes == 0


def test_a_failed_sample_is_skipped_and_sampling_goes_on(monkeypatch):
    monkeypatch.setattr(measure, "rss_bytes", lambda: 10 * 1024 ** 2)
    meter = measure.RunMeter(interval=0.01)
    calls, done = [0], threading.Event()
    real = meter._read

    def flaky(first=False):
        calls[0] += 1
        if calls[0] == 2:  # the sampler's first sample (the first read is __enter__'s)
            raise OSError("sample lost")
        if calls[0] >= 4:
            done.set()
        return real(first)
    meter._read = flaky
    with meter:
        assert done.wait(5)
    assert meter.samples >= 3 and meter.result()["peak_memory_mb"] == 10.0


# --------------------------------------------------------------------------- the Windows reads, on any OS

class Win:
    """kernel32 and psapi as the meter calls them: one process's working set and CPU times."""

    def __init__(self):
        self.closed, self.working_set, self.memory_ok = [], 300 * 1024 ** 2, True
        self.times = {1234: (0, 0, 30_000_000, 12_500_000)}  # created, exited, kernel, user (100 ns units)

    # kernel32
    def GetCurrentProcess(self):
        return -1

    def OpenProcess(self, access, inherit, pid):
        assert access == kernel_proc.PROCESS_QUERY_LIMITED_INFORMATION
        return 77 if pid in self.times else 0

    def GetProcessTimes(self, handle, *refs):
        if handle != 77:
            return 0
        for ref, value in zip(refs, self.times[1234], strict=True):
            ref._obj.dwLowDateTime, ref._obj.dwHighDateTime = value & 0xFFFFFFFF, value >> 32
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)

    # psapi
    def GetProcessMemoryInfo(self, handle, ref, cb):
        assert handle == -1 and cb == ref._obj.cb  # the structure's own size
        if not self.memory_ok:
            return 0
        ref._obj.WorkingSetSize = self.working_set
        return 1


@pytest.fixture
def windows(monkeypatch):
    import ctypes
    w = Win()
    monkeypatch.setattr(ctypes, "windll", type("windll", (), {"kernel32": w, "psapi": w})(), raising=False)
    monkeypatch.setattr(kernel_proc, "win_kernel32", lambda: w)
    monkeypatch.setattr(measure.os, "name", "nt")
    return w


def test_on_windows_memory_is_the_working_set(windows):
    assert measure.rss_bytes() == 300 * 1024 ** 2
    windows.memory_ok = False
    assert measure.rss_bytes() is None


def test_on_windows_cpu_is_kernel_plus_user_time_and_the_handle_is_closed(windows):
    assert measure.cpu_seconds(1234) == pytest.approx(4.25)
    assert windows.closed == [77]
    assert measure.cpu_seconds(999) is None and windows.closed == [77]  # not opened: nothing to close


def test_on_windows_cpu_times_that_cant_be_read_are_none_and_still_closed(windows, monkeypatch):
    monkeypatch.setattr(windows, "GetProcessTimes", lambda handle, *refs: 0)
    assert measure.cpu_seconds(1234) is None and windows.closed == [77]
