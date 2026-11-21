"""The audit trail's end in the Windows event log (audit_anchor.py), the paths the other tests leave: writing a
record (registering the source once, and a write the event log refuses), reading the newest records back (a
buffer too small for a record, the oldest record reached, a read that fails, the record limit, a record of no
length), and looking a folder up without a cache key. The event log is a stand-in: advapi32 is faked, so this
runs on any OS."""

from __future__ import annotations

import ctypes
import logging

import pytest

from fabric_desktop import audit_anchor as A
from test_audit_anchor import _record


class _Fn:
    def __init__(self, impl):
        self.impl, self.calls = impl, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.impl(*args)


class _Advapi32:
    """advapi32 with each function given as a Python callable; restype and argtypes are kept, not used."""

    def __init__(self, **impls):
        for name, impl in impls.items():
            setattr(self, name, _Fn(impl))


@pytest.fixture
def advapi(monkeypatch):
    errors = []
    monkeypatch.setattr(A, "_handle", None)
    monkeypatch.setattr(A, "_last_error", lambda: errors.pop(0))

    def use(**impls):
        dll = _Advapi32(**impls)
        monkeypatch.setattr(A, "_advapi32", lambda: dll)
        return dll
    use.errors = errors
    return use


def test_the_source_is_registered_once_and_each_record_holds_the_text(advapi):
    written = []
    dll = advapi(RegisterEventSourceW=lambda server, source: 77,
                 ReportEventW=lambda h, kind, cat, event, sid, n, size, strings, data: written.append(
                     (h, kind, event, n, strings[0])) or 1)
    A._windows_writer("first")
    A._windows_writer("second")
    assert dll.RegisterEventSourceW.calls == [(None, A.SOURCE)]
    assert written == [(77, 0x0004, A.EVENT_ID, 1, "first"), (77, 0x0004, A.EVENT_ID, 1, "second")]
    assert A._handle == 77


def test_a_source_that_cant_be_registered_raises_with_the_windows_error(advapi):
    advapi(RegisterEventSourceW=lambda server, source: 0)
    advapi.errors.append(5)
    with pytest.raises(OSError, match="RegisterEventSourceW failed") as caught:
        A._windows_writer("x")
    assert caught.value.errno == 5 and A._handle is None


def test_a_refused_record_lets_go_of_the_source_so_the_next_write_registers_again(advapi):
    handles = iter([11, 12])
    dll = advapi(RegisterEventSourceW=lambda server, source: next(handles),
                 ReportEventW=lambda h, *rest: 0 if h == 11 else 1,
                 DeregisterEventSource=lambda h: 1)
    advapi.errors.append(1502)  # ERROR_LOG_FILE_FULL
    with pytest.raises(OSError, match="ReportEventW failed") as caught:
        A._windows_writer("x")
    assert caught.value.errno == 1502
    assert dll.DeregisterEventSource.calls == [(11,)] and A._handle is None
    A._windows_writer("y")
    assert A._handle == 12 and [c[0] for c in dll.ReportEventW.calls] == [11, 12]


def _pages(*pages):
    """ReadEventLogW returning each page in turn: bytes fill the buffer, an int fails with that error and
    (for ERROR_INSUFFICIENT_BUFFER) says how big the record is."""
    pages, sizes = list(pages), []

    def read(handle, flags, offset, buffer, size, read_ref, needed_ref):
        sizes.append(size)
        page = pages.pop(0)
        if isinstance(page, tuple):
            error, needed = page
            needed_ref._obj.value = needed
            read.errors.append(error)
            return 0
        ctypes.memmove(buffer, page, len(page))
        read_ref._obj.value = len(page)
        return 1
    read.errors, read.sizes = [], sizes
    return read


def _reader(advapi, read):
    dll = advapi(OpenEventLogW=lambda server, name: 9, ReadEventLogW=read, CloseEventLog=lambda h: 1)
    advapi.errors[:] = read.errors  # the same list: each failed read leaves its error
    read.errors = advapi.errors
    return dll


def test_the_newest_records_are_read_back_for_our_source_and_a_big_record_gets_a_bigger_buffer(advapi):
    ours, theirs = _record(A.SOURCE, ["newest"]), _record("Other", ["not ours"])
    big = _record(A.SOURCE, ["x" * 40_000])
    read = _pages(ours + theirs, (122, 100_000), big, (38, 0))
    dll = _reader(advapi, read)
    assert A._windows_reader() == ["newest", "x" * 40_000]
    assert read.sizes == [64 * 1024, 64 * 1024, 100_000, 100_000]
    assert dll.OpenEventLogW.calls == [(None, "Application")] and dll.CloseEventLog.calls == [(9,)]


def test_a_too_small_error_that_doesnt_ask_for_more_room_is_a_failed_read(advapi):
    dll = _reader(advapi, _pages((122, 10)))
    with pytest.raises(OSError, match="ReadEventLogW failed"):
        A._windows_reader()
    assert dll.CloseEventLog.calls == [(9,)], "the log is closed after a failed read"


def test_the_event_log_that_cant_be_opened_raises(advapi):
    dll = advapi(OpenEventLogW=lambda server, name: 0, ReadEventLogW=lambda *a: 1, CloseEventLog=lambda h: 1)
    advapi.errors.append(1314)
    with pytest.raises(OSError, match="OpenEventLogW failed"):
        A._windows_reader()
    assert dll.ReadEventLogW.calls == [] and dll.CloseEventLog.calls == []


def test_reading_stops_at_the_record_limit(advapi, monkeypatch):
    monkeypatch.setattr(A, "READ_RECORDS", 2)
    page = b"".join(_record(A.SOURCE, [f"r{i}"]) for i in range(4))
    dll = _reader(advapi, _pages(page))
    assert A._windows_reader() == ["r0", "r1"]
    assert len(dll.ReadEventLogW.calls) == 1


def test_a_record_of_no_length_ends_its_page(advapi):
    empty = bytes(56)  # Length 0: nothing after it on this page can be found
    dll = _reader(advapi, _pages(_record(A.SOURCE, ["kept"]) + empty + _record(A.SOURCE, ["unreachable"]), (38, 0)))
    assert A._windows_reader() == ["kept"]
    assert len(dll.ReadEventLogW.calls) == 2


def test_without_a_key_the_event_log_is_read_every_time_and_nothing_is_cached(tmp_path, monkeypatch):
    anchor = A.Anchor(A.fingerprint(tmp_path), "h" * 64, 3)
    reads = []
    monkeypatch.setattr(A, "reader", lambda: reads.append(1) or [A.text(anchor)])
    monkeypatch.setattr(A, "_cache", {})
    assert A.newest(tmp_path) == (anchor, "")
    assert A.newest(str(tmp_path)) == (anchor, "")
    assert len(reads) == 2 and A._cache == {}


def test_a_failed_read_is_a_note_and_warns_once(tmp_path, monkeypatch, caplog):
    def broken():
        raise OSError(5, "denied")
    monkeypatch.setattr(A, "reader", broken)
    monkeypatch.setattr(A, "_warned", False)
    with caplog.at_level(logging.WARNING, logger=A.log.name):
        assert A.newest(tmp_path) == (None, ("event log: the Windows Application event log couldn't be read, so "
                                             "the end of the trail is checked against head.json only"))
        A.newest(tmp_path)
    assert [r.getMessage().count("couldn't be read from the Windows event log (OSError)") for r in caplog.records] == [1]
