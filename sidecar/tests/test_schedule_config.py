"""Schedule settings that must not quietly become a different schedule: the agent's [[schedule]] keys,
Fabric's three recurrence types and their weekdays, what Task Scheduler is given for each, and
the checks on what a schedule on this PC runs (local_schedules.py, fabric_run.schedule_configuration)."""

import datetime as dt
import json
import subprocess
import sys

import pytest

from fabric_desktop import agent_config, local_schedules
from fabric_desktop.fabric_run import describe_schedule, schedule_configuration
from fabric_desktop.local_schedules import (
    LocalSchedules, TaskScheduler, check_target, local_configuration, next_run, target_label, task_configuration,
    task_xml,
)

from test_agent import config_text, setup  # noqa: F401 - setup is a fixture
from test_schedules import FakeScheduler


# ---- the agent's [[schedule]]

def _problems(cfg, tmp_path, schedule: str) -> list[str]:
    path = tmp_path / "s.toml"
    path.write_text(config_text(cfg.project, tmp_path / "h", f'[[schedule]]\nid = "s"\nenv = "dev"\n{schedule}'),
                    encoding="utf-8")
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.load(path)
    return err.value.errors


@pytest.mark.parametrize("schedule, says", [
    ("every_minutes = 1.5", "every_minutes is a whole number of at least 1"),  # was every minute
    ("every_minutes = true", "every_minutes is a whole number of at least 1"),  # was every minute
    ('every_minutes = "60"', "every_minutes is a whole number of at least 1"),
    ("every_minutes = 0", "every_minutes is a whole number from 1 to 5270400, like 60"),
    ("every_minutes = 5270401", "every_minutes is a whole number from 1 to 5270400, like 60"),
    ('daily = "06:00"', 'times are a list, like ["06:00"]'),  # was five one-letter "times"
    ('daily = ["6:00"]', "Times are HH:MM in 24-hour form"),
    ('daily = ["24:00"]', "Times are HH:MM in 24-hour form"),
    ("daily = []", 'daily needs at least one time, like ["06:00"]'),
    ('weekly = "Monday"', "weekly is { days ="),
    ('weekly = { days = "Monday", times = ["06:00"] }', "weekly is { days ="),
    ('weekly = { days = ["Monday"], times = "06:00" }', "times are a list"),
    ('weekly = { days = ["Monday", "Tusday"], times = ["06:00"] }', "Days are Monday to Sunday (got 'Tusday')"),
    ('weekly = { days = [], times = ["06:00"] }', "weekly needs at least one day and one time, like { days ="),
    ('weekly = { days = ["Monday"], times = [] }', "weekly needs at least one day and one time"),
    ('weekly = { times = ["06:00"] }', "weekly needs at least one day and one time"),
    ('every_minutes = 60\ndaily = ["06:00"]', "give exactly one of every_minutes, daily or weekly"),
])
def test_a_schedule_written_wrong_is_refused_in_words(tmp_path, setup, schedule, says):  # noqa: F811
    problems = "\n".join(_problems(setup, tmp_path, schedule))
    assert says in problems, problems


def test_weekdays_are_read_in_any_case_once_each(tmp_path, setup):  # noqa: F811
    # "monday" was dropped without a word, so a Monday-and-Thursday schedule ran on Thursdays only.
    path = tmp_path / "s.toml"
    path.write_text(config_text(setup.project, tmp_path / "h",
                                '[[schedule]]\nid = "s"\nenv = "dev"\n'
                                'weekly = { days = ["monday", " THURSDAY", "Monday"], times = ["06:00"] }'),
                    encoding="utf-8")
    [s] = agent_config.load(path).schedules
    assert s.config["weekdays"] == ["Monday", "Thursday"] and s.when["weekdays"] == ["Monday", "Thursday"]
    assert next_run(s.config, dt.datetime(2026, 10, 13, 7, 0)) == dt.datetime(2026, 10, 15, 6, 0)  # Tue -> Thu


