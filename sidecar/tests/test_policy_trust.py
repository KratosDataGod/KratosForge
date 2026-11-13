"""policy_trust.py: the policy file may widen what KratosForge trusts only when SYSTEM and
Administrators (root off Windows) alone could have written it and its folder, by the shell's rule."""

from __future__ import annotations

import os
import sys

import pytest

from fabric_desktop import policy_trust as pt

ALLOWED, DENIED, INHERIT_ONLY = pt.ACCESS_ALLOWED, pt.ACCESS_DENIED, pt.INHERIT_ONLY


def test_an_admin_owned_path_granting_write_only_to_admins_passes():
    dacl = [(ALLOWED, 0, pt.GENERIC_ALL, True), (ALLOWED, 0, 0x1200A9, False)]  # Users: read and execute
    assert pt.judge("p", True, dacl, pt.FILE_RIGHTS) is None


@pytest.mark.parametrize("owner, dacl, why", [
    (False, [], "isn't owned by SYSTEM or Administrators"),                     # another account created it
    (True, None, "no permissions set"),                                        # a NULL DACL: everyone
    (True, [(ALLOWED, 0, pt.FILE_WRITE_DATA, False)], "someone other than"),
    (True, [(ALLOWED, 0, pt.WRITE_DAC, False)], "someone other than"),
    (True, [(ALLOWED, 0, pt.GENERIC_WRITE, False)], "someone other than"),
    (True, [(5, 0, pt.GENERIC_ALL, False)], "can't check"),                     # an object ACE: not trusted
    (True, [(19, 0, 0, False)], "can't check"),                                 # a type newer than the rule
])
def test_anything_else_fails(owner, dacl, why):
    assert why in pt.judge("p", owner, dacl, pt.FILE_RIGHTS)


def test_entries_that_grant_nothing_are_skipped():
    dacl = [(DENIED, 0, pt.GENERIC_ALL, False), (ALLOWED, INHERIT_ONLY, pt.GENERIC_ALL, False),
            (17, 0, pt.GENERIC_ALL, False), (2, 0, pt.GENERIC_ALL, False)]
    assert pt.judge("p", True, dacl, pt.FILE_RIGHTS) is None


def test_creating_files_in_the_folder_isnt_a_folder_right():
    assert pt.judge("d", True, [(ALLOWED, 0, pt.FILE_WRITE_DATA, False)], pt.FOLDER_RIGHTS) is None  # add file
    assert pt.judge("d", True, [(ALLOWED, 0, pt.FILE_DELETE_CHILD, False)], pt.FOLDER_RIGHTS)


def test_a_file_the_test_account_wrote_isnt_trusted(tmp_path):
    file = tmp_path / "FabricDesktop" / "policy.json"
    file.parent.mkdir()
    file.write_text("{}")
    file.chmod(0o666)  # writable by others, even when the tests run as root
    assert pt.admin_only(file)  # on Windows the temp folder is the account's; elsewhere not root's
    assert pt.admin_only(tmp_path / "missing" / "policy.json")  # can't be checked: not trusted


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner and mode")
def test_off_windows_only_root_may_own_and_write(tmp_path, monkeypatch):
    file = tmp_path / "policy.json"
    file.write_text("{}")
    modes = {str(tmp_path): 0o40755, str(file): 0o100644}
    owners = {str(tmp_path): 0, str(file): 0}
    monkeypatch.setattr(pt.os, "stat", lambda p: os.stat_result((modes[str(p)], 0, 0, 1, owners[str(p)], 0, 2, 0, 0, 0)))
    assert pt.admin_only(file) is None
    modes[str(file)] = 0o100664
    assert "someone other than root" in pt.admin_only(file)
    modes[str(file)], owners[str(tmp_path)] = 0o100644, 1000
    assert "isn't owned by root" in pt.admin_only(file)


def test_an_error_while_checking_is_not_trusted(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "_posix_check", lambda p: 1 / 0)
    monkeypatch.setattr(pt, "_windows_check", lambda p, r: 1 / 0)
    assert "can't be read" in pt.admin_only(tmp_path / "policy.json")


def test_group_policys_key_its_parent_and_its_list_subkeys_are_checked(monkeypatch):
    seen = []
    verdicts = {}
    monkeypatch.setattr(pt.sys, "platform", "win32")
    monkeypatch.setattr(pt, "_windows_check", lambda name, kind, rights: seen.append((name, kind, rights)) or verdicts.get(name))
    key = r"Software\Policies\KratosBI\KratosForge"
    assert pt.key_admin_only(key, ["fabric_view_sign_in_hosts"]) is None
    assert seen == [(r"MACHINE\Software\Policies\KratosBI", pt.SE_REGISTRY_WOW64_64KEY, pt.PARENT_KEY_RIGHTS),
                    (rf"MACHINE\{key}", pt.SE_REGISTRY_WOW64_64KEY, pt.KEY_RIGHTS),
                    (rf"MACHINE\{key}\fabric_view_sign_in_hosts", pt.SE_REGISTRY_WOW64_64KEY, pt.KEY_RIGHTS)]
    verdicts[rf"MACHINE\{key}\fabric_view_sign_in_hosts"] = "someone other than SYSTEM and Administrators can change it"
    assert pt.key_admin_only(key, ["fabric_view_sign_in_hosts"]).startswith("someone other than")
    monkeypatch.setattr(pt, "_windows_check", lambda *a: 1 / 0)
    assert "can't be read" in pt.key_admin_only(key)


def test_off_windows_there_is_no_key_to_trust(monkeypatch):
    monkeypatch.setattr(pt.sys, "platform", "linux")
    assert pt.key_admin_only(r"Software\Policies\KratosBI\KratosForge")


def test_the_object_types_are_windows_values():
    # SE_OBJECT_TYPE (accctrl.h): 0 unknown, 1 file, 2 service, 3 printer, 4 registry key, 5 share,
    # 6 kernel object, 7 window, 8 DS object, 9 DS object all, 10 provider defined, 11 WMI GUID,
    # 12 registry WOW64 32-bit view, 13 registry WOW64 64-bit view. Group Policy writes the 64-bit view.
    assert (pt.SE_FILE_OBJECT, pt.SE_REGISTRY_WOW64_64KEY) == (1, 13)
