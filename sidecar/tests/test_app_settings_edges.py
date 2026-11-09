"""App settings and the organization's policy file: the edges the main tests don't reach. A settings
file that can't be set aside or opened, a last-good policy copy that can't be written or dated,
policy values that aren't a client id, a skin or an https engine folder, who can change the policy
file (on POSIX, as root, and on Windows through a stand-in for its rights check), and the SQL page's
limits and temp folder."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fabric_desktop import app_settings as s
from fabric_desktop import jsonfile

# conftest stands these in for every test (saved_policy_in_tmp); these tests check the real ones.
user_can_change = s.user_can_change
saved_policy_path = s.saved_policy_path


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.delenv("FABRIC_DESKTOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("FABRIC_DESKTOP_BUILTIN_CLIENT_ID", raising=False)
    monkeypatch.delenv("FABRIC_DESKTOP_ENGINE_URL_IGNORED", raising=False)
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    return policy


def test_a_broken_settings_file_that_cant_be_set_aside_still_reads_as_defaults(dirs, monkeypatch):
    path = s.user_path()
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    def refuse(*a, **kw):
        raise PermissionError("in use")

    monkeypatch.setattr(jsonfile, "set_aside", refuse)
    assert s._read(path) == {}
    assert path.read_text(encoding="utf-8") == "{broken"  # left in place


def test_a_settings_file_that_cant_be_opened_reads_as_defaults(dirs, monkeypatch):
    def refuse(path):
        raise PermissionError("locked")

    monkeypatch.setattr(jsonfile, "read_text", refuse)
    assert s._read(s.user_path()) == {}


def test_the_last_good_policy_copy_is_optional_and_dated_when_it_can_be(dirs, monkeypatch):
    assert saved_policy_path() == s.user_path().with_name("policy-last-good.json")
    saved = s.saved_policy_path()
    real = jsonfile.write_text

    def refuse(path, text):
        if path == saved:
            raise PermissionError("read-only")
        return real(path, text)

    monkeypatch.setattr(jsonfile, "write_text", refuse)
    s._save_copy(saved, '{"telemetry": false}')  # no copy this time; nothing raised
    assert not saved.exists()
    assert s._saved_at(saved) == "saved earlier"


def test_a_broken_policy_falls_back_to_the_saved_copy_with_its_date(dirs):
    dirs.write_text('{"telemetry": false}', encoding="utf-8")
    assert s.policy_problems() == []  # read once, so its last good copy is kept
    dirs.write_text("[1, 2]", encoding="utf-8")
    data, reason, from_copy = s._policy_file()
    assert from_copy and data == {"telemetry": False} and "must hold one JSON object" in reason and "saved 20" in reason


@pytest.mark.parametrize("policy, problem", [
    ({"client_id": "not-a-guid"}, "client_id isn't an app (client) ID"),
    ({"ui_skin": "Fancy Skin"}, "ui_skin must be a skin id"),
])
def test_policy_values_that_dont_fit_are_named(dirs, policy, problem, monkeypatch):
    monkeypatch.setattr(s, "user_can_change", lambda p: False)
    dirs.write_text(json.dumps(policy), encoding="utf-8")
    assert any(problem in p for p in s.policy_problems())


@pytest.mark.parametrize("url, ok", [("https://files.contoso.com/kf", True), ("https://files.contoso.com:8443/kf", True),
                                     ("https://files.contoso.com:99999999/kf", False), ("http://files.contoso.com", False),
                                     ("https://files.contoso.com/a\x01b", False), ("https://bad host/", False),
                                     ("https://files.contoso.com:abc/", False)])
def test_engine_folder_addresses(url, ok):
    assert s.engine_url_ok(url) is ok


def test_engine_folder_the_shell_ignored_says_why(dirs, monkeypatch):
    monkeypatch.setenv("FABRIC_DESKTOP_ENGINE_URL_IGNORED", "the file can be changed by users")
    problems = s._engine_url_problems("https://files.contoso.com/kf", False)
    assert problems and "ignored" in problems[0] and "the file can be changed by users" in problems[0]
    assert "auto_update" in s._engine_url_problems("https://files.contoso.com/kf", True)[0]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership and permission bits")
def test_who_can_change_the_policy_file_on_posix(dirs, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 12345)  # CI may run as root
    assert user_can_change(dirs) is False  # not there
    dirs.write_text("{}", encoding="utf-8")
    assert user_can_change(dirs) is True  # a file this account can write
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert user_can_change(dirs) is False  # an administrator isn't counted

    def broken(*a, **kw):
        raise PermissionError("can't stat")

    monkeypatch.setattr(os, "geteuid", lambda: 12345)
    monkeypatch.setattr(os, "access", broken)
    assert user_can_change(dirs) is False


class Kernel32:
    """CreateFileW opens only for the rights in ``allowed``; every handle is closed again."""

    def __init__(self, allowed: dict[str, set[int]]):
        self.allowed, self.opened, self.closed = allowed, [], 0
        kernel = self

        class CreateFileW:  # a ctypes function: the code sets restype and argtypes on it
            def __call__(self, *args):
                return kernel._create(*args)

        self.CreateFileW = CreateFileW()

    def _create(self, target, access, share, sec, disposition, flags, template):
        self.opened.append((Path(target).name, access, share, disposition))
        return 7 if access in self.allowed.get(Path(target).name, set()) else -1

    def CloseHandle(self, handle):  # noqa: N802 - the Windows name
        self.closed += 1


def windows(monkeypatch, admin: bool, kernel: Kernel32):
    import ctypes
    from ctypes import wintypes
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: admin),
                                                          kernel32=kernel), raising=False)
    monkeypatch.setattr(wintypes.HANDLE, "value", property(lambda self: -1), raising=False)
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.mark.parametrize("allowed, admin, expected", [
    ({}, False, False),
    ({"policy.json": {0x10000}}, False, True),  # DELETE on the file
    ({"FabricDesktop": {0x40}}, False, True),  # FILE_DELETE_CHILD on the folder
    ({"FabricDesktop": {0x4}}, False, False),  # creating files in the folder isn't a right to replace IT's
    ({"policy.json": {0x2}}, True, False),  # an administrator isn't counted
])
def test_who_can_change_the_policy_file_on_windows(dirs, monkeypatch, allowed, admin, expected):
    dirs.write_text("{}", encoding="utf-8")
    kernel = Kernel32(allowed)
    windows(monkeypatch, admin, kernel)
    assert user_can_change(dirs) is expected
    assert kernel.closed == sum(1 for name, access, _, _ in kernel.opened if access in allowed.get(name, set()))
    assert all(share == 0x7 and disposition == 3 for _, _, share, disposition in kernel.opened)  # opens only, shares all


def test_sql_limits_and_temp_folder_are_checked_when_saved(dirs, tmp_path):
    with pytest.raises(ValueError, match="sql_page_rows must be a whole number"):
        s.save({"sql_page_rows": "lots"})
    with pytest.raises(ValueError, match="sql_page_rows must be between"):
        s.save({"sql_page_rows": 5})
    with pytest.raises(ValueError, match="sql_temp_dir must be a full folder path"):
        s.save({"sql_temp_dir": "relative/temp"})
    s.save({"sql_page_rows": 500, "sql_temp_dir": str(tmp_path)})
    assert s.value("sql_page_rows") == 500 and s.value("sql_temp_dir") == str(tmp_path)
    s.save({"sql_page_rows": "", "sql_temp_dir": ""})  # back to the defaults
    assert s.value("sql_page_rows") == s.DEFAULTS["sql_page_rows"]
