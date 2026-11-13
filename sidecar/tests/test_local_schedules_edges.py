"""Task Scheduler on Windows (local_schedules.TaskScheduler): saving a signed-in task through a definition
file, pausing a signed-out task, and running one now. schtasks and the signed-out task helpers are
stood in for, so no task is saved and nothing runs."""

from __future__ import annotations

from pathlib import Path

from fabric_desktop import local_schedules
from fabric_desktop.local_schedules import TaskScheduler


SCHEDULE = {"id": "abc", "enabled": True, "target": {"kind": "notebook", "name": "Load"},
            "config": {"type": "Daily", "times": ["06:00"], "startDateTime": "2026-10-15T07:00:00"}}


def scheduler(monkeypatch):
    monkeypatch.setattr(TaskScheduler, "available", True)
    ts = TaskScheduler()
    ts.calls = []

    def run(*args):
        if "/XML" in args:  # the definition is on disk while schtasks reads it
            xml = Path(args[args.index("/XML") + 1])
            ts.calls.append((args, xml, xml.read_text(encoding="utf-16")))
        else:
            ts.calls.append((args, None, None))

    monkeypatch.setattr(ts, "_run", run)
    return ts


def test_a_signed_in_task_is_saved_from_a_definition_file_that_is_then_removed(monkeypatch):
    ts = scheduler(monkeypatch)
    ts.register(SCHEDULE, r"C:\engine\pythonw.exe", r"C:\work")
    (args, xml, text), = ts.calls
    assert args[:3] == ("/Create", "/TN", local_schedules.task_name("abc")) and args[-1] == "/F"
    assert "<CalendarTrigger>" in text
    assert not xml.exists()  # removed once schtasks has read it


def test_the_definition_file_is_removed_even_when_task_scheduler_refuses(monkeypatch):
    ts = scheduler(monkeypatch)
    seen = []

    def refuse(*args):
        seen.append(Path(args[args.index("/XML") + 1]))
        raise RuntimeError("Task Scheduler: Access is denied.")

    monkeypatch.setattr(ts, "_run", refuse)
    try:
        ts.register(SCHEDULE, "p", "w")
    except RuntimeError as exc:
        assert "Access is denied" in str(exc)
    else:
        raise AssertionError("the refusal was swallowed")
    assert seen and not seen[0].exists()


def test_pausing_a_signed_out_task_and_running_one_now(monkeypatch):
    ts = scheduler(monkeypatch)
    paused = []
    monkeypatch.setattr(local_schedules.signed_out_tasks, "set_enabled",
                        lambda folder, sid, enabled: paused.append((folder, sid, enabled)))
    ts.set_enabled("abc", False)
    assert paused == [(local_schedules.TASK_FOLDER, "abc", False)]
    assert ts.run_now("abc") is True
    assert ts.calls[-1][0] == ("/Run", "/TN", local_schedules.task_name("abc"))


def test_a_signed_out_task_is_saved_through_the_password_prompt_helper(monkeypatch):
    ts = scheduler(monkeypatch)
    saved = []
    monkeypatch.setattr(local_schedules.signed_out_tasks, "register",
                        lambda folder, sid, xml: saved.append((folder, sid, xml)))
    ts.register(SCHEDULE | {"signed_out": True}, "p", "w", launcher=r"C:\app\KratosForge.exe")
    assert saved[0][:2] == (local_schedules.TASK_FOLDER, "abc") and "KratosForge.exe" in saved[0][2]
    assert ts.calls == []  # schtasks isn't used for it
