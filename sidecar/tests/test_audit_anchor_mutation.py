"""Mutation batch 269: audit_anchor.py where test_audit_anchor.py didn't look. The Windows event log
writer and reader run here against a stand-in for advapi32, so their loops (buffer growth, end of log,
the record and time limits, records that stop short) are checked on any PC. The stand-in answers as
the Win32 functions do: it fills the buffer it is given and sets the counts through their pointers."""

from __future__ import annotations

import ctypes
import dataclasses
import importlib.util
import logging
import sys
from types import SimpleNamespace

import pytest

from fabric_desktop import audit_anchor as aa

SOURCE = aa.SOURCE


def record(source=SOURCE, strings=("x",), *, num=None, at=None, raw_rest=None, extra_header=None, pad=b"\0\0", tail=True):
    """An EVENTLOGRECORD: a 56-byte header, the source and computer names, then the strings."""
    names = (source + "\0PC\0").encode("utf-16-le") + pad
    rest = raw_rest if raw_rest is not None else "".join(s + "\0" for s in strings).encode("utf-16-le")
    at = 56 + len(names) if at is None else at
    body = names + rest
    length = 56 + len(body) + (4 if tail else 0)
    header = bytearray(56)
    header[0:4] = length.to_bytes(4, "little")
    header[4:8] = b"LfLe"  # Reserved: every record carries this mark after its length
    header[26:28] = (len(strings) if num is None else num).to_bytes(2, "little")
    header[36:40] = at.to_bytes(4, "little")
    for i, b in (extra_header or {}).items():
        header[i] = b
    return bytes(header) + body + (length.to_bytes(4, "little") if tail else b"")


def filler(size=56):
    """A record of another source with no strings, ``size`` bytes long."""
    header = bytearray(size)
    header[0:4] = size.to_bytes(4, "little")
    header[4:8] = b"LfLe"
    return bytes(header)


class Advapi:
    """advapi32's event log functions. ``reads`` is what each ReadEventLogW call does: bytes to
    return, or ("error", code[, needed])."""

    def __init__(self, reads=(), open_ok=True, register_ok=True, report_ok=True):
        self.reads, self.open_ok, self.register_ok, self.report_ok = list(reads), open_ok, register_ok, report_ok
        self.calls, self.sizes, self.flags, self.closed, self.reports, self.registered = 0, [], [], 0, [], 0
        self.deregistered = 0
        self.error = 0
        for name in ("OpenEventLogW", "ReadEventLogW", "CloseEventLog", "RegisterEventSourceW", "ReportEventW",
                     "DeregisterEventSource"):
            setattr(self, name, _Fn(getattr(self, "_" + name)))

    def _OpenEventLogW(self, server, name):
        assert name == "Application"
        if not self.open_ok:
            self.error = 5
            return 0
        return 77

    def _ReadEventLogW(self, handle, flags, offset, buffer, size, read_p, needed_p):
        self.calls += 1
        self.sizes.append(size)
        self.flags.append(flags)
        step = self.reads.pop(0) if self.reads else ("error", 38)
        if isinstance(step, tuple):
            self.error = step[1]
            needed_p._obj.value = step[2] if len(step) > 2 else 0
            return 0
        if len(step) > size:
            self.error = 122
            needed_p._obj.value = len(step)
            self.reads.insert(0, step)
            return 0
        ctypes.memmove(buffer, step, len(step))
        read_p._obj.value = len(step)
        return 1

    def _CloseEventLog(self, handle):
        self.closed += 1

    def _RegisterEventSourceW(self, server, source):
        self.registered += 1
        if not self.register_ok:
            self.error = 1722
            return 0
        assert source == SOURCE
        return 55

    def _ReportEventW(self, handle, kind, category, event_id, sid, count, size, strings, data):
        self.reports.append((handle, kind, category, event_id, sid, count, size, strings[0], data))
        if not self.report_ok:
            self.error = 6
            return 0
        return 1

    def _DeregisterEventSource(self, handle):
        self.deregistered += 1


