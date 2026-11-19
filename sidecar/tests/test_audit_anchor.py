"""The trail's end, also recorded in the Windows Application event log (audit_anchor.py).

``event_log`` (conftest.py) is an event log in memory; every other test runs with none. One test, on
Windows only, writes a record to the real Application log and reads it back.
"""

from __future__ import annotations

import json
import logging
import sys
import datetime as dt

import pytest

from fabric_desktop import audit_anchor, audit_chain
from fabric_desktop.audit import AuditLog

COUNT = 4


def _trail(folder, n=COUNT):
    log = AuditLog(folder)
    for i in range(n):
        log.record("meta_run", "ok", 0.1, {"n": i})
    return log


def _lines(folder):
    [path] = audit_chain.files(folder)
    return path, path.read_text(encoding="utf-8").splitlines()


def test_each_write_records_the_trails_end(tmp_path, event_log):
    folder = tmp_path / "audit"
    log = _trail(folder)
    assert len(event_log.records) == COUNT
    anchors = [audit_anchor.parse(r) for r in event_log.records]
    head = json.loads((folder / audit_chain.HEAD).read_text())
    assert anchors[-1] == audit_anchor.Anchor(audit_anchor.fingerprint(folder), head["hash"], COUNT)
    assert [a.lines for a in anchors] == [1, 2, 3, 4]
    assert all(str(tmp_path) not in r and "meta_run" not in r for r in event_log.records)  # no path, no trail data
    assert log.verify() == {"lines": COUNT, "ok": True, "problems": []}


def test_a_cut_end_with_the_head_rewritten_to_match_is_caught(tmp_path, event_log):
    folder = tmp_path / "audit"
    log = _trail(folder)
    path, lines = _lines(folder)
    path.write_text("".join(line + "\n" for line in lines[:2]), encoding="utf-8")
    (folder / audit_chain.HEAD).write_text(json.dumps({"hash": json.loads(lines[1])["hash"], "lines": 2}))
    assert log.verify()["problems"] == [
        (f"event log: the trail ends before the newest line the Windows event log recorded (line {COUNT}); "
        "lines were deleted from the end")]


def test_a_resealed_trail_with_its_head_rewritten_is_caught(tmp_path, event_log):
    folder = tmp_path / "audit"
    log = _trail(folder)
    path, lines = _lines(folder)
    out, prev = lines[:1], json.loads(lines[0])["hash"]
    for line in lines[1:]:
        entry = json.loads(line)
        entry.pop("hash")
        entry["outcome"] = "error"
        out.append(audit_chain.seal(prev, entry))
        prev = entry["hash"]
    path.write_text("".join(line + "\n" for line in out), encoding="utf-8")
    (folder / audit_chain.HEAD).write_text(json.dumps({"hash": prev, "lines": COUNT}))
    assert log.verify()["problems"] == [
        (f"event log: line {COUNT} isn't the line the Windows event log recorded, so the trail was re-sealed "
        f"at or before line {COUNT}")]


def test_only_the_newest_record_for_this_folder_counts(tmp_path, event_log):
    """Another trail's records don't stand in for this one's; after a gap is cleared by deleting head.json
    (the documented way), the next write's record is the one compared."""
    other = _trail(tmp_path / "other", 6)
    folder = tmp_path / "audit"
    log = _trail(folder)
    path, lines = _lines(folder)
    path.write_text("".join(line + "\n" for line in lines[:2]), encoding="utf-8")
    (folder / audit_chain.HEAD).unlink()
    assert not log.verify()["ok"]
    log.record("meta_run", "ok", 0.1)
    assert log.verify() == {"lines": 3, "ok": True, "problems": []}
    assert other.verify()["ok"]


def test_a_prune_keeps_the_count_the_event_log_holds(tmp_path, event_log, monkeypatch):
    """Records count the lines a prune deleted, so an older record still lines up after a prune whose own
    record didn't reach the event log."""
    folder = tmp_path / "audit"
    for i, at in enumerate(["2026-01-10T00:00:00+00:00", "2026-01-11T00:00:00+00:00", "2026-11-01T00:00:00+00:00"]):
        audit_chain.append(folder, {"at": at, "action": "meta_run", "n": i})
    before = audit_anchor.parse(event_log.records[-1])
    monkeypatch.setattr(audit_anchor, "writer", lambda value: None)  # the prune's record is lost
    audit_chain.prune(folder, 30, now=dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc).timestamp())
    assert [p.name for p in audit_chain.files(folder)] == ["audit-2026-11.jsonl"]
    assert audit_anchor.parse(event_log.records[-1]) == before and before.lines == 3
    assert audit_chain.verify(folder)["problems"] == []


