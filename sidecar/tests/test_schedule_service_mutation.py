"""schedule_service paths that mutation testing (batch 12, docs/quality/mutation-testing.md) showed no test
checked: what each call returns, the settings a pipeline schedule passes on, how a lock's process is told
apart, and the Fabric side of the list. Nothing reaches Fabric, Teams or Task Scheduler."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import kernel_proc, schedule_service
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service
from test_kernel_process_edges import FakeDLL
from test_schedules import FakeScheduler, _dead_pid, svc  # noqa: F401 - svc is a fixture

DAILY = {"kind": "daily", "times": ["06:00"]}


def _notebook_schedule(svc, name="Daily sales"):
    svc.notebooks.save(name, [{"type": "code", "source": "x = 1"}])
    return svc.add_pc_schedule({"kind": "notebook", "name": name}, DAILY)["schedule"]["id"]


# ---- what the calls return

def test_deleting_a_schedule_says_which(svc):
    sid = _notebook_schedule(svc)
    assert svc.delete_pc_schedule(sid) == {"deleted": sid}


def test_changing_a_schedule_passes_signed_out_only_when_given(svc, monkeypatch):
    seen = []
    monkeypatch.setattr(LocalSchedules, "update", lambda self, sid, schedule, signed_out=None: seen.append(signed_out) or {})
    svc.update_pc_schedule("s1", DAILY)
    svc.update_pc_schedule("s1", DAILY, signed_out=1)
    svc.update_pc_schedule("s1", DAILY, signed_out=0)
    assert seen == [None, True, False]


def test_the_run_history_shows_200_runs_unless_asked(svc):
    sid = _notebook_schedule(svc)
    for n in range(201):
        svc.pc_schedules.log_run({"schedule_id": sid, "label": "Daily sales", "trigger": "schedule",
                                  "started": f"2026-10-20T06:{n // 60:02d}:{n % 60:02d}+00:00", "status": "ok"})
    assert len(svc.pc_schedule_runs()) == 200
    assert len(svc.notebook_runs("Daily sales")) == 50  # a notebook's panel: the newest 50


def test_a_notebook_with_one_cell_says_one_cell(svc):
    sid = _notebook_schedule(svc, "One")
    row = svc.run_schedule(sid)
    assert row["status"] == "ok" and row["message"] == "1 cell ran"


# ---- a pipeline schedule's settings

def _pipeline(monkeypatch, secure=()):
    params = {n: {"type": "SecureString"} for n in secure} | {"region": {"type": "String"}}
    pipeline = SimpleNamespace(load=lambda path: SimpleNamespace(pipeline=SimpleNamespace(parameters=params)))
    monkeypatch.setattr(schedule_service, "pipeline_definition", pipeline)


def test_a_pipeline_schedule_checks_with_empty_settings_left_out(svc, monkeypatch, tmp_path):
    _pipeline(monkeypatch)
    seen = {}

    def check(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "pipeline": "Load sales", "problems": []}
    svc.pipeline_run_check = check
    target = svc._pipeline_schedule_target({"kind": "pipeline", "path": str(tmp_path), "parameters": {"region": "eu"}})
    assert target["name"] == "Load sales"
    assert seen["notebooks"] is None and seen["workspace_id"] is None and seen["sql_projects"] is None
    assert seen["parameters"] == {"region": "eu"}


def test_a_pipeline_that_cant_run_here_or_cant_be_read_is_refused(svc, monkeypatch, tmp_path):
    _pipeline(monkeypatch)
    svc.pipeline_run_check = lambda **kw: {"ok": False, "problems": [{"message": "No mock for Copy1."}]}
    with pytest.raises(ValueError, match="can't run on this PC with these settings: No mock for Copy1."):
        svc._pipeline_schedule_target({"kind": "pipeline", "path": str(tmp_path)})

    def unreadable(path):
        raise schedule_service.DefinitionError("no pipeline-content.json", "missing")
    monkeypatch.setattr(schedule_service, "pipeline_definition", SimpleNamespace(load=unreadable))
    with pytest.raises(ValueError, match="Could not read the pipeline: no pipeline-content.json"):
        svc._pipeline_schedule_target({"kind": "pipeline", "path": str(tmp_path)})


def test_one_secure_string_is_named_as_one(svc, monkeypatch, tmp_path):
    _pipeline(monkeypatch, secure=("key",))
    with pytest.raises(ValueError, match="key is a SecureString;"):
        svc._pipeline_schedule_target({"kind": "pipeline", "path": str(tmp_path), "parameters": {"key": "x"}})


def _started(svc, monkeypatch, record):
    seen = {}

    def start(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "run_id": "r1"}
    svc.pipeline_run_start = start
    states = iter([{"state": "running"}, {"state": record["state"]}])
    svc.pipeline_run_status = lambda rid, values=True: record if values else next(states)
    monkeypatch.setattr(schedule_service, "_PIPELINE_POLL", 0)
    return seen


def test_a_scheduled_pipeline_run_is_followed_to_its_end(svc, monkeypatch):
    acts = [{"name": "Copy1", "state": "succeeded", "executed_by": "mock"}]
    seen = _started(svc, monkeypatch, {"state": "succeeded", "pipeline": "Load", "activities": acts})
    out = svc._run_pipeline_here({"path": "p", "notebooks": {}, "workspace_id": "", "sql_projects": {}})
    assert out == {"status": "ok", "message": "Load succeeded: 1 activity, 1 mocked", "run_id": "r1"}
    assert seen["notebooks"] is None and seen["workspace_id"] is None and seen["sql_projects"] is None


def test_a_failed_pipeline_run_names_the_failed_activity(svc, monkeypatch):
    acts = [{"name": "Copy1", "state": "succeeded"},
            {"name": "Copy2", "state": "failed", "error": {"message": "table not found"}}]
    _started(svc, monkeypatch, {"state": "failed", "pipeline": "Load", "activities": acts})
    out = svc._run_pipeline_here({"path": "p"})
    assert out == {"status": "failed", "message": "Load failed: 2 activities. Copy2: table not found", "run_id": "r1"}


def test_a_pipeline_run_with_no_activities_and_one_refused(svc, monkeypatch):
    _started(svc, monkeypatch, {"state": "cancelled", "pipeline": "Load", "activities": None})
    assert svc._run_pipeline_here({"path": "p"})["message"] == "Load cancelled: 0 activities."
    svc.pipeline_run_start = lambda **kw: {"ok": False, "problems": [{"message": "A"}, {"message": "B"}]}
    assert svc._run_pipeline_here({"path": "p"}) == {
        "status": "failed", "message": "The pipeline can't run on this PC now: A B"}


# ---- runs while signed out of Windows

def test_only_signed_out_schedules_are_skipped_when_it_turned_them_off(svc, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(
        lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe", allow_signed_out=False)))
    sid = _notebook_schedule(svc, "Signed in")
    row = svc.run_schedule(sid)
    assert row["status"] == "ok"  # a schedule that runs only while signed in still runs
    monkeypatch.setattr(Service, "pc_schedules", property(
        lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe", allow_signed_out=True)))
    svc.notebooks.save("Away", [{"type": "code", "source": "x = 1"}])
    away = svc.add_pc_schedule({"kind": "notebook", "name": "Away"}, DAILY, signed_out=True)["schedule"]["id"]
    monkeypatch.setattr(Service, "pc_schedules", property(
        lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe", allow_signed_out=False)))
    skipped = svc.run_schedule(away)
    assert skipped["status"] == "skipped" and skipped["seconds"] == 0


# ---- a notebook's runs panel

def test_another_kinds_schedule_with_the_notebooks_name_is_not_its(svc, monkeypatch):
    _notebook_schedule(svc)
    other = svc.pc_schedules.add({"kind": "meta", "env": "dev"}, DAILY)["id"]
    svc.pc_schedules.log_run({"schedule_id": other, "label": "dev", "trigger": "schedule",
                              "started": "2026-10-20T06:00:00+00:00", "status": "ok"})
    listing = svc.pc_schedules.listing()
    for s in listing:  # the metadata schedule's target named like the notebook
        if s["id"] == other:
            s["target"]["name"] = "Daily sales"
    monkeypatch.setattr(LocalSchedules, "listing", lambda self: listing)
    assert svc.notebook_runs("Daily sales") == []


def test_fabric_run_seconds_keep_one_decimal_and_both_reads_ask_for_500(svc):
    svc._log_fabric_run({"kind": "Notebook run", "name": "Daily sales", "workspace_id": "ws1", "item_id": "it1",
                         "job_id": "done", "status": "Completed", "started_at": "2026-10-20T07:00:00+00:00",
                         "finished_at": "2026-10-20T07:00:02.500000Z"})
    svc._log_fabric_run({"kind": "Notebook run", "name": "Daily sales", "workspace_id": "ws1", "item_id": "it1",
                         "job_id": "going", "status": "InProgress", "started_at": "2026-10-20T08:00:00+00:00"})
    svc.notebooks.save("Daily sales", [{"type": "code", "source": "x = 1"}])
    calls = []
    real = svc.fabric_runs

    def recorded(limit=50, refresh=True, job_ids=None):
        calls.append((limit, refresh, job_ids))
        return real(limit, refresh=False)
    svc.fabric_runs = recorded
    runs = svc.notebook_runs("Daily sales", refresh=True)
    assert [r["seconds"] for r in runs] == [None, 2.5]
    assert calls == [(500, False, None), (500, True, ["going"])]


# ---- locks

def test_a_lock_older_than_12_hours_is_taken_over_even_from_a_live_process(svc):
    sid = _notebook_schedule(svc)
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.lock"
    path.write_text(json.dumps({"pid": os.getpid(), "process": schedule_service.process_identity(os.getpid())}))
    old = time.time() - schedule_service.STALE_LOCK_SECONDS - 1800  # 12 and a half hours
    os.utime(path, (old, old))
    assert schedule_service.STALE_LOCK_SECONDS == 12 * 3600
    assert svc._take_over(sid, path)[0] is True


def test_the_alerts_lock_left_by_a_dead_run_is_taken(svc):
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{schedule_service.ALERTS_LOCK}.lock").write_text(json.dumps({"pid": _dead_pid()}))
    lock = svc._lock(schedule_service.ALERTS_LOCK)
    assert lock is not None and lock.exists()


def test_a_stopped_runs_pid_is_kept_only_when_it_is_a_whole_number(svc):
    sid = _notebook_schedule(svc)
    for held, pid in (({"pid": 4242}, 4242), ({"pid": True}, None), ({"pid": "4242"}, None)):
        assert svc._log_stopped_run(sid, held) is True
        assert svc.pc_schedules.runs(1)[0]["pid"] == pid


@pytest.mark.skipif(sys.platform == "win32", reason="os.kill signals")
def test_checking_a_process_doesnt_signal_it():
    # SIGHUP back to its default, as a run started under nohup would ignore it
    script = ("import signal, time; "
              "[signal.signal(n, signal.SIG_DFL) for n in (signal.SIGHUP, signal.SIGINT)]; "
              "print('ready', flush=True); time.sleep(30)")
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == b"ready"
        assert schedule_service._pid_alive(child.pid) is True
        time.sleep(0.2)
        assert child.poll() is None  # still running: the check sent no signal
    finally:
        child.kill()
        child.wait()
        child.stdout.close()


def test_an_ended_process_is_false_not_just_falsy():
    assert schedule_service._pid_alive(_dead_pid()) is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_pid_1_with_another_start_time_has_ended():
    assert schedule_service._pid_alive(1, "not-its-start-time") is False


def test_windows_process_times_ask_for_four_times(monkeypatch):
    monkeypatch.setattr(schedule_service, "sys", SimpleNamespace(platform="win32"))
    dll = FakeDLL(OpenProcess=7, GetProcessTimes=1)
    monkeypatch.setattr(kernel_proc, "win_kernel32", lambda: dll)
    schedule_service.process_identity(4242)
    [call] = [args for name, args in dll.calls if name == "GetProcessTimes"]
    assert len(call) == 5  # the handle, then creation, exit, kernel and user time


def test_windows_pid_check_reads_its_last_error(monkeypatch):
    monkeypatch.setattr(schedule_service, "sys", SimpleNamespace(platform="win32"))
    asked = []
    dll = FakeDLL(OpenProcess=0)

    def windll(name, use_last_error=False):
        asked.append(use_last_error)
        return dll
    monkeypatch.setattr(ctypes, "WinDLL", windll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)
    assert schedule_service._pid_alive(4242) is False
    assert asked == [True]


# ---- Teams alerts

def test_no_webhook_a_run_that_worked_and_a_sent_alert_all_return_empty_text(svc, monkeypatch):
    from fabric_desktop import dataops_service
    row = {"status": "ok", "label": "Daily sales", "schedule_id": "s1"}
    assert svc._send_schedule_alerts(row) == ""
    monkeypatch.setattr(Service, "_settings", lambda self: {"teams_webhook": "https://example.webhook.office.com/x"})
    assert svc._send_schedule_alerts(row) == ""
    posted = []
    monkeypatch.setattr(dataops_service, "post_teams", lambda url, title, lines: posted.append((title, lines)))
    failed = row | {"status": "failed", "message": "Cell 1 of 1 failed"}
    assert svc._send_schedule_alerts(failed) == ""  # it went out: nothing to record with the run
    assert posted == [("Scheduled run failed on this PC: Daily sales", ["Cell 1 of 1 failed"])]


# ---- Fabric's side

class _Jobs:
    EXECUTE_PATHS = {"CopyJob": "copyJobs"}

    def __init__(self):
        self.calls = []

    def update_schedule(self, *args):
        self.calls.append(("update", args))
        return {"id": args[3]}

    def run_item_execute(self, ws, item, item_type):
        return "exec-job"

    def run_item_job(self, ws, item, job_type):
        return "spark-job"


def test_fabric_schedule_changes_return_fabrics_answer(svc, monkeypatch):
    jobs = _Jobs()
    monkeypatch.setattr(Service, "fabric_jobs", property(lambda self: jobs))
    assert svc.set_fabric_schedule_enabled("ws", "it", "RunNotebook", "s1", False, {}) == {"id": "s1"}
    assert svc.update_fabric_schedule("ws", "it", "RunNotebook", "s1", DAILY, True) == {"id": "s1"}


def test_run_now_uses_data_factory_only_for_its_item_types(svc, monkeypatch):
    monkeypatch.setattr(Service, "fabric_jobs", property(lambda self: _Jobs()))
    copy = svc.run_fabric_item_now("ws", "it1", "Execute", "Copy sales", item_type="CopyJob")
    spark = svc.run_fabric_item_now("ws", "it2", "RunNotebook", "Load", item_type="Notebook")
    assert (copy["job_id"], copy["engine"]) == ("exec-job", "data_factory")
    assert (spark["job_id"], spark["engine"], spark["kind"]) == ("spark-job", "spark", "Scheduled item run")


class _Client:
    def __init__(self, token_fn):
        pass

    def list_workspaces(self):
        return [{"id": "ws1", "displayName": "Sales"}, {"id": "ws2", "displayName": "Ops"},
                {"id": "ws3", "displayName": "Other"}]

    def list_items(self, ws):
        return [{"id": "it1", "type": "Notebook", "displayName": "Load"}]

    def list_schedules(self, ws, item, job):
        return [{"id": "s1", "configuration": {"type": "Daily", "times": ["06:00"]}}]  # no "enabled"

    def list_job_instances(self, ws, item):
        return []


def test_a_notebooks_workspace_is_listed_and_one_without_is_not(svc, monkeypatch):
    monkeypatch.setattr(schedule_service, "FabricClient", _Client)
    svc.notebooks.save("Here", [{"type": "code", "source": "x = 1"}],
                       {"workspace_id": "ws2", "lakehouse_id": "lh", "name": "lh"})
    svc.notebooks.save("Nowhere", [{"type": "code", "source": "x = 1"}])
    names = {w["name"] for w in svc._schedule_workspaces(False, "token")}
    assert names == {"Ops"}


def test_a_fabric_schedule_without_enabled_is_off(svc, monkeypatch):
    monkeypatch.setattr(schedule_service, "FabricClient", _Client)
    svc._scan_fabric_schedules("token", True)
    items = svc._fabric_schedules["items"]
    assert items and all(r["enabled"] is False for r in items)
