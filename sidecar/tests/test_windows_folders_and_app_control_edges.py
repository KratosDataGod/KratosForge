"""Two Windows-only lookups, with stand-ins for the Windows calls so they run anywhere:
where Windows keeps Downloads and Documents (known_folders.py), and which files a notebook
environment holds that application control won't load (app_control.py)."""

from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import app_control as A
from fabric_desktop import known_folders as K

WINDOWS = SimpleNamespace(platform="win32")


# ---- known folders


class Shell32:
    """SHGetKnownFolderPath answers ``folders[guid]`` (HRESULT 0), or E_FAIL for a folder it doesn't know."""

    def __init__(self, folders):
        self.folders = folders

    def SHGetKnownFolderPath(self, guid_ref, flags, token, out_ref):
        assert flags == K._KF_FLAG_DONT_VERIFY and token is None
        g = guid_ref._obj
        key = f"{g.Data1:08X}-{g.Data2:04X}-{g.Data3:04X}"
        for folder_id, path in self.folders.items():
            if folder_id.strip("{}").upper().startswith(key):
                out_ref._obj.value = path
                return 0
        return -2147467259  # E_FAIL


class Ole32:
    def __init__(self):
        self.freed = 0

    def CoTaskMemFree(self, ptr):
        self.freed += 1


@pytest.fixture
def windows_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "sys", WINDOWS)
    home = tmp_path / "me"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    def use(folders):
        ole = Ole32()
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=Shell32(folders), ole32=ole), raising=False)
        return home, ole

    return use


def test_a_documents_folder_moved_to_onedrive_is_the_one_used(windows_shell, tmp_path):
    onedrive = str(tmp_path / "OneDrive - Contoso" / "Documents")
    home, ole = windows_shell({K.PROFILE: str(tmp_path / "me"), K.DOCUMENTS: onedrive})
    assert K.documents() == Path(onedrive)
    assert K.downloads() == home / "Downloads"  # Windows has no answer for Downloads here
    assert ole.freed == 4  # every answer's memory is freed, found or not


def test_a_home_that_isnt_the_windows_profile_keeps_writes_under_the_home(windows_shell, tmp_path):
    home, _ = windows_shell({K.PROFILE: str(tmp_path / "real-profile"), K.DOWNLOADS: str(tmp_path / "D")})
    assert K.downloads() == home / "Downloads"


def test_a_windows_without_the_shell_call_falls_back_to_the_home(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "sys", WINDOWS)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(), raising=False)
    said = []
    monkeypatch.setattr(K._log, "info", lambda msg, *args: said.append(msg % args))
    assert K._lookup(K.DOWNLOADS) is None
    assert said and said[0].startswith(f"known folder {K.DOWNLOADS}:")


# ---- application control


class Fn:
    """A kernel32 function: settable restype/argtypes, and a call that answers ``result``."""

    def __init__(self, result=None):
        self.result, self.calls = result, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def _kernel(monkeypatch, handle, last_error):
    k = SimpleNamespace(LoadLibraryExW=Fn(handle), FreeLibrary=Fn(1))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: k, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error, raising=False)
    return k


def test_a_file_windows_loads_isnt_blocked_and_is_freed(monkeypatch):
    k = _kernel(monkeypatch, handle=1234, last_error=0)
    assert A._load_blocked("C:\\env\\a.pyd") is False
    assert k.LoadLibraryExW.calls == [("C:\\env\\a.pyd", None, A.DONT_RESOLVE_DLL_REFERENCES)]
    assert k.FreeLibrary.calls == [(1234,)]


@pytest.mark.parametrize("code, blocked", [(4551, True), (577, True), (2, False)])
def test_a_file_windows_refuses_is_blocked_only_for_a_policy_reason(monkeypatch, code, blocked):
    k = _kernel(monkeypatch, handle=None, last_error=code)
    assert A._load_blocked("C:\\env\\a.pyd") is blocked
    assert k.FreeLibrary.calls == []


def _env(tmp_path, names):
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"MZ")
    return tmp_path


def test_the_scan_stops_at_its_limit_and_a_failing_check_keeps_what_it_found(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "sys", WINDOWS)
    env = _env(tmp_path, ["a/x.pyd", "b/y.pyd", "c/z.pyd"])
    monkeypatch.setattr(A, "MAX_FILES", 2)
    assert A.blocked_packages(env, probe=lambda p: True) == {"a": ["a/x.pyd".replace("/", A.os.sep)],
                                                             "b": ["b/y.pyd".replace("/", A.os.sep)]}
    monkeypatch.setattr(A, "MAX_FILES", 5000)

    def probe(path):
        if path.endswith("y.pyd"):
            raise PermissionError(13, "denied")
        return True

    assert list(A.blocked_packages(env, probe=probe)) == ["a"]
