"""Delete left-overs and its process check (local_writes_service.py): assertions that pin the exact
calls and answers, written to stop the changes a mutation run found the other tests let through."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from fabric_desktop import local_writes_service as L

OTHER = 4242  # never this test's own process


def test_the_posix_check_only_asks_whether_the_process_exists(monkeypatch):
    # Signal 0 sends nothing: any other number would signal (or end) someone else's process.
    sent = []
    monkeypatch.setattr(L, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(L.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert L.pid_running(OTHER) is True
    assert sent == [(OTHER, 0)]


def test_a_process_that_is_gone_is_not_running(monkeypatch):
    def gone(pid, sig):
        raise ProcessLookupError(pid)

    monkeypatch.setattr(L, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(L.os, "kill", gone)
    assert L.pid_running(OTHER) is False


def test_windows_opens_kernel32_so_the_last_error_can_be_read(monkeypatch):
    # get_last_error only reports the error OpenProcess set when kernel32 was loaded with use_last_error.
    loaded = []

    class Kernel32:
        def OpenProcess(self, access, inherit, pid):
            return 0

    def windll(name, use_last_error=False):
        loaded.append((name, use_last_error))
        return Kernel32()

    monkeypatch.setattr(L, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", windll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    assert L.pid_running(OTHER) is True
    assert loaded == [("kernel32", True)]


def _svc(root):
    class Svc(L.LocalWritesMixin):
        pass

    svc = Svc()
    svc.root, svc.sessions = root, SimpleNamespace(any_running=lambda: False)
    return svc


def test_reveal_makes_a_missing_staging_folder_and_its_parents_and_opens_it_without_selecting(tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(L, "_reveal", lambda path, select: shown.append((path, select)))
    monkeypatch.setattr(L, "_tpch_folders", lambda: [])
    root = tmp_path / "not" / "made" / "yet"
    svc = _svc(root)
    assert svc.local_writes_reveal()["shown"] == str(root / "staging")
    assert (root / "staging").is_dir()
    assert shown == [(root / "staging", False)]
    # A second look at a folder that is already there works too.
    svc.local_writes_reveal()
    assert len(shown) == 2


@pytest.mark.parametrize("folder", ["staging", "_writes"])
def test_reveal_of_an_existing_folder_does_not_fail(tmp_path, monkeypatch, folder):
    monkeypatch.setattr(L, "_reveal", lambda path, select: None)
    monkeypatch.setattr(L, "_tpch_folders", lambda: [])
    svc = _svc(tmp_path)
    target = tmp_path / ("staging" if folder == "staging" else L.SPILLS)
    target.mkdir(parents=True)
    assert svc.local_writes_reveal(str(target))["shown"] == str(target)
