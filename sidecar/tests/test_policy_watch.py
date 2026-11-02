"""#1670: a change to the organization's policy file is recorded in the audit trail and shown in
Settings > Help and about. A changed file, a broken file (the last copy that could be read applies),
an unchanged file and a removed file each record what enterprise-install.md says."""

import hashlib
import json

import pytest

from fabric_desktop import app_settings, policy_watch
from fabric_desktop.audit import AuditLog
from fabric_desktop.service import Service


@pytest.fixture
def pc(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    root = tmp_path / "la" / "FabricDesktop"
    root.mkdir(parents=True)
    audit = AuditLog(root / "audit")

    class PC:
        def write(self, data):
            path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def check(self):
            return policy_watch.check(root, audit)

        def entries(self):
            return [e for e in audit.entries() if e["action"].startswith("policy_file_")]
    pc = PC()
    pc.path, pc.root, pc.audit = path, root, audit
    return pc


def test_first_look_records_a_baseline_and_an_unchanged_file_records_nothing(pc):
    sha = pc.write({"show_preview": False})
    first = pc.check()
    assert first["sha256"] == sha and first["state"] == "applied" and first["first"] is True
    assert [(e["action"], e["target"]["sha256"], e["target"]["state"]) for e in pc.entries()] == [
        ("policy_file_seen", sha, "applied")]
    assert pc.check()["sha256"] == sha
    assert pc.check()["changed_at"] == first["changed_at"]
    assert len(pc.entries()) == 1  # unchanged: nothing new


def test_a_changed_file_records_the_hash_before_and_after(pc):
    old = pc.write({"show_preview": False})
    pc.check()
    new = pc.write({"show_preview": True})
    record = pc.check()
    assert record["previous"] == old and record["sha256"] == new and record["first"] is False
    last = pc.entries()[-1]
    assert last["action"] == "policy_file_changed" and last["outcome"] == "ok"
    assert last["target"]["previous"] == old and last["target"]["sha256"] == new
    assert last["target"]["managed"] is True  # the tests' conftest: only administrators can change it
    assert pc.audit.verify()["ok"] is True


def test_a_change_the_signed_in_account_could_make_is_marked_unmanaged(pc, monkeypatch):
    pc.write({"show_preview": False})
    pc.check()
    monkeypatch.setattr(app_settings, "user_can_change", lambda path: True)
    pc.write({"show_preview": True})
    assert pc.check()["managed"] is False
    assert pc.entries()[-1]["target"]["managed"] is False


def test_a_broken_file_records_that_the_last_good_copy_applies(pc):
    pc.write({"show_preview": False})
    pc.check()
    broken = pc.write("{not json")
    record = pc.check()
    assert record["state"] == "last_good" and record["sha256"] == broken
    assert pc.entries()[-1]["target"]["state"] == "last_good"


def test_a_broken_file_with_no_good_copy_records_fail_closed(pc):
    pc.write("{not json")
    assert pc.check()["state"] == "fail_closed"


def test_a_removed_file_is_recorded_too(pc):
    old = pc.write({"show_preview": False})
    pc.check()
    pc.path.unlink()
    record = pc.check()
    assert record["state"] == "none" and record["sha256"] is None and record["previous"] == old
    assert pc.entries()[-1]["action"] == "policy_file_changed"


def test_an_unreadable_record_of_what_was_seen_starts_again_from_a_baseline(pc):
    pc.write({"show_preview": False})
    pc.check()
    (pc.root / policy_watch.SEEN).write_text("{broken", encoding="utf-8")
    assert pc.check()["first"] is True
    assert [e["action"] for e in pc.entries()] == ["policy_file_seen", "policy_file_seen"]


def test_settings_carries_the_record_and_a_failure_never_breaks_it(pc, monkeypatch):
    sha = pc.write({"show_preview": False})
    svc = Service(root=pc.root)
    assert svc.app_settings()["policy_record"]["sha256"] == sha

    def broken(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(policy_watch, "check", broken)
    assert svc.app_settings()["policy_record"] is None


def test_two_looks_at_once_record_a_change_once(pc):
    import threading
    pc.write({"show_preview": False})
    pc.check()
    pc.write({"show_preview": True})
    threads = [threading.Thread(target=pc.check) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [e["action"] for e in pc.entries()] == ["policy_file_seen", "policy_file_changed"]
