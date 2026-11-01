"""Reading a credman: secret from Windows Credential Manager (databricks_auth._credman_read), against a
fake advapi32 so it runs on every OS, plus one call to the real one on Windows. Covers a missing
entry, a blob that isn't text, Windows' size limit, that the credential is always freed, and that
the secret never reaches an error or the log."""

from __future__ import annotations

import ctypes
import logging
import sys

import pytest

from fabric_desktop.engines import databricks_auth as dba

SECRET = "dapi-0123456789abcdef-secret"


class FakeAdvapi:
    """CredReadW fills the caller's pointer with a credential holding ``blob``; CredFree records it."""

    def __init__(self, blob: bytes | None, size: int | None = None):
        self.blob, self.size = blob, size
        self.read, self.freed = [], 0
        self._keep = []

    def CredReadW(self, target, kind, flags, ref):  # noqa: N802 - Windows' name
        self.read.append((target, kind, flags))
        if self.blob is None:
            return 0
        pointer = ref._obj
        cred = pointer._type_()
        data = (ctypes.c_ubyte * max(1, len(self.blob))).from_buffer_copy(self.blob or b"\0")
        self._keep += [cred, data]
        cred.CredentialBlobSize = len(self.blob) if self.size is None else self.size
        cred.CredentialBlob = ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte))
        pointer.contents = cred
        return 1

    def CredFree(self, _pointer):  # noqa: N802
        self.freed += 1


@pytest.fixture
def advapi(monkeypatch):
    def install(blob, size=None):
        fake = FakeAdvapi(blob, size)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(dba, "_advapi", lambda: fake)
        return fake
    return install


@pytest.mark.parametrize("blob", [SECRET.encode("utf-16-le"), SECRET.encode("utf-16-le") + b"\0\0",
                                  SECRET.encode("utf-8"), SECRET.encode("utf-8") + b"\0"])
def test_a_secret_saved_as_utf16_or_utf8_reads_back_the_same(advapi, blob):
    fake = advapi(blob)
    assert dba.resolve_secret("credman:KratosForge/dbx") == SECRET
    assert fake.read == [("KratosForge/dbx", 1, 0)] and fake.freed == 1


def test_an_even_length_utf8_secret_isnt_misread_as_utf16(advapi):
    advapi(b"abcd")   # read as UTF-16 this would be two other characters, a wrong secret
    assert dba.resolve_secret("credman:t") == "abcd"


def test_a_non_ascii_utf16_secret_reads_back(advapi):
    advapi("pässwörd-✓".encode("utf-16-le"))
    assert dba.resolve_secret("credman:t") == "pässwörd-✓"


def test_a_utf16_secret_with_no_zero_byte_is_read_as_utf8_the_documented_ambiguity(advapi):
    # "中中" in UTF-16 is 2D 4E 2D 4E, which is also the UTF-8 text "-N-N". Pinned so a change to the
    # order of the two readings is a deliberate one (see credman_text's docstring).
    advapi("中中".encode("utf-16-le"))
    assert dba.resolve_secret("credman:t") == "-N-N"


def test_a_missing_entry_names_the_credential_and_frees_nothing(advapi):
    fake = advapi(None)
    with pytest.raises(dba.DatabricksAuthError, match="no generic credential named 'KratosForge/dbx'"):
        dba.resolve_secret("credman:KratosForge/dbx")
    assert fake.freed == 0


@pytest.mark.parametrize("blob", [b"", b"\0\0", b"\0"])
def test_an_empty_credential_is_refused(advapi, blob):
    fake = advapi(blob)
    with pytest.raises(dba.DatabricksAuthError, match="is empty"):
        dba.resolve_secret("credman:t")
    assert fake.freed == 1


@pytest.mark.parametrize("blob", [b"\xff\xfe\x00\xd8x",          # odd length and not UTF-8
                                  b"\x00\xd8\x41\x00",          # a lone UTF-16 surrogate
                                  b"\x80" + SECRET.encode()])   # not UTF-8, odd length
def test_a_blob_that_isnt_text_is_refused_without_quoting_it(advapi, blob):
    fake = advapi(blob)
    with pytest.raises(dba.DatabricksAuthError, match="isn't readable text") as exc:
        dba.resolve_secret("credman:t")
    assert SECRET not in str(exc.value) and repr(blob) not in str(exc.value) and fake.freed == 1


def test_a_size_beyond_windows_limit_is_refused_before_reading_and_still_freed(advapi):
    fake = advapi(SECRET.encode(), size=dba.CRED_MAX_BLOB + 1)
    with pytest.raises(dba.DatabricksAuthError, match=f"larger than Windows allows \\({dba.CRED_MAX_BLOB} bytes\\)"):
        dba.resolve_secret("credman:t")
    assert fake.freed == 1


def test_a_secret_at_windows_limit_is_read(advapi):
    advapi(b"a" * dba.CRED_MAX_BLOB)
    assert dba.resolve_secret("credman:t") == "a" * dba.CRED_MAX_BLOB


def test_the_secret_never_reaches_the_log_or_an_error(advapi, caplog):
    caplog.set_level(logging.DEBUG)
    advapi(SECRET.encode("utf-16-le"))
    assert dba.resolve_secret("credman:t") == SECRET
    errors = []
    for blob, size in ((SECRET.encode() + b"\x80", None), (SECRET.encode(), dba.CRED_MAX_BLOB + 1)):
        advapi(blob, size)
        with pytest.raises(dba.DatabricksAuthError) as exc:
            dba.resolve_secret("credman:t")
        errors.append(str(exc.value))
    assert all(SECRET not in e for e in errors)
    assert SECRET not in caplog.text


def test_off_windows_credman_says_it_is_windows_only(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(dba, "_advapi", lambda: pytest.fail("advapi32 opened off Windows"))
    with pytest.raises(dba.DatabricksAuthError, match="on Windows only"):
        dba.resolve_secret("credman:t")


@pytest.mark.skipif(sys.platform != "win32", reason="the real Credential Manager is on Windows only")
def test_the_real_credential_manager_says_a_missing_entry_is_missing():
    with pytest.raises(dba.DatabricksAuthError, match="no generic credential named"):
        dba.resolve_secret("credman:KratosForge/test-never-saved-7f3a9c")
