"""A save Windows keeps refusing because a program has the file open says which file and which program.

jsonfile.replace waits out a brief hold (a virus scanner, the indexer, OneDrive). A program that keeps
the file open, such as Excel, still has Windows refusing after the wait; the error then names the file
and, where Windows' Restart Manager says, the program. The Restart Manager case runs for real on the
windows-latest runner, with a second Python process holding the file.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time

import pytest

from fabric_desktop import file_locks, jsonfile


def _locked(winerror: int = 32) -> PermissionError:
    exc = PermissionError(13, "The process cannot access the file because it is being used by another process")
    exc.winerror = winerror
    return exc


def test_only_windows_refusing_an_open_file_is_explained(tmp_path):
    assert file_locks.explain(PermissionError(13, "denied"), tmp_path / "a.csv") is None  # no Windows code
    assert file_locks.explain(_locked(2), tmp_path / "a.csv") is None
    assert file_locks.explain(OSError(28, "No space left on device"), tmp_path / "a.csv") is None
    for code in (5, 32, 33):
        assert file_locks.explain(_locked(code), tmp_path / "a.csv") is not None


def test_the_message_names_the_file_and_the_programs_windows_names(tmp_path, monkeypatch):
    monkeypatch.setattr(file_locks, "holders", lambda p: ["Microsoft Excel"])
    assert file_locks.explain(_locked(), tmp_path / "sales.csv") == (
        "sales.csv couldn't be saved because it is open in Microsoft Excel. Close it there and try again.")
    monkeypatch.setattr(file_locks, "holders", lambda p: ["A", "B", "C", "D", "E"])
    assert file_locks.explain(_locked(), tmp_path / "sales.csv") == (
        "sales.csv couldn't be saved because it is open in A, B, C and 2 more. Close it in each of them and try again.")


def test_without_names_it_says_what_usually_holds_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(file_locks, "holders", lambda p: [])
    msg = file_locks.explain(_locked(), tmp_path / "settings.json")
    assert msg == ("settings.json couldn't be saved because another program has it open, such as Excel. "
                   "Close the file there, or wait a moment, and try again.")


def test_access_denied_without_names_doesnt_blame_another_program(tmp_path, monkeypatch):
    # WinError 5 also comes from a read-only file or a folder the account can't change.
    monkeypatch.setattr(file_locks, "holders", lambda p: [])
    assert file_locks.explain(_locked(5), tmp_path / "settings.json") == (
        "settings.json couldn't be saved. Another program may have it open, or this account can't change it. "
        "Close the file if it is open, check that it isn't read-only, and try again.")


@pytest.mark.skipif(sys.platform == "win32", reason="off Windows there is no Restart Manager to ask")
def test_off_windows_no_program_is_named(tmp_path):
    assert file_locks.holders(tmp_path) == []


def test_a_replace_still_refused_after_the_wait_says_which_file_and_keeps_the_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)
    monkeypatch.setattr(file_locks, "holders", lambda p: ["Microsoft Excel"])

    def held(a, b):
        raise _locked()
    monkeypatch.setattr(jsonfile.os, "replace", held)
    with pytest.raises(PermissionError) as caught:
        jsonfile.replace(tmp_path / "new", tmp_path / "sales.csv")
    # Only the message: no "[WinError 32]" before it and no path after it, as a toast or dialog shows it.
    assert str(caught.value) == ("sales.csv couldn't be saved because it is open in Microsoft Excel. "
                                 "Close it there and try again.")
    assert caught.value.errno == 13 and caught.value.winerror == 32
    assert caught.value.path == os.fspath(tmp_path / "sales.csv")
    assert isinstance(caught.value.__cause__, PermissionError)


def test_a_refusal_windows_doesnt_code_as_a_held_file_is_raised_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)
    original = PermissionError(13, "busy")

    def busy(a, b):
        raise original
    monkeypatch.setattr(jsonfile.os, "replace", busy)
    with pytest.raises(PermissionError) as caught:
        jsonfile.replace(tmp_path / "a", tmp_path / "b")
    assert caught.value is original


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' Restart Manager")
def test_windows_names_the_process_that_has_the_file_open(tmp_path):
    target = tmp_path / "held.csv"
    target.write_text("id\n1\n")
    child = subprocess.Popen([sys.executable, "-c", "import sys; f = open(sys.argv[1], 'rb'); sys.stdin.read()",
                              str(target)], stdin=subprocess.PIPE)
    try:
        found = []
        for _ in range(50):  # until the child has opened it
            found = file_locks.holders(target)
            if found:
                break
            time.sleep(0.1)
        assert found, "Restart Manager named no program for a file another process has open"
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    assert file_locks.holders(target) == []


def _windows_error(cls: type[OSError], err: int, winerror: int, text: str) -> OSError:
    exc = cls(err, text)
    exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


def test_a_read_only_file_is_named_as_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(file_locks, "holders", lambda p: [])
    target = tmp_path / "project.toml"
    target.write_text("x")
    os.chmod(target, stat.S_IREAD)   # the read-only attribute on Windows; the mode bits say so even for root
    assert file_locks.explain(_locked(5), target) == (
        "project.toml couldn't be saved because it is read-only. Clear Read-only in its Properties, then try again.")
    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)   # writable again: back to the Access-is-denied text
    assert "may have it open" in file_locks.explain(_locked(5), target)


def test_a_file_windows_security_blocks_is_refused_at_once_and_names_it(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(jsonfile.time, "sleep", waits.append)
    virus = _windows_error(OSError, 22, 225, "Operation did not complete successfully because the file contains a virus")

    def blocked(a, b):
        raise virus
    monkeypatch.setattr(jsonfile.os, "replace", blocked)
    with pytest.raises(OSError) as caught:
        jsonfile.write_text(tmp_path / "sales.csv", "id\n1\n")
    assert str(caught.value) == ("sales.csv couldn't be saved because Windows Security blocked it. Check "
                                 "Protection history in Windows Security, then try again.")
    assert caught.value.winerror == 225 and caught.value.__cause__ is virus
    assert waits == []   # not waited out: a scan's verdict doesn't change in 1.5 s
    assert list(tmp_path.iterdir()) == []   # no partial file and no temporary file left


def test_a_folder_controlled_folder_access_protects_is_refused_by_name_and_leaves_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "Documents"
    folder.mkdir()
    denied = _windows_error(PermissionError, 13, 5, "Access is denied")

    def mkstemp(*a, **k):   # Controlled folder access refuses even a new file in a protected folder
        raise denied
    monkeypatch.setattr(jsonfile.tempfile, "mkstemp", mkstemp)
    with pytest.raises(PermissionError) as caught:
        jsonfile.write_json(folder / "settings.json", {"a": 1})
    assert str(caught.value) == (
        "settings.json couldn't be saved because Windows won't let KratosForge write in Documents. If Controlled "
        "folder access is on in Windows Security, allow KratosForge there, or use another folder.")
    assert caught.value.winerror == 5 and caught.value.__cause__ is denied
    assert list(folder.iterdir()) == []


def test_a_notebook_save_into_a_protected_folder_is_refused_by_name(tmp_path, monkeypatch):
    from fabric_desktop import notebook

    real_open = open
    denied = _windows_error(PermissionError, 13, 5, "Access is denied")

    def guarded(file, *a, **k):
        if str(file).endswith(".tmp"):
            raise denied
        return real_open(file, *a, **k)
    monkeypatch.setattr("builtins.open", guarded)
    target = tmp_path / "Load sales.ipynb"
    with pytest.raises(PermissionError, match="^Load sales.ipynb couldn't be saved because Windows won't let"):
        notebook._write_atomic(target, "{}")
    assert list(tmp_path.iterdir()) == []


def test_other_failures_making_the_temporary_file_are_raised_as_they_were(tmp_path, monkeypatch):
    full = OSError(28, "No space left on device")

    def mkstemp(*a, **k):
        raise full
    monkeypatch.setattr(jsonfile.tempfile, "mkstemp", mkstemp)
    with pytest.raises(OSError) as caught:
        jsonfile.write_text(tmp_path / "a.json", "{}")
    assert caught.value is full


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to replace a read-only file; Linux renames over it")
def test_saving_over_a_read_only_file_refuses_and_leaves_it_and_no_temporary_file(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonfile.time, "sleep", lambda s: None)
    target = tmp_path / "project.toml"
    target.write_text("old")
    os.chmod(target, stat.S_IREAD)
    try:
        with pytest.raises(PermissionError, match="^project.toml couldn't be saved because it is read-only"):
            jsonfile.write_text(target, "new")
        assert target.read_text() == "old"
        assert [p.name for p in tmp_path.iterdir()] == ["project.toml"]
    finally:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
