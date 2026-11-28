"""credman.py from a mutation run: the numbers Windows reads are Windows' own (a generic credential, kept
on this PC, "not found" as 1168), and every flags argument and field Windows wants zero is zero. ctypes.WinDLL
is stood in for (test_credman_dpapi_calls.Windows), so this runs on every OS and touches no real credential."""

import ctypes
import sys

import pytest

from fabric_desktop import credman

from test_credman_dpapi_calls import Windows


class Recording(Windows):
    """The fake Windows, also keeping each call's arguments and the structure CredWriteW was given."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def CredWriteW(self, ref, flags):
        c = ref._obj
        self.calls.append(("write", c.Flags, c.Type, c.Persist, c.AttributeCount, flags))
        return super().CredWriteW(ref, flags)

    def CredReadW(self, target, kind, flags, ref):
        self.calls.append(("read", kind, flags))
        return super().CredReadW(target, kind, flags, ref)

    def CredDeleteW(self, target, kind, flags):
        self.calls.append(("delete", kind, flags))
        return super().CredDeleteW(target, kind, flags)


@pytest.fixture
def win(monkeypatch):
    w = Recording()
    monkeypatch.setattr(ctypes, "WinDLL", w.dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: w.last_error, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    return w


def test_the_windows_numbers_are_windows_own():
    assert (credman.CRED_TYPE_GENERIC, credman.CRED_PERSIST_LOCAL_MACHINE, credman.ERROR_NOT_FOUND) == (1, 2, 1168)


def test_a_write_is_a_generic_credential_kept_on_this_pc_with_no_flags_or_attributes(win):
    credman.write("t", "u", "secret")  # pragma: allowlist secret (test value)
    assert win.calls == [("write", 0, 1, 2, 0, 0)]


def test_a_read_and_a_delete_ask_for_a_generic_credential_with_no_flags(win):
    credman.write("t", "u", "secret")  # pragma: allowlist secret (test value)
    win.calls.clear()
    assert credman.read("t") == "secret"  # pragma: allowlist secret (test value)
    credman.delete("t")
    assert win.calls == [("read", 1, 0), ("delete", 1, 0)]


def test_only_windows_not_found_reads_as_nothing(win):
    win.fail_with = 1169
    with pytest.raises(credman.CredentialError, match="error 1169"):
        credman.read("t")
    win.fail_with = 1168
    assert credman.read("t") is None
    win.fail_with = 1168
    credman.delete("t")
