"""audit_chain.py (mutation batch 520): what verify() says of a line whose time is earlier than the line
before it (the PC's clock was set back), from five minutes on, and in minutes, hours or days. The event log
is the in-memory stand-in conftest sets, so nothing is written to this PC's. Also: exports that keep an
earlier one, a line a power cut left unfinished, a prune of several months, and the trail against the event
log's record after a prune."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fabric_desktop import audit_anchor, audit_chain

START = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)


def _trail(folder, *back: dt.timedelta):
    audit_chain.append(folder, {"at": START.isoformat(), "action": "x"})
    for step in back:
        audit_chain.append(folder, {"at": (START - step).isoformat(), "action": "x"})
    return audit_chain.verify(folder)


@pytest.mark.parametrize("seconds", [0, 60, 299, 300])
def test_up_to_five_minutes_back_isnt_noted(tmp_path, seconds):
    assert _trail(tmp_path, dt.timedelta(seconds=seconds)) == {"lines": 2, "ok": True, "problems": []}


@pytest.mark.parametrize("back, said", [
    (dt.timedelta(seconds=302), "5 minutes"),
    (dt.timedelta(minutes=61), "61 minutes"),
    (dt.timedelta(minutes=119), "119 minutes"),
    (dt.timedelta(minutes=120), "2 hours"),
    (dt.timedelta(hours=3), "3 hours"),
    (dt.timedelta(hours=47), "47 hours"),
    (dt.timedelta(hours=48), "2 days"),
    (dt.timedelta(days=9), "9 days"),
])
def test_a_clock_set_back_is_noted_with_how_far(tmp_path, back, said):
    result = _trail(tmp_path, back)
    assert result["ok"] and result["problems"] == []
    [note] = result["notes"]
    assert note == (f"audit-2026-11.jsonl line 2: its time is {said} earlier than the line before it, so this "
                    "PC's clock was changed (the chain itself is intact)")


@pytest.mark.parametrize("left", ["export", "manifest", "both"])
def test_an_export_never_writes_over_an_earlier_one_unless_told(tmp_path, left):
    folder, out = tmp_path / "audit", tmp_path / "out" / "trail.jsonl"
    audit_chain.append(folder, {"at": START.isoformat(), "action": "x"})
    out.parent.mkdir()
    if left in ("export", "both"):
        out.write_text("earlier\n", encoding="utf-8")
    if left in ("manifest", "both"):
        audit_chain.manifest_path(out).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="is there already"):
        audit_chain.export(folder, out)
    assert audit_chain.export(folder, out, replace=True)["lines"] == 1
    assert audit_chain.verify_export(out, folder)["ok"]


def test_an_export_makes_the_folders_it_goes_in(tmp_path):
    folder, out = tmp_path / "audit", tmp_path / "exports" / "2026" / "trail.jsonl"
    audit_chain.append(folder, {"at": START.isoformat(), "action": "x"})
    assert audit_chain.export(folder, out)["lines"] == 1
    assert audit_chain.verify_export(out, folder) == {"ok": True, "lines": 1, "problems": []}


@pytest.mark.parametrize("manifest", ['{"schema": "something-else@1"}', "[1, 2]", "null"])
def test_a_manifest_that_isnt_an_export_manifest_checks_no_line(tmp_path, manifest):
    out = tmp_path / "trail.jsonl"
    out.write_text('{"a": 1}\n', encoding="utf-8")
    audit_chain.manifest_path(out).write_text(manifest, encoding="utf-8")
    assert audit_chain.verify_export(out) == {
        "ok": False, "lines": 0, "problems": ["trail.jsonl.manifest.json: missing or not an audit export manifest"]}


def test_a_line_a_power_cut_left_unfinished_is_kept_and_the_next_starts_its_own(tmp_path):
    audit_chain.append(tmp_path, {"at": START.isoformat(), "action": "x"})
    [path] = audit_chain.files(tmp_path)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"at": "2026-11-20T12:00:01+00:00", "act')  # no newline: the write stopped there
    audit_chain.append(tmp_path, {"at": START.isoformat(), "action": "y"})
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[1] == '{"at": "2026-11-20T12:00:01+00:00", "act' and '"action": "y"' in lines[2] and lines[3] == ""
    result = audit_chain.verify(tmp_path)
    # The unfinished line is reported, and so is the next one, which chained onto nothing it could read.
    assert "lost" not in _head(tmp_path)  # the line head.json named is still there
    assert result["lines"] == 3 and result["problems"] == [
        "audit-2026-11.jsonl line 2: not an audit line",
        "audit-2026-11.jsonl line 3: a line before it is missing or changed"]


def _head(folder):
    return json.loads((folder / audit_chain.HEAD).read_text(encoding="utf-8"))


def _at(month: str, day: int = 10) -> str:
    return f"{month}-{day:02d}T09:00:00+00:00"


NOW = dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc).timestamp()


def test_a_one_byte_file_with_no_newline_ends_mid_line(tmp_path):
    (tmp_path / "audit-2026-11.jsonl").write_text("x", encoding="utf-8")
    audit_chain.append(tmp_path, {"at": START.isoformat(), "action": "y"})
    lines = (tmp_path / "audit-2026-11.jsonl").read_text(encoding="utf-8").split("\n")
    assert lines[0] == "x" and json.loads(lines[1])["action"] == "y"


def test_the_minute_wording():
    assert audit_chain._span(dt.timedelta(minutes=1)) == "1 minute"
    assert audit_chain._span(dt.timedelta(seconds=30)) == "0 minutes"
    assert audit_chain._span(dt.timedelta(minutes=2)) == "2 minutes"


def test_a_line_noted_as_earlier_is_judged_against_the_latest_time_before_it(tmp_path):
    result = _trail(tmp_path, dt.timedelta(minutes=10), dt.timedelta(minutes=6))
    assert result["ok"] and [n.split(": ", 1)[1][:30] for n in result["notes"]] == [
        "its time is 10 minutes earlier", "its time is 6 minutes earlier "]


def test_a_prune_of_several_months_starts_the_trail_after_the_last_line_deleted(tmp_path):
    for month in ("2026-01", "2026-01", "2026-02", "2026-02", "2026-02", "2026-11"):
        audit_chain.append(tmp_path, {"at": _at(month), "action": "x"})
    last_feb = json.loads((tmp_path / "audit-2026-02.jsonl").read_text(encoding="utf-8").splitlines()[-1])["hash"]
    gone = audit_chain.prune(tmp_path, 30, now=NOW)
    assert gone == {"files": ["audit-2026-01.jsonl", "audit-2026-02.jsonl"], "lines": 5, "through": "2026-02"}
    assert _head(tmp_path)["base"] == {"hash": last_feb, "through": "2026-02", "lines": 5}
    assert _head(tmp_path)["lines"] == 1
    assert audit_chain.verify(tmp_path) == {"lines": 1, "ok": True, "problems": [],
                                            "pruned": {"through": "2026-02", "lines": 5}}


def test_a_head_counting_fewer_lines_than_a_prune_deletes_counts_none_left(tmp_path):
    for _ in range(3):
        audit_chain.append(tmp_path, {"at": _at("2026-01"), "action": "x"})
    head = _head(tmp_path)
    head["lines"] = 2  # edited, or written by an older version
    (tmp_path / audit_chain.HEAD).write_text(json.dumps(head), encoding="utf-8")
    (tmp_path / "audit-2026-11.jsonl").write_text("", encoding="utf-8")  # this month's file, still empty
    assert audit_chain.prune(tmp_path, 30, now=NOW)["lines"] == 3
    assert _head(tmp_path)["lines"] == 0


def _pruned_trail(folder, event_log):
    """Two January lines pruned and one November line kept; the event log's records, oldest first."""
    for month in ("2026-01", "2026-01", "2026-11"):
        audit_chain.append(folder, {"at": _at(month), "action": "x"})
    audit_chain.prune(folder, 30, now=NOW)
    audit_anchor._cache.clear()
    return [audit_anchor.parse(r) for r in event_log.records]


