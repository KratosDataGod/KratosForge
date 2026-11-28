"""local_schedules.py paths that the uncapped mutation run (batch 229, docs/quality/mutation-testing.md)
showed no test checked: the windowless interpreter picked only when it is there, Task Scheduler's
results asked for only where it runs, the runs list's default of 500, a schedule saved before it had
an "enabled" field counted as on, and a new schedule's 12-character id. Task Scheduler is stood in
for, so nothing is registered and nothing runs."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fabric_desktop import local_schedules
from fabric_desktop.local_schedules import LocalSchedules, TaskScheduler


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


class FakeScheduler:
    available = True

    def __init__(self):
        self.calls = []

    def register(self, schedule, executable, workdir, launcher=None):
        self.calls.append(("register", schedule["id"]))

    def unregister(self, schedule_id):
        self.calls.append(("unregister", schedule_id))

    def set_enabled(self, schedule_id, enabled):
        self.calls.append(("set_enabled", schedule_id, enabled))

    def results(self):
        return {}


DAILY = {"kind": "daily", "times": ["06:00"]}


def _pcs(tmp_path, allow_signed_out=True):
    fake = FakeScheduler()
    return LocalSchedules(tmp_path / "app", fake, str(tmp_path / "engine" / "python.exe"), allow_signed_out), fake


def _without_enabled(pcs, signed_out=False):
    """A schedules file written before schedules had an "enabled" field."""
    item = pcs.add({"kind": "notebook", "name": "nb"}, DAILY)
    data = json.loads(pcs.file.read_text(encoding="utf-8"))
    for s in data["schedules"]:
        s.pop("enabled")
        if signed_out:
            s["signed_out"] = True
    pcs.file.write_text(json.dumps(data), encoding="utf-8")
    return item["id"]


def test_pythonw_is_used_only_when_it_is_next_to_the_interpreter(tmp_path):
    exe = tmp_path / "python.exe"
    exe.write_text("", encoding="utf-8")
    assert local_schedules.pythonw(str(exe)) == str(exe)
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    assert local_schedules.pythonw(str(exe)) == str(tmp_path / "pythonw.exe")


def test_task_scheduler_results_are_asked_for_only_where_it_runs(monkeypatch):
    monkeypatch.setattr(local_schedules.signed_out_tasks, "last_results", lambda folder: {"abc": {"folder": folder}})
    off, on = TaskScheduler(), TaskScheduler()
    off.available, on.available = False, True
    assert off.results() == {}
    assert on.results() == {"abc": {"folder": "FabricDesktop"}}


def test_runs_lists_the_newest_500_by_default(tmp_path):
    pcs, _ = _pcs(tmp_path)
    pcs.root.mkdir(parents=True)
    pcs.runs_file.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(501)), encoding="utf-8")
    got = pcs.runs()
    assert len(got) == 500
    assert got[0] == {"n": 500} and got[-1] == {"n": 1}


def test_a_new_schedule_has_a_twelve_character_id(tmp_path):
    pcs, _ = _pcs(tmp_path)
    item = pcs.add({"kind": "notebook", "name": "nb"}, DAILY)
    assert len(item["id"]) == 12
    int(item["id"], 16)


def test_a_task_without_enabled_is_saved_enabled():
    schedule = {"id": "abc", "target": {"kind": "notebook", "name": "nb"},
                "config": {"type": "Daily", "times": ["06:00"], "startDateTime": "2026-11-20T00:00:00"}}
    xml = local_schedules.task_xml(schedule, "python.exe", "C:\\work")
    assert "<Enabled>true</Enabled>\n  </Settings>" in xml


def test_a_schedule_saved_without_enabled_still_shows_its_next_run(tmp_path):
    pcs, _ = _pcs(tmp_path)
    _without_enabled(pcs)
    (row,) = pcs.listing(now=dt.datetime(2026, 11, 20, 5, 0))
    assert row["next_run"] == "2026-11-20T06:00:00"


def test_a_signed_out_schedule_saved_without_enabled_is_paused_when_the_policy_forbids_it(tmp_path):
    pcs, fake = _pcs(tmp_path, allow_signed_out=False)
    sid = _without_enabled(pcs, signed_out=True)
    assert pcs.pause_disallowed() == [sid]
    assert ("set_enabled", sid, False) in fake.calls
