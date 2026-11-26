"""audit_anchor.py: the mutants that test_audit_anchor_mutation.py and the module's other tests leave alive. The
event log is read backwards from its first record, a record's names ending on an odd byte are read to the last
whole character, and the Windows error and advapi32 come from ctypes, with use_last_error set. advapi32 is a
stand-in, so this runs on any OS."""

from __future__ import annotations

import ctypes

from fabric_desktop import audit_anchor as A
from test_audit_anchor_cov6 import _Advapi32, _pages, _reader, advapi  # noqa: F401 - advapi is a fixture


def _record(source=A.SOURCE, strings=("anchor",), *, names=None, category=0, data_length=0, signature=b"LfLe",
            at=None, body=None, num_strings=None):
    """An EVENTLOGRECORD as Windows writes it: Length, the 'LfLe' signature, ..., NumStrings at 26,
    EventCategory at 28, StringOffset at 36, DataLength at 40, then the names and the strings."""
    names = (source + "\0PC\0").encode("utf-16-le") + b"\0\0" if names is None else names
    body = "".join(s + "\0" for s in strings).encode("utf-16-le") if body is None else body
    at = 56 + len(names) if at is None else at
    length = at + len(body)
    header = bytearray(56)
    header[0:4] = length.to_bytes(4, "little")
    header[4:8] = signature
    header[26:28] = (len(strings) if num_strings is None else num_strings).to_bytes(2, "little")
    header[28:30] = category.to_bytes(2, "little")
    header[36:40] = at.to_bytes(4, "little")
    header[40:44] = data_length.to_bytes(4, "little")
    return bytes(header) + names + body


def test_the_log_is_read_backwards_one_record_after_another_from_the_start(advapi):  # noqa: F811
    dll = _reader(advapi, _pages(_record(), (38, 0)))
    assert A._windows_reader() == ["anchor"]
    handle, flags, offset = dll.ReadEventLogW.calls[0][:3]
    assert (handle, flags, offset) == (9, 0x0009, 0)


def test_names_ending_on_an_odd_byte_are_read_to_the_last_whole_character():
    names = A.SOURCE.encode("utf-16-le") + b"\x00"  # the source name, then one padding byte
    assert A._record_text(_record(names=names, strings=("anchor",)), 0) == "anchor"


def test_the_windows_error_and_advapi32_come_from_ctypes(monkeypatch):
    loaded = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1502, raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: loaded.append((name, kw)) or "dll", raising=False)
    assert A._last_error() == 1502
    assert A._advapi32() == "dll" and loaded == [("advapi32", {"use_last_error": True})]










