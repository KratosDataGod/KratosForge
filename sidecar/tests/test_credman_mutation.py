"""Windows Credential Manager (credman.py): what mutation testing found unchecked. The Win32 values the
module sends (a generic credential, kept on this PC, "not found" as Windows numbers it) and the flags of
each call, which Windows reserves and wants as 0. ctypes.WinDLL is a fake; no real credential is touched."""

from __future__ import annotations

import ctypes

import pytest

from fabric_desktop import credman
from tests.test_credman_dpapi_calls import Windows, win  # noqa: F401 - the fake and its fixture


def test_the_win32_values_are_windows_own():
    # wincred.h: CRED_TYPE_GENERIC 1, CRED_PERSIST_LOCAL_MACHINE 2; winerror.h: ERROR_NOT_FOUND 1168.
    assert (credman.CRED_TYPE_GENERIC, credman.CRED_PERSIST_LOCAL_MACHINE, credman.ERROR_NOT_FOUND) == (1, 2, 1168)


@pytest.fixture
def calls(win, monkeypatch):  # noqa: F811
    seen = []
    write, read, delete = win.CredWriteW, win.CredReadW, win.CredDeleteW

    def cred_write(ref, flags):
        seen.append(("write", ref._obj.Flags, ref._obj.Type, ref._obj.AttributeCount, flags))
        return write(ref, flags)

    def cred_read(target, kind, flags, ref):
        seen.append(("read", kind, flags))
        return read(target, kind, flags, ref)

    def cred_delete(target, kind, flags):
        seen.append(("delete", kind, flags))
        return delete(target, kind, flags)

    monkeypatch.setattr(win, "CredWriteW", cred_write)
    monkeypatch.setattr(win, "CredReadW", cred_read)
    monkeypatch.setattr(win, "CredDeleteW", cred_delete)
    return seen


def test_every_call_is_for_a_generic_credential_with_no_flags_or_attributes(win, calls):  # noqa: F811
    credman.write("t", "u", "secret")
    assert credman.read("t") == "secret"
    credman.delete("t")
    assert calls == [("write", 0, 1, 0, 0), ("read", 1, 0), ("delete", 1, 0)]


def test_only_windows_own_not_found_reads_as_nothing(win):  # noqa: F811
    win.fail_with = 1168
    assert credman.read("t") is None
    win.fail_with = 1169
    with pytest.raises(credman.CredentialError, match="error 1169"):
        credman.read("t")
    win.fail_with = 1168
    credman.delete("t")
    assert ctypes.get_last_error() == 1168