class _Fn:
    """A function object that also takes argtypes and restype, as ctypes' do."""

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *a):
        return self.fn(*a)


@pytest.fixture
def advapi(monkeypatch):
    holder = {}

    def use(**kw):
        api = Advapi(**kw)
        holder["api"] = api
        monkeypatch.setattr(aa, "_advapi32", lambda: api)
        monkeypatch.setattr(aa, "_last_error", lambda: api.error)
        monkeypatch.setattr(aa, "_handle", None)
        return api
    return use


# ----------------------------------------------------------------------------- reading

def test_the_reader_opens_the_application_log_reads_backwards_and_closes_it(advapi):
    api = advapi(reads=[record(strings=("one", "x")) + record("Other", ("no",)) + record(strings=("two",))])
    assert aa._windows_reader() == ["one", "two"]
    assert api.flags[0] == 0x0001 | 0x0008 and api.sizes[0] == 64 * 1024
    assert api.closed == 1


def test_a_log_that_wont_open_is_an_error(advapi):
    advapi(open_ok=False)
    with pytest.raises(OSError):
        aa._windows_reader()


def test_a_record_bigger_than_the_buffer_gets_a_bigger_buffer(advapi):
    big = record(strings=("y" * 40000,))
    api = advapi(reads=[big])
    assert aa._windows_reader() == ["y" * 40000]
    assert api.sizes[:2] == [64 * 1024, len(big)]


def test_a_too_small_answer_that_asks_for_no_more_room_is_an_error(advapi):
    advapi(reads=[("error", 122, 64 * 1024)])  # needs exactly the room it had
    with pytest.raises(OSError):
        aa._windows_reader()


def test_the_end_of_the_log_ends_the_read_and_any_other_error_fails_it(advapi):
    api = advapi(reads=[record(strings=("a",)), ("error", 38), record(strings=("never",))])
    assert aa._windows_reader() == ["a"]
    assert api.closed == 1
    advapi(reads=[("error", 39)])
    with pytest.raises(OSError):
        aa._windows_reader()


def test_at_most_read_records_records_are_read(advapi, monkeypatch):
    monkeypatch.setattr(aa, "READ_RECORDS", 2)
    api = advapi(reads=[record(strings=("a",)) + record(strings=("b",)) + record(strings=("c",)),
                        record(strings=("d",))])
    assert aa._windows_reader() == ["a", "b"]
    assert api.calls == 1


def test_a_record_of_exactly_the_header_size_at_the_end_counts(advapi, monkeypatch):
    monkeypatch.setattr(aa, "READ_RECORDS", 2)
    api = advapi(reads=[record(strings=("a",)) + filler(56), record(strings=("b",))])
    assert aa._windows_reader() == ["a"]
    assert api.calls == 1


def test_bytes_too_few_for_a_record_after_the_last_one_dont_count(advapi, monkeypatch):
    monkeypatch.setattr(aa, "READ_RECORDS", 2)
    advapi(reads=[record(strings=("a",)) + b"\0" * 4, record(strings=("b",))])
    assert aa._windows_reader() == ["a", "b"]


def test_a_record_with_no_length_stops_that_buffer_only(advapi, monkeypatch):
    monkeypatch.setattr(aa, "READ_RECORDS", 5)
    advapi(reads=[bytes(56), record(strings=("b",))])
    assert aa._windows_reader() == ["b"]


def test_the_read_stops_at_its_time_limit(advapi, monkeypatch):
    monkeypatch.setattr(aa, "time", SimpleNamespace(monotonic=lambda: 5.0))
    monkeypatch.setattr(aa, "READ_SECONDS", 0.0)
    api = advapi(reads=[record(strings=("a",))])
    assert aa._windows_reader() == [] and api.calls == 0
    assert aa.READ_RECORDS == 2000


# ----------------------------------------------------------------------------- one record's text

def test_a_record_with_no_strings_has_no_text_whatever_follows_the_count():
    assert aa._record_text(record(num=0, extra_header={28: 1}), 0) is None


