"""This PC's schedules (schedule_service.py): the edges test_schedules.py leaves out. Who holds a run's
lock and when it may be taken over, a PID reused by another process, the list of Fabric schedules when a
read fails, Run now, and Task Scheduler refusing a sync. Fabric is a stand-in; nothing reaches a tenant."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from fabric_desktop import schedule_service as ss
from fabric_desktop.local_schedules import LocalSchedules
from fabric_desktop.service import Service

from test_schedules import FakeScheduler


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    s = Service(root=tmp_path / "app")
    s.notebooks.save("Daily", [{"type": "code", "source": "x = 1"}])
    s.sid = s.add_pc_schedule({"kind": "notebook", "name": "Daily"}, {"kind": "daily", "times": ["06:00"]})["schedule"]["id"]
    return s


def lock_file(svc, schedule_id, body):
    folder = svc.root / "schedule_locks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{schedule_id}.lock"
    path.write_text(json.dumps(body) if isinstance(body, dict) else body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- is the lock's run still going

@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="Linux's /proc")
def test_a_process_is_told_apart_from_a_later_one_with_its_pid():
    assert ss.process_identity(os.getpid()) and ss.process_identity(os.getpid()) == ss.process_identity(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert ss.process_identity(child.pid) is None  # ended: nothing to read


def test_a_pid_now_used_by_another_process_counts_as_ended(monkeypatch):
    monkeypatch.setattr(ss, "process_identity", lambda pid: "started-later")
    assert ss._pid_alive(os.getpid(), "started-earlier") is False
    assert ss._pid_alive(os.getpid(), "started-later") is True


@pytest.mark.parametrize("pid", [None, "12", True, 0, -4])
def test_a_lock_without_a_real_pid_counts_as_running(pid):
    assert ss._pid_alive(pid) is True


@pytest.mark.skipif(sys.platform == "win32", reason="os.kill(pid, 0) is the POSIX check; Windows asks OpenProcess")
def test_another_users_process_counts_as_running(monkeypatch):
    def kill(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(ss.os, "kill", kill)
    assert ss._pid_alive(999999) is True


# --------------------------------------------------------------------------- taking a lock over

def test_a_takeover_guard_left_by_a_killed_run_is_cleared_after_a_minute(svc):
    path = lock_file(svc, svc.sid, {"pid": 999999999, "schedule_id": svc.sid})
    guard = path.with_name(path.name + ".takeover")
    guard.write_text("")
    old = time.time() - 120
    os.utime(guard, (old, old))
    assert svc._take_over(svc.sid, path) == (True, True)
    assert not path.exists() and not guard.exists()


def test_while_another_run_is_deciding_the_lock_isnt_taken(svc):
    path = lock_file(svc, svc.sid, {"pid": 999999999})
    path.with_name(path.name + ".takeover").write_text("")  # fresh: someone is deciding right now
    assert svc._take_over(svc.sid, path) == (False, False) and path.exists()
    assert svc._lock(svc.sid) is None


def test_a_lock_that_goes_while_deciding_is_free_and_logs_nothing(svc, monkeypatch):
    path = lock_file(svc, svc.sid, {"pid": 999999999})
    real = ss._read_lock

    def read(p):
        p.unlink()  # its run ended just now
        return real(p)
    monkeypatch.setattr(ss, "_read_lock", read)
    assert svc._take_over(svc.sid, path) == (True, False)


def test_a_live_run_keeps_its_lock(svc):
    path = lock_file(svc, svc.sid, {"pid": os.getpid(), "process": ss.process_identity(os.getpid())})
    assert svc._take_over(svc.sid, path) == (False, False) and path.exists()


def test_the_alerts_lock_is_taken_over_without_a_stopped_run(svc):
    path = lock_file(svc, ss.ALERTS_LOCK, {"pid": 999999999})
    assert svc._take_over(ss.ALERTS_LOCK, path) == (True, False)
    assert svc.pc_schedule_runs() == []


def test_a_stopped_run_of_a_removed_schedule_is_logged_by_its_id_with_only_what_the_app_writes(svc):
    path = lock_file(svc, "gone", {"pid": 999999999, "schedule_id": "another", "trigger": "manual",
                                   "started": "2026-11-07T01:00:00+00:00"})
    assert svc._take_over("gone", path) == (True, True)
    [row] = svc.pc_schedule_runs()
    assert row["label"] == "gone" and row["interrupted"] and row["trigger"] == "schedule" and row["started"] is None


def test_starting_the_app_logs_a_stopped_run_of_a_removed_schedule_and_still_syncs(svc):
    """A run was killed, then its schedule was deleted: its lock is left. The start-up check logs it and
    goes on to the next lock and the sync, rather than failing on the schedule that isn't there."""
    lock_file(svc, "deleted-1", {"pid": 999999999})
    lock_file(svc, svc.sid, {"pid": 999999999, "schedule_id": svc.sid})
    out = svc.sync_pc_schedules()
    assert out["interrupted"] == 2 and "error" not in out
    assert sorted(r["label"] for r in svc.pc_schedule_runs()) == ["Daily", "deleted-1"]


