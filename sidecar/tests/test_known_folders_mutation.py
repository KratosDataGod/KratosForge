"""known_folders._lookup paths that mutation testing (batch 71, docs/quality/mutation-testing.md) showed
no test pinned: the flag it passes, the whole folder id it asks for, and an answer Windows marks as failed."""

from __future__ import annotations

import ctypes
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import known_folders as K


class Shell32:
    def __init__(self, hr, value):
        self.hr, self.value, self.calls = hr, value, []

    def SHGetKnownFolderPath(self, guid_ref, flags, token, out_ref):
        g = guid_ref._obj
        self.calls.append((g.Data1, g.Data2, g.Data3, bytes(g.Data4), flags, token))
        if self.value is not None:
            out_ref._obj.value = self.value
        return self.hr


@pytest.fixture
def shell(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(K, "sys", SimpleNamespace(platform="win32"))

    def use(hr, value):
        s = Shell32(hr, value)
        ole = SimpleNamespace(CoTaskMemFree=lambda ptr: None)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=s, ole32=ole), raising=False)
        return s

    return use


def test_the_folder_is_only_located_never_created_or_checked(shell, tmp_path):
    s = shell(0, str(tmp_path / "D"))
    assert K._lookup(K.DOWNLOADS) == tmp_path / "D"
    assert s.calls[0][4] == 0x4000 and s.calls[0][5] is None   # KF_FLAG_DONT_VERIFY, no token


@pytest.mark.parametrize("folder_id", [K.PROFILE, K.DOWNLOADS, K.DOCUMENTS])
def test_windows_is_asked_for_the_whole_folder_id(shell, tmp_path, folder_id):
    s = shell(0, str(tmp_path / "x"))
    K._lookup(folder_id)
    u = uuid.UUID(folder_id)
    assert s.calls == [(u.time_low, u.time_mid, u.time_hi_version, u.bytes[8:], 0x4000, None)]


def test_an_answer_windows_marks_as_failed_is_no_answer(shell, tmp_path):
    shell(-2147467259, str(tmp_path / "stale"))   # E_FAIL, though a path came back
    assert K._lookup(K.DOCUMENTS) is None


def test_an_empty_answer_is_no_answer(shell):
    shell(0, "")
    assert K._lookup(K.DOCUMENTS) is None


def test_off_windows_nothing_is_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(K, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(ctypes, "windll", None, raising=False)   # would fail if touched
    assert K._lookup(K.DOWNLOADS) is None
    assert K.downloads() == Path.home() / "Downloads"
