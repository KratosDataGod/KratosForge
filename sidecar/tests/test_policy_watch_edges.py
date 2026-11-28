"""policy_watch at its edges: a record of what was seen that can't be saved (the change is still in the
audit trail, and the next look records it again), a record that isn't an object, a Group Policy key that
can't be read, and a registry change alone counting as a change."""

from __future__ import annotations

import json
import logging

import pytest

from fabric_desktop import app_settings, jsonfile, policy_watch
from fabric_desktop.audit import AuditLog


@pytest.fixture
def pc(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    root = tmp_path / "la" / "FabricDesktop"
    root.mkdir(parents=True)
    now = {"sha256": "a" * 64, "state": "applied", "user_can_change": False, "registry_sha256": None}
    monkeypatch.setattr(app_settings, "policy_fingerprint", lambda: dict(now))
    audit = AuditLog(root / "audit")

    class PC:
        def check(self):
            return policy_watch.check(root, audit)

        def entries(self):
            return [e for e in audit.entries() if e["action"].startswith("policy_file_")]
    out = PC()
    out.root, out.now = root, now
    return out


def test_a_record_that_cant_be_saved_is_still_audited_and_recorded_again_next_time(pc, monkeypatch, caplog):
    write = jsonfile.write_text

    def full(path, text, *a, **k):
        if path.name == policy_watch.SEEN:
            raise OSError("disk full")
        return write(path, text, *a, **k)
    monkeypatch.setattr(jsonfile, "write_text", full)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.policy_watch"):
        first = pc.check()
    assert first["first"] is True and first["sha256"] == "a" * 64
    assert any("Couldn't save" in r.getMessage() and "disk full" in r.getMessage() for r in caplog.records)
    assert not (pc.root / policy_watch.SEEN).exists()
    assert pc.check()["first"] is True  # nothing was kept, so it's a first look again
    assert [e["action"] for e in pc.entries()] == ["policy_file_seen", "policy_file_seen"]


def test_a_record_that_isnt_an_object_is_a_first_look(pc):
    (pc.root / policy_watch.SEEN).write_text(json.dumps(["a" * 64]), encoding="utf-8")
    assert pc.check()["first"] is True
    assert [e["action"] for e in pc.entries()] == ["policy_file_seen"]


def test_a_group_policy_key_that_cant_be_read_is_said_in_the_audit_line(pc):
    pc.now["registry_unreadable"] = "PermissionError"
    pc.check()
    assert pc.entries()[-1]["target"]["registry_unreadable"] == "PermissionError"


def test_an_unread_key_isnt_mentioned_when_it_reads(pc):
    pc.check()
    assert "registry_unreadable" not in pc.entries()[-1]["target"]


def test_a_change_to_group_policy_alone_is_a_change(pc):
    pc.check()
    pc.now["registry_sha256"] = "b" * 64
    record = pc.check()
    assert record["first"] is False and record["previous"] == "a" * 64
    assert record["registry_sha256"] == "b" * 64 and record["registry_previous"] is None
    last = pc.entries()[-1]
    assert last["action"] == "policy_file_changed" and last["target"]["registry_sha256"] == "b" * 64
    assert pc.check() == record  # unchanged again: the saved record comes back, nothing new is written
    assert len(pc.entries()) == 2


def test_a_change_of_which_copy_applies_is_a_change(pc):
    pc.check()
    pc.now["state"] = "last_good"
    assert pc.check()["state"] == "last_good"
    assert pc.entries()[-1]["target"]["state"] == "last_good"
