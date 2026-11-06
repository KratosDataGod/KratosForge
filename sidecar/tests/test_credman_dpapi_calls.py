"""Windows Credential Manager (credman.py) and DPAPI (dpapi.py), on any OS: ctypes.WinDLL is stood in for by
fakes of advapi32, crypt32 and kernel32, so the structures the engine builds, the error codes it reads and
the memory it hands back to Windows are checked without Windows. Nothing here touches a real credential."""

import ctypes
import sys

import pytest

from fabric_desktop import credman, dpapi
from fabric_desktop.engines.databricks_auth import CRED_MAX_BLOB


class Fn:
    """One DLL function: argtypes/restype are set by the module under test; calls go to ``impl``."""

    def __init__(self, impl):
        self.impl, self.argtypes, self.restype = impl, None, None

    def __call__(self, *args):
        return self.impl(*args)


class Windows:
    """The Win32 calls credman and dpapi make, over a dict of credentials and a toy cipher."""

    def __init__(self):
        self.creds, self.last_error, self.freed, self.local_freed, self.flags = {}, 0, [], [], []
        self.fail_with = None  # an error code the next call fails with
        self._keep = []

    def _fail(self):
        if self.fail_with is not None:
            self.last_error, self.fail_with = self.fail_with, None
            return True
        return False

    # ---- advapi32
    def CredWriteW(self, ref, flags):
        if self._fail():
            return 0
        c = ref._obj
        blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        self.creds[c.TargetName] = {"type": c.Type, "persist": c.Persist, "user": c.UserName,
                                    "comment": c.Comment, "blob": blob}
        return 1

    def CredReadW(self, target, kind, flags, ref):
        if self._fail():
            return 0
        if target not in self.creds:
            self.last_error = credman.ERROR_NOT_FOUND
            return 0
        _, Credential = credman._api()
        saved = self.creds[target]
        blob = saved.get("raw_blob", saved["blob"])
        buf = ctypes.create_string_buffer(blob, max(len(blob), 1))
        cred = Credential(Type=kind, TargetName=target, CredentialBlobSize=saved.get("size", len(blob)),
                          CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        self._keep += [buf, cred]
        ref._obj.contents = cred
        return 1

    def CredDeleteW(self, target, kind, flags):
        if self._fail():
            return 0
        if self.creds.pop(target, None) is None:
            self.last_error = credman.ERROR_NOT_FOUND
            return 0
        return 1

    def CredFree(self, ptr):
        self.freed.append(ptr)

    # ---- crypt32 (a toy cipher: the purpose must match, as DPAPI's entropy must)
    def _crypt(self, protect):
        def call(data_in, _desc, entropy, _reserved, _prompt, flags, out):
            self.flags.append(flags)
            if self._fail():
                return 0
            data = ctypes.string_at(data_in._obj.pbData, data_in._obj.cbData)
            purpose = ctypes.string_at(entropy._obj.pbData, entropy._obj.cbData)
            if protect:
                result = b"DPAPI" + bytes([len(purpose)]) + purpose + bytes(b ^ 0x5A for b in data)
            else:
                if not data.startswith(b"DPAPI") or data[6:6 + data[5]] != purpose:
                    self.last_error = 13  # ERROR_INVALID_DATA
                    return 0
                result = bytes(b ^ 0x5A for b in data[6 + data[5]:])
            buf = ctypes.create_string_buffer(result, max(len(result), 1))
            self._keep.append(buf)
            out._obj.cbData, out._obj.pbData = len(result), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
            return 1
        return call

    # ---- kernel32
    def LocalFree(self, ptr):
        self.local_freed.append(ptr)
        return None

    def dll(self, name, use_last_error=False):
        assert use_last_error  # every error code is read with get_last_error, not GetLastError
        funcs = {"advapi32": ["CredWriteW", "CredReadW", "CredDeleteW", "CredFree"],
                 "crypt32": ["CryptProtectData", "CryptUnprotectData"], "kernel32": ["LocalFree"]}[name]
        impls = {"CryptProtectData": self._crypt(True), "CryptUnprotectData": self._crypt(False)}
        return type(name, (), {f: Fn(impls.get(f) or getattr(self, f)) for f in funcs})()


@pytest.fixture
def win(monkeypatch):
    w = Windows()
    monkeypatch.setattr(ctypes, "WinDLL", w.dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: w.last_error, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    return w


# --------------------------------------------------------------------------- Credential Manager

def test_a_secret_is_saved_as_utf16_for_this_account_on_this_pc_and_read_back(win):
    credman.write("KratosForge/smtp/mail.contoso.com", "svc-mail", "pä55wörd")
    saved = win.creds["KratosForge/smtp/mail.contoso.com"]
    assert saved["blob"] == "pä55wörd".encode("utf-16-le")
    assert (saved["type"], saved["persist"]) == (credman.CRED_TYPE_GENERIC, credman.CRED_PERSIST_LOCAL_MACHINE)
    assert saved["user"] == "svc-mail" and saved["comment"] == "Saved by KratosForge"
    assert credman.read("KratosForge/smtp/mail.contoso.com") == "pä55wörd"
    assert len(win.freed) == 1  # what CredReadW allocated goes back to Windows


def test_no_user_name_is_saved_as_none(win):
    credman.write("t", "", "x")
    assert win.creds["t"]["user"] is None


def test_a_credential_that_isnt_there_reads_as_none_and_deletes_quietly(win):
    assert credman.read("missing") is None and win.freed == []
    credman.delete("missing")
    credman.write("t", "u", "secret")
    credman.delete("t")
    assert "t" not in win.creds


@pytest.mark.parametrize("call", [lambda: credman.write("t", "u", "hunter2"), lambda: credman.read("t"),
                                  lambda: credman.delete("t")])
def test_a_windows_failure_names_the_credential_and_the_error_never_the_secret(win, call):
    win.creds["t"] = {"type": 1, "persist": 2, "user": "u", "comment": "", "blob": "hunter2".encode("utf-16-le")}
    win.fail_with = 5  # ERROR_ACCESS_DENIED
    with pytest.raises(credman.CredentialError) as err:
        call()
    assert "'t'" in str(err.value) and "error 5" in str(err.value) and "hunter2" not in str(err.value)


@pytest.mark.parametrize("secret", ["", "x" * (CRED_MAX_BLOB // 2 + 1)])
def test_an_empty_or_too_long_secret_is_refused_before_windows_is_called(win, secret):
    with pytest.raises(credman.CredentialError, match=f"1 to {CRED_MAX_BLOB // 2} characters"):
        credman.write("t", "u", secret)
    assert win.creds == {}


def test_the_longest_secret_windows_allows_is_saved(win):
    credman.write("t", "u", "x" * (CRED_MAX_BLOB // 2))
    assert credman.read("t") == "x" * (CRED_MAX_BLOB // 2)


def test_a_blob_larger_than_windows_allows_is_not_read_and_is_still_freed(win):
    win.creds["t"] = {"type": 1, "persist": 2, "user": "u", "comment": "", "blob": b"ab", "size": CRED_MAX_BLOB + 2}
    with pytest.raises(credman.CredentialError, match="larger than Windows allows"):
        credman.read("t")
    assert len(win.freed) == 1


def test_an_empty_blob_or_one_that_isnt_text_is_refused_by_name(win):
    win.creds["empty"] = {"type": 1, "persist": 2, "user": "u", "comment": "", "blob": b""}
    win.creds["odd"] = {"type": 1, "persist": 2, "user": "u", "comment": "", "blob": b"\xff\x00\xd8"}  # neither UTF-8 nor UTF-16
    with pytest.raises(credman.CredentialError, match="'empty' in Windows Credential Manager is empty"):
        credman.read("empty")
    with pytest.raises(credman.CredentialError) as err:
        credman.read("odd")
    assert "odd" in str(err.value) and "\\xff" not in str(err.value) and err.value.__cause__ is None
    assert len(win.freed) == 2


def test_off_windows_reads_find_nothing_and_writes_are_refused(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert credman.read("t") is None
    credman.delete("t")
    with pytest.raises(credman.CredentialError, match="only be saved on Windows"):
        credman.write("t", "u", "secret")


# --------------------------------------------------------------------------- DPAPI

def test_protect_and_unprotect_round_trip_without_ever_prompting(win):
    sealed = dpapi.protect(b"refresh-token-bytes")
    assert sealed != b"refresh-token-bytes" and b"refresh-token-bytes" not in sealed
    assert dpapi.unprotect(sealed) == b"refresh-token-bytes"
    assert win.flags == [dpapi._UI_FORBIDDEN, dpapi._UI_FORBIDDEN]
    assert len(win.local_freed) == 2  # each output buffer goes back to Windows


def test_a_blob_for_another_purpose_or_damaged_is_an_oserror(win):
    sealed = dpapi.protect(b"data", purpose=b"another purpose")
    with pytest.raises(OSError) as err:
        dpapi.unprotect(sealed)
    assert err.value.errno == 13 and "CryptUnprotectData failed" in str(err.value)
    with pytest.raises(OSError):
        dpapi.unprotect(b"not a dpapi blob")
    assert dpapi.unprotect(sealed, purpose=b"another purpose") == b"data"


def test_a_failed_protect_is_an_oserror_and_frees_nothing(win):
    win.fail_with = 8  # ERROR_NOT_ENOUGH_MEMORY
    with pytest.raises(OSError) as err:
        dpapi.protect(b"data")
    assert err.value.errno == 8 and win.local_freed == []


def test_empty_data_round_trips(win):
    assert dpapi.unprotect(dpapi.protect(b"")) == b""


def test_dpapi_is_available_only_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert not dpapi.available()
    monkeypatch.setattr(sys, "platform", "win32")
    assert dpapi.available()
