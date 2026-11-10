"""Running out of memory on Windows, measured rather than inferred (local-performance/out-of-memory).

Windows doesn't overcommit: an allocation past what the PC can commit fails. These tests make that
happen by capping the process's committed memory with a job object, then allocating in Polars (Rust),
in Arrow (C++) and in Python. They check the exit code or error each gives against what the app reads
as running out of memory (kernel_proc._OUT_OF_MEMORY_CODES, notebook's MemoryError message), so a
change in Polars, Arrow or Windows that the app would no longer recognise shows up here.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from fabric_desktop.kernel_proc import _OUT_OF_MEMORY_CODES, ProcessKernel

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' commit limit; Linux is in test_out_of_memory")

# Puts the calling process in a new job object whose committed memory can't pass ``limit`` bytes.
CAP = textwrap.dedent('''
    import ctypes
    from ctypes import wintypes

    def cap(limit):
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = limit
        if not job or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) \\
                or not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            raise OSError(ctypes.get_last_error(), "couldn't cap this process's memory")
''')

# Each allocates about 8 GB, far past a cap of the process's current use plus 512 MB.
ALLOCATE = {
    "polars": "import polars as pl\npl.int_range(0, 1_000_000_000, eager=True)",
    "arrow": "import pyarrow as pa\npa.allocate_buffer(8 << 30)",
    "python": "bytearray(8 << 30)",
}


def capped(allocate: str) -> str:
    """A script that imports what it needs, caps its memory 512 MB above what it uses, then allocates."""
    *imports, line = allocate.splitlines()
    return CAP + "\n".join(imports) + textwrap.dedent('''
        import os
        from fabric_desktop.kernel_proc import memory_used
        cap(memory_used(os.getpid()) + (512 << 20))
        print("capped", flush=True)
    ''') + line + "\nprint('allocated', flush=True)\n"


def run(allocate: str) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([sys.executable, "-c", capped(allocate)], capture_output=True, text=True, timeout=300)
    print(f"exit code 0x{r.returncode & 0xFFFFFFFF:08X}\nstdout: {r.stdout[-400:]}\nstderr: {r.stderr[-800:]}")
    assert "capped" in r.stdout, r.stderr  # the cap took, so what follows is the allocation's doing
    return r


def test_a_failed_allocation_in_polars_ends_the_process_with_a_code_the_app_reads_as_out_of_memory():
    r = run(ALLOCATE["polars"])
    assert "allocated" not in r.stdout
    assert r.returncode & 0xFFFFFFFF in _OUT_OF_MEMORY_CODES, f"0x{r.returncode & 0xFFFFFFFF:08X}"


@pytest.mark.parametrize("name", ["arrow", "python"])
def test_a_failed_allocation_in_arrow_or_python_raises_memory_error_and_the_process_lives(name):
    r = run(ALLOCATE[name])
    assert r.returncode == 1 and "allocated" not in r.stdout
    assert "MemoryError" in r.stderr, r.stderr  # pyarrow's ArrowMemoryError is a MemoryError


def test_a_notebook_cell_that_runs_out_of_memory_in_polars_says_so_and_the_next_cell_runs():
    k = ProcessKernel()
    try:
        assert k.run("print(1)")["status"] == "ok"
        r = k.run(capped(ALLOCATE["polars"]))
        print(r["outputs"][-1])
        assert r["status"] == "error" and r["outputs"][-1]["ename"] == "KernelStopped"
        assert "most likely ran out of memory" in r["outputs"][-1]["evalue"]
        assert k.run("print(1 + 1)")["outputs"][0]["text"].strip() == "2"  # a fresh process, without the cap
    finally:
        k.close()
