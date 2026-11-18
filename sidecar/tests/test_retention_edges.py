"""The retention purges at their edges: a record exactly at its expiry instant, a clock that went back
or jumped forward, a purge stopped part way, and a person's own setting against a longer policy.

Every purge here keeps a record that is exactly at its limit and removes it one second later (``<``
against ``now - keep``): the local copies sweep, left-over spills and previews, run receipts, audit
trail months, the Teams notice ledger and the notify outbox. Clocks are fakes, never slept on."""

import json
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import pytest

from fabric_desktop import app_settings, audit_chain, registry_policy, retention, sqlitefile
from fabric_desktop import platform_notify_service as pns
from fabric_desktop.audit import AuditLog
from fabric_desktop.workloads.notify import KEEP_TRYING, Outbox
from fabric_desktop.workloads.platform_notify import Ledger

DAY = retention.DAY
NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc).timestamp()
YEAR = pns.SENT_KEEP_SECONDS


def _file(path: Path, mtime: float, body: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    os.utime(path, (mtime, mtime))
    return path


def _receipt(folder: Path, letter: str, mtime: float) -> Path:
    return _file(folder / (letter * 64 + ".json"), mtime, "{}")


# ---- the local copies sweep (local_retention_days), left-over spills and previews


def test_a_local_copy_exactly_at_its_limit_stays_and_goes_one_second_later(tmp_path):
    cutoff = NOW - 30 * DAY
    at = _file(tmp_path / "files" / "at.csv", cutoff)
    before = _file(tmp_path / "files" / "before.csv", cutoff - 1)
    after = _file(tmp_path / "files" / "after.csv", cutoff + 1)
    out = retention.sweep(tmp_path, 30, now=NOW)
    assert out["removed"] == ["files/before.csv"]
    assert at.exists() and after.exists() and not before.exists()
    assert retention.sweep(tmp_path, 30, now=NOW + 1)["removed"] == ["files/at.csv"]
    assert after.exists()


def test_a_delta_table_exactly_at_its_limit_stays_whole(tmp_path):
    cutoff = NOW - 30 * DAY
    table = tmp_path / "staging" / "sales"
    _file(table / "_delta_log" / "00000.json", cutoff - 10 * DAY)
    newest = _file(table / "part-0.parquet", cutoff)  # its newest file decides
    assert retention.sweep(tmp_path, 30, now=NOW)["removed"] == []
    assert newest.exists() and (table / "_delta_log" / "00000.json").exists()
    assert retention.sweep(tmp_path, 30, now=NOW + 1)["removed"] == ["staging/sales"]
    assert not table.exists()


def test_left_over_spills_and_previews_exactly_at_their_limit_stay(tmp_path):
    spills = tmp_path / retention.SPILLS
    spill_cut = NOW - retention.SPILL_HOURS * 3600
    _file(spills / "at.spill", spill_cut)
    _file(spills / "before.spill", spill_cut - 1)
    preview_cut = NOW - retention.PREVIEW_HOURS * 3600
    for name, when in (("at", preview_cut), ("before", preview_cut - 1)):
        folder = tmp_path / retention.PREVIEWS / name
        folder.mkdir(parents=True)
        os.utime(folder, (when, when))
    out = retention.sweep(tmp_path, 0, now=NOW)
    assert sorted(out["removed"]) == ["preview-files/before", "staging/_writes/before.spill"]
    assert (spills / "at.spill").exists() and (tmp_path / retention.PREVIEWS / "at").exists()


def test_a_local_copy_from_the_future_is_never_swept_early(tmp_path):
    # The clock was ahead when it was written and has been put back: its age counts from then.
    ahead = _file(tmp_path / "files" / "ahead.csv", NOW + 10 * DAY)
    spill = _file(tmp_path / retention.SPILLS / "ahead.spill", NOW + 10 * DAY)
    assert retention.sweep(tmp_path, 30, now=NOW)["removed"] == []
    assert retention.sweep(tmp_path, 30, now=NOW + 11 * DAY)["removed"] == []  # a day after its time
    assert retention.sweep(tmp_path, 30, now=NOW + 11 * DAY + 1)["removed"] == ["staging/_writes/ahead.spill"]
    assert retention.sweep(tmp_path, 30, now=NOW + 40 * DAY)["removed"] == []  # 30 days after its time
    assert ahead.exists() and not spill.exists()
    assert retention.sweep(tmp_path, 30, now=NOW + 40 * DAY + 1)["removed"] == ["files/ahead.csv"]


def test_a_clock_jump_forwards_takes_only_what_the_period_says(tmp_path):
    kept = _file(tmp_path / "files" / "kept.csv", NOW - 20 * DAY)
    gone = _file(tmp_path / "files" / "gone.csv", NOW - 20 * DAY - 1)
    # The clock jumps ten days on: a copy 20 days old is now 30 days old exactly, still kept under 30.
    assert retention.sweep(tmp_path, 30, now=NOW + 10 * DAY)["removed"] == ["files/gone.csv"]
    assert kept.exists() and not gone.exists()
    assert retention.sweep(tmp_path, 90, now=NOW + 60 * DAY)["removed"] == []  # a longer period keeps it


def test_a_sweep_stopped_part_way_keeps_what_is_new_and_the_next_finishes(tmp_path, monkeypatch):
    old = [_file(tmp_path / "files" / f"old{i}.csv", NOW - 40 * DAY) for i in range(4)]
    new = _file(tmp_path / "files" / "new.csv", NOW)
    real, calls = retention._remove, {"n": 0}

    def killed_after_two(path):
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt("engine ended")
        return real(path)
    monkeypatch.setattr(retention, "_remove", killed_after_two)
    with pytest.raises(KeyboardInterrupt):
        retention.sweep(tmp_path, 30, now=NOW)
    assert sum(p.exists() for p in old) == 2 and new.exists()
    monkeypatch.setattr(retention, "_remove", real)
    assert len(retention.sweep(tmp_path, 30, now=NOW)["removed"]) == 2
    assert not any(p.exists() for p in old) and new.exists()


def test_a_table_whose_delete_was_killed_is_never_left_half_under_its_name(tmp_path, monkeypatch):
    table = tmp_path / "staging" / "sales"
    for name in ("_delta_log/00000.json", "part-0.parquet", "part-1.parquet"):
        _file(table / name, NOW - 40 * DAY)
    fresh = _file(tmp_path / "staging" / "orders" / "part-0.parquet", NOW)
    _file(tmp_path / "staging" / "orders" / "_delta_log" / "00000.json", NOW)
    real = shutil.rmtree

    def killed_mid_delete(path, ignore_errors=False):
        next(Path(path).rglob("*.parquet")).unlink()  # one file went, then the process was killed
        raise KeyboardInterrupt("engine ended")
    monkeypatch.setattr(retention.shutil, "rmtree", killed_mid_delete)
    with pytest.raises(KeyboardInterrupt):
        retention.sweep(tmp_path, 30, now=NOW)
    assert not table.exists()  # renamed aside first: never half there under its own name
    [aside] = [p for p in (tmp_path / "staging").iterdir() if retention._is_trash(p)]
    monkeypatch.setattr(retention.shutil, "rmtree", real)
    out = retention.sweep(tmp_path, 30, now=NOW)
    assert out["removed"] == [aside.relative_to(tmp_path).as_posix()] and not aside.exists()
    assert fresh.exists()


# ---- run receipts (receipt_retention_days)


def test_a_receipt_exactly_at_its_limit_stays_and_goes_one_second_later(tmp_path):
    folder = tmp_path / "receipts"
    cutoff = NOW - 365 * DAY
    at, before, after = _receipt(folder, "a", cutoff), _receipt(folder, "b", cutoff - 1), _receipt(folder, "c", cutoff + 1)
    calls = []
    assert retention.prune_receipts(folder, 365, lambda *a: calls.append(a), now=NOW) == {"removed": 1}
    assert at.exists() and after.exists() and not before.exists()
    assert retention.prune_receipts(folder, 365, lambda *a: calls.append(a), now=NOW + 1) == {"removed": 1}
    assert not at.exists() and after.exists()
    assert [c[1]["receipts"] for c in calls] == [1, 1]


def test_a_receipt_from_the_future_stays_and_a_jump_forwards_keeps_the_period(tmp_path):
    folder = tmp_path / "receipts"
    ahead = _receipt(folder, "a", NOW + 30 * DAY)  # written while the clock was a month ahead
    recent = _receipt(folder, "b", NOW - 300 * DAY)
    calls = []
    assert retention.prune_receipts(folder, 365, lambda *a: calls.append(a), now=NOW) == {"removed": 0}
    # The clock jumps 65 days on: the 300-day-old receipt is 365 days old exactly, still kept.
    assert retention.prune_receipts(folder, 365, lambda *a: calls.append(a), now=NOW + 65 * DAY) == {"removed": 0}
    assert ahead.exists() and recent.exists() and calls == []


def test_a_receipt_cleanup_stopped_part_way_keeps_new_ones_and_the_next_finishes(tmp_path, monkeypatch):
    folder = tmp_path / "receipts"
    old = [_receipt(folder, letter, NOW - 400 * DAY) for letter in "abcd"]
    new = _receipt(folder, "e", NOW)
    real, calls = Path.unlink, {"n": 0}

    def killed_after_two(self, missing_ok=False):
        calls["n"] += 1
        if calls["n"] > 2:
            raise PermissionError("in use")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", killed_after_two)
    recorded = []
    with pytest.raises(PermissionError):
        retention.prune_receipts(folder, 365, lambda *a: recorded.append(a), now=NOW)
    assert sum(p.exists() for p in old) == 2 and new.exists()
    monkeypatch.setattr(Path, "unlink", real)
    assert retention.prune_receipts(folder, 365, lambda *a: recorded.append(a), now=NOW) == {"removed": 2}
    assert not any(p.exists() for p in old) and new.exists()
    assert [r[1]["receipts"] for r in recorded] == [4, 2]  # each cleanup recorded before it deleted


# ---- audit trails (audit_retention_days): whole months, judged by when the month ended


def _trail(folder, months):
    log = AuditLog(folder)
    for year, month in months:
        at = datetime(year, month, 15, 12, tzinfo=timezone.utc).isoformat(timespec="milliseconds")
        audit_chain.append(folder, {"schema": "audit@1", "at": at, "action": "run_sql", "outcome": "ok",
                                    "seconds": 0, "target": {}})
    return log


def _names(folder):
    return [p.name for p in audit_chain.files(folder)]


def test_a_month_that_ended_exactly_the_limit_ago_stays_and_goes_one_second_later(tmp_path):
    folder = tmp_path / "audit"
    log = _trail(folder, [(2025, 9), (2025, 10), (2026, 11)])
    edge = audit_chain._month_end("2025-10") + 365 * DAY  # October ended exactly 365 days before this
    assert [p.name for p in audit_chain.prunable(folder, 365, now=edge - 1)] == ["audit-2025-09.jsonl"]
    assert [p.name for p in audit_chain.prunable(folder, 365, now=edge)] == ["audit-2025-09.jsonl"]
    assert retention.prune_audit(folder, 365, lambda *a: None, "app", now=edge)["files"] == ["audit-2025-09.jsonl"]
    assert _names(folder) == ["audit-2025-10.jsonl", "audit-2026-11.jsonl"]
    assert retention.prune_audit(folder, 365, lambda *a: None, "app", now=edge + 1)["files"] == ["audit-2025-10.jsonl"]
    assert log.verify()["ok"] and log.verify()["pruned"] == {"through": "2025-10", "lines": 2}


def test_audit_months_from_the_future_are_never_pruned_early(tmp_path):
    # Written while the clock was months ahead, then the clock was put back.
    folder = tmp_path / "audit"
    _trail(folder, [(2027, 3), (2027, 4), (2027, 5)])
    calls = []
    assert retention.prune_audit(folder, 30, lambda *a: calls.append(a), "app", now=NOW) == {"files": [], "lines": 0}
    assert calls == [] and len(_names(folder)) == 3


def test_a_clock_jump_forwards_keeps_the_months_a_longer_limit_covers(tmp_path):
    folder = tmp_path / "audit"
    _trail(folder, [(2026, 1), (2026, 6), (2026, 11)])
    later = NOW + 40 * DAY  # the clock jumps forward 40 days, past the 30-day minimum
    assert retention.prune_audit(folder, 365, lambda *a: None, "app", now=later) == {"files": [], "lines": 0}
    assert len(_names(folder)) == 3


def test_a_prune_stopped_part_way_and_a_clock_put_back_still_finishes_and_verifies(tmp_path, monkeypatch):
    folder = tmp_path / "audit"
    log = _trail(folder, [(2020, 1), (2020, 2), (2020, 3), (2026, 11)])
    now = audit_chain._month_end("2020-03") + 30 * DAY + 1
    real, calls = Path.unlink, {"n": 0}

    def killed_after_one(self, missing_ok=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("engine ended")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", killed_after_one)
    with pytest.raises(KeyboardInterrupt):
        retention.prune_audit(folder, 30, lambda *a: None, "app", now=now)
    monkeypatch.setattr(Path, "unlink", real)
    assert _names(folder) == ["audit-2020-02.jsonl", "audit-2020-03.jsonl", "audit-2026-11.jsonl"]
    # The clock is put back before the next prune: by it, only January and February are old enough.
    back = audit_chain._month_end("2020-02") + 30 * DAY + 1
    done = retention.prune_audit(folder, 30, lambda *a: None, "app", now=back)
    assert done["files"] == ["audit-2020-02.jsonl", "audit-2020-03.jsonl"] and done["through"] == "2020-03"
    assert _names(folder) == ["audit-2026-11.jsonl"]
    check = log.verify()
    assert check["ok"], check["problems"]
    assert check["pruned"] == {"through": "2020-03", "lines": 3}  # every deleted line counted once
    assert retention.prune_audit(folder, 30, lambda *a: None, "app", now=now) == {"files": [], "lines": 0}
    assert log.verify()["pruned"] == {"through": "2020-03", "lines": 3}


def test_a_head_edited_to_claim_recent_months_were_pruned_never_has_them_deleted(tmp_path):
    folder = tmp_path / "audit"
    _trail(folder, [(2020, 1), (2026, 10), (2026, 11)])
    head_path = folder / audit_chain.HEAD
    head = json.loads(head_path.read_text(encoding="utf-8"))
    head["base"] = {**(head.get("base") or {}), "through": "9999-12"}  # edited by hand
    head_path.write_text(json.dumps(head), encoding="utf-8")
    now = audit_chain._month_end("2026-11") - DAY
    assert [p.name for p in audit_chain.prunable(folder, 30, now=now)] == ["audit-2020-01.jsonl"]


def test_a_prune_whose_head_couldnt_be_written_deletes_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "audit"
    log = _trail(folder, [(2020, 1), (2020, 2), (2026, 11)])

    def full(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(audit_chain.jsonfile, "write_json", full)
    with pytest.raises(OSError):
        audit_chain.prune(folder, 30, now=NOW)
    monkeypatch.undo()
    assert len(_names(folder)) == 3 and log.verify()["ok"]


# ---- the Teams notice ledger: closed and one-off keys go after SENT_KEEP_SECONDS, open ones never


def _ledger(tmp_path, t):
    return Ledger(tmp_path / "platform_outbox.sqlite", clock=lambda: t[0])


def _keys(ledger):
    with closing(sqlite3.connect(ledger.path)) as db:
        return {r[0] for r in db.execute("SELECT key FROM sent")}


def test_a_ledger_key_exactly_a_year_old_stays_and_goes_one_second_later(tmp_path):
    t = [NOW - YEAR]
    ledger = _ledger(tmp_path, t)
    ledger.mark("request.decided:r1", "request.decided", False)
    ledger.mark("request.waiting:r2", "request.waiting", True)
    t[0] = NOW
    assert ledger.prune(YEAR) == 0
    assert _keys(ledger) == {"request.decided:r1", "request.waiting:r2"}
    t[0] = NOW + 1
    assert ledger.prune(YEAR) == 1
    assert _keys(ledger) == {"request.waiting:r2"}  # still open: kept however old


class _Tidy(pns.PlatformNotifyMixin):
    def __init__(self, root):
        self.root = root


def _notice(path, key):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(pns._NOTICE)
        db.execute("INSERT OR REPLACE INTO notice VALUES (?, 'request.waiting', 'Access', 'ana', 'sales', 'prod', '', '{}')",
                   (key,))


def _notices(path):
    with closing(sqlite3.connect(path)) as db:
        return {r[0] for r in db.execute("SELECT key FROM notice")}


def test_a_notice_open_a_year_stays_and_once_closed_goes_a_year_later(tmp_path, monkeypatch):
    t = [NOW - YEAR - 60]
    ledger = _ledger(tmp_path, t)
    monkeypatch.setattr(pns, "Ledger", partial(Ledger, clock=lambda: t[0]))
    tidy = _Tidy(tmp_path)
    key = "request.waiting:r1"
    ledger.mark(key, "request.waiting", True)
    _notice(ledger.path, key)
    t[0] = NOW  # a year and a minute on, still waiting on someone
    tidy._pn_tidy()
    assert ledger.get(key) == ("request.waiting", True) and _notices(ledger.path) == {key}
    ledger.mark(key, "request.waiting", False)  # decided now: closed
    tidy._pn_tidy()
    assert _notices(ledger.path) == set()  # the bell forgets a closed notice at once
    assert ledger.get(key) == ("request.waiting", False)  # its key stays a year from closing, for dedupe
    t[0] = NOW + YEAR
    tidy._pn_tidy()
    assert ledger.get(key) is not None  # exactly a year after it closed
    t[0] = NOW + YEAR + 1
    tidy._pn_tidy()
    assert ledger.get(key) is None


def test_a_ledger_key_from_the_future_is_never_pruned_early(tmp_path):
    t = [NOW + 30 * DAY]  # marked while the clock was a month ahead
    ledger = _ledger(tmp_path, t)
    ledger.mark("request.decided:r1", "request.decided", False)
    t[0] = NOW
    assert ledger.prune(YEAR) == 0
    t[0] = NOW + 30 * DAY + YEAR
    assert ledger.prune(YEAR) == 0  # a year after the time it carries, exactly
    t[0] += 1
    assert ledger.prune(YEAR) == 1


@contextmanager
def _killed_mid_delete(monkeypatch, table, after):
    """sqlitefile.transaction whose connections die while deleting from ``table``, after ``after`` rows."""
    real = sqlitefile.transaction
    count = {"n": 0}

    def kill():
        count["n"] += 1
        if count["n"] > after:
            raise RuntimeError("killed")
        return 0

    @contextmanager
    def dying(path, *a, **kw):
        with real(path, *a, **kw) as db:
            db.create_function("kill", 0, kill)
            db.execute(f"CREATE TEMP TRIGGER IF NOT EXISTS die BEFORE DELETE ON {table} BEGIN SELECT kill(); END")
            yield db
    monkeypatch.setattr(sqlitefile, "transaction", dying)
    yield count
    monkeypatch.setattr(sqlitefile, "transaction", real)


def test_a_ledger_cleanup_killed_part_way_deletes_nothing_and_the_next_finishes(tmp_path, monkeypatch):
    t = [NOW - YEAR - 60]
    ledger = _ledger(tmp_path, t)
    for i in range(5):
        ledger.mark(f"request.decided:r{i}", "request.decided", False)
    ledger.mark("request.waiting:w", "request.waiting", True)
    t[0] = NOW
    ledger.mark("request.decided:new", "request.decided", False)
    with _killed_mid_delete(monkeypatch, "sent", after=2):
        with pytest.raises(sqlite3.OperationalError):
            ledger.prune(YEAR)
    assert len(_keys(ledger)) == 7  # the delete rolled back whole
    assert ledger.prune(YEAR) == 5
    assert _keys(ledger) == {"request.waiting:w", "request.decided:new"}


# ---- the notify outbox: a message older than KEEP_TRYING is dropped unsent


def _outbox(tmp_path, t):
    return Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0])


def test_a_waiting_message_exactly_at_keep_trying_stays_and_goes_one_second_later(tmp_path):
    t = [NOW - KEEP_TRYING]
    box = _outbox(tmp_path, t)
    box.add("at the limit", ["x"], "no network")
    t[0] = NOW - KEEP_TRYING + 1
    box.add("a second younger", ["x"], "no network")
    t[0] = NOW
    assert box.trim() == 0 and box.count() == 2
    t[0] = NOW + 1
    assert box.trim() == 1
    assert [m["title"] for m in box.pending()] == ["a second younger"]


def test_a_waiting_message_from_the_future_is_never_dropped_early(tmp_path):
    t = [NOW + 2 * DAY]  # added while the clock was two days ahead
    box = _outbox(tmp_path, t)
    box.add("ahead", ["x"], "no network")
    t[0] = NOW
    assert box.trim() == 0
    t[0] = NOW + 2 * DAY + KEEP_TRYING
    assert box.trim() == 0
    t[0] += 1
    assert box.trim() == 1


def test_a_clock_jump_forwards_drops_only_what_is_past_keep_trying(tmp_path):
    t = [NOW]
    box = _outbox(tmp_path, t)
    box.add("old", ["x"], "no network")
    t[0] = NOW + DAY
    box.add("newer", ["x"], "no network")
    t[0] = NOW + DAY + KEEP_TRYING  # jumps on: "newer" is exactly at the limit
    assert box.trim() == 1 and [m["title"] for m in box.pending()] == ["newer"]


def test_a_trim_killed_part_way_drops_nothing_and_the_next_finishes(tmp_path, monkeypatch):
    t = [NOW - KEEP_TRYING - 60]
    box = _outbox(tmp_path, t)
    for i in range(4):
        box.add(f"old {i}", ["x"], "no network")
    t[0] = NOW
    box.add("new", ["x"], "no network")
    with _killed_mid_delete(monkeypatch, "outbox", after=2):
        with pytest.raises(sqlite3.OperationalError):
            box.trim()
    assert box.count() == 5  # the delete rolled back whole: nothing half dropped
    assert box.trim() == 4 and [m["title"] for m in box.pending()] == ["new"]


# ---- a policy's longer retention can't be shortened by the person using the PC


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user" / "settings.json")
    registry: dict = {}
    monkeypatch.setattr(registry_policy, "read", lambda reader=None: (dict(registry), []))
    path = tmp_path / "pd" / "FabricDesktop" / "policy.json"
    path.parent.mkdir(parents=True)
    app_settings.user_path().parent.mkdir(parents=True)
    return path, registry


KEYS = ("audit_retention_days", "receipt_retention_days")


def test_a_persons_own_settings_cant_shorten_the_policys_retention(policy):
    path, _ = policy
    path.write_text(json.dumps({"audit_retention_days": 365, "receipt_retention_days": 365, "local_retention_days": 90}))
    app_settings.user_path().write_text(json.dumps({"audit_retention_days": 30, "receipt_retention_days": 30,
                                                    "local_retention_days": 1}))
    assert [app_settings.value(k) for k in (*KEYS, "local_retention_days")] == [365, 365, 90]
    for key, short in (("audit_retention_days", 30), ("receipt_retention_days", 30), ("local_retention_days", 1)):
        with pytest.raises(ValueError):
            app_settings.save({key: short})
    assert [app_settings.value(k) for k in KEYS] == [365, 365]


def test_a_policy_file_the_person_could_change_cant_shorten_group_policys_retention(policy, monkeypatch):
    path, registry = policy
    registry.update({"audit_retention_days": 365, "receipt_retention_days": 365})
    path.write_text(json.dumps({"audit_retention_days": 30, "receipt_retention_days": 30}))
    monkeypatch.setattr(app_settings, "policy_file_untrusted", lambda: "the person using this PC can change it")
    assert [app_settings.value(k) for k in KEYS] == [365, 365]


def test_a_group_policy_key_the_person_could_change_cant_shorten_the_policy_files(policy, monkeypatch):
    path, registry = policy
    path.write_text(json.dumps({"audit_retention_days": 365, "receipt_retention_days": 365}))
    registry.update({"audit_retention_days": 30, "receipt_retention_days": 30})
    monkeypatch.setattr(app_settings, "registry_key_untrusted", lambda lists: "the person using this PC can change it")
    assert [app_settings.value(k) for k in KEYS] == [365, 365]


def test_a_shorter_limit_in_a_file_the_person_could_change_keeps_everything(policy, monkeypatch):
    path, _ = policy
    path.write_text(json.dumps({"audit_retention_days": 30, "receipt_retention_days": 30}))
    monkeypatch.setattr(app_settings, "policy_file_untrusted", lambda: "the person using this PC can change it")
    assert [app_settings.value(k) for k in KEYS] == [0, 0]  # ignored: the records are kept


def test_the_last_good_copy_in_the_persons_profile_cant_shorten_retention(policy):
    path, _ = policy
    path.write_text(json.dumps({"audit_retention_days": 365}))
    assert app_settings.value("audit_retention_days") == 365  # read once: its copy is kept
    app_settings.saved_policy_path().write_text(json.dumps({"audit_retention_days": 30}))  # edited by the person
    path.write_text("{ not json")
    assert app_settings.value("audit_retention_days") == 0  # the copy stands in, but not for this key
