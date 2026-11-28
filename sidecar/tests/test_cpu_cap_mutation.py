"""cpu_cap.py paths that mutation testing (batch 35, docs/quality/mutation-testing.md) showed no test pinned:
the affinity calls are made for this process (pid 0) with the processors it may use now, the
Windows mask starts at processor 0, and kernel32 is loaded so its last error can be read. Every OS
call is a stand-in, so nothing here changes this process's affinity."""

from __future__ import annotations

import ctypes

import pytest

from fabric_desktop import cpu_cap


def test_off_windows_the_first_processors_this_process_may_use_are_picked(monkeypatch):
    asked, set_to = [], []
    monkeypatch.setattr(cpu_cap.sys, "platform", "linux")
    monkeypatch.setattr(cpu_cap.os, "sched_getaffinity", lambda pid: asked.append(pid) or {6, 2, 4, 8}, raising=False)
    monkeypatch.setattr(cpu_cap.os, "sched_setaffinity", lambda pid, cpus: set_to.append((pid, list(cpus))), raising=False)
    assert cpu_cap.limit(2) is True
    assert asked == [0] and set_to == [(0, [2, 4])]
    assert cpu_cap.allowed() == 4 and asked == [0, 0]


class Kernel32:
    """GetProcessAffinityMask and SetProcessAffinityMask on a process allowed ``mask``, as plain functions,
    which take the argtypes and restype set on them as ctypes' do."""

    def __init__(self, mask):
        self.mask, self.set_to = mask, None

        def get(process, allowed, system):
            allowed._obj.value = system._obj.value = self.mask
            return 1

        def put(process, mask):
            self.set_to = mask.value
            return 1
        self.GetCurrentProcess = lambda: 42
        self.GetProcessAffinityMask, self.SetProcessAffinityMask = get, put


@pytest.fixture
def windows(monkeypatch):
    loaded = []
    kernel = Kernel32(0b0101)

    def win_dll(name, **kw):
        loaded.append((name, kw))
        return kernel
    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    monkeypatch.setattr(cpu_cap.sys, "platform", "win32")
    return kernel, loaded


def test_on_windows_processor_zero_is_the_first_one_picked(windows):
    kernel, _ = windows
    assert cpu_cap.limit(1) is True and kernel.set_to == 0b0001


def test_on_windows_kernel32_is_loaded_so_its_last_error_can_be_read(windows):
    _, loaded = windows
    cpu_cap.limit(1)
    assert cpu_cap.allowed() == 2
    assert loaded == [("kernel32", {"use_last_error": True})] * 2
