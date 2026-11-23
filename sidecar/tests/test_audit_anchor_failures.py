"""The Windows event log can be unavailable, full or closed to this account. The audit trail is the record
that fails closed; the event log is extra evidence, so an action still runs, but the end that went
unrecorded is never skipped silently: head.json keeps it (``anchor_missed``), verify() says so with what IT
can do, and the next record that is written clears it (WR-411)."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit_anchor, audit_chain
from fabric_desktop.audit import AuditLog

FIXES = {audit_anchor.ACCESS_DENIED: "IT can restore the Application log's default permissions",
         audit_anchor.FULL: "IT can raise its maximum size or set it to overwrite events as needed",
         audit_anchor.UNAVAILABLE: "IT can start the Windows Event Log service"}
ERRORS = [(OSError(5, "ReportEventW failed"), audit_anchor.ACCESS_DENIED),
          (OSError(1502, "ReportEventW failed"), audit_anchor.FULL),
          (OSError(1501, "RegisterEventSourceW failed"), audit_anchor.UNAVAILABLE),
          (OSError(1722, "RegisterEventSourceW failed"), audit_anchor.UNAVAILABLE),
          (RuntimeError("anything else"), audit_anchor.UNAVAILABLE)]


def _head(folder):
    return json.loads((folder / audit_chain.HEAD).read_text(encoding="utf-8"))


@pytest.mark.parametrize("error, kind", ERRORS, ids=lambda x: str(x))
def test_an_unrecorded_end_is_kept_and_said_with_what_it_can_do(tmp_path, event_log, error, kind):
    log = AuditLog(tmp_path)
    log.record("meta_run", "ok", 0.1)
    event_log.fail = error
    log.record("meta_promote", "started", 0.0)  # the action's line is still written: the trail fails closed
    log.record("meta_promote", "ok", 0.1)  # waiting to retry: still counted, with the same reason
    assert [e["action"] for e in log.recent()] == ["meta_promote", "meta_promote", "meta_run"]
    missed = _head(tmp_path)["anchor_missed"]
    assert missed["writes"] == 2 and missed["why"] == kind and missed["since"]
    result = log.verify()
    assert result["ok"]  # nothing in the trail is wrong
    [note] = [n for n in result["notes"] if "wasn't recorded" in n]
    assert note.startswith("event log: the trail's end wasn't recorded in the Windows Application event log for "
                           "the last 2 writes (since ")
    assert FIXES[kind] in note and note.endswith("The trail itself is complete")


def test_the_next_record_written_clears_it(tmp_path, event_log, monkeypatch):
    log = AuditLog(tmp_path)
    event_log.fail = OSError(1502, "ReportEventW failed")
    log.record("meta_run", "ok", 0.1)
    assert _head(tmp_path)["anchor_missed"]["writes"] == 1
    event_log.fail = None
    monkeypatch.setattr(audit_anchor, "_off_until", 0.0)  # the minute's wait is over
    log.record("meta_run", "ok", 0.1)
    assert "anchor_missed" not in _head(tmp_path)
    assert audit_anchor.parse(event_log.records[-1]).lines == 2  # the record covers every line before it
    assert log.verify() == {"lines": 2, "ok": True, "problems": []}


def test_a_failing_event_log_never_stops_or_breaks_a_write(tmp_path, event_log):
    log = AuditLog(tmp_path)
    event_log.fail = OSError(5, "ReportEventW failed")
    for _ in range(5):
        log.record("meta_run", "ok", 0.1)
    assert log.verify()["lines"] == 5 and _head(tmp_path)["anchor_missed"]["writes"] == 5


def test_a_trail_written_with_no_event_log_off_windows_says_nothing(tmp_path):
    log = AuditLog(tmp_path)
    log.record("meta_run", "ok", 0.1)
    assert "anchor_missed" not in _head(tmp_path) and log.verify() == {"lines": 1, "ok": True, "problems": []}


def test_a_failed_record_is_reported_and_returns_why(tmp_path, event_log):
    event_log.fail = OSError(5, "denied")
    assert audit_anchor.record(tmp_path, "a" * 64, 1) == audit_anchor.ACCESS_DENIED
    event_log.fail = None
    assert audit_anchor.record(tmp_path, "a" * 64, 1) == audit_anchor.ACCESS_DENIED  # within the minute: not tried
    assert event_log.records == []


@pytest.mark.parametrize("writes", ["x", None, -3, 2.5, True, [1]])
def test_a_damaged_count_in_head_json_never_stops_an_audit_write(tmp_path, event_log, monkeypatch, writes):
    log = AuditLog(tmp_path)
    event_log.fail = OSError(1502, "ReportEventW failed")
    log.record("meta_run", "ok", 0.1)
    head = _head(tmp_path)
    head["anchor_missed"]["writes"] = writes
    (tmp_path / audit_chain.HEAD).write_text(json.dumps(head), encoding="utf-8")
    monkeypatch.setattr(audit_anchor, "_off_until", 0.0)  # past the retry minute: the record is tried again
    log.record("meta_promote", "ok", 0.1)  # written, and counted again from a sane number
    assert [e["action"] for e in log.recent()] == ["meta_promote", "meta_run"]
    assert _head(tmp_path)["anchor_missed"]["writes"] == 1
    [note] = [n for n in log.verify()["notes"] if "wasn't recorded" in n]
    assert "for the last write (since " in note


def _missed(tmp_path, event_log, **change):
    log = AuditLog(tmp_path)
    event_log.fail = OSError(5, "ReportEventW failed")
    log.record("meta_run", "ok", 0.1)
    log.record("meta_promote", "ok", 0.1)
    head = _head(tmp_path)
    head["anchor_missed"].update(change)
    (tmp_path / audit_chain.HEAD).write_text(json.dumps(head), encoding="utf-8")
    return log


@pytest.mark.parametrize("since", ["x" * 5000, 12, "2026-11-23T09:00:00Z; and more", None])
def test_a_since_that_isnt_the_time_written_is_never_shown(tmp_path, event_log, since):
    log = _missed(tmp_path, event_log, since=since)
    [note] = [n for n in log.verify()["notes"] if "wasn't recorded" in n]
    assert "(since an earlier write)" in note and len(note) < 600


def test_a_trail_with_problems_isnt_called_complete(tmp_path, event_log):
    log = _missed(tmp_path, event_log)
    path = audit_chain.files(tmp_path / "audit" if (tmp_path / "audit").is_dir() else tmp_path)[-1]
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n" + lines[1].replace('"ok"', '"error"', 1) + "\n", encoding="utf-8")
    result = log.verify()
    assert result["problems"]
    [note] = [n for n in result["notes"] if "wasn't recorded" in n]
    assert "complete" not in note
