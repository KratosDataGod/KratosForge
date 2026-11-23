"""The local audit trail can't be changed by hand without ``verify()`` saying so, and saying where.

test_audit.py and test_audit_chain.py already show an edited field, a deleted line, a cut-off end and a
line that isn't JSON being reported by ``verify()``. These cover the rest: lines swapped, inserted or
duplicated, a deleted line named by place, an older month's file changed or gone, blank lines, and
forged lines whose hashes were recomputed. Each one is also checked through the app's ``audit_log``
call, whose ``check`` is what the app shows.

The end of the trail is anchored twice: in ``head.json``, and in the Windows Application event log
(audit_anchor.py), which a user who isn't an administrator can't change. With an event-log record,
rewriting every hash and ``head.json`` to match, or cutting lines and deleting ``head.json``, is caught.
Without one (off Windows, an event log that can't be read, or a trail from before either existed) those
two still check out; the limits below pin that, so a change to it is seen.
"""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit, audit_chain
from fabric_desktop.service import Service

COUNT = 6
CUT = (f"{audit_chain.HEAD}: the trail ends before the newest line recorded (line {COUNT}); "
       "lines were deleted from the end")


@pytest.fixture
def service(tmp_path):
    service = Service(root=tmp_path)
    for i in range(COUNT):
        service.audit.record("meta_run", "ok", 0.1, {"env": "dev", "n": i})
    assert service.audit.verify() == {"lines": COUNT, "ok": True, "problems": []}
    return service


def _file(service):
    [path] = audit_chain.files(service.audit.folder)
    return path


def _read(path):
    return path.read_text(encoding="utf-8").splitlines()


def _write(path, lines):
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _check(service):
    """What the app shows: the audit_log call's check, which must agree with verify()."""
    check = service.audit_log()["check"]
    assert check == service.audit.verify()
    return check


def _reseal(lines, start):
    """A forger's rewrite: every line from ``start`` (0-based) on gets a new prev and hash."""
    out = list(lines[:start])
    prev = json.loads(out[-1])["hash"] if out else audit_chain.GENESIS
    for line in lines[start:]:
        entry = json.loads(line)
        entry.pop("hash")
        out.append(audit_chain.seal(prev, entry))
        prev = entry["hash"]
    return out


def test_two_lines_swapped_are_named(service):
    path = _file(service)
    lines = _read(path)
    lines[2], lines[3] = lines[3], lines[2]
    _write(path, lines)
    check = _check(service)
    assert not check["ok"] and check["lines"] == COUNT
    assert f"{path.name} line 3: a line before it is missing or changed" in check["problems"]
    assert f"{path.name} line 4: a line before it is missing or changed" in check["problems"]
    assert not any("this line was changed" in p for p in check["problems"])  # each line's own content is intact


def test_the_last_two_lines_swapped_are_named(service):
    path = _file(service)
    lines = _read(path)
    lines[-2], lines[-1] = lines[-1], lines[-2]
    _write(path, lines)
    check = _check(service)
    assert not check["ok"]
    assert f"{path.name} line {COUNT - 1}: a line before it is missing or changed" in check["problems"]
    assert f"{path.name} line {COUNT}: a line before it is missing or changed" in check["problems"]


def test_a_line_deleted_from_the_middle_is_named(service):
    path = _file(service)
    lines = _read(path)
    del lines[2]
    _write(path, lines)
    check = _check(service)
    assert not check["ok"] and check["lines"] == COUNT - 1
    # the line that followed the deleted one, now line 3, no longer chains onto the line before it
    assert check["problems"][0] == f"{path.name} line 3: a line before it is missing or changed"
    assert any(p.startswith(audit_chain.HEAD) for p in check["problems"])  # and the count no longer adds up


def test_the_whole_newest_file_deleted_is_reported(service):
    _file(service).unlink()
    check = _check(service)
    assert not check["ok"] and check["lines"] == 0
    assert check["problems"] == [CUT]


def test_a_made_up_line_inserted_is_named(service):
    path = _file(service)
    lines = _read(path)
    fake = json.loads(lines[1])
    fake["action"], fake["hash"] = "meta_promote", "f" * 64
    lines.insert(2, json.dumps(fake))
    _write(path, lines)
    check = _check(service)
    assert not check["ok"] and check["lines"] == COUNT + 1
    assert f"{path.name} line 3: this line was changed" in check["problems"]
    assert f"{path.name} line 4: a line before it is missing or changed" in check["problems"]


def test_a_real_line_copied_in_from_elsewhere_is_named(service):
    path = _file(service)
    lines = _read(path)
    lines.insert(1, lines[4])  # a genuine line, hash and all, moved earlier
    _write(path, lines)
    check = _check(service)
    assert not check["ok"]
    assert f"{path.name} line 2: a line before it is missing or changed" in check["problems"]
    assert f"{path.name} line 3: a line before it is missing or changed" in check["problems"]