def test_a_lock_taken_between_the_check_and_the_create_is_left(svc, monkeypatch):
    real = os.open

    def opener(path, flags, *a):
        if str(path).endswith(f"{svc.sid}.lock") and flags & os.O_EXCL:
            raise FileExistsError(17, "File exists")  # another run made it first
        return real(path, flags, *a)
    monkeypatch.setattr(ss.os, "open", opener)
    assert svc._lock(svc.sid) is None


# --------------------------------------------------------------------------- the schedules list and Run now

def test_a_sync_task_scheduler_refuses_still_answers(svc, monkeypatch):
    def refuse(self):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(LocalSchedules, "sync", refuse)

    def unreadable():
        raise OSError("schedule_locks can't be read")
    monkeypatch.setattr(svc, "recover_pc_schedule_runs", unreadable)
    out = svc.sync_pc_schedules()
    assert out == {"updated": 0, "interrupted": 0, "error": "PermissionError: [Errno 5] Access is denied"}


def test_run_now_starts_its_own_process_and_the_list_filters(svc, monkeypatch):
    started = []
    monkeypatch.setattr(ss.subprocess, "Popen", lambda args, **kw: started.append((args, kw["cwd"])))
    assert svc.run_pc_schedule_now(svc.sid) == {"started": svc.sid}
    (args, cwd), = started
    assert args[-3:] == ["run-schedule", svc.sid, "--manual"] and cwd == str(svc.root)
    assert [s["id"] for s in svc.pc_schedules_for("all")] == [svc.sid]
    assert svc.pc_schedules_for("notebook", name="Other") == []
    with pytest.raises(LookupError, match="no schedule nope"):
        svc.run_pc_schedule_now("nope")


def test_a_run_that_raises_is_logged_as_failed_and_lets_its_lock_go(svc, monkeypatch):
    def boom(name, *a, **kw):
        raise RuntimeError("kernel wouldn't start")
    monkeypatch.setattr(svc, "_run_notebook_here", boom)
    out = svc.run_schedule(svc.sid)
    assert out["status"] == "failed" and out["message"] == "RuntimeError: kernel wouldn't start"
    assert not (svc.root / "schedule_locks" / f"{svc.sid}.lock").exists()


# --------------------------------------------------------------------------- Fabric's schedules

def test_no_sign_in_lists_no_fabric_schedules_and_says_why(svc, monkeypatch):
    def no_token(_self):
        raise RuntimeError("Sign in first")
    monkeypatch.setattr(Service, "auth", property(lambda self: type("A", (), {"fabric_token": no_token})()))
    out = svc.all_schedules()
    assert out["fabric"] == {"pending": False, "items": [], "all_workspaces": False, "error": "RuntimeError: Sign in first"}


class Flaky:
    """Fabric where some reads fail."""

    def __init__(self, token):
        pass

    def list_workspaces(self):
        return [{"id": "ws1", "displayName": "Sales"}, {"id": "ws2", "displayName": "Broken"}]

    def list_items(self, ws):
        if ws == "ws2":
            raise RuntimeError("403")
        return [{"id": "nb1", "type": "Notebook", "displayName": "Load"},
                {"id": "nb2", "type": "Notebook", "displayName": "No schedule"},
                {"id": "nb3", "type": "Notebook", "displayName": "Unreadable"}]

    def list_schedules(self, ws, item, job):
        if item == "nb3":
            raise RuntimeError("500")
        return [] if item == "nb2" else [{"id": "s1", "enabled": True, "owner": "not a dict",
                                          "configuration": {"type": "Daily", "times": ["05:00"], "localTimeZoneId": "UTC"}}]

    def list_job_instances(self, ws, item):
        raise RuntimeError("429")