# ---- Fabric's recurrence types

def test_each_recurrence_type_has_fabrics_shape():
    start, end = "2026-11-01T00:00:00", "2026-12-01T00:00:00"
    cron = schedule_configuration("cron", every_minutes=15, time_zone="W. Europe Standard Time", start=start, end=end)
    assert cron == {"startDateTime": start, "endDateTime": end, "localTimeZoneId": "W. Europe Standard Time",
                    "type": "Cron", "interval": 15}
    daily = schedule_configuration("daily", times=[" 06:00 ", "", "18:30"], start=start, end=end)
    assert daily["type"] == "Daily" and daily["times"] == ["06:00", "18:30"] and daily["localTimeZoneId"] == "UTC"
    weekly = schedule_configuration("weekly", times=["06:00"], weekdays=["friday", "Monday"], start=start, end=end)
    assert weekly["type"] == "Weekly" and weekly["weekdays"] == ["Friday", "Monday"]
    assert describe_schedule({"configuration": cron}) == "Every 15 minutes"
    assert describe_schedule({"configuration": daily}) == "Daily at 06:00, 18:30 UTC"
    assert describe_schedule({"configuration": weekly}) == "Fri, Mon at 06:00 UTC"
    with pytest.raises(ValueError, match="unknown schedule kind 'monthly'"):
        schedule_configuration("monthly", times=["06:00"])
    with pytest.raises(ValueError, match="Pick how many minutes"):
        schedule_configuration("cron", every_minutes=5270401)


def test_a_schedule_on_this_pc_keeps_local_time_and_no_end():
    config = local_configuration({"kind": "weekly", "times": ["06:00"], "weekdays": ["Sunday"]})
    assert config["localTimeZoneId"] == "Local" and "endDateTime" not in config
    assert dt.datetime.fromisoformat(config["startDateTime"]).second == 0
    assert local_configuration({"times": ["07:00"]})["type"] == "Daily"  # daily when no kind is given


# ---- next_run for each type

def test_next_run_for_each_type_and_what_never_runs():
    now = dt.datetime(2026, 10, 18, 23, 30)  # a Sunday
    later = {"type": "Cron", "interval": 60, "startDateTime": "2026-10-20T00:00:00"}
    assert next_run(later, now) == dt.datetime(2026, 10, 20)  # not started yet: its start
    assert next_run({"type": "Daily", "times": ["18:00", "06:00"]}, now) == dt.datetime(2026, 10, 19, 6)
    weekly = {"type": "Weekly", "weekdays": ["Sunday"], "times": ["23:00"]}
    assert next_run(weekly, now) == dt.datetime(2026, 10, 25, 23)  # a week on, the same day
    assert next_run({"type": "Daily", "times": []}, now) is None
    assert next_run({"type": "Weekly", "weekdays": [], "times": ["06:00"]}, now) is None
    assert next_run({"type": "Monthly", "times": ["06:00"]}, now) is None  # a type this PC doesn't run


# ---- what Task Scheduler is given

def _xml(config, **schedule):
    return task_xml({"id": "abc", "target": {"kind": "notebook", "name": "N & <b>"}, "config": config, **schedule},
                    "C:/e/pythonw.exe", "C:/app")


def test_task_scheduler_gets_one_trigger_per_time_for_each_type():
    start = "2026-11-01T09:15:00"
    cron = _xml({"type": "Cron", "interval": 90, "startDateTime": start})
    assert f"<StartBoundary>{start}</StartBoundary>" in cron and "<Interval>PT90M</Interval>" in cron
    daily = _xml({"type": "Daily", "times": ["06:00", "18:00"], "startDateTime": start})
    assert daily.count("<CalendarTrigger>") == 2 and "<StartBoundary>2026-11-01T18:00:00</StartBoundary>" in daily
    assert "<DaysInterval>1</DaysInterval>" in daily
    weekly = _xml({"type": "Weekly", "times": ["06:00"], "weekdays": ["Monday", "Friday"], "startDateTime": start})
    assert "<DaysOfWeek><Monday /><Friday /></DaysOfWeek>" in weekly
    assert "N &amp; &lt;b&gt;" in daily and "<Enabled>false</Enabled>" in _xml({"type": "Daily", "times": ["06:00"]},
                                                                                enabled=False)


