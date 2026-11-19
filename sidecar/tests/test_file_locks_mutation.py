"""Mutation batch 96: file_locks' Restart Manager calls and the names they give back.

The Restart Manager runs for real only on Windows. Here a stand-in for rstrtmgr.dll answers the same
calls through the same ctypes objects, so the session, the sizes, the retry on ERROR_MORE_DATA and the
error paths can be checked on any platform. The module's ``sys`` is swapped, not the process's.
"""

from __future__ import annotations

import ctypes
import types

import pytest

from fabric_desktop import file_locks

_ERROR_MORE_DATA = 234


class FakeRm:
    """rstrtmgr.dll's five calls. ``lists`` is what each RmGetList call reports: (error, holders)."""

    def __init__(self, holders=(), *, start=0, register=0, lists=None):
        self.holders = list(holders)
        self.start = start
        self.register = register
        self.lists = list(lists) if lists is not None else None
        self.calls: list[tuple] = []

    def RmStartSession(self, session_ref, flags, key):  # noqa: N802 - Windows' names
        self.calls.append(("start", flags, len(key)))
        session_ref._obj.value = 4711
        return self.start

    def RmRegisterResources(self, session, nfiles, files, napps, apps, nsvcs, svcs):  # noqa: N802
        self.calls.append(("register", session.value, nfiles, [files[i] for i in range(nfiles)],
                           napps, apps, nsvcs, svcs))
        return self.register

    def RmGetList(self, session, needed_ref, count_ref, infos, reasons_ref):  # noqa: N802
        count = count_ref._obj
        self.calls.append(("list", session.value, count.value, len(infos)))
        err, procs = self.lists.pop(0) if self.lists is not None else (None, self.holders)
        if err is None:
            err = 0 if len(procs) <= count.value else _ERROR_MORE_DATA
        needed_ref._obj.value = len(procs)
        if err == _ERROR_MORE_DATA:
            count.value = 0
            return err
        if err:  # Windows leaves the count as it was on any other failure
            return err
        for i, (name, pid) in enumerate(procs):
            infos[i].strAppName = name
            infos[i].Process.dwProcessId = pid
        count.value = len(procs)
        return 0

    def RmEndSession(self, session):  # noqa: N802
        self.calls.append(("end", session.value))
        return 0


@pytest.fixture
def windows(monkeypatch):
    """Make file_locks believe it is on Windows, with ``rm`` as rstrtmgr.dll."""
    box: dict[str, FakeRm] = {}
    loads: list[tuple] = []

    def win_dll(name, winmode=None):
        loads.append((name, winmode))
        return box["rm"]

    monkeypatch.setattr(file_locks, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)

    def use(rm: FakeRm) -> FakeRm:
        box["rm"] = rm
        return rm
    use.loads = loads  # type: ignore[attr-defined]
    return use


def kinds(rm: FakeRm) -> list[str]:
    return [c[0] for c in rm.calls]


def test_the_programs_holding_a_file_are_named_each_once_in_order(windows, tmp_path):
    rm = windows(FakeRm([("Microsoft Excel", 10), ("Notepad", 11), ("Microsoft Excel", 12)]))
    assert file_locks.holders(tmp_path / "a.csv") == ["Microsoft Excel", "Notepad"]
    assert kinds(rm) == ["start", "register", "list", "list", "end"]


def test_a_program_without_a_name_is_named_by_its_process_id(windows, tmp_path):
    windows(FakeRm([("   ", 4242), ("Notepad", 7)]))
    assert file_locks.holders(tmp_path / "a.csv") == ["process 4242", "Notepad"]


def test_the_dll_comes_from_system32_only(windows, tmp_path):
    windows(FakeRm([("Notepad", 1)]))
    file_locks.holders(tmp_path / "a.csv")
    assert windows.loads == [("rstrtmgr.dll", 0x00000800)]


def test_the_calls_carry_windows_sizes_and_the_one_file(windows, tmp_path):
    rm = windows(FakeRm([("Notepad", 1)]))
    target = tmp_path / "a.csv"
    assert file_locks.holders(target) == ["Notepad"]
    start, register, listing, end = rm.calls
    assert start == ("start", 0, 33)  # CCH_RM_SESSION_KEY + 1
    assert register == ("register", 4711, 1, [str(target)], 0, None, 0, None)
    assert listing == ("list", 4711, 1, 1)  # one holder fits the first buffer: asked once
    assert end == ("end", 4711)


