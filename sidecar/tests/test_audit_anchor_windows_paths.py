"""The event-log anchor's Windows calls, driven through a stand-in advapi32 so every branch of the write, the
read and the record parsing runs on any OS: a source that can't be registered, a report that fails (and the
handle that's dropped and registered again), a log that can't be opened, a record bigger than the buffer,
the end of the log, a read error, records from other sources or with no strings, and the read limits."""

from __future__ import annotations

import ctypes

import pytest

from fabric_desktop import audit_anchor


def _record(source: str, value: str | None, *, strings: int | None = None, at: int | None = None) -> bytes:
    """One EVENTLOGRECORD: the 56-byte header, then SourceName and ComputerName, then the strings."""
    names = (source + "\0" + "PC\0").encode("utf-16-le")
    body = (value + "\0").encode("utf-16-le") if value is not None else b""
    offset = 56 + len(names) if at is None else at
    length = 56 + len(names) + len(body)
    header = bytearray(56)
    header[0:4] = length.to_bytes(4, "little")
    header[20:24] = audit_anchor.EVENT_ID.to_bytes(4, "little")
    count = strings if strings is not None else int(value is not None)
    header[26:28] = count.to_bytes(2, "little")
    header[36:40] = offset.to_bytes(4, "little")
    return bytes(header) + names + body


class Advapi32:
    """The parts of advapi32 the anchor calls. ``reads`` is what each ReadEventLogW call does in turn: bytes
    to hand back, ("big", n) for ERROR_INSUFFICIENT_BUFFER needing n bytes, or an error code."""

    def __init__(self, *, register=1, report=True, open_log=7, reads=()):
        self.register, self.report, self.open_log, self.reads = register, report, open_log, list(reads)
        self.calls: list[str] = []
        self.reported: list[str] = []
        self.error = 0
        for name in ("RegisterEventSourceW", "ReportEventW", "DeregisterEventSource", "OpenEventLogW",
                     "ReadEventLogW", "CloseEventLog"):
            method = getattr(self, "_" + name)

            def call(*args, _m=method, _n=name):
                self.calls.append(_n)
                return _m(*args)
            setattr(self, name, call)  # a plain function, so restype and argtypes can be set on it

    def _RegisterEventSourceW(self, server, source):
        assert source == audit_anchor.SOURCE
        if not self.register:
            self.error = 5
        return self.register

    def _ReportEventW(self, handle, kind, category, event, sid, count, size, strings, data):
        if self.report is not True:
            self.error = self.report
            return False
        self.reported.append(strings[0])
        return True

    def _DeregisterEventSource(self, handle):
        return True

    def _OpenEventLogW(self, server, name):
        assert name == "Application"
        if not self.open_log:
            self.error = 1722
        return self.open_log

    def _ReadEventLogW(self, handle, flags, offset, buffer, size, read, needed):
        step = self.reads.pop(0) if self.reads else 38
        if isinstance(step, bytes):
            ctypes.memmove(buffer, step, len(step))
            read._obj.value = len(step)
            return True
        if isinstance(step, tuple):
            needed._obj.value = step[1]
            self.error = 122
            return False
        self.error = step
        return False

    def _CloseEventLog(self, handle):
        return True


@pytest.fixture
def advapi(monkeypatch):
    def make(**kwargs):
        fake = Advapi32(**kwargs)
        monkeypatch.setattr(audit_anchor, "_advapi32", lambda: fake)
        monkeypatch.setattr(audit_anchor, "_last_error", lambda: fake.error)
        monkeypatch.setattr(audit_anchor, "_handle", None)
        return fake
    return make


ANCHOR = audit_anchor.Anchor("f" * 32, "h" * 64, 3)


# ----------------------------------------------------------------------------- writing

def test_a_record_is_written_and_the_source_is_registered_once(advapi):
    fake = advapi()
    audit_anchor._windows_writer("one")
    audit_anchor._windows_writer("two")
    assert fake.reported == ["one", "two"]
    assert fake.calls.count("RegisterEventSourceW") == 1


def test_a_source_that_cant_be_registered_raises_with_windows_error(advapi):
    advapi(register=0)
    with pytest.raises(OSError) as raised:
        audit_anchor._windows_writer("one")
    assert raised.value.errno == 5 and audit_anchor.kind(raised.value) == audit_anchor.ACCESS_DENIED
    assert audit_anchor._handle is None


def test_a_failed_report_drops_the_handle_so_the_next_write_registers_again(advapi):
    fake = advapi(report=1502)
    with pytest.raises(OSError) as raised:
        audit_anchor._windows_writer("one")
    assert audit_anchor.kind(raised.value) == audit_anchor.FULL
    assert "DeregisterEventSource" in fake.calls and audit_anchor._handle is None
    fake.report = True
    audit_anchor._windows_writer("two")
    assert fake.calls.count("RegisterEventSourceW") == 2 and fake.reported == ["two"]


# ----------------------------------------------------------------------------- reading

def test_our_records_are_read_newest_first_and_other_sources_skipped(advapi):
    data = (_record(audit_anchor.SOURCE, "newest") + _record("Other", "theirs")
            + _record(audit_anchor.SOURCE, "older"))
    fake = advapi(reads=[data])
    assert audit_anchor._windows_reader() == ["newest", "older"]
    assert fake.calls[-1] == "CloseEventLog"