def test_a_signed_out_task_runs_the_app_with_the_saved_password():
    xml = task_xml({"id": "abc", "target": {"kind": "meta", "env": "dev"}, "signed_out": True,
                    "config": {"type": "Daily", "times": ["06:00"], "startDateTime": "2026-11-01T00:00:00"}},
                   "C:/e/pythonw.exe", "C:/app", launcher="C:/Program Files/KratosForge/KratosForge.exe")
    assert "<LogonType>Password</LogonType>" in xml
    assert "<Command>C:/Program Files/KratosForge/KratosForge.exe</Command>" in xml
    assert "<Arguments>--run-schedule abc</Arguments>" in xml and "Dev metadata: every table" in xml


def test_an_interval_task_scheduler_cant_repeat_is_refused_before_anything_is_saved(tmp_path):
    # Task Scheduler repeats at most every 31 days; a longer interval failed in schtasks as "out of range".
    assert task_configuration({"kind": "cron", "every_minutes": 31 * 24 * 60})["interval"] == 44640
    fake = FakeScheduler()
    pc = LocalSchedules(tmp_path, fake, "C:/e/python.exe")
    with pytest.raises(ValueError, match="at least every 31 days"):
        pc.add({"kind": "notebook", "name": "n"}, {"kind": "cron", "every_minutes": 44641})
    assert fake.tasks == {} and not pc.file.exists()
    s = pc.add({"kind": "notebook", "name": "n"}, {"kind": "cron", "every_minutes": 60})
    with pytest.raises(ValueError, match="at least every 31 days"):
        pc.update(s["id"], {"kind": "cron", "every_minutes": 50000})
    assert pc.get(s["id"])["config"]["interval"] == 60


# ---- what a schedule runs

@pytest.mark.parametrize("target, says", [
    ({"kind": "notebook"}, "Pick the notebook to run"),
    ({"kind": "meta", "env": "qa"}, "Pick the environment to run"),
    ({"kind": "pipeline"}, "Pick the pipeline to run"),
    ({"kind": "pipeline", "path": "p", "parameters": ["a"]}, "parameters must be an object"),
    ({"kind": "sql"}, "A schedule runs a notebook, a metadata environment or a pipeline"),
])
def test_a_target_that_cant_run_is_refused(target, says):
    with pytest.raises(ValueError, match=says):
        check_target(target)


def test_targets_keep_only_what_their_runs_need_and_are_labelled():
    pipe = check_target({"kind": "pipeline", "path": "C:/p/load", "parameters": {"day": "1"}, "extra": "x"})
    assert pipe == {"kind": "pipeline", "path": "C:/p/load", "name": "", "parameters": {"day": "1"}, "mocks": {},
                    "notebooks": {}, "sql_projects": {}, "read_lakehouses": False, "workspace_id": ""}
    assert target_label(pipe) == "Pipeline: load"
    assert target_label({"kind": "notebook", "name": "Daily load"}) == "Daily load"
    assert target_label({"kind": "meta", "env": "prod", "entities": ["a", "b"]}) == "Prod metadata: a, b"


# ---- the schedules file

def test_lookups_of_a_schedule_that_isnt_there_say_so(tmp_path):
    pc = LocalSchedules(tmp_path, FakeScheduler(), "C:/e/python.exe")
    for call in (lambda: pc.get("nope"), lambda: pc.set_enabled("nope", False),
                 lambda: pc.update("nope", {"kind": "daily", "times": ["06:00"]}),
                 lambda: pc.set_signed_out("nope", True)):
        with pytest.raises(LookupError, match="There is no schedule nope on this PC"):
            call()