def test_the_event_log_recording_the_start_of_a_pruned_trail_matches_it(tmp_path, event_log):
    anchors = _pruned_trail(tmp_path, event_log)
    assert anchors[1].lines == 2 and anchors[1].hash == _head(tmp_path)["base"]["hash"]
    del event_log.records[2:]  # the newest record is the one for the last line pruned
    assert audit_chain.verify(tmp_path)["problems"] == []


def test_an_event_log_record_of_another_start_is_reported(tmp_path, event_log):
    _pruned_trail(tmp_path, event_log)
    event_log.records[:] = [audit_anchor.text(audit_anchor.Anchor(audit_anchor.fingerprint(tmp_path), "ab" * 32, 2))]
    assert audit_chain.verify(tmp_path)["problems"] == [
        "event log: the trail's start isn't the one the Windows event log recorded"]


def test_a_pruned_trail_ending_before_the_event_logs_record_counts_every_line(tmp_path, event_log):
    _pruned_trail(tmp_path, event_log)
    event_log.records.append(audit_anchor.text(audit_anchor.Anchor(audit_anchor.fingerprint(tmp_path), "cd" * 32, 5)))
    assert audit_chain.verify(tmp_path)["problems"] == [
        ("event log: the trail ends before the newest line the Windows event log recorded (line 3), so lines were "
         "deleted from the end. The event log recorded 5 lines in all, ending with cdcdcdcdcdcd, and the trail has 3")]


def test_the_line_head_json_names_found_again_at_its_place_isnt_lost(tmp_path):
    # Every line is gone and the same line is written again: it chains onto nothing, as the first did, so it
    # is the line head.json names, at the place head.json names.
    entry = {"at": START.isoformat(), "action": "x"}
    audit_chain.append(tmp_path, dict(entry))
    [path] = audit_chain.files(tmp_path)
    path.write_text("", encoding="utf-8")
    audit_chain.append(tmp_path, dict(entry))
    assert "lost" not in _head(tmp_path) and _head(tmp_path)["lines"] == 1
    assert audit_chain.verify(tmp_path) == {"lines": 1, "ok": True, "problems": []}
