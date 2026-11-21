"""Whether the process that owns a local write is still running, as Windows answers it
(local_writes_service.pid_running): a running process, one that has exited, one this user may not
open, and one that is gone. kernel32 is a stand-in, so this runs on any machine."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from fabric_desktop import local_writes_service as L

STILL_ACTIVE = 259


class Kernel32:
    """OpenProcess gives a handle (or 0); GetExitCodeProcess reports ``exit_code``."""

    def __init__(self, handle=7, exit_code=STILL_ACTIVE, answered=True):
        self.handle, self.exit_code, self.answered = handle, exit_code, answered
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        assert access == 0x1000 and inherit is False
        return self.handle

    def GetExitCodeProcess(self, handle, ref):
        ref._obj.value = self.exit_code
        return self.answered

    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(L, "sys", SimpleNamespace(platform="win32"))

    def use(kernel, last_error=0):
        monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: kernel, raising=False)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error, raising=False)
        return kernel

    return use


OTHER = 4242  # never this test's own process


@pytest.mark.parametrize("exit_code, answered, running", [
    (STILL_ACTIVE, True, True),   # still running
    (0, True, False),             # exited
    (0, False, True),             # Windows couldn't say: treated as running, so its files are kept
])
def test_a_process_windows_opens_is_running_until_it_exits(windows, exit_code, answered, running):
    kernel = windows(Kernel32(exit_code=exit_code, answered=answered))
    assert L.pid_running(OTHER) is running
    assert kernel.closed == [7]


@pytest.mark.parametrize("last_error, running", [(5, True), (87, False)])
def test_a_process_windows_wont_open_is_running_only_when_access_is_denied(windows, last_error, running):
    windows(Kernel32(handle=0), last_error=last_error)
    assert L.pid_running(OTHER) is running


def test_this_process_is_always_running(windows):
    windows(Kernel32(handle=0), last_error=87)
    assert L.pid_running(L.os.getpid()) is True
