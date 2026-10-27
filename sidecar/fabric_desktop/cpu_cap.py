"""Hold a kernel process to some of this PC's cores (side by side's "Match Fabric").

The engine sets ``FABRIC_DESKTOP_CORES`` on a kernel process it starts (kernel_proc), and the
process calls ``from_env`` first thing. The cap is the process's CPU affinity, which the OS
enforces and every process it starts inherits (the TPC-H generator's parts), so DuckDB, Polars
and any other thread pool get at most that many cores whatever they size themselves to. Pools
that read a count are told it too: ``POLARS_MAX_THREADS``, ``PYTHON_CPU_COUNT`` (Python 3.13+)
and ``os.cpu_count`` here for older Pythons; DuckDB, which sizes its pool from the hardware
and reads no variable, gets ``threads`` on every connection the process opens.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

ENV = "FABRIC_DESKTOP_CORES"

_log = logging.getLogger(__name__)


def env(cores: int | None) -> dict[str, str]:
    """The variables a kernel process started with a cap of ``cores`` gets; none without one."""
    if not cores:
        return {}
    n = str(int(cores))
    return {ENV: n, "POLARS_MAX_THREADS": n, "PYTHON_CPU_COUNT": n}


def from_env() -> int | None:
    """Apply the cap ``FABRIC_DESKTOP_CORES`` asks for to this process. Returns the cores it now
    has, or None when no cap was asked for or the OS refused it (the run then uses every core)."""
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return None
    try:
        cores = int(raw)
    except ValueError:
        return None
    if cores < 1 or not limit(cores):
        return None
    os.cpu_count = lambda: cores  # type: ignore[assignment] - Python 3.13 does this for PYTHON_CPU_COUNT
    if hasattr(os, "process_cpu_count"):
        os.process_cpu_count = lambda: cores  # type: ignore[attr-defined,assignment]
    _duckdb_threads(cores)
    return cores


def _duckdb_threads(cores: int) -> None:
    """Every DuckDB connection this process opens, and its default one, gets ``threads = cores``
    unless the code asks for its own number."""
    try:
        import duckdb
    except ImportError:
        return
    connect = duckdb.connect

    def capped(database: Any = ":memory:", read_only: bool = False, config: dict[str, Any] | None = None,
               **kwargs: Any) -> Any:
        return connect(database, read_only, {"threads": cores, **(config or {})}, **kwargs)

    capped.__doc__, capped.__wrapped__ = connect.__doc__, connect  # type: ignore[attr-defined]
    duckdb.connect = capped  # type: ignore[assignment]
    try:
        duckdb.execute(f"SET threads = {int(cores)}")  # duckdb.sql() and friends use the default connection
    except duckdb.Error as exc:
        _log.warning("Couldn't set DuckDB's default connection to %s threads: %s", cores, exc)


def limit(cores: int) -> bool:
    """Set this process's affinity to the first ``cores`` processors it may use now."""
    try:
        if sys.platform == "win32":
            return _limit_windows(cores)
        if hasattr(os, "sched_setaffinity"):
            allowed = sorted(os.sched_getaffinity(0))
            os.sched_setaffinity(0, allowed[:cores])
            return True
    except OSError as exc:
        _log.warning("Couldn't hold this process to %s cores: %s", cores, exc)
    return False


def _limit_windows(cores: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
    kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    process = kernel32.GetCurrentProcess()
    allowed, system = ctypes.c_size_t(), ctypes.c_size_t()
    if not kernel32.GetProcessAffinityMask(process, ctypes.byref(allowed), ctypes.byref(system)):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    mask, picked, bit = 0, 0, 0
    while picked < cores and (allowed.value >> bit):
        if allowed.value >> bit & 1:
            mask |= 1 << bit
            picked += 1
        bit += 1
    if not mask:
        return False
    if not kernel32.SetProcessAffinityMask(process, ctypes.c_size_t(mask)):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    return True
