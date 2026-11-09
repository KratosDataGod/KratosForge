"""Schedules on this PC (schedule_service.py), the paths the main tests don't reach: telling a lock's
process apart on Windows (kernel32 stood in, so this runs on any OS), a stopped run whose Teams alert
fails, a metadata run with tables that didn't finish, and waiting alerts that can't go after a run
that worked. Nothing reaches Teams or Task Scheduler."""

from __future__ import annotations

import ctypes
import logging
from types import SimpleNamespace

from fabric_desktop import kernel_proc, schedule_service
from test_kernel_process_edges import FakeDLL
from test_schedules import _dead_pid, svc  # noqa: F401 - svc is a fixture

WEBHOOK = "https://example.webhook.office.com/x?sig=SECRET"


def _windows(monkeypatch):
    monkeypatch.setattr(schedule_service, "sys", SimpleNamespace(platform="win32"))


# ---- a lock's process on Windows


def test_a_process_identity_on_windows_is_its_creation_time(monkeypatch):
    _windows(monkeypatch)

    def times(handle, created, *_rest):
        created._obj.dwHighDateTime, created._obj.dwLowDateTime = 1, 2
        return 1
    dll = FakeDLL(OpenProcess=7, GetProcessTimes=times)
    monkeypatch.setattr(kernel_proc, "win_kernel32", lambda: dll)
    assert schedule_service.process_identity(4242) == str(1 << 32 | 2)
    assert dll.calls[0] == ("OpenProcess", (kernel_proc.PROCESS_QUERY_LIMITED_INFORMATION, False, 4242))
    assert dll.calls[-1] == ("CloseHandle", (7,))


def test_a_process_identity_windows_wont_give_is_none(monkeypatch):
    _windows(monkeypatch)
    monkeypatch.setattr(kernel_proc, "win_kernel32", lambda: FakeDLL(OpenProcess=0))
    assert schedule_service.process_identity(4242) is None
    dll = FakeDLL(OpenProcess=7, GetProcessTimes=0)
    monkeypatch.setattr(kernel_proc, "win_kernel32", lambda: dll)
    assert schedule_service.process_identity(4242) is None
    assert dll.calls[-1] == ("CloseHandle", (7,))  # the handle is closed either way


def _kernel32(monkeypatch, last_error=0, **returns):
    dll = FakeDLL(**returns)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error, raising=False)
    return dll


def _exit_code(value):
    def get(handle, code):
        code._obj.value = value
        return 1
    return get


def test_a_lock_process_still_running_on_windows_is_alive(monkeypatch):
    _windows(monkeypatch)
    dll = _kernel32(monkeypatch, OpenProcess=9, GetExitCodeProcess=_exit_code(259))  # STILL_ACTIVE
    assert schedule_service._pid_alive(4242) is True
    assert dll.OpenProcess.restype is not None  # typed, so a 64-bit handle isn't cut
    assert dll.calls[-1] == ("CloseHandle", (9,))


def test_a_lock_process_that_exited_on_windows_has_ended(monkeypatch):
    _windows(monkeypatch)
    _kernel32(monkeypatch, OpenProcess=9, GetExitCodeProcess=_exit_code(0))
    assert schedule_service._pid_alive(4242) is False


def test_a_lock_process_windows_cant_tell_about_counts_as_running(monkeypatch):
    _windows(monkeypatch)
    _kernel32(monkeypatch, OpenProcess=9, GetExitCodeProcess=0)
    assert schedule_service._pid_alive(4242) is True
    _kernel32(monkeypatch, last_error=5, OpenProcess=0)  # access denied: it exists
    assert schedule_service._pid_alive(4242) is True


def test_a_pid_windows_says_doesnt_exist_has_ended(monkeypatch):
    _windows(monkeypatch)
    _kernel32(monkeypatch, last_error=87, OpenProcess=0)  # ERROR_INVALID_PARAMETER
    assert schedule_service._pid_alive(4242) is False


# ---- runs and their alerts


def test_turning_signed_out_runs_on_and_off(svc):  # noqa: F811
    svc.notebooks.save("Night", [{"type": "code", "source": "x = 1"}])
    sid = svc.add_pc_schedule({"kind": "notebook", "name": "Night"}, {"kind": "daily", "times": ["06:00"]})["schedule"]["id"]
    assert svc.set_pc_schedule_signed_out(sid, True)["signed_out"] is True
    assert "signed_out" not in svc.set_pc_schedule_signed_out(sid, False)


def test_a_stopped_run_whose_teams_alert_fails_says_so_in_its_record(svc, monkeypatch):  # noqa: F811
    from fabric_desktop import dataops_service
    svc.notebooks.save("Gone", [{"type": "code", "source": "x = 1"}])
    sid = svc.add_pc_schedule({"kind": "notebook", "name": "Gone"}, {"kind": "daily", "times": ["06:00"]})["schedule"]["id"]
    (svc.root / "schedule_locks").mkdir(parents=True, exist_ok=True)
    (svc.root / "schedule_locks" / f"{sid}.lock").write_text(str(_dead_pid()))
    monkeypatch.setattr(svc, "_settings", lambda: {"teams_webhook": WEBHOOK})

    def refused(url, title, lines):
        raise RuntimeError(f"Teams answered 403 for {url}")
    monkeypatch.setattr(dataops_service, "post_teams", refused)
    assert svc.sync_pc_schedules()["interrupted"] == 1
    [stopped] = svc.pc_schedule_runs()
    assert stopped["interrupted"] and stopped["alert_error"].startswith("Couldn't post the Teams alert: RuntimeError")
    assert "SECRET" not in stopped["alert_error"]


def test_a_metadata_run_with_tables_that_didnt_finish_names_the_first(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(svc, "_project", lambda: object(), raising=False)
    results = [{"entity": "Orders", "status": "ok", "rows_written": 10, "message": ""},
               {"entity": "Lines", "status": "failed", "rows_written": None, "message": "source file missing"},
               {"entity": "Notes", "status": "skipped", "message": ""},
               {"entity": "Items", "status": "failed", "rows_written": 2, "message": "bad key"}]
    monkeypatch.setattr(svc, "_run_here", lambda project, env, names: results, raising=False)
    out = svc._run_meta_here("dev", None)
    assert out == {"status": "failed", "tables": 4, "rows": 12,
                   "message": "2 of 4 tables didn't finish. Lines: source file missing"}


def test_waiting_alerts_that_cant_go_after_a_run_that_worked_only_warn(svc, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(svc, "_settings", lambda: {"teams_webhook": WEBHOOK})

    def unreachable(url, post):
        raise ConnectionError(f"couldn't reach {url}")
    monkeypatch.setattr(svc, "_send_waiting_schedule_alerts", unreachable)
    with caplog.at_level(logging.WARNING, logger=schedule_service._log.name):
        assert svc._send_schedule_alerts({"status": "ok", "label": "Daily", "schedule_id": "s1"}) == ""
    assert "Couldn't send waiting schedule alerts: ConnectionError" in caplog.text
    assert "SECRET" not in caplog.text
