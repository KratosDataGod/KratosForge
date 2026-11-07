"""Holding a kernel process to some of this PC's cores (cpu_cap.py, side by side's "Match Fabric"). The cap
end to end in a process of its own (it changes os.cpu_count and duckdb.connect for its whole process), and
the Windows affinity calls with a stand-in kernel32."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys

import pytest

from fabric_desktop import cpu_cap

SIDECAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_a_cap_sets_the_variables_pools_read_and_none_without_one():
    assert cpu_cap.env(None) == {} and cpu_cap.env(0) == {}
    assert cpu_cap.env(2) == {"FABRIC_DESKTOP_CORES": "2", "POLARS_MAX_THREADS": "2", "PYTHON_CPU_COUNT": "2"}


@pytest.mark.parametrize("raw", ["", "  ", "two", "0", "-3"])
def test_no_cap_or_a_cap_that_isnt_a_count_leaves_every_core(monkeypatch, raw):
    monkeypatch.setenv(cpu_cap.ENV, raw)
    called = []
    monkeypatch.setattr(cpu_cap, "limit", lambda n: called.append(n) or True)
    assert cpu_cap.from_env() is None and called == []


def test_a_cap_the_os_refuses_leaves_every_core(monkeypatch):
    monkeypatch.setenv(cpu_cap.ENV, "2")
    monkeypatch.setattr(cpu_cap, "limit", lambda n: False)
    count = os.cpu_count
    assert cpu_cap.from_env() is None and os.cpu_count is count


@pytest.mark.skipif(not hasattr(os, "sched_setaffinity"), reason="Linux affinity")
def test_a_kernel_process_with_a_cap_runs_on_that_many_cores_and_duckdb_uses_them():
    code = """
import json, os, duckdb
from fabric_desktop import cpu_cap
got = cpu_cap.from_env()
own = duckdb.connect().execute("SELECT current_setting('threads')").fetchone()[0]
asked = duckdb.connect(config={"threads": 1}).execute("SELECT current_setting('threads')").fetchone()[0]
default = duckdb.execute("SELECT current_setting('threads')").fetchone()[0]
print(json.dumps([got, os.cpu_count(), cpu_cap.allowed(), own, asked, default]))
"""
    env = dict(os.environ, PYTHONPATH=SIDECAR, **cpu_cap.env(1))
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [1, 1, 1, 1, 1, 1]


def test_a_default_duckdb_connection_that_refuses_the_setting_is_logged(monkeypatch, caplog):
    import duckdb

    class Refusing:
        Error = duckdb.Error

        def __init__(self):
            self.connect = lambda *a, **kw: None

        def execute(self, sql):
            raise duckdb.Error("configuration has been locked")
    fake = Refusing()
    monkeypatch.setitem(sys.modules, "duckdb", fake)
    cpu_cap._duckdb_threads(2)
    assert "Couldn't set DuckDB's default connection to 2 threads" in caplog.text
    assert fake.connect.__wrapped__ is not None  # wrapped all the same


def test_without_duckdb_nothing_is_wrapped(monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb", None)  # import duckdb raises ImportError
    cpu_cap._duckdb_threads(2)


def test_an_affinity_the_os_refuses_is_logged_and_not_applied(monkeypatch, caplog):
    if not hasattr(os, "sched_setaffinity"):
        pytest.skip("Linux affinity")

    def refuse(pid, cpus):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(cpu_cap.os, "sched_setaffinity", refuse)
    assert cpu_cap.limit(1) is False and "Couldn't hold this process to 1 cores" in caplog.text


def test_where_affinity_cant_be_read_or_set_there_is_no_cap(monkeypatch):
    monkeypatch.setattr(cpu_cap.sys, "platform", "darwin")
    monkeypatch.delattr(cpu_cap.os, "sched_getaffinity", raising=False)
    monkeypatch.delattr(cpu_cap.os, "sched_setaffinity", raising=False)
    assert cpu_cap.allowed() is None and cpu_cap.limit(2) is False


# --------------------------------------------------------------------------- Windows, with a stand-in kernel32

class Kernel32:
    """GetProcessAffinityMask and SetProcessAffinityMask on a process allowed ``mask``."""

    def __init__(self, mask, get_ok=True, set_ok=True):
        self.mask, self.get_ok, self.set_ok, self.set_to = mask, get_ok, set_ok, None
        self.GetCurrentProcess = self._current
        self.GetProcessAffinityMask = self._get
        self.SetProcessAffinityMask = self._set

    class _Fn:
        pass

    def _current(self):
        return 42

    def _get(self, process, allowed, system):
        if not self.get_ok:
            return 0
        allowed._obj.value = self.mask
        system._obj.value = self.mask
        return 1

    def _set(self, process, mask):
        self.set_to = mask.value
        return 1 if self.set_ok else 0


@pytest.fixture
def windows(monkeypatch):
    def install(kernel):
        # the calls take argtypes and restype set on them: plain functions accept any attribute
        monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: kernel, raising=False)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
        monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "Access is denied"), raising=False)
        monkeypatch.setattr(cpu_cap.sys, "platform", "win32")
        return kernel
    return install


def _wrap(kernel):
    """Functions that take argtypes and restype, as ctypes' do."""
    for name in ("GetCurrentProcess", "GetProcessAffinityMask", "SetProcessAffinityMask"):
        fn = getattr(kernel, name)

        def call(*a, _fn=fn):
            return _fn(*a)
        setattr(kernel, name, call)
    return kernel


def test_on_windows_the_first_allowed_processors_are_picked(windows):
    k = windows(_wrap(Kernel32(0b1011_0100)))
    assert cpu_cap.limit(2) is True and k.set_to == 0b0001_0100
    assert cpu_cap.allowed() == 4


def test_on_windows_a_cap_over_the_allowed_count_takes_them_all(windows):
    k = windows(_wrap(Kernel32(0b0110)))
    assert cpu_cap.limit(8) is True and k.set_to == 0b0110


def test_on_windows_a_mask_that_cant_be_read_or_set_is_refused_and_logged(windows, caplog):
    windows(_wrap(Kernel32(0b11, get_ok=False)))
    assert cpu_cap.limit(1) is False and cpu_cap.allowed() is None
    windows(_wrap(Kernel32(0b11, set_ok=False)))
    assert cpu_cap.limit(1) is False
    windows(_wrap(Kernel32(0)))  # no processor allowed at all: nothing to pick
    assert cpu_cap.limit(1) is False
    assert caplog.text.count("Couldn't hold this process to 1 cores") == 2


def test_a_cap_the_os_applies_is_what_this_process_counts(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", os.cpu_count)  # restored after: from_env replaces them
    if hasattr(os, "process_cpu_count"):
        monkeypatch.setattr(os, "process_cpu_count", os.process_cpu_count)
    told = []
    monkeypatch.setenv(cpu_cap.ENV, " 3 ")
    monkeypatch.setattr(cpu_cap, "limit", lambda n: True)
    monkeypatch.setattr(cpu_cap, "_duckdb_threads", told.append)
    assert cpu_cap.from_env() == 3 and os.cpu_count() == 3 and told == [3]
    if hasattr(os, "process_cpu_count"):
        assert os.process_cpu_count() == 3
