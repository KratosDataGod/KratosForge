"""Mutation tests for audit_anchor.py: the behaviour its other tests left loose. The exact ReportEventW and
ReadEventLogW calls, the record limit and buffer rules, EVENTLOGRECORD fields read at their exact offsets (a
record with a signature, a category, data, names ending on an odd byte, a string that fills the record), the
writer and reader chosen at import, the minute's pause and the cache at their exact ends, and the parts of an
anchor (frozen, a 32-character fingerprint, junk that isn't text). advapi32 is a stand-in, so this runs on any
OS."""

from __future__ import annotations

import ctypes
import dataclasses
import runpy
import sys
from types import SimpleNamespace

import pytest

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


def test_a_record_is_reported_as_one_information_event_with_one_string(advapi):  # noqa: F811
    calls = []
    advapi(RegisterEventSourceW=lambda server, source: 77,
           ReportEventW=lambda *args: calls.append(args[:7] + (args[7][0], args[8])) or 1)
    A._windows_writer("text")
    assert calls == [(77, 4, 0, 1000, None, 1, 0, "text", None)]


def test_the_log_is_read_backwards_one_record_after_another_from_the_start(advapi):  # noqa: F811
    dll = _reader(advapi, _pages(_record(), (38, 0)))
    assert A._windows_reader() == ["anchor"]
    handle, flags, offset = dll.ReadEventLogW.calls[0][:3]
    assert (handle, flags, offset) == (9, 0x0009, 0)


def test_records_with_the_lfle_signature_follow_each_other(advapi):  # noqa: F811
    page = _record(strings=("first",)) + _record(strings=("second",))
    _reader(advapi, _pages(page, (38, 0)))
    assert A._windows_reader() == ["first", "second"]


def test_at_most_2000_records_are_read(advapi):  # noqa: F811
    one = _record(strings=("r",))
    per_page = (64 * 1024) // len(one)
    pages = [one * per_page for _ in range(2000 // per_page + 1)]
    _reader(advapi, _pages(*pages))
    assert len(A._windows_reader()) == 2000


def test_a_record_of_exactly_the_header_counts_toward_the_limit(advapi, monkeypatch):  # noqa: F811
    monkeypatch.setattr(A, "READ_RECORDS", 2)
    bare = bytearray(56)
    bare[0:4] = (56).to_bytes(4, "little")
    _reader(advapi, _pages(_record(strings=("r0",)) + bytes(bare), _record(strings=("r1",)), (38, 0)))
    assert A._windows_reader() == ["r0"]


def test_a_too_small_error_asking_for_the_same_size_is_a_failed_read(advapi):  # noqa: F811
    _reader(advapi, _pages((122, 64 * 1024)))
    with pytest.raises(OSError, match="ReadEventLogW failed"):
        A._windows_reader()


def test_a_record_with_no_strings_and_a_category_is_not_read():
    assert A._record_text(_record(strings=("x",), num_strings=0, category=1), 0) is None


def test_a_record_with_data_after_its_strings_is_read():
    assert A._record_text(_record(strings=("anchor",), data_length=4), 0) == "anchor"


def test_strings_that_start_at_the_records_end_are_not_read():
    names = (A.SOURCE + "\0").encode("utf-16-le")
    assert A._record_text(_record(names=names, body=b""), 0) is None


def test_names_ending_on_an_odd_byte_are_read_to_the_last_whole_character():
    names = A.SOURCE.encode("utf-16-le") + b"\x00"  # the source name, then one padding byte
    assert A._record_text(_record(names=names, strings=("anchor",)), 0) == "anchor"


def test_a_string_with_an_odd_byte_after_it_ends_at_its_last_whole_character():
    assert A._record_text(_record(body="anchor".encode("utf-16-le") + b"\x00"), 0) == "anchor"


def test_off_windows_nothing_is_written_or_read_and_no_warning_has_been_given():
    fresh = runpy.run_path(A.__file__)
    if sys.platform == "win32":
        assert fresh["writer"].__name__ == "_windows_writer" and fresh["reader"].__name__ == "_windows_reader"
    else:
        assert fresh["writer"].__name__ == "_no_writer" and fresh["reader"].__name__ == "_no_reader"
    assert fresh["_warned"] is False


def test_the_windows_error_and_advapi32_come_from_ctypes(monkeypatch):
    loaded = []
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1502, raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: loaded.append((name, kw)) or "dll", raising=False)
    assert A._last_error() == 1502
    assert A._advapi32() == "dll" and loaded == [("advapi32", {"use_last_error": True})]


def test_after_a_failed_write_the_event_log_is_tried_again_at_exactly_a_minute(monkeypatch):
    clock = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(A, "time", SimpleNamespace(monotonic=lambda: clock.t))
    monkeypatch.setattr(A, "_off_until", 0.0)
    monkeypatch.setattr(A, "_warned", True)
    written = []

    def failing(text):
        raise OSError("full")
    monkeypatch.setattr(A, "writer", failing)
    A.record("folder", "h", 1)
    monkeypatch.setattr(A, "writer", written.append)
    clock.t = 1000.0 + A.RETRY_SECONDS - 0.5
    A.record("folder", "h", 2)
    assert written == []
    clock.t = 1000.0 + A.RETRY_SECONDS
    A.record("folder", "h", 3)
    assert len(written) == 1


def test_a_cached_read_is_used_until_exactly_cache_seconds(monkeypatch, tmp_path):
    clock = SimpleNamespace(t=500.0)
    monkeypatch.setattr(A, "time", SimpleNamespace(monotonic=lambda: clock.t))
    monkeypatch.setattr(A, "_cache", {})
    reads = []
    monkeypatch.setattr(A, "reader", lambda: reads.append(1) or [])
    A.newest(tmp_path, key="k")
    clock.t = 500.0 + A.CACHE_SECONDS - 0.5
    A.newest(tmp_path, key="k")
    assert len(reads) == 1
    clock.t = 500.0 + A.CACHE_SECONDS
    A.newest(tmp_path, key="k")
    assert len(reads) == 2


def test_an_anchor_is_frozen_and_a_fingerprint_is_32_hex_characters(tmp_path):
    anchor = A.Anchor("f", "h", 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        anchor.lines = 2  # type: ignore[misc]
    fp = A.fingerprint(tmp_path)
    assert len(fp) == 32 and int(fp, 16) >= 0


@pytest.mark.parametrize("junk", [None, 7, b"KratosForge audit trail anchor v1; folder=a; lines=1; hash=b"])
def test_a_record_that_isnt_text_is_not_an_anchor(junk):
    assert A.parse(junk) is None


def test_a_string_without_a_terminator_is_read_whole():
    assert A._record_text(_record(body="anchors".encode("utf-16-le")), 0) == "anchors"