def test_the_string_offset_is_four_bytes():
    assert aa._record_text(record(strings=("a",), extra_header={40: 1}), 0) == "a"


def test_strings_that_start_at_the_very_end_are_no_text():
    r = record(strings=("a",), raw_rest=b"")
    r = r[:-4]  # no trailing length: StringOffset is now the record's end
    r = len(r).to_bytes(4, "little") + r[4:]
    assert aa._record_text(r, 0) is None


def test_names_with_an_odd_byte_before_the_strings_are_still_read():
    assert aa._record_text(record(strings=("odd",), pad=b"\0\0\0"), 0) == "odd"


def test_a_last_string_without_its_end_is_read_whole_and_an_odd_byte_is_dropped():
    assert aa._record_text(record(raw_rest="ab".encode("utf-16-le"), tail=False), 0) == "ab"
    assert aa._record_text(record(raw_rest="ab".encode("utf-16-le") + b"!", tail=False), 0) == "ab"


# ----------------------------------------------------------------------------- writing

def test_the_writer_registers_once_and_writes_an_information_record(advapi):
    api = advapi()
    aa._windows_writer("first")
    aa._windows_writer("second")
    assert api.registered == 1
    assert [r[:7] for r in api.reports] == [(55, 0x0004, 0, 1000, None, 1, 0)] * 2
    assert [r[7] for r in api.reports] == ["first", "second"]
    assert aa.EVENT_ID == 1000


def test_a_source_that_wont_register_is_an_error(advapi):
    advapi(register_ok=False)
    with pytest.raises(OSError):
        aa._windows_writer("x")
    assert aa._handle is None


def test_a_failed_report_lets_go_of_the_source_and_is_an_error(advapi):
    api = advapi(report_ok=False)
    with pytest.raises(OSError):
        aa._windows_writer("x")
    assert api.deregistered == 1 and aa._handle is None


# ----------------------------------------------------------------------------- the rest

def test_an_anchor_cant_be_changed_and_its_fingerprint_is_32_characters(tmp_path):
    anchor = aa.Anchor("f", "h", 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        anchor.lines = 2  # type: ignore[misc]
    assert len(aa.fingerprint(tmp_path)) == 32


def test_only_text_is_parsed():
    assert aa.parse(None) is None and aa.parse(5) is None  # type: ignore[arg-type]


def test_a_fresh_module_uses_this_platforms_event_log_and_hasnt_warned(monkeypatch):
    spec = importlib.util.spec_from_file_location("audit_anchor_fresh_copy", aa.__file__)
    fresh = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fresh)  # dataclasses look their module up there
    spec.loader.exec_module(fresh)
    windows = sys.platform == "win32"
    assert fresh.writer is (fresh._windows_writer if windows else fresh._no_writer)
    assert fresh.reader is (fresh._windows_reader if windows else fresh._no_reader)
    assert fresh._warned is False


def test_the_warning_is_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(aa, "_warned", False)
    with caplog.at_level(logging.WARNING, logger=aa.__name__):
        aa._warn("read from", OSError("a"))
        aa._warn("read from", OSError("b"))
    assert len([r for r in caplog.records if r.name == aa.__name__]) == 1


def test_writing_starts_again_once_the_pause_after_a_failure_is_over(monkeypatch, event_log):
    monkeypatch.setattr(aa, "time", SimpleNamespace(monotonic=lambda: 10.0))
    monkeypatch.setattr(aa, "_off_until", 10.0)
    aa.record("folder", "h" * 64, 3)
    assert len(event_log.records) == 1


def test_a_read_is_reused_for_less_than_cache_seconds_only(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(aa, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(aa, "_cache", {})
    reads = []
    monkeypatch.setattr(aa, "reader", lambda: reads.append(1) or [])
    aa.newest("folder", key="k")
    now[0] = aa.CACHE_SECONDS - 0.5
    aa.newest("folder", key="k")
    assert len(reads) == 1
    now[0] = aa.CACHE_SECONDS
    aa.newest("folder", key="k")
    assert len(reads) == 2
