"""How much memory this PC has free now, and how much of it a local query may take.

DuckDB, left to itself, takes 80% of the PC's memory, and the SQL page asked for a share of the
total too. On a laptop where other apps already hold most of it, that limit is more than is free:
the query grows past what the PC can give and is ended by the OS (or, on Windows, pages the whole
PC) instead of spilling to disk. A query therefore gets a budget: the smaller of its share of the
total and most of what is free when it starts.

DuckDB's memory_limit doesn't cover everything its process holds. A lakehouse table is read
through an Arrow dataset, whose Parquet reads (row groups read ahead and decoded) sit outside
DuckDB's buffers: at TPC-H scale 5 that was about 2 GB more than the limit. So DuckDB is given
half the budget, and spills what doesn't fit, and the process as a whole is held to the budget
by its own watch (sql_worker.py), which stops it with a clear message instead of letting the OS
end it or page the PC.
"""

from __future__ import annotations

import os

# Of the memory free when a query starts, the share its limit may take. DuckDB's process uses
# somewhat more than its memory_limit (the limit covers its buffers, not Python or the result
# being written), so the limit leaves a quarter for that and for other apps.
FREE_SHARE = 0.75
MIN_MB = 256


def available() -> int | None:
    """Bytes of memory free for a new allocation on this machine, or None when it can't be told.
    On Linux, a memory limit on this process's control group (a container) counts too."""
    try:
        if os.name == "nt":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            return int(status.ullAvailPhys)
        free = None
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    free = int(line.split()[1]) * 1024
                    break
        group = _group_free()
        if group is not None:
            free = group if free is None else min(free, group)
        return free
    except (OSError, ValueError, AttributeError):
        return None


def _group_free() -> int | None:
    """What this process's memory control group still allows (limit less use), or None without a limit."""
    try:
        with open("/proc/self/cgroup", encoding="ascii") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in lines:
        _, controllers, path = line.split(":", 2)
        if controllers == "":  # cgroup v2
            base = f"/sys/fs/cgroup{path}"
            limit, used = _read(f"{base}/memory.max"), _read(f"{base}/memory.current")
        elif "memory" in controllers.split(","):  # cgroup v1
            base = f"/sys/fs/cgroup/memory{path}"
            limit, used = _read(f"{base}/memory.limit_in_bytes"), _read(f"{base}/memory.usage_in_bytes")
        else:
            continue
        if limit is not None and used is not None and limit < 1 << 60:  # v1 writes a huge number for none
            return max(0, limit - used)
    return None


def _read(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as f:
            text = f.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def budget(share_mb: int, free: int | None) -> dict[str, int | str]:
    """A query's memory, in MB: ``cap_mb`` for its whole process, ``share_mb`` (its share of the
    PC's memory) held to FREE_SHARE of the ``free`` bytes when that is known; ``duckdb_mb``, half
    of that, for DuckDB's memory_limit; ``bound``, "free" when what was free set the cap, else
    "share"; and ``free_mb``, what was free (0 when unknown)."""
    cap, bound = share_mb, "share"
    if free is not None and int(free * FREE_SHARE / (1 << 20)) < share_mb:
        cap, bound = int(free * FREE_SHARE / (1 << 20)), "free"
    cap = max(2 * MIN_MB, cap)
    return {"cap_mb": cap, "duckdb_mb": max(MIN_MB, cap // 2), "bound": bound,
            "free_mb": int(free / (1 << 20)) if free is not None else 0}
