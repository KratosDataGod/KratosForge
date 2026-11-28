"""Windows' Restart Manager, asked which program has a file open, faked so it runs on every runner.

holders() asks rstrtmgr.dll through ctypes; these tests stand in a fake DLL for ctypes.WinDLL and make
the module think it is on Windows, so the names it reads back, the retry when the list grows, the
failures that leave no names and the session being ended every time are checked off Windows too.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import file_locks

_MORE_DATA = 234


def _locked(winerror: int = 32) -> PermissionError:
    exc = PermissionError(13, "The process cannot access the file because it is being used by another process")
    exc.winerror = winerror
    return exc


class _FakeRestartManager:
    """rstrtmgr.dll's calls holders makes, answering from ``lists``: one (error, [(pid, name)]) per
    RmGetList call, the last repeated."""

    def __init__(self, lists, start_err=0, register_err=0):
        self.lists = list(lists)
        self.start_err = start_err
        self.register_err = register_err
        self.registered: list[str] = []
        self.ended: list[int] = []
        self.list_sizes: list[int] = []

    def RmStartSession(self, session_ref, flags, key):  # noqa: N802 - Windows' name
        session_ref._obj.value = 7
        return self.start_err

    def RmRegisterResources(self, session, n_files, files, n_apps, apps, n_svcs, svcs):  # noqa: N802
        assert session.value == 7 and n_files == 1
        self.registered.append(files[0])
        return self.register_err

    def RmGetList(self, session, needed_ref, count_ref, infos, reasons_ref):  # noqa: N802
        err, procs = self.lists.pop(0) if len(self.lists) > 1 else self.lists[0]
        self.list_sizes.append(count_ref._obj.value)
        needed_ref._obj.value = len(procs)
        if err == _MORE_DATA or len(procs) > count_ref._obj.value:
            return _MORE_DATA
        for slot, (pid, name) in zip(infos, procs, strict=False):
            slot.Process.dwProcessId = pid
            slot.strAppName = name
        count_ref._obj.value = len(procs)
        return err

    def RmEndSession(self, session):  # noqa: N802
        self.ended.append(session.value)
        return 0


@pytest.fixture
def windows(monkeypatch):
    """Make file_locks believe it runs on Windows and load ``fake`` for rstrtmgr.dll."""
    loads = []

    def install(fake):
        def win_dll(name, winmode=None):
            loads.append((name, winmode))
            if isinstance(fake, BaseException):
                raise fake
            return fake
        monkeypatch.setattr(file_locks, "sys", SimpleNamespace(platform="win32"))
        monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
        return loads
    return install


def test_names_each_program_once_and_ends_the_session(tmp_path, windows):
    rm = _FakeRestartManager([(0, [(4100, "Microsoft Excel")])])
    loads = windows(rm)
    target = tmp_path / "sales.csv"
    assert file_locks.holders(target) == ["Microsoft Excel"]
    assert loads == [("rstrtmgr.dll", 0x800)]  # from System32 only
    assert rm.registered == [str(target)]
    assert rm.ended == [7]


def test_a_list_longer_than_asked_for_is_read_again_at_its_size(tmp_path, windows):
    # Two Excel windows (one name), and a program Windows gives no name, shown by its process id.
    procs = [(1, "Microsoft Excel"), (2, "Microsoft Excel"), (4242, "   ")]
    rm = _FakeRestartManager([(_MORE_DATA, procs), (0, procs)])
    windows(rm)
    assert file_locks.holders(tmp_path / "sales.csv") == ["Microsoft Excel", "process 4242"]
    assert rm.list_sizes == [1, 3]
    assert rm.ended == [7]


def test_nobody_holding_the_file_names_no_one(tmp_path, windows):
    rm = _FakeRestartManager([(0, [])])
    windows(rm)
    assert file_locks.holders(tmp_path / "free.csv") == []
    assert rm.ended == [7]


def test_a_list_that_keeps_growing_gives_up_after_three_tries(tmp_path, windows, caplog):
    rm = _FakeRestartManager([(_MORE_DATA, [])])  # needed 0: still asks with room for one
    windows(rm)
    with caplog.at_level(logging.INFO, logger=file_locks.__name__):
        assert file_locks.holders(tmp_path / "sales.csv") == []
    assert rm.list_sizes == [1, 1, 1]
    assert rm.ended == [7]
    assert "RmGetList failed" in caplog.text and "sales.csv" in caplog.text


def test_a_failed_list_names_no_one_and_ends_the_session(tmp_path, windows, caplog):
    rm = _FakeRestartManager([(5, [])])
    windows(rm)
    with caplog.at_level(logging.INFO, logger=file_locks.__name__):
        assert file_locks.holders(tmp_path / "sales.csv") == []
    assert rm.ended == [7]
    assert "RmGetList failed" in caplog.text


def test_a_file_restart_manager_wont_take_names_no_one_and_ends_the_session(tmp_path, windows, caplog):
    rm = _FakeRestartManager([(0, [(1, "Microsoft Excel")])], register_err=87)
    windows(rm)
    with caplog.at_level(logging.INFO, logger=file_locks.__name__):
        assert file_locks.holders(tmp_path / "sales.csv") == []
    assert rm.ended == [7]
    assert "RmRegisterResources failed" in caplog.text


def test_a_session_that_wont_start_names_no_one_and_is_not_ended(tmp_path, windows, caplog):
    rm = _FakeRestartManager([(0, [(1, "Microsoft Excel")])], start_err=121)
    windows(rm)
    with caplog.at_level(logging.INFO, logger=file_locks.__name__):
        assert file_locks.holders(tmp_path / "sales.csv") == []
    assert rm.registered == [] and rm.ended == []
    assert "RmStartSession failed" in caplog.text


@pytest.mark.parametrize("failure", [OSError("rstrtmgr.dll not found"), AttributeError("RmStartSession")])
def test_no_restart_manager_names_no_one(tmp_path, windows, failure):
    windows(failure)
    assert file_locks.holders(tmp_path / "sales.csv") == []


def test_the_message_names_the_program_restart_manager_reports(tmp_path, windows):
    windows(_FakeRestartManager([(0, [(9, "Microsoft Excel"), (10, "Notepad")])]))
    err = file_locks.locked_error(_locked(), tmp_path / "sales.csv", "exported")
    assert str(err) == ("sales.csv couldn't be exported because it is open in Microsoft Excel, Notepad. "
                        "Close it in each of them and try again.")
    assert err.winerror == 32 and err.errno == 13 and err.path == str(tmp_path / "sales.csv")


def test_an_error_that_isnt_a_held_file_has_no_locked_error(tmp_path):
    assert file_locks.locked_error(OSError(28, "No space left on device"), tmp_path / "a.csv") is None


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows this asks the real Restart Manager")
def test_the_restart_manager_call_itself_is_a_no_op_off_windows(tmp_path):
    assert file_locks._restart_manager_list(str(tmp_path / "a.csv")) == []