@pytest.mark.parametrize("at", [2, COUNT - 1])  # a line in the middle, and the newest line
def test_a_duplicated_line_is_named(service, at):
    path = _file(service)
    lines = _read(path)
    lines.insert(at + 1, lines[at])
    _write(path, lines)
    check = _check(service)
    assert not check["ok"] and check["lines"] == COUNT + 1
    assert f"{path.name} line {at + 2}: a line before it is missing or changed" in check["problems"]
    assert not any("this line was changed" in p for p in check["problems"])


# ------------------------------------------------------------------------------------- month files

MONTHS = ["2026-09-30T09:00:00+00:00", "2026-10-15T09:00:00+00:00", "2026-10-20T09:00:00+00:00",
          "2026-11-01T09:00:00+00:00", "2026-11-02T09:00:00+00:00"]


@pytest.fixture
def months(tmp_path):
    """A trail across three month files (the log starts a new file each month; nothing else rotates it)."""
    service = Service(root=tmp_path)
    for i, at in enumerate(MONTHS):
        audit_chain.append(service.audit.folder, {"at": at, "action": "meta_run", "outcome": "ok", "n": i})
    names = [p.name for p in audit_chain.files(service.audit.folder)]
    assert names == ["audit-2026-09.jsonl", "audit-2026-10.jsonl", "audit-2026-11.jsonl"]
    assert _check(service)["ok"]
    return service


def _month(service, name):
    return service.audit.folder / name


def test_an_older_months_line_edited_is_named_by_its_file(months):
    path = _month(months, "audit-2026-10.jsonl")
    lines = _read(path)
    edited = json.loads(lines[1])
    edited["outcome"] = "error"
    lines[1] = json.dumps(edited)
    _write(path, lines)
    assert _check(months)["problems"] == ["audit-2026-10.jsonl line 2: this line was changed"]


def test_an_older_months_line_deleted_is_named_by_its_file(months):
    path = _month(months, "audit-2026-10.jsonl")
    _write(path, _read(path)[1:])
    check = _check(months)
    assert not check["ok"]
    assert check["problems"][0] == "audit-2026-10.jsonl line 1: a line before it is missing or changed"


@pytest.mark.parametrize("gone, first", [("audit-2026-09.jsonl", "audit-2026-10.jsonl"),
                                         ("audit-2026-10.jsonl", "audit-2026-11.jsonl")])
def test_an_older_months_file_deleted_is_reported(months, gone, first):
    """The oldest month too: without a prune's ``base`` in head.json, the trail must start at GENESIS."""
    _month(months, gone).unlink()
    check = _check(months)
    assert not check["ok"]
    assert check["problems"][0] == f"{first} line 1: a line before it is missing or changed"


def test_a_line_moved_into_another_months_file_is_reported(months):
    old, new = _month(months, "audit-2026-10.jsonl"), _month(months, "audit-2026-11.jsonl")
    moved = _read(old)
    _write(old, moved[:1])
    _write(new, moved[1:] + _read(new)[:1] + [moved[1]])  # one line moved to after another, and copied
    assert not _check(months)["ok"]


# ------------------------------------------------------------------------------- blank and junk lines

@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_line_changes_no_record_and_doesnt_shift_where_a_change_is_named(service, blank):
    path = _file(service)
    lines = _read(path)
    lines.insert(2, blank)
    _write(path, lines)
    assert _check(service) == {"lines": COUNT, "ok": True, "problems": []}  # every record is intact
    edited = json.loads(lines[4])
    edited["outcome"] = "error"
    lines[4] = json.dumps(edited)
    _write(path, lines)
    assert _check(service)["problems"] == [f"{path.name} line 5: this line was changed"]  # the line in the file


@pytest.mark.parametrize("junk", ["{not json", "[1, 2]", "\"text\"", "null", "{\"no\": \"hash\"}"])
def test_a_line_that_isnt_an_audit_line_still_reaches_the_app(service, junk):
    """The app's audit_log call reads every line to list them. A line that isn't JSON made it raise, so
    the app showed an error and never the check that names the damaged line."""
    path = _file(service)
    lines = _read(path)
    lines.insert(2, junk)
    _write(path, lines)
    out = service.audit_log()
    assert f"{path.name} line 3: not an audit line" in out["check"]["problems"] and not out["check"]["ok"]
    assert all(isinstance(e, dict) for e in out["entries"])
    assert len(out["entries"]) == COUNT + 1  # a JSON object is listed as it is; anything else as a placeholder
    if junk != "{\"no\": \"hash\"}":
        [row] = [e for e in out["entries"] if e["action"] == audit.UNREADABLE]
        assert (row["file"], row["line"], row["outcome"]) == (path.name, 3, "error")
        assert "line 3 isn't an audit line" in row["error"]
        assert not [e for e in service.audit.recent(200, action="run_schedule") if e["action"] == audit.UNREADABLE]


