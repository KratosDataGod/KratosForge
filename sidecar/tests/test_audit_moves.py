"""The audit trail and its exports still check out after files are moved, renamed or rotated (WR-408),
and a trail that doesn't match its Windows event log record says what each side holds."""

from __future__ import annotations

import datetime as dt
import json
import shutil

import pytest

from fabric_desktop import audit_chain

OCTOBER = ["2026-10-30T09:00:00+00:00", "2026-10-31T09:00:00+00:00"]
NOVEMBER = ["2026-11-01T09:00:00+00:00", "2026-11-02T09:00:00+00:00", "2026-11-03T09:00:00+00:00"]
DECEMBER = dt.datetime(2026, 12, 15, tzinfo=dt.timezone.utc).timestamp()  # October has ended a month ago


def _add(folder, times, who="dana@example.com"):
    for at in times:
        audit_chain.append(folder, {"at": at, "action": "meta_run", "outcome": "ok", "who": who})


@pytest.fixture
def trail(tmp_path):
    folder = tmp_path / "audit"
    _add(folder, OCTOBER + NOVEMBER)
    return folder


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------- the trail's folder moved

def test_a_moved_trail_is_checked_against_the_records_for_the_folder_it_was_written_in(event_log, trail, tmp_path):
    moved = tmp_path / "elsewhere" / "audit"
    moved.parent.mkdir()
    shutil.move(str(trail), str(moved))
    result = audit_chain.verify(moved)
    assert result["ok"] and any("was moved" in n for n in result["notes"]), result


def test_lines_cut_from_a_moved_trail_are_still_caught(event_log, trail, tmp_path):
    moved = tmp_path / "moved"
    shutil.move(str(trail), str(moved))
    path = audit_chain.files(moved)[-1]
    path.write_text("".join(line + "\n" for line in _lines(path)[:1]), encoding="utf-8")
    head = json.loads((moved / audit_chain.HEAD).read_text())
    head.update(hash=json.loads(_lines(path)[0])["hash"], lines=3)  # the head rewritten to match
    (moved / audit_chain.HEAD).write_text(json.dumps(head))
    [problem] = audit_chain.verify(moved)["problems"]
    assert problem.startswith("event log: the trail ends before the newest line the Windows event log recorded "
                              "(line 5)") and problem.endswith("; the trail has 3"), problem


def test_after_its_next_write_a_moved_trail_is_checked_against_its_own_records(event_log, trail, tmp_path):
    moved = tmp_path / "moved"
    shutil.move(str(trail), str(moved))
    _add(moved, ["2026-11-04T09:00:00+00:00"])
    result = audit_chain.verify(moved)
    assert result == {"lines": 6, "ok": True, "problems": []}


# ---------------------------------------------------------------- month files renamed or rotated

def test_a_copy_of_a_month_under_another_name_isnt_read_and_is_named(trail):
    shutil.copy(trail / "audit-2026-10.jsonl", trail / "audit-2026-10 (1).jsonl")
    result = audit_chain.verify(trail)
    assert result["ok"] and result["lines"] == 5
    assert result["notes"] == [("audit-2026-10 (1).jsonl: looks like a month of this trail under another name "
                               "(renamed, copied or rotated), so it isn't checked. Name a month's file "
                               "audit-YYYY-MM.jsonl to check it with the trail")]
    audit_chain.append(trail, {"at": NOVEMBER[-1], "action": "meta_run", "outcome": "ok"})
    assert audit_chain.verify(trail)["ok"]  # the copy is never written to or chained onto


def test_the_newest_month_rotated_away_is_caught_and_named(event_log, trail):
    (trail / "audit-2026-11.jsonl").rename(trail / "audit-2026-11.jsonl.1")
    result = audit_chain.verify(trail)
    assert not result["ok"]
    assert any("lines were deleted from the end" in p for p in result["problems"])
    assert any(n.startswith("audit-2026-11.jsonl.1: looks like a month of this trail") for n in result["notes"])
    (trail / "audit-2026-11.jsonl.1").rename(trail / "audit-2026-11.jsonl")
    assert audit_chain.verify(trail)["ok"]


# ---------------------------------------------------------------- exports moved or renamed