def test_fabric_reads_that_fail_leave_out_only_what_they_couldnt_read(svc, monkeypatch, caplog):
    monkeypatch.setattr(ss, "FabricClient", Flaky)
    svc._scan_fabric_schedules("tok", all_workspaces=True)
    out = svc._fabric_schedules
    [row] = out["items"]
    assert (row["item_id"], row["last"], row["owner"]) == ("nb1", None, None)
    assert out["workspaces"] == 2 and "3 reads failed" in caplog.text


def test_a_workspace_list_that_fails_is_reported(svc, monkeypatch):
    class NoList(Flaky):
        def list_workspaces(self):
            raise RuntimeError("401")
    monkeypatch.setattr(ss, "FabricClient", NoList)
    svc._scan_fabric_schedules("tok", all_workspaces=True)
    assert svc._fabric_schedules["error"] == "RuntimeError: 401" and svc._fabric_schedules["items"] == []


def test_a_notebook_that_cant_be_loaded_doesnt_stop_the_workspace_list(svc, monkeypatch):
    monkeypatch.setattr(ss, "FabricClient", Flaky)
    svc.notebooks.save("Pinned", [{"type": "code", "source": ""}], )
    real = svc.notebooks.load

    def load(name):
        if name == "Daily":
            raise ValueError("damaged notebook")
        return real(name)
    monkeypatch.setattr(svc.notebooks, "load", load)
    svc.set_workspace_mark("ws1", "pins", True)
    assert svc._schedule_workspaces(False, "tok") == [{"id": "ws1", "name": "Sales"}]


def test_turning_off_or_deleting_a_fabric_schedule_updates_the_list_in_place(svc, monkeypatch):
    calls = []

    class Jobs:
        def update_schedule(self, *a):
            calls.append(("update", a))
            return {"ok": True}

        def delete_schedule(self, *a):
            calls.append(("delete", a))
    monkeypatch.setattr(Service, "fabric_jobs", property(lambda self: Jobs()))
    svc._forget_fabric_schedule("s1")  # nothing listed yet: nothing to change
    svc._fabric_schedules = {"pending": False, "items": [{"id": "s1", "enabled": True}, {"id": "s2", "enabled": True}]}
    svc.set_fabric_schedule_enabled("ws", "it", "RunNotebook", "s1", False, {"type": "Daily"})
    assert svc._fabric_schedules["items"][0]["enabled"] is False
    assert svc.delete_fabric_schedule("ws", "it", "RunNotebook", "s2") == {"deleted": "s2"}
    assert [r["id"] for r in svc._fabric_schedules["items"]] == ["s1"]
    assert [c[0] for c in calls] == ["update", "delete"]


# --------------------------------------------------------------------------- Windows time zones

class FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, zones, here):
        self.zones, self.here = zones, here

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, parent, sub):
        path = sub if isinstance(parent, str) else f"{parent.path}\\{sub}"
        if path == ss_tz.CURRENT_KEY and self.here is None:
            raise OSError("no current zone")
        return self.Key(path)

    def QueryInfoKey(self, key):
        return (len(self.zones), 0, 0)

    def EnumKey(self, key, i):
        return list(self.zones)[i]

    def QueryValueEx(self, key, name):
        if name == "TimeZoneKeyName":
            return (self.here, 1)
        zone = key.path.rsplit("\\", 1)[1]
        if self.zones[zone] is None:
            raise OSError("no Display")
        return (self.zones[zone], 1)


from fabric_desktop import time_zones as ss_tz  # noqa: E402


def test_the_windows_time_zones_are_read_from_the_registry_and_sorted_by_offset(monkeypatch):
    fake = FakeWinreg({"W. Europe Standard Time": "(UTC+01:00) Amsterdam", "UTC": "(UTC) Coordinated Universal Time",
                       "Pacific Standard Time": "(UTC-08:00) Pacific Time", "Broken": None}, "UTC")
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(ss_tz.sys, "platform", "win32")
    out = ss_tz.time_zones()
    assert [z["id"] for z in out["zones"]] == ["Pacific Standard Time", "UTC", "W. Europe Standard Time"]
    assert out["here"] == "UTC"
    fake.here = None
    assert ss_tz.time_zones()["here"] is None


def test_off_windows_or_without_a_registry_the_app_keeps_its_short_list(monkeypatch, svc):
    monkeypatch.setattr(ss_tz.sys, "platform", "linux")
    assert ss_tz.time_zones() == {"zones": [], "here": None} == svc.time_zones()
    with pytest.raises(OSError, match="only on Windows"):
        ss_tz._registry()
