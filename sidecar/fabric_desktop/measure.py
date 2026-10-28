"""Measuring a run on this PC: wall time, CPU time and peak memory of the process the code ran in.

With notebooks in the engine process, that is the engine: CPU time used by every thread of the
process, and the largest resident memory seen, sampled four times a second. With **Run notebooks
in their own process** on, the code runs in the kernel's own process (kernel_proc.py), so the meter
is given that process's id and measures it instead: its CPU time (user and system) and its memory
(private bytes on Windows, resident memory elsewhere, as the kernel's memory limit counts it).
A kernel process that starts during the run counts from its start; one that restarts counts both.
They are measurements of this PC, labeled as such; nothing here is estimated.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from .redact import describe

_log = logging.getLogger(__name__)


def rss_bytes() -> int | None:
    """The process's resident memory now, or None where it can't be read."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):  # type: ignore[attr-defined]
                return int(counters.WorkingSetSize)
            return None
        with open("/proc/self/statm", encoding="ascii") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception as exc:  # noqa: BLE001 - shown as "not measured"
        _log.info("Couldn't read this process's memory: %s", describe(exc))
        return None


def cpu_seconds(pid: int) -> float | None:
    """CPU seconds (user and system) another process has used so far, or None if it can't be read
    (it ended, or isn't this user's)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                times = [wintypes.FILETIME() for _ in range(4)]  # created, exited, kernel, user
                if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                    return None
                return sum((t.dwHighDateTime << 32 | t.dwLowDateTime) for t in times[2:]) / 1e7  # 100 ns units
            finally:
                kernel32.CloseHandle(handle)
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:
            fields = f.read().rsplit(")", 1)[1].split()  # after "pid (name)", which may hold spaces
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")  # utime, stime
    except Exception as exc:  # noqa: BLE001 - shown as "not measured"
        _log.info("Couldn't read process %s's CPU time: %s", pid, describe(exc))
        return None


class RunMeter:
    """Start before a run and stop after it: wall seconds, CPU seconds, peak and starting memory.

    ``process`` gives the id of the process the run's code runs in (a process kernel's ``pid``),
    asked at every sample since the kernel may start or restart during the run; None, or a
    function that returns None, measures this process."""

    def __init__(self, interval: float = 0.25, process: Callable[[], int | None] | None = None):
        self.interval = interval
        self.process = process
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.peak: int | None = None
        self.start_rss: int | None = None
        self.samples = 0
        # Per kernel process: CPU seconds when the run first saw it, and the latest reading.
        self._cpu_seen: dict[int, list[float]] = {}

    def _pid(self) -> int | None:
        try:
            return self.process() if self.process else None
        except Exception as exc:  # noqa: BLE001 - measured as this process instead
            _log.info("Couldn't tell which process to measure: %s", describe(exc))
            return None

    def _memory(self, pid: int | None) -> int | None:
        if pid is None:
            return rss_bytes()
        from .kernel_proc import memory_used
        return memory_used(pid)

    def _read(self, first: bool = False) -> None:
        """One sample: memory, and for a kernel process its CPU time so far."""
        pid = self._pid()
        now = self._memory(pid)
        with self._lock:
            if now is not None and (self.peak is None or now > self.peak):
                self.peak = now
            if pid is None:
                return
            cpu = cpu_seconds(pid)
            if cpu is None:
                return
            if pid not in self._cpu_seen:
                # Running when the run started: count from here. Started during the run: all of it.
                self._cpu_seen[pid] = [cpu if first else 0.0, cpu]
            self._cpu_seen[pid][1] = cpu

    def __enter__(self) -> RunMeter:
        self._kernel = self.process is not None
        self.start_rss = self._memory(self._pid())
        self.peak = self.start_rss
        self._read(first=True)
        self._wall, self._cpu = time.monotonic(), time.process_time()
        self._thread = threading.Thread(target=self._sample, daemon=True, name="run-meter")
        self._thread.start()
        return self

    def _sample(self) -> None:
        while not self._stop.wait(self.interval):
            self.samples += 1
            self._read()

    def __exit__(self, *_exc: Any) -> None:
        self.wall = round(time.monotonic() - self._wall, 3)
        engine_cpu = time.process_time() - self._cpu
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        self._read()
        with self._lock:
            # A kernel run that never reached its process (it didn't start) used no kernel CPU.
            self.cpu = round(sum(last - first for first, last in self._cpu_seen.values()), 3) if self._kernel \
                else round(engine_cpu, 3)
            self.processes = len(self._cpu_seen)

    def result(self) -> dict[str, Any]:
        mb = (lambda b: None if b is None else round(b / 1024 ** 2, 1))  # noqa: E731
        where = ("the notebook's own kernel process on this PC" if self._kernel
                 else "the engine process on this PC (the notebook runs inside it)")
        return {"seconds": self.wall, "cpu_seconds": self.cpu, "peak_memory_mb": mb(self.peak),
                "start_memory_mb": mb(self.start_rss), "memory_samples": self.samples, "measured": where}
