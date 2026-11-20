"""schedule_service behaviour that the uncapped mutation run (batch 174, docs/quality/mutation-testing.md)
showed no test checked. Nothing reaches Fabric, Teams or Task Scheduler."""

from __future__ import annotations

import os

from fabric_desktop import schedule_service
from test_schedules import FakeScheduler, svc  # noqa: F401 - svc is a fixture

DAILY = {"kind": "daily", "times": ["06:00"]}


def test_a_lock_identity_that_is_empty_or_not_text_is_not_checked():
    me = os.getpid()
    assert schedule_service._pid_alive(me, "") is True
    assert schedule_service._pid_alive(me, 123) is True


def test_this_pcs_schedules_follow_its_folder_and_it_s_setting(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fabric_desktop.local_schedules import LocalSchedules
    allowed = {"allow_signed_out_schedules": False}
    monkeypatch.setattr(schedule_service.app_settings, "value", lambda key: allowed[key])
    owner = SimpleNamespace(root=tmp_path)
    schedules = schedule_service.ScheduleMixin.pc_schedules.fget(owner)
    assert isinstance(schedules, LocalSchedules) and schedules.root == tmp_path
    assert schedules.allow_signed_out is False
    allowed["allow_signed_out_schedules"] = 1
    assert schedule_service.ScheduleMixin.pc_schedules.fget(owner).allow_signed_out is True


def test_a_linux_process_identity_is_its_start_time_field(monkeypatch):
    from types import SimpleNamespace
    # pid, (comm with a space and a bracket), then fields 3 to 52, each named by its number
    stat = "4242 (odd ) name) " + " ".join(f"f{n}" for n in range(3, 53))
    monkeypatch.setattr(schedule_service, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(schedule_service, "Path", lambda p: SimpleNamespace(read_text=lambda: stat))
    assert schedule_service.process_identity(4242) == "f22"


def test_windows_pid_check_opens_the_process_for_limited_query_only(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    from test_kernel_process_edges import FakeDLL
    monkeypatch.setattr(schedule_service, "sys", SimpleNamespace(platform="win32"))
    dll = FakeDLL(OpenProcess=0)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)  # access denied: it exists
    assert schedule_service._pid_alive(4242) is True
    [args] = [a for name, a in dll.calls if name == "OpenProcess"]
    assert args == (0x1000, False, 4242)


def test_run_now_starts_the_cli_with_no_window_flag_only_where_there_is_one(svc, monkeypatch):
    import subprocess
    sid = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    seen = {}
    monkeypatch.setattr(schedule_service.subprocess, "Popen", lambda args, **kw: seen.update(args=args, **kw))
    assert svc.run_pc_schedule_now(sid) == {"started": sid}
    assert seen["args"][-3:] == ["run-schedule", sid, "--manual"]
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_a_notebooks_runs_read_the_last_500_runs_of_this_pc(svc, monkeypatch):
    from fabric_desktop.local_schedules import LocalSchedules
    asked = []
    monkeypatch.setattr(LocalSchedules, "runs", lambda self, limit=200: asked.append(limit) or [])
    svc.notebook_runs("Daily sales")
    assert asked.count(500) == 1  # the notebook's own runs come from the last 500


def test_starting_the_app_counts_the_runs_that_stopped(svc):
    from test_schedules import _dead_pid
    sid = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.lock").write_text('{"pid": %d, "trigger": "schedule"}' % _dead_pid(), encoding="utf-8")
    assert svc.recover_pc_schedule_runs() == {"interrupted": 1}
    assert svc.recover_pc_schedule_runs() == {"interrupted": 0}


def test_a_lock_makes_its_folder_and_the_folders_above_it(tmp_path):
    from types import SimpleNamespace
    owner = SimpleNamespace(root=tmp_path / "not" / "yet")
    path = schedule_service.ScheduleMixin._lock(owner, "s1")
    assert path == tmp_path / "not" / "yet" / "schedule_locks" / "s1.lock" and path.exists()


def test_a_takeover_guard_just_over_a_minute_old_is_cleared(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from test_schedules import _dead_pid
    monkeypatch.setattr(schedule_service, "time", _Clock(NOW))
    lock = tmp_path / "_alerts.lock"
    lock.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")
    os.utime(lock, ns=(int(NOW) * 10**9,) * 2)
    guard = tmp_path / "_alerts.lock.takeover"
    guard.write_text("", encoding="utf-8")
    os.utime(guard, ns=(int(NOW - 61) * 10**9,) * 2)
    owner = SimpleNamespace(root=tmp_path)
    assert schedule_service.ScheduleMixin._take_over(owner, schedule_service.ALERTS_LOCK, lock) == (True, False)
    assert not lock.exists() and not guard.exists()


def test_a_run_skipped_behind_a_running_one_took_no_time(svc):
    import json
    sid = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    row = svc.run_schedule(sid)
    assert row["status"] == "skipped" and row["seconds"] == 0 and row["message"].startswith("The previous run")


def test_a_runs_length_is_its_monotonic_seconds_to_a_tenth(svc, monkeypatch):
    import time
    from types import SimpleNamespace
    sid = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    ticks = iter([100.0, 102.36])
    monkeypatch.setattr(schedule_service, "time", SimpleNamespace(monotonic=lambda: next(ticks), time=time.time,
                                                                  sleep=time.sleep))
    monkeypatch.setattr(svc, "_run_meta_here", lambda env, names: {"status": "ok", "message": "done"}, raising=False)
    row = svc.run_schedule(sid)
    assert row["status"] == "ok" and row["seconds"] == 2.4


def test_a_scheduled_pipeline_is_followed_without_values_then_read_whole(svc, monkeypatch):
    calls = []
    states = iter(["running", "succeeded"])
    svc.pipeline_run_start = lambda **kw: {"ok": True, "run_id": "r1"}

    def status(rid, values=True):
        calls.append(values)
        state = next(states, "succeeded")
        return {"state": state, "pipeline": "Load", "activities": [{"name": "a", "state": "succeeded"}]}
    svc.pipeline_run_status = status
    monkeypatch.setattr(schedule_service, "_PIPELINE_POLL", 0)
    out = svc._run_pipeline_here({"path": "p"})
    assert out == {"status": "ok", "message": "Load succeeded: 1 activity", "run_id": "r1"}
    assert calls == [False, False, True]


def test_every_schedule_lists_the_last_200_runs(svc, monkeypatch):
    from fabric_desktop.local_schedules import LocalSchedules
    asked = []
    monkeypatch.setattr(LocalSchedules, "runs", lambda self, *a, **k: asked.append(a) or [])

    def no_token():
        raise RuntimeError("signed out")
    monkeypatch.setattr(svc.auth, "fabric_token", no_token)
    out = svc.all_schedules()
    assert (200,) in asked and (201,) not in asked
    assert out["fabric"]["error"] == "RuntimeError: signed out"


# ---- a stopped run's lock, at its limits

def _stopped_row(svc, monkeypatch, held):
    import json
    sid = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    monkeypatch.setattr(schedule_service, "_pid_alive", lambda pid, identity=None: False)
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.lock").write_text(json.dumps(held), encoding="utf-8")
    assert svc.recover_pc_schedule_runs() == {"interrupted": 1}
    [row] = [r for r in svc.pc_schedule_runs() if r.get("schedule_id") == sid]
    return row


def test_a_stopped_runs_start_is_kept_up_to_40_characters(svc, monkeypatch):
    assert _stopped_row(svc, monkeypatch, {"pid": 7, "started": "s" * 40})["started"] == "s" * 40
    assert _stopped_row(svc, monkeypatch, {"pid": 7, "started": "s" * 41})["started"] is None


class _Clock:
    """schedule_service's time with a fixed now."""

    def __init__(self, now):
        import time
        self.now, self.sleep, self.monotonic = now, time.sleep, time.monotonic

    def time(self):
        return self.now


NOW = 1_700_000_000.0


def test_a_takeover_guard_exactly_a_minute_old_is_still_respected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(schedule_service, "time", _Clock(NOW))
    lock = tmp_path / "_alerts.lock"
    lock.write_text("1", encoding="utf-8")
    guard = tmp_path / "_alerts.lock.takeover"
    guard.write_text("", encoding="utf-8")
    os.utime(guard, ns=(int(NOW - 60) * 10**9,) * 2)
    owner = SimpleNamespace(root=tmp_path)
    assert schedule_service.ScheduleMixin._take_over(owner, schedule_service.ALERTS_LOCK, lock) == (False, False)
    assert lock.exists() and guard.exists()


def test_a_lock_exactly_12_hours_old_of_a_running_process_is_kept(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(schedule_service, "time", _Clock(NOW))
    lock = tmp_path / "_alerts.lock"
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    os.utime(lock, ns=(int(NOW - schedule_service.STALE_LOCK_SECONDS) * 10**9,) * 2)
    owner = SimpleNamespace(root=tmp_path)
    assert schedule_service.ScheduleMixin._take_over(owner, schedule_service.ALERTS_LOCK, lock) == (False, False)
    assert lock.exists()
    os.utime(lock, ns=(int(NOW - schedule_service.STALE_LOCK_SECONDS - 1) * 10**9,) * 2)
    assert schedule_service.ScheduleMixin._take_over(owner, schedule_service.ALERTS_LOCK, lock) == (True, False)


def test_pid_0_counts_as_running_on_windows_too(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    from test_kernel_process_edges import FakeDLL
    monkeypatch.setattr(schedule_service, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: FakeDLL(OpenProcess=0), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)
    assert schedule_service._pid_alive(0) is True
    assert schedule_service._pid_alive(-3) is True


def test_a_run_whose_lock_was_taken_over_meanwhile_is_still_logged(svc, monkeypatch):
    sid = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]

    def run(env, names):
        (svc.root / "schedule_locks" / f"{sid}.lock").unlink()
        return {"status": "ok", "message": "done"}
    monkeypatch.setattr(svc, "_run_meta_here", run, raising=False)
    assert svc.run_schedule(sid)["status"] == "ok"
    assert [r["status"] for r in svc.pc_schedule_runs() if r.get("schedule_id") == sid] == ["ok"]


# ---- Teams alerts that don't go out

def _failed_alert(svc, monkeypatch, exc):
    from fabric_desktop import dataops_service
    from fabric_desktop.service import Service
    monkeypatch.setattr(Service, "_settings", lambda self: {"teams_webhook": "https://example.webhook.office.com/x"})

    def post(url, title, lines):
        raise exc
    monkeypatch.setattr(dataops_service, "post_teams", post)
    troubles = []
    monkeypatch.setattr(schedule_service, "audit_trouble", lambda audit, result, outbox: troubles.append(result))
    problem = svc._send_schedule_alerts({"status": "failed", "label": "Nightly", "message": "boom", "schedule_id": "s1"})
    [details] = troubles
    return problem, details


def test_an_alert_teams_refused_is_audited_as_one_queued(svc, monkeypatch):
    problem, details = _failed_alert(svc, monkeypatch, RuntimeError("HTTP 503"))
    assert problem.startswith("Couldn't post the Teams alert") and details["queued"] == 1


def test_an_alert_sent_with_no_answer_is_audited_as_one_unconfirmed(svc, monkeypatch):
    import requests
    problem, details = _failed_alert(svc, monkeypatch, requests.exceptions.ReadTimeout("no answer"))
    assert problem.startswith("Couldn't confirm the Teams alert") and details["unconfirmed"] == 1


# ---- Fabric's schedules

def test_a_fabric_scan_keeps_each_schedules_configuration_and_names_the_first_failed_read(svc, monkeypatch, caplog):
    from test_schedule_locks_and_lists import Flaky
    monkeypatch.setattr(schedule_service, "FabricClient", Flaky)
    svc._scan_fabric_schedules("tok", all_workspaces=True)
    [row] = svc._fabric_schedules["items"]
    assert row["configuration"] == {"type": "Daily", "times": ["05:00"], "localTimeZoneId": "UTC"}
    assert "3 reads failed; the first was items in workspace ws2" in caplog.text


def test_a_workspace_list_that_fails_is_no_longer_pending(svc, monkeypatch):
    from test_schedule_locks_and_lists import Flaky

    class NoList(Flaky):
        def list_workspaces(self):
            raise RuntimeError("401")
    monkeypatch.setattr(schedule_service, "FabricClient", NoList)
    svc._scan_fabric_schedules("tok", all_workspaces=True)
    assert svc._fabric_schedules["pending"] is False and svc._fabric_schedules["all_workspaces"] is True


def test_waiting_alerts_still_go_when_their_lock_was_taken_over_meanwhile(svc, monkeypatch):
    from fabric_desktop import dataops_service
    from fabric_desktop.service import Service
    monkeypatch.setattr(Service, "_settings", lambda self: {"teams_webhook": "https://example.webhook.office.com/x"})
    row = {"status": "failed", "label": "Nightly", "message": "boom", "schedule_id": "s1"}

    def refused(url, title, lines):
        raise RuntimeError("HTTP 503")
    monkeypatch.setattr(dataops_service, "post_teams", refused)
    assert svc._send_schedule_alerts(row).startswith("Couldn't post the Teams alert")  # it waits
    posted = []

    def taken_over(url, title, lines):
        (svc.root / "schedule_locks" / "_alerts.lock").unlink(missing_ok=True)
        posted.append(title)
    monkeypatch.setattr(dataops_service, "post_teams", taken_over)
    assert svc._send_schedule_alerts(row) == ""
    assert len(posted) == 2  # the waiting one, then this run's


# ---- Prod schedules and the audit log

class _Recorder:
    def __init__(self, broken=False):
        self.lines, self.broken = [], broken

    def record(self, action, outcome, seconds, params, **kw):
        if self.broken:
            raise OSError("disk full")
        self.lines.append((action, outcome, kw))


def test_a_prod_run_the_audit_refused_took_no_time(svc):
    sid = svc.pc_schedules.add({"kind": "meta", "env": "prod"}, DAILY)["id"]
    svc._audit_log = _Recorder(broken=True)
    row = svc.run_schedule(sid)
    assert row["status"] == "failed" and row["seconds"] == 0
    assert not (svc.root / "schedule_locks" / f"{sid}.lock").exists()


def test_a_failed_prod_runs_audit_error_is_cut_at_300_characters(svc, monkeypatch):
    long = "x" * 400
    monkeypatch.setattr(svc, "_run_meta_here", lambda env, names: {"status": "failed", "message": long}, raising=False)
    sid = svc.pc_schedules.add({"kind": "meta", "env": "prod"}, DAILY)["id"]
    rec = _Recorder()
    svc._audit_log = rec
    svc.run_schedule(sid)
    [(action, outcome, kw)] = [line for line in rec.lines if line[1] != "started"]
    assert (action, outcome) == ("run_schedule", "error") and kw["error"] == "x" * 300
