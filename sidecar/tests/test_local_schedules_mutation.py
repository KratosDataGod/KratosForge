"""local_schedules.py paths that mutation testing (batch 22, docs/quality/mutation-testing.md) showed no
test checked: the runs log limits handed to the trimmer, the saved file's layout, how schtasks is
started, pausing a signed-out schedule under a policy that forbids it, the answers of set_enabled and
set_signed_out, renaming a notebook, pausing when the policy allows signed-out runs, and the next run
when the clocks change. Task Scheduler is stood in for, so nothing is registered and nothing runs."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop import local_schedules, runlog
from fabric_desktop.local_schedules import LocalSchedules, TaskScheduler, local_configuration


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class FakeScheduler:
    available = True

    def __init__(self):
        self.tasks, self.calls = {}, []

    def register(self, schedule, executable, workdir, launcher=None):
        self.calls.append(("register", schedule["id"]))
        self.tasks[schedule["id"]] = schedule["enabled"]

    def unregister(self, schedule_id):
        self.tasks.pop(schedule_id, None)

    def set_enabled(self, schedule_id, enabled):
        self.calls.append(("set_enabled", schedule_id, enabled))
        self.tasks[schedule_id] = enabled

    def results(self):
        return {}


DAILY = {"kind": "daily", "times": ["06:00"]}


def pcs(tmp_path, allow_signed_out=True):
    fake = FakeScheduler()
    return LocalSchedules(tmp_path / "app", fake, str(tmp_path / "engine" / "python.exe"), allow_signed_out), fake


# ---- the runs log

def test_the_runs_log_limits_are_ten_thousand_rows_and_eight_then_four_megabytes():
    assert local_schedules.RUNS_KEEP == 10_000
    assert local_schedules.RUNS_TRIM_BYTES == 8 * 1024 * 1024
    assert local_schedules.RUNS_KEEP_BYTES == 4 * 1024 * 1024


def test_logging_a_run_hands_the_limits_to_the_trimmer_and_works_twice(monkeypatch, tmp_path):
    s, _ = pcs(tmp_path)
    seen = []
    real = runlog.append

    def spy(path, row, trim, keep, keep_bytes):
        seen.append((trim, keep, keep_bytes))
        return real(path, row, trim, keep, keep_bytes)

    monkeypatch.setattr(runlog, "append", spy)
    s.log_run({"schedule_id": "a", "ok": True})
    s.log_run({"schedule_id": "a", "ok": False})
    assert seen == [(8 * 1024 * 1024, 10_000, 4 * 1024 * 1024)] * 2
    assert [r["ok"] for r in s.runs()] == [False, True]


def test_logging_a_run_makes_the_folders_it_needs(tmp_path):
    s = LocalSchedules(tmp_path / "a" / "b" / "app", FakeScheduler(), str(tmp_path / "python.exe"), True)
    s.log_run({"schedule_id": "a", "ok": True})
    assert [r["ok"] for r in s.runs()] == [True]


# ---- the saved file and the start time

def test_the_schedules_file_is_saved_with_one_space_indent(tmp_path):
    s, _ = pcs(tmp_path)
    s.add({"kind": "notebook", "name": "Load"}, DAILY)
    text = s.file.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=1)


def test_the_start_time_is_on_the_minute():
    assert local_configuration(DAILY)["startDateTime"].endswith(":00")


# ---- schtasks

def test_schtasks_runs_as_text_without_a_window_flag_where_there_is_none(monkeypatch):
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")

    monkeypatch.setattr(local_schedules.subprocess, "run", run)
    TaskScheduler()._run("/Query")
    assert seen["creationflags"] == 0
    assert seen["text"] is True
    assert seen["capture_output"] is True


# ---- pausing, resuming and running signed out

def test_a_signed_out_schedule_can_be_paused_when_the_policy_forbids_it(tmp_path):
    s, fake = pcs(tmp_path)
    item = s.add({"kind": "notebook", "name": "Load"}, DAILY, signed_out=True)
    strict = LocalSchedules(s.root, fake, s.executable, allow_signed_out=False)
    got = strict.set_enabled(item["id"], False)
    assert got["enabled"] is False and got["id"] == item["id"]
    assert fake.calls[-1] == ("set_enabled", item["id"], False)
    with pytest.raises(PermissionError):
        strict.set_enabled(item["id"], True)


def test_set_enabled_answers_with_the_schedule(tmp_path):
    s, _ = pcs(tmp_path)
    item = s.add({"kind": "notebook", "name": "Load"}, DAILY)
    got = s.set_enabled(item["id"], False)
    assert got["id"] == item["id"] and got["enabled"] is False


def test_set_signed_out_answers_with_the_schedule(tmp_path):
    s, _ = pcs(tmp_path)
    item = s.add({"kind": "notebook", "name": "Load"}, DAILY)
    got = s.set_signed_out(item["id"], True)
    assert got["id"] == item["id"] and got["signed_out"] is True
    got = s.set_signed_out(item["id"], False)
    assert got["id"] == item["id"] and "signed_out" not in got


def test_pausing_disallowed_schedules_does_nothing_when_the_policy_allows_them(tmp_path):
    s, fake = pcs(tmp_path)
    s.add({"kind": "notebook", "name": "Load"}, DAILY, signed_out=True)
    fake.calls.clear()
    assert s.pause_disallowed() == []
    assert fake.calls == []
    assert all(x.get("enabled") for x in s._load()["schedules"])


# ---- renaming a notebook

def test_renaming_a_notebook_moves_only_its_own_schedules_and_saves_them(tmp_path):
    s, _ = pcs(tmp_path)
    old = s.add({"kind": "notebook", "name": "Old"}, DAILY)
    other = s.add({"kind": "notebook", "name": "Other"}, DAILY)
    pipe = s.add({"kind": "pipeline", "path": str(tmp_path / "p.json"), "name": "Old"}, DAILY)
    assert s.rename_notebook("Old", "New") == [old["id"]]
    saved = {x["id"]: x["target"] for x in LocalSchedules(s.root, FakeScheduler(), s.executable)._load()["schedules"]}
    assert saved[old["id"]]["name"] == "New"
    assert saved[other["id"]]["name"] == "Other"
    assert saved[pipe["id"]]["name"] == "Old"


def test_renaming_a_notebook_no_schedule_runs_leaves_the_file_alone(monkeypatch, tmp_path):
    s, _ = pcs(tmp_path)
    s.add({"kind": "notebook", "name": "Load"}, DAILY)
    saves = []
    monkeypatch.setattr(s, "_save", lambda data: saves.append(data))
    assert s.rename_notebook("Missing", "New") == []
    assert saves == []


# ---- the next run across a clock change (Europe/London, 2026)

@pytest.fixture
def london():
    import os
    import time
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is not available on this platform")
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/London"
    time.tzset()
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def _cron(minutes, start):
    return {"type": "Cron", "interval": minutes, "startDateTime": start.isoformat()}


def _at(text, fold=0):
    import datetime as dt
    return dt.datetime.fromisoformat(text).replace(fold=fold)


@pytest.mark.parametrize("minutes, start, now, want, fold", [
    # clocks go back: inside the first 01:xx, the repeated 01:00 is next
    (15, "2026-10-24T00:00", ("2026-10-25T01:45", 0), "2026-10-25T01:00", 1),
    # clocks go back: at 00:00 of the later fold, the grid's 01:30 is next
    (90, "2026-10-24T00:00", ("2026-10-25T00:00", 1), "2026-10-25T01:30", 0),
    # clocks go back: at the first 01:30, the same wall time an hour on is next
    (90, "2026-10-24T00:00", ("2026-10-25T01:30", 0), "2026-10-25T01:30", 1),
    # clocks go forward: 02:00 does not exist, and the next run is not an hour late
    (15, "2026-03-28T00:00", ("2026-03-29T01:45", 0), "2026-03-29T02:00", 0),
])
def test_the_next_run_across_a_clock_change_is_the_first_moment_still_to_come(london, minutes, start, now, want, fold):
    got = local_schedules.next_run(_cron(minutes, _at(start)), _at(*now))
    assert got == _at(want) and got.fold == fold
