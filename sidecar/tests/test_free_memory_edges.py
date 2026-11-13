"""Free memory: Windows' answer, a container's memory limit (cgroup v1 and v2), and files that
can't be read. The files are served from a table; nothing reads the real /proc."""

from __future__ import annotations

import ctypes
import io
from types import SimpleNamespace

import pytest

from fabric_desktop import free_memory as fm

GB = 1 << 30


def serve(monkeypatch, files):
    """free_memory's open() answers from ``files``; a missing path is OSError, as on a PC without it."""

    def fake_open(path, encoding=None):
        if path not in files:
            raise FileNotFoundError(path)
        return io.StringIO(files[path])

    monkeypatch.setattr(fm, "open", fake_open, raising=False)
    monkeypatch.setattr(fm, "os", SimpleNamespace(name="posix"))  # the /proc and cgroup path, on Windows CI too


MEMINFO = "MemTotal: 16000000 kB\nMemAvailable: 8388608 kB\n"  # 8 GiB free


def test_without_a_control_group_limit_free_memory_is_what_the_kernel_says(monkeypatch):
    serve(monkeypatch, {"/proc/meminfo": MEMINFO})
    assert fm.available() == 8 * GB


def test_a_cgroup_v2_limit_below_free_memory_wins(monkeypatch):
    serve(monkeypatch, {"/proc/meminfo": MEMINFO, "/proc/self/cgroup": "0::/app\n",
                        "/sys/fs/cgroup/app/memory.max": str(4 * GB), "/sys/fs/cgroup/app/memory.current": str(3 * GB)})
    assert fm.available() == 1 * GB


def test_a_cgroup_v2_without_a_limit_says_max_and_changes_nothing(monkeypatch):
    serve(monkeypatch, {"/proc/meminfo": MEMINFO, "/proc/self/cgroup": "0::/app\n",
                        "/sys/fs/cgroup/app/memory.max": "max\n", "/sys/fs/cgroup/app/memory.current": "100\n"})
    assert fm.available() == 8 * GB


def test_a_cgroup_v1_memory_controller_is_read_and_its_huge_no_limit_ignored(monkeypatch):
    v1 = "12:cpu,cpuacct:/x\n4:memory:/docker/abc\n"
    files = {"/proc/meminfo": MEMINFO, "/proc/self/cgroup": v1,
             "/sys/fs/cgroup/memory/docker/abc/memory.limit_in_bytes": str(2 * GB),
             "/sys/fs/cgroup/memory/docker/abc/memory.usage_in_bytes": str(3 * GB)}
    serve(monkeypatch, files)
    assert fm.available() == 0  # over its limit already: nothing more
    files["/sys/fs/cgroup/memory/docker/abc/memory.limit_in_bytes"] = str(1 << 62)
    assert fm.available() == 8 * GB


def test_only_the_container_s_limit_is_known(monkeypatch):
    serve(monkeypatch, {"/proc/meminfo": "MemTotal: 1 kB\n", "/proc/self/cgroup": "0::/app\n",
                        "/sys/fs/cgroup/app/memory.max": str(2 * GB), "/sys/fs/cgroup/app/memory.current": "0"})
    assert fm.available() == 2 * GB


def test_no_meminfo_or_an_unreadable_one_is_unknown(monkeypatch):
    serve(monkeypatch, {})
    assert fm.available() is None
    serve(monkeypatch, {"/proc/meminfo": "MemAvailable: lots kB\n"})
    assert fm.available() is None


def test_windows_asks_globalmemorystatusex(monkeypatch):
    monkeypatch.setattr(fm, "os", SimpleNamespace(name="nt"))

    def status(ref):
        ref._obj.ullAvailPhys = 6 * GB
        return 1

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=status)),
                        raising=False)
    assert fm.available() == 6 * GB
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GlobalMemoryStatusEx=lambda ref: 0)),
                        raising=False)
    assert fm.available() is None


def test_windows_without_kernel32_is_unknown(monkeypatch):
    monkeypatch.setattr(fm, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(), raising=False)
    assert fm.available() is None


@pytest.mark.parametrize("free, cap, bound", [(None, 4096, "share"), (2 * GB, 1536, "free"), (100, 512, "free")])
def test_the_budget_takes_the_smaller_of_share_and_free(free, cap, bound):
    got = fm.budget(4096, free)
    assert got["cap_mb"] == cap and got["bound"] == bound and got["duckdb_mb"] == max(fm.MIN_MB, cap // 2)


def test_a_control_group_file_that_cant_be_read_sets_no_limit(monkeypatch):
    serve(monkeypatch, {"/proc/meminfo": MEMINFO, "/proc/self/cgroup": "0::/app\n",
                        "/sys/fs/cgroup/app/memory.max": str(1 * GB)})  # memory.current can't be read
    assert fm.available() == 8 * GB