def test_a_record_bigger_than_the_buffer_is_read_again_with_room_for_it(advapi):
    advapi(reads=[("big", 128 * 1024), _record(audit_anchor.SOURCE, "big one")])
    assert audit_anchor._windows_reader() == ["big one"]


def test_a_too_small_buffer_that_doesnt_need_more_room_is_a_read_error(advapi):
    fake = advapi(reads=[("big", 10)])
    with pytest.raises(OSError):
        audit_anchor._windows_reader()
    assert fake.calls[-1] == "CloseEventLog"  # closed even when the read fails


def test_a_log_that_cant_be_opened_raises(advapi):
    advapi(open_log=0)
    with pytest.raises(OSError) as raised:
        audit_anchor._windows_reader()
    assert audit_anchor.kind(raised.value) == audit_anchor.UNAVAILABLE


def test_reading_stops_at_the_read_limit(advapi, monkeypatch):
    monkeypatch.setattr(audit_anchor, "READ_RECORDS", 2)
    data = b"".join(_record(audit_anchor.SOURCE, f"r{i}") for i in range(5))
    advapi(reads=[data, data])
    assert audit_anchor._windows_reader() == ["r0", "r1"]


def test_reading_stops_at_the_time_limit(advapi, monkeypatch):
    monkeypatch.setattr(audit_anchor, "READ_SECONDS", -1.0)
    fake = advapi(reads=[_record(audit_anchor.SOURCE, "never read")])
    assert audit_anchor._windows_reader() == []
    assert "ReadEventLogW" not in fake.calls


def test_a_record_with_no_length_ends_the_buffer(advapi):
    advapi(reads=[bytes(56) + _record(audit_anchor.SOURCE, "after")])
    assert audit_anchor._windows_reader() == []


@pytest.mark.parametrize("record", [
    _record(audit_anchor.SOURCE, None, strings=0),  # no strings
    _record(audit_anchor.SOURCE, "x", at=56),  # string offset inside the header
    _record(audit_anchor.SOURCE, "x", at=10_000),  # string offset past the record
], ids=["no strings", "offset in header", "offset past end"])
def test_records_without_a_usable_string_are_skipped(record):
    assert audit_anchor._record_text(record, 0) is None


def test_a_record_cut_short_is_skipped():
    whole = _record(audit_anchor.SOURCE, "x")
    assert audit_anchor._record_text(whole[:40], 0) is None


def test_the_windows_reader_feeds_newest(advapi, monkeypatch):
    fingerprint = audit_anchor.fingerprint("C:/audit")
    mine = audit_anchor.text(audit_anchor.Anchor(fingerprint, "a" * 64, 9))
    other = audit_anchor.text(audit_anchor.Anchor("0" * 32, "b" * 64, 4))
    advapi(reads=[_record(audit_anchor.SOURCE, other) + _record(audit_anchor.SOURCE, mine)])
    monkeypatch.setattr(audit_anchor, "reader", audit_anchor._windows_reader)
    monkeypatch.setattr(audit_anchor, "_cache", {})
    assert audit_anchor.newest("C:/audit") == (audit_anchor.Anchor(fingerprint, "a" * 64, 9), "")


# ----------------------------------------------------------------------------- the rest of the module

def test_the_ctypes_helpers_use_the_windows_calls(monkeypatch):
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1717, raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error: ("dll", name, use_last_error), raising=False)
    assert audit_anchor._last_error() == 1717
    assert audit_anchor._advapi32() == ("dll", "advapi32", True)


def test_an_empty_head_hash_writes_nothing(event_log):
    assert audit_anchor.record("C:/audit", "", 0) is None
    assert event_log.records == []


def test_newest_without_a_key_reads_every_time(event_log):
    audit_anchor.record("C:/audit", "a" * 64, 1)
    first, _ = audit_anchor.newest("C:/audit")
    audit_anchor.record("C:/audit", "b" * 64, 2)
    second, _ = audit_anchor.newest("C:/audit")
    assert (first.lines, second.lines) == (1, 2)


def test_a_cached_read_is_reused_for_the_same_key_and_not_for_another(event_log, monkeypatch):
    audit_anchor.record("C:/audit", "a" * 64, 1)
    assert audit_anchor.newest("C:/audit", key="k1")[0].lines == 1
    event_log.records.append(audit_anchor.text(audit_anchor.Anchor(audit_anchor.fingerprint("C:/audit"),
                                                                      "b" * 64, 2)))
    assert audit_anchor.newest("C:/audit", key="k1")[0].lines == 1  # reused
    assert audit_anchor.newest("C:/audit", key="k2")[0].lines == 2  # a new key reads again
    monkeypatch.setattr(audit_anchor, "CACHE_SECONDS", -1.0)
    event_log.records.append(audit_anchor.text(audit_anchor.Anchor(audit_anchor.fingerprint("C:/audit"),
                                                                      "c" * 64, 3)))
    assert audit_anchor.newest("C:/audit", key="k2")[0].lines == 3  # an old read isn't reused
