"""Measuring a run on this PC: wall time, CPU time and peak memory of the engine process.

The notebook runs inside the engine process (until kernels get their own process), so these are
the engine's numbers while the run went: CPU time used by every thread of the process, and the
largest resident memory seen, sampled four times a second. They are measurements of this PC,
labeled as such; nothing here is estimated.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

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


class RunMeter:
    """Start before a run and stop after it: wall seconds, CPU seconds, peak and starting memory."""

    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak: int | None = None
        self.start_rss: int | None = None
        self.samples = 0

    def __enter__(self) -> RunMeter:
        self.start_rss = self.peak = rss_bytes()
        self._wall, self._cpu = time.monotonic(), time.process_time()
        self._thread = threading.Thread(target=self._sample, daemon=True, name="run-meter")
        self._thread.start()
        return self

    def _sample(self) -> None:
        while not self._stop.wait(self.interval):
            now = rss_bytes()
            self.samples += 1
            if now is not None and (self.peak is None or now > self.peak):
                self.peak = now

    def __exit__(self, *_exc: Any) -> None:
        self.wall = round(time.monotonic() - self._wall, 3)
        self.cpu = round(time.process_time() - self._cpu, 3)
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        now = rss_bytes()
        if now is not None and (self.peak is None or now > self.peak):
            self.peak = now

    def result(self) -> dict[str, Any]:
        mb = (lambda b: None if b is None else round(b / 1024 ** 2, 1))  # noqa: E731
        return {"seconds": self.wall, "cpu_seconds": self.cpu, "peak_memory_mb": mb(self.peak),
                "start_memory_mb": mb(self.start_rss), "memory_samples": self.samples,
                "measured": "the engine process on this PC (the notebook runs inside it)"}