def test_an_export_moved_with_its_manifest_checks_out(trail, tmp_path):
    out = tmp_path / "a" / "audit-export.jsonl"
    audit_chain.export(trail, out)
    there = tmp_path / "b"
    there.mkdir()
    for path in (out, audit_chain.manifest_path(out)):
        shutil.move(str(path), str(there / path.name))
    assert audit_chain.verify_export(there / out.name, trail) == {"ok": True, "lines": 5, "problems": []}


def test_a_renamed_export_finds_its_manifest_by_checksum(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out)
    renamed = out.rename(tmp_path / "november-audit.jsonl")
    result = audit_chain.verify_export(renamed, trail)
    assert result["ok"] and result["notes"] == [
        ("audit-export.jsonl.manifest.json: the manifest for this export, written for audit-export.jsonl "
        "(the export was renamed)")]


def test_a_renamed_and_changed_export_still_fails(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out)
    renamed = out.rename(tmp_path / "november-audit.jsonl")
    renamed.write_text(renamed.read_text().replace('"ok"', '"error"', 1))
    [problem] = audit_chain.verify_export(renamed)["problems"]
    assert problem.startswith("november-audit.jsonl.manifest.json: missing. An export is checked with the "
                              "manifest written beside it")


def test_the_manifest_can_be_given_in_place_of_the_export(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out)
    assert audit_chain.verify_export(audit_chain.manifest_path(out))["ok"]


def test_a_missing_export_says_so(trail, tmp_path):
    out = tmp_path / "audit-export.jsonl"
    audit_chain.export(trail, out)
    out.unlink()
    assert audit_chain.verify_export(out)["problems"] == ["audit-export.jsonl: missing"]


# ---------------------------------------------------------------- exports of lines pruned since

def test_an_export_partly_pruned_since_checks_against_what_the_trail_still_has(trail, tmp_path):
    out = tmp_path / "all.jsonl"
    audit_chain.export(trail, out)
    assert audit_chain.prune(trail, 1, now=DECEMBER)["files"] == ["audit-2026-10.jsonl"]
    result = audit_chain.verify_export(out, trail)
    assert result["ok"] and result["notes"] == [
        ("the export's first 2 lines were pruned from this trail (through 2026-10) after it was taken; "
        "the rest match the trail")]


def test_an_export_wholly_pruned_since_joins_where_the_trail_now_starts(tmp_path):
    folder = tmp_path / "audit"
    _add(folder, OCTOBER)
    out = tmp_path / "october.jsonl"
    audit_chain.export(folder, out)
    _add(folder, NOVEMBER)
    audit_chain.prune(folder, 1, now=DECEMBER)
    result = audit_chain.verify_export(out, folder)
    assert result["ok"] and "its last line is the one the trail now starts after" in result["notes"][0]


def test_a_partly_pruned_export_that_differs_from_the_trail_fails(trail, tmp_path):
    out = tmp_path / "all.jsonl"
    audit_chain.export(trail, out)
    audit_chain.prune(trail, 1, now=DECEMBER)
    lines = _lines(out)
    forged = tmp_path / "forged"
    _add(forged, OCTOBER, who="someone-else@example.com")  # other October lines, then the trail's own
    for line in lines[2:]:
        (forged / "audit-2026-11.jsonl").open("a").write(line + "\n")
    bad = tmp_path / "bad.jsonl"
    audit_chain.export(forged, bad)
    assert "the export's lines differ from this trail's" in audit_chain.verify_export(bad, trail)["problems"]


def test_a_forged_export_dated_after_the_pruned_months_fails(trail, tmp_path):
    audit_chain.prune(trail, 1, now=DECEMBER)
    forged = tmp_path / "forged"
    _add(forged, NOVEMBER, who="someone-else@example.com")
    out = tmp_path / "forged.jsonl"
    audit_chain.export(forged, out)
    assert audit_chain.verify_export(out, trail)["problems"] == ["the export's lines aren't in this trail"]


def test_an_export_of_pruned_months_is_checked_against_its_manifest_only_and_says_so(trail, tmp_path):
    """The documented limit: lines a prune deleted can't be compared with the trail any more."""
    audit_chain.prune(trail, 1, now=DECEMBER)
    other = tmp_path / "other"
    _add(other, OCTOBER, who="someone-else@example.com")
    out = tmp_path / "old.jsonl"
    audit_chain.export(other, out)
    result = audit_chain.verify_export(out, trail)
    assert result["ok"] and result["notes"] == [
        ("the export's lines are in months pruned from this trail (through 2026-10), so it's checked against "
        "its manifest only")]