def test_the_process_info_matches_windows_layout(windows, tmp_path):
    seen = []

    class Recording(FakeRm):
        def RmGetList(self, session, needed_ref, count_ref, infos, reasons_ref):  # noqa: N802
            seen.append(type(infos)._type_)
            return super().RmGetList(session, needed_ref, count_ref, infos, reasons_ref)
    windows(Recording([("Notepad", 1)]))
    file_locks.holders(tmp_path / "a.csv")
    info = seen[0]
    wchar = ctypes.sizeof(ctypes.c_wchar)
    assert info.strAppName.size == 256 * wchar  # CCH_RM_MAX_APP_NAME + 1
    assert info.strServiceShortName.size == 64 * wchar  # CCH_RM_MAX_SVC_NAME + 1
    assert [f[0] for f in info._fields_] == ["Process", "strAppName", "strServiceShortName", "ApplicationType",
                                             "AppStatus", "TSSessionId", "bRestartable"]


def test_a_list_that_grows_is_asked_again_with_room_for_it(windows, tmp_path):
    rm = windows(FakeRm(lists=[(None, [("A", 1), ("B", 2)]), (None, [("A", 1), ("B", 2)])]))
    assert file_locks.holders(tmp_path / "a.csv") == ["A", "B"]
    assert [c[2:] for c in rm.calls if c[0] == "list"] == [(1, 1), (2, 2)]


def test_more_data_with_nothing_needed_still_asks_with_room_for_one(windows, tmp_path):
    rm = windows(FakeRm(lists=[(_ERROR_MORE_DATA, []), (None, [("A", 1)])]))
    assert file_locks.holders(tmp_path / "a.csv") == ["A"]
    assert [c[2:] for c in rm.calls if c[0] == "list"] == [(1, 1), (1, 1)]


def test_it_asks_three_times_at_most(windows, tmp_path):
    grows = [(_ERROR_MORE_DATA, [("A", 1)] * n) for n in (2, 3, 4)] + [(None, [("A", 1)])]
    rm = windows(FakeRm(lists=grows))
    assert file_locks.holders(tmp_path / "a.csv") == []
    assert kinds(rm) == ["start", "register", "list", "list", "list", "end"]


def test_a_failed_listing_names_no_one_and_ends_the_session(windows, tmp_path):
    rm = windows(FakeRm(lists=[(5, [])]))
    assert file_locks.holders(tmp_path / "a.csv") == []
    assert kinds(rm) == ["start", "register", "list", "end"]


def test_a_failed_registration_names_no_one_and_ends_the_session(windows, tmp_path):
    rm = windows(FakeRm([("Notepad", 1)], register=87))
    assert file_locks.holders(tmp_path / "a.csv") == []
    assert kinds(rm) == ["start", "register", "end"]


def test_a_session_that_doesnt_start_is_not_used_or_ended(windows, tmp_path):
    rm = windows(FakeRm([("Notepad", 1)], start=87))
    assert file_locks.holders(tmp_path / "a.csv") == []
    assert kinds(rm) == ["start"]


def test_a_missing_restart_manager_names_no_one(windows, monkeypatch, tmp_path):
    windows(FakeRm())

    def missing(name, winmode=None):
        raise OSError(126, "The specified module could not be found")
    monkeypatch.setattr(ctypes, "WinDLL", missing, raising=False)
    assert file_locks.holders(tmp_path / "a.csv") == []


def test_off_windows_the_list_is_empty_even_when_asked_directly(tmp_path):
    assert file_locks._restart_manager_list(str(tmp_path / "a.csv")) == []


def test_exactly_three_holders_are_all_named_with_nothing_more(tmp_path, monkeypatch):
    monkeypatch.setattr(file_locks, "holders", lambda p: ["A", "B", "C"])
    assert file_locks.explain(PermissionError(13, "x"), tmp_path / "a.csv") is None
    exc = PermissionError(13, "held")
    exc.winerror = 32
    assert file_locks.explain(exc, tmp_path / "sales.csv") == (
        "sales.csv couldn't be saved because it is open in A, B, C. Close it in each of them and try again.")
    monkeypatch.setattr(file_locks, "holders", lambda p: ["A", "B", "C", "D"])
    assert file_locks.explain(exc, tmp_path / "sales.csv", "exported") == (
        "sales.csv couldn't be exported because it is open in A, B, C and 1 more. "
        "Close it in each of them and try again.")