@pytest.mark.parametrize("fail", [OSError(5, "Access is denied"), RuntimeError("no event log service")])
def test_an_event_log_that_fails_never_fails_a_write_and_is_noted_not_reported(tmp_path, event_log, monkeypatch,
                                                                                caplog, fail):
    monkeypatch.setattr(audit_anchor, "_warned", False)
    event_log.fail = fail
    calls = []
    real = event_log.write
    monkeypatch.setattr(audit_anchor, "writer", lambda value: (calls.append(value), real(value)))
    folder = tmp_path / "audit"
    with caplog.at_level(logging.WARNING, logger=audit_anchor.__name__):
        log = _trail(folder)
    assert [len(line) > 0 for line in _lines(folder)[1]] == [True] * COUNT  # every line was written
    assert len(calls) == 1  # after a failure the event log is left alone for a while: writes stay fast
    assert len([r for r in caplog.records if r.name == audit_anchor.__name__]) == 1  # warned once
    check = log.verify()
    assert check["ok"] and check["problems"] == []
    assert check["notes"] == [("event log: the Windows Application event log couldn't be read, so the end of the "
                              "trail is checked against head.json only")]


def test_no_record_for_this_trail_is_a_note_not_a_problem(tmp_path, event_log):
    folder = tmp_path / "audit"
    log = _trail(folder)
    event_log.records.clear()  # an event log cleared, overwritten, or a trail from before the anchor
    check = log.verify()
    assert check["ok"] and check["problems"] == [] and len(check["notes"]) == 1
    assert check["notes"][0].startswith("event log: no record of this trail")


def test_off_windows_there_is_no_event_log_and_no_note(tmp_path):
    """The default in tests, as off Windows: nothing written, nothing read, and the result keeps its shape."""
    log = _trail(tmp_path / "audit")
    (tmp_path / "audit" / audit_chain.HEAD).unlink()
    assert log.verify() == {"lines": COUNT, "ok": True, "problems": []}


def test_a_record_text_round_trips_and_junk_is_ignored():
    anchor = audit_anchor.Anchor("a" * 32, "b" * 64, 12)
    assert audit_anchor.parse(audit_anchor.text(anchor)) == anchor
    for junk in ["", "something else", audit_anchor.PREFIX + "; folder=x; lines=many; hash=y", audit_anchor.PREFIX]:
        assert audit_anchor.parse(junk) is None


@pytest.mark.skipif(sys.platform != "win32", reason="the real event log is on Windows only")
def test_the_real_event_log_writes_and_reads_back_a_record(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_anchor, "writer", audit_anchor._windows_writer)
    monkeypatch.setattr(audit_anchor, "reader", audit_anchor._windows_reader)
    anchor = audit_anchor.Anchor(audit_anchor.fingerprint(tmp_path), "c" * 64, 1)
    audit_anchor.writer(audit_anchor.text(anchor))
    assert audit_anchor.newest(tmp_path) == (anchor, "")


def _record(source, strings):
    """An EVENTLOGRECORD as ReadEventLogW returns it: a 56-byte header, the source and computer names,
    then the strings at StringOffset."""
    names = (source + "\0PC\0").encode("utf-16-le") + b"\0\0"  # and a padding byte pair
    body = "".join(s + "\0" for s in strings).encode("utf-16-le")
    at = 56 + len(names)
    length = at + len(body) + 4
    header = bytearray(56)
    header[0:4] = length.to_bytes(4, "little")
    header[26:28] = len(strings).to_bytes(2, "little")
    header[36:40] = at.to_bytes(4, "little")
    return bytes(header) + names + body + length.to_bytes(4, "little")


def test_an_event_log_record_is_read_for_our_source_only():
    ours, theirs = _record(audit_anchor.SOURCE, ["anchor text", "x"]), _record("Other", ["anchor text"])
    data = theirs + ours
    assert audit_anchor._record_text(data, 0) is None
    assert audit_anchor._record_text(data, len(theirs)) == "anchor text"
    assert audit_anchor._record_text(_record(audit_anchor.SOURCE, []), 0) is None


def test_the_event_log_is_read_once_while_the_trails_head_is_unchanged(tmp_path, event_log, monkeypatch):
    folder = tmp_path / "audit"
    log = _trail(folder)
    reads = []
    real = event_log.read
    monkeypatch.setattr(audit_anchor, "reader", lambda: reads.append(1) or real())
    assert log.verify()["ok"] and log.verify()["ok"]  # the audit page, loaded twice
    assert len(reads) == 1
    log.record("meta_run", "ok", 0.1, {"n": 99})  # the head moves: read again
    assert log.verify()["ok"] and len(reads) == 2
    monkeypatch.setattr(audit_anchor, "_cache", {k: (key, at - audit_anchor.CACHE_SECONDS - 1, found)
                                                 for k, (key, at, found) in audit_anchor._cache.items()})
    assert log.verify()["ok"] and len(reads) == 3  # and after CACHE_SECONDS anyway


def test_a_line_with_a_stray_byte_shows_as_its_own_row_not_a_failed_list(tmp_path):
    folder = tmp_path / "audit"
    log = _trail(folder)
    path, lines = _lines(folder)
    raw = path.read_bytes().split(b"\n")
    raw[1] = raw[1][:10] + b"\xff\xfe" + raw[1][10:]
    path.write_bytes(b"\n".join(raw))
    rows = log.recent()
    assert len(rows) == COUNT and sum(1 for r in rows if r["action"] == "unreadable_line") == 1