# ------------------------------------------------------------------------ forged lines, hashes recomputed

def test_a_forged_line_with_its_own_hash_recomputed_is_named(service):
    path = _file(service)
    lines = _read(path)
    entry = json.loads(lines[2])
    entry.pop("hash")
    entry["outcome"] = "error"
    lines[2] = audit_chain.seal(entry["prev"], entry)  # its own hash is right now; the next line's prev isn't
    _write(path, lines)
    assert _check(service)["problems"] == [f"{path.name} line 4: a line before it is missing or changed"]


def test_a_forged_newest_line_with_its_hash_recomputed_is_caught_by_the_head(service):
    path = _file(service)
    lines = _read(path)
    entry = json.loads(lines[-1])
    entry.pop("hash")
    entry["outcome"] = "error"
    lines[-1] = audit_chain.seal(entry["prev"], entry)
    _write(path, lines)
    assert _check(service)["problems"] == [CUT]


def test_a_forgery_that_recomputes_every_later_hash_is_caught_only_by_the_head(service):
    path = _file(service)
    lines = _read(path)
    entry = json.loads(lines[2])
    entry["outcome"] = "error"
    lines[2] = json.dumps(entry)
    _write(path, _reseal(lines, 2))
    check = _check(service)
    assert check["problems"] == [CUT]


@pytest.fixture
def anchored(tmp_path, event_log):
    """The same trail, written while the event log records its end."""
    service = Service(root=tmp_path)
    for i in range(COUNT):
        service.audit.record("meta_run", "ok", 0.1, {"env": "dev", "n": i})
    assert len(event_log.records) == COUNT
    assert service.audit.verify() == {"lines": COUNT, "ok": True, "problems": []}
    return service


def _forge_with_head(service):
    path = _file(service)
    lines = _read(path)
    entry = json.loads(lines[2])
    entry["outcome"] = "error"
    lines[2] = json.dumps(entry)
    forged = _reseal(lines, 2)
    _write(path, forged)
    (service.audit.folder / audit_chain.HEAD).write_text(
        json.dumps({"hash": json.loads(forged[-1])["hash"], "lines": len(forged)}), encoding="utf-8")


def test_a_forgery_that_also_rewrites_the_head_is_caught_by_the_event_log(anchored):
    _forge_with_head(anchored)
    [problem] = _check(anchored)["problems"]
    assert problem.startswith(f"event log: line {COUNT} isn't the line the Windows event log recorded, so the "
                              f"trail was re-sealed at or before line {COUNT}. Its hash is ")


def test_lines_cut_from_the_end_with_the_head_deleted_are_caught_by_the_event_log(anchored):
    path = _file(anchored)
    _write(path, _read(path)[:COUNT - 2])
    (anchored.audit.folder / audit_chain.HEAD).unlink()
    missing, cut = _check(anchored)["problems"]
    assert missing == "head.json: missing, so the end of the trail can't be confirmed"
    assert cut.startswith(f"event log: the trail ends before the newest line the Windows event log recorded "
                          f"(line {COUNT}), so lines were deleted from the end. The event log recorded {COUNT} lines")
    assert cut.endswith(f", and the trail has {COUNT - 2}")


def test_the_head_deleted_alone_is_caught_by_the_event_log(anchored):
    (anchored.audit.folder / audit_chain.HEAD).unlink()
    assert _check(anchored)["problems"] == ["head.json: missing, so the end of the trail can't be confirmed"]


def test_limit_a_forgery_that_also_rewrites_the_head_checks_out_with_no_event_log_record(service):
    """With no event-log record (off Windows, or an event log that can't be read): the hash has no key, so
    whoever can write the folder can rebuild the chain and head.json. Shipping the trail, or an export with
    its manifest, elsewhere is what catches it."""
    _forge_with_head(service)
    assert _check(service) == {"lines": COUNT, "ok": True, "problems": []}


def test_limit_lines_cut_from_the_end_of_a_trail_from_before_the_head_check_out(service):
    """A trail with no head.json and no event-log record is taken for one from before head.json existed
    (test_a_trail_from_before_the_head_starts_one_without_a_false_alarm): nothing in its lines says which
    version wrote them, so a cut end with the head deleted, and no event log to compare with, checks out."""
    path = _file(service)
    _write(path, _read(path)[:COUNT - 2])
    assert not _check(service)["ok"]  # with the head there, the cut is seen
    (service.audit.folder / audit_chain.HEAD).unlink()
    assert _check(service) == {"lines": COUNT - 2, "ok": True, "problems": []}
