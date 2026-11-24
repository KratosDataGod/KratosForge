"""known_folders' OneDrive registry reading, and the de-duplication of OneDrive folders, driven on any OS
through a fake winreg. The real registry is never touched: winreg is replaced in sys.modules and the module's
own ``sys`` is swapped so the Windows branch runs on Linux too."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import known_folders as K

ACCOUNTS = r"Software\Microsoft\OneDrive\Accounts"
HKCU = object()


class _Key:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    """accounts: {account name: UserFolder value, or an OSError to raise on open/query}. None means no
    Accounts key at all (OneDrive never set up)."""

    HKEY_CURRENT_USER = HKCU

    def __init__(self, accounts, broken_open=(), broken_query=()):
        self.accounts = accounts
        self.broken_open, self.broken_query = set(broken_open), set(broken_query)
        self.opened: list[tuple[object, str]] = []

    def OpenKey(self, parent, name):
        self.opened.append((parent, name))
        if parent is HKCU:
            if self.accounts is None:
                raise FileNotFoundError(2, "The system cannot find the file specified")
            return _Key(name)
        if name in self.broken_open:
            raise PermissionError(5, "Access is denied")
        return _Key(name)

    def EnumKey(self, key, i):
        names = list(self.accounts)
        if i >= len(names):
            raise OSError(259, "No more data is available")
        return names[i]

    def QueryValueEx(self, key, value_name):
        assert value_name == "UserFolder"
        if key.name in self.broken_query:
            raise FileNotFoundError(2, "value not found")
        return self.accounts[key.name], 1  # REG_SZ


@pytest.fixture
def win(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for name in K._ONEDRIVE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(K, "sys", SimpleNamespace(platform="win32"))

    def use(reg):
        monkeypatch.setitem(sys.modules, "winreg", reg)
        return reg

    return use


def test_each_signed_in_account_folder_is_found_in_order(win, tmp_path):
    personal, work = tmp_path / "OneDrive", tmp_path / "OneDrive - Contoso"
    reg = win(FakeWinreg({"Personal": str(personal), "Business1": str(work)}))
    assert K._registry_onedrive() == [personal, work]
    assert reg.opened[0] == (HKCU, ACCOUNTS)
    assert [n for _p, n in reg.opened[1:]] == ["Personal", "Business1"]


def test_surrounding_whitespace_in_a_folder_value_is_dropped(win, tmp_path):
    win(FakeWinreg({"Personal": f"  {tmp_path / 'OneDrive'}\t"}))
    assert K._registry_onedrive() == [tmp_path / "OneDrive"]


@pytest.mark.parametrize("value", ["", "   ", "OneDrive", r"relative\OneDrive", 42, None, b"/x"])
def test_an_empty_relative_or_non_text_folder_value_is_skipped(win, tmp_path, value):
    win(FakeWinreg({"Bad": value, "Good": str(tmp_path / "OD")}))
    assert K._registry_onedrive() == [tmp_path / "OD"]


def test_an_account_that_cannot_be_opened_or_has_no_folder_is_skipped_not_fatal(win, tmp_path):
    reg = win(FakeWinreg({"Locked": str(tmp_path / "A"), "NoValue": str(tmp_path / "B"),
                          "Fine": str(tmp_path / "C")},
                         broken_open={"Locked"}, broken_query={"NoValue"}))
    assert K._registry_onedrive() == [tmp_path / "C"]
    assert [n for _p, n in reg.opened[1:]] == ["Locked", "NoValue", "Fine"]   # kept going after each failure


def test_no_onedrive_accounts_key_means_no_folders(win):
    reg = win(FakeWinreg(None))
    assert K._registry_onedrive() == []
    assert reg.opened == [(HKCU, ACCOUNTS)]


def test_an_accounts_key_with_no_accounts_means_no_folders(win):
    win(FakeWinreg({}))
    assert K._registry_onedrive() == []


def test_without_winreg_there_are_no_registry_folders(win, monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)   # import winreg raises ImportError
    assert K._registry_onedrive() == []


def test_off_windows_the_registry_is_not_read(monkeypatch):
    monkeypatch.setattr(K, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace())   # any attribute use would raise
    assert K._registry_onedrive() == []


def test_a_folder_named_by_both_the_environment_and_the_registry_is_listed_once(win, monkeypatch, tmp_path):
    od, work = tmp_path / "OneDrive", tmp_path / "OneDrive - Contoso"
    monkeypatch.setenv("OneDrive", str(od))
    monkeypatch.setenv("OneDriveConsumer", str(od) + "/")      # same folder, written differently
    monkeypatch.setenv("OneDriveCommercial", str(work))
    win(FakeWinreg({"Personal": str(od), "Business1": str(work) + "/./"}))
    assert K.onedrive_roots() == [od, work]


def test_a_path_in_a_registry_only_onedrive_folder_is_in_onedrive(win, tmp_path):
    work = tmp_path / "elsewhere" / "Corp Sync"   # not under the home, not named OneDrive
    win(FakeWinreg({"Business1": str(work)}))
    assert K.onedrive_root(work / "Reports" / "q3.pbix") == work
    assert K.in_onedrive(work)
    assert not K.in_onedrive(tmp_path / "elsewhere" / "Corp Sync2" / "x")   # a prefix is not a parent
    assert "is in OneDrive" in K.onedrive_refusal(work / "proj", "a project")


def test_exports_move_to_the_local_folder_when_windows_puts_downloads_in_a_registry_onedrive(
        win, monkeypatch, tmp_path):
    home = Path.home()
    work = tmp_path / "Corp Sync"
    win(FakeWinreg({"Business1": str(work)}))
    answers = {K.PROFILE: home, K.DOWNLOADS: work / "Downloads"}
    monkeypatch.setattr(K, "_lookup", lambda fid: answers.get(fid))
    assert K.downloads() == work / "Downloads"
    out = K.export_folder()
    assert out == home / "KratosForge" / "Exports" and out.is_dir()
