"""audit_retention_days and receipt_retention_days: kept forever by default; with a limit, whole old
months of an audit trail and old receipts go, each cleanup recorded first, and the trail still verifies."""

import json
import os
import time
from datetime import datetime, timezone

import pytest

from fabric_desktop import app_settings, audit_chain, retention
from fabric_desktop.audit import AuditLog

DAY = 86400


def _at(year, month, day=15):
    return datetime(year, month, day, 12, tzinfo=timezone.utc)


def _trail(folder, months):
    """One audit line per (year, month), in order, as the app writes them."""
    log = AuditLog(folder)
    for year, month in months:
        entry = {"schema": "audit@1", "at": _at(year, month).isoformat(timespec="milliseconds"), "action": "run_sql",
                 "outcome": "ok", "seconds": 0, "target": {}}
        audit_chain.append(folder, entry)
    return log


NOW = _at(2026, 11, 9).timestamp()


def test_records_are_kept_by_default_and_a_limit_needs_at_least_30_days(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    assert app_settings.value("audit_retention_days") == 0 and app_settings.value("receipt_retention_days") == 0
    policy = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    policy.parent.mkdir(parents=True)
    for good in (0, 30, 2555):
        policy.write_text(json.dumps({"audit_retention_days": good, "receipt_retention_days": good}))
        assert app_settings.value("audit_retention_days") == good and app_settings.value("receipt_retention_days") == good
    for bad in (1, 29, -5, "90"):
        policy.write_text(json.dumps({"audit_retention_days": bad}))
        assert app_settings.value("audit_retention_days") == 0  # ignored: falls back to keeping them


def test_without_a_limit_nothing_is_deleted(tmp_path):
    folder = tmp_path / "audit"
    _trail(folder, [(2020, 1), (2026, 11)])
    calls = []
    assert retention.prune_audit(folder, 0, lambda *a: calls.append(a), "app", now=NOW) == {"files": [], "lines": 0}
    assert calls == [] and len(audit_chain.files(folder)) == 2


def test_old_months_go_whole_the_cleanup_is_recorded_first_and_the_trail_verifies(tmp_path):
    folder = tmp_path / "audit"
    log = _trail(folder, [(2025, 1), (2025, 1), (2025, 2), (2026, 9), (2026, 10)])
    recorded = []

    def record(action, params):
        recorded.append((action, params, [p.name for p in audit_chain.files(folder)]))
        log.record(action, "ok", 0, params)  # lands in the newest month, which is never deleted

    gone = retention.prune_audit(folder, 365, record, "app", now=NOW)
    assert gone == {"files": ["audit-2025-01.jsonl", "audit-2025-02.jsonl"], "lines": 3, "through": "2025-02"}
    action, params, files_then = recorded[0]
    assert action == "audit_pruned" and params == {"trail": "app", "days": 365, "files": 2, "through": "2025-02"}
    assert "audit-2025-01.jsonl" in files_then  # recorded while everything was still there
    assert [p.name for p in audit_chain.files(folder)][0] == "audit-2026-09.jsonl"
    check = log.verify()
    assert check["ok"], check["problems"]
    assert check["pruned"] == {"through": "2025-02", "lines": 3}
    # Later writes and a second prune keep the chain whole.
    log.record("run_sql", "ok", 0, {})
    assert retention.prune_audit(folder, 30, record, "app", now=NOW)["through"] == "2026-09"
    assert log.verify()["ok"] and log.verify()["pruned"]["lines"] == 4


def test_the_newest_month_is_never_deleted_whatever_the_limit(tmp_path):
    folder = tmp_path / "audit"
    _trail(folder, [(2020, 1)])
    assert audit_chain.prunable(folder, 30, now=NOW) == []


def test_a_cleanup_whose_line_cant_be_written_deletes_nothing(tmp_path):
    folder = tmp_path / "audit"
    _trail(folder, [(2020, 1), (2026, 11)])

    def full(*_):
        raise OSError("disk full")
    with pytest.raises(OSError):
        retention.prune_audit(folder, 30, full, "app", now=NOW)
    assert len(audit_chain.files(folder)) == 2


def test_a_deleted_line_is_still_found_after_a_prune(tmp_path):
    folder = tmp_path / "audit"
    log = _trail(folder, [(2020, 1), (2026, 10), (2026, 10), (2026, 11)])
    retention.prune_audit(folder, 30, lambda *a: None, "app", now=NOW)
    oct_ = folder / "audit-2026-10.jsonl"
    lines = oct_.read_text().splitlines()
    oct_.write_text(lines[1] + "\n")  # the first line after the cut taken out
    assert not log.verify()["ok"]


def test_a_prune_stopped_part_way_is_finished_by_the_next(tmp_path, monkeypatch):
    folder = tmp_path / "audit"
    log = _trail(folder, [(2020, 1), (2020, 2), (2026, 11)])
    calls = {"n": 0}
    from pathlib import Path
    orig = Path.unlink

    def stop_after_one(self, missing_ok=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("stopped")
        return orig(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", stop_after_one)
    with pytest.raises(OSError):
        audit_chain.prune(folder, 30, now=NOW)
    monkeypatch.setattr(Path, "unlink", orig)
    audit_chain.prune(folder, 30, now=NOW)
    assert [p.name for p in audit_chain.files(folder)] == ["audit-2026-11.jsonl"]
    assert log.verify()["ok"], log.verify()["problems"]


def test_old_receipts_go_after_their_cleanup_is_recorded(tmp_path):
    folder = tmp_path / "receipts"
    folder.mkdir()
    old, new = folder / ("a" * 64 + ".json"), folder / ("b" * 64 + ".json")
    for p, age in ((old, 400), (new, 10)):
        p.write_text("{}")
        os.utime(p, (time.time() - age * DAY,) * 2)
    (folder / "notes.txt").write_text("not a receipt")
    os.utime(folder / "notes.txt", (time.time() - 400 * DAY,) * 2)
    calls = []
    assert retention.prune_receipts(folder, 0, lambda *a: calls.append(a)) == {"removed": 0}
    assert retention.prune_receipts(folder, 365, lambda *a: calls.append(a)) == {"removed": 1}
    assert calls == [("receipts_pruned", {"days": 365, "receipts": 1})]
    assert not old.exists() and new.exists() and (folder / "notes.txt").exists()


def test_the_app_prunes_both_trails_and_receipts_at_start_under_the_policy(tmp_path, monkeypatch):
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path)
    _trail(svc.audit.folder, [(2020, 1)])  # its only month, so the newest: kept until a newer one exists
    _trail(tmp_path / "integrations" / "audit", [(2020, 1), (2026, 11)])
    receipt = tmp_path / "receipts" / ("c" * 64 + ".json")
    receipt.parent.mkdir()
    receipt.write_text("{}")
    os.utime(receipt, (time.time() - 400 * DAY,) * 2)
    values = {"audit_retention_days": 365, "receipt_retention_days": 365}
    monkeypatch.setattr(app_settings, "value", lambda key: values.get(key, app_settings.DEFAULTS.get(key)))
    svc._prune_records()
    assert [p.name for p in audit_chain.files(tmp_path / "integrations" / "audit")] == ["audit-2026-11.jsonl"]
    assert not receipt.exists()
    actions = [e["action"] for e in svc.audit.entries()]
    assert actions.count("audit_pruned") == 1 and "receipts_pruned" in actions
    assert svc.audit.verify()["ok"]
    svc._prune_records()  # now the app's trail has this month too: its 2020 month goes
    actions = [e["action"] for e in svc.audit.entries()]
    assert "run_sql" not in actions and actions.count("audit_pruned") == 2  # the 2020 line went with its month
    assert len(audit_chain.files(svc.audit.folder)) == 1 and svc.audit.verify()["ok"]



def _daily(tmp_path, monkeypatch, prune):
    """Run the app's daily sweep with a short day until ``prune`` has been called twice."""
    import threading

    from fabric_desktop import retention as retention_mod
    from fabric_desktop import service as service_mod
    from fabric_desktop.service import Service
    svc = Service(root=tmp_path)
    calls, done = [], threading.Event()
    monkeypatch.setattr(retention_mod, "sweep", lambda *a, **k: {"removed": [], "bytes": 0, "days": 30})
    monkeypatch.setattr(service_mod, "SWEEP_EVERY", 0.05)

    def counted():
        calls.append(1)
        if len(calls) >= 2:
            done.set()
        prune()
    monkeypatch.setattr(svc, "_prune_records", counted)
    try:
        svc._start_retention_sweep()
        assert done.wait(10)
    finally:
        svc._sweep_stop.set()
        svc._sweeper.join(10)
    return calls


def test_the_app_checks_the_limits_again_each_day_while_it_runs(tmp_path, monkeypatch):
    assert len(_daily(tmp_path, monkeypatch, lambda: None)) >= 2


def test_a_daily_check_that_fails_is_logged_and_tried_again(tmp_path, monkeypatch, caplog):
    import logging

    from fabric_desktop import service as service_mod

    def broken():
        raise ValueError("policy.json can't be read")
    with caplog.at_level(logging.ERROR, logger=service_mod.__name__):
        assert len(_daily(tmp_path, monkeypatch, broken)) >= 2
    assert "Audit trail and receipt cleanup failed" in caplog.text