def test_notebooks_lists_what_enabled_schedules_run(tmp_path):
    pc = LocalSchedules(tmp_path, FakeScheduler(), "C:/e/python.exe")
    assert pc.notebooks() == set()
    pc.file.write_text(json.dumps({"schedules": [
        {"target": {"kind": "notebook", "name": "a"}},
        {"target": {"kind": "notebook", "name": "off"}, "enabled": False},
        {"target": {"kind": "pipeline", "notebooks": {"step": "b", "odd": 3}}},
        {"target": {"kind": "pipeline", "notebooks": "c"}},
        {"target": {"kind": "notebook", "name": ""}},
    ]}), encoding="utf-8")
    assert pc.notebooks() == {"a", "b"}
    pc.file.write_text("{torn", encoding="utf-8")
    with pytest.raises(ValueError):
        pc.notebooks()
    assert pc.file.exists()  # only read: left where it is


def test_a_torn_line_in_the_runs_log_is_skipped(tmp_path):
    pc = LocalSchedules(tmp_path, FakeScheduler(), "C:/e/python.exe")
    pc.log_run({"schedule_id": "a", "status": "ok"})
    with pc.runs_file.open("a", encoding="utf-8") as f:
        f.write('{"schedule_id": "b", "sta\n')
    pc.log_run({"schedule_id": "c", "status": "error"})
    assert [r["schedule_id"] for r in pc.runs()] == ["c", "a"]


def test_turning_signed_out_off_drops_the_flag(tmp_path):
    fake = FakeScheduler()
    pc = LocalSchedules(tmp_path, fake, "C:/e/python.exe")
    s = pc.add({"kind": "notebook", "name": "n"}, {"kind": "daily", "times": ["06:00"]}, signed_out=True)
    assert pc.get(s["id"])["signed_out"] is True and "launcher" in pc.get(s["id"])
    pc.set_signed_out(s["id"], False)
    assert "signed_out" not in pc.get(s["id"]) and "launcher" not in pc.get(s["id"])


# ---- schtasks

def test_schtasks_is_called_quietly_and_its_errors_come_back_in_words(monkeypatch):
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        ok = args[1] != "/Run"
        return subprocess.CompletedProcess(args, 0 if ok else 1, "", "" if ok else "ERROR: The task doesn't exist.\n")
    monkeypatch.setattr(local_schedules.subprocess, "run", run)
    monkeypatch.setattr(TaskScheduler, "available", True)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    ts = TaskScheduler()
    ts.unregister("abc")
    assert calls[0][0] == [r"C:\Windows\System32\schtasks.exe", "/Delete", "/TN", "\\FabricDesktop\\abc", "/F"]
    assert calls[0][1]["timeout"] == local_schedules.SCHTASKS_SECONDS and calls[0][1]["capture_output"]
    with pytest.raises(RuntimeError, match="Task Scheduler: ERROR: The task doesn't exist."):
        ts.run_now("abc")
    monkeypatch.setattr(local_schedules.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "ERROR: gone"))
    ts.unregister("abc")  # already gone: not an error


def test_without_task_scheduler_schedules_are_saved_and_nothing_starts(monkeypatch):
    monkeypatch.setattr(TaskScheduler, "available", False)
    monkeypatch.setattr(local_schedules.subprocess, "run", lambda *a, **k: pytest.fail("schtasks ran"))
    ts = TaskScheduler()
    ts.register({"id": "a", "config": {"type": "Daily", "times": ["06:00"]}}, "e", "w")
    ts.unregister("a")
    ts.set_enabled("a", False)
    assert ts.results() == {} and ts.run_now("a") is False


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows default")
def test_task_scheduler_is_only_available_on_windows():
    assert TaskScheduler.available is False
