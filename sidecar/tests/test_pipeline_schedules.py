"""Pipeline schedules on this PC (pipelines plan P6): Task Scheduler starts a local run of a
.DataPipeline folder with the settings kept on the schedule, and the run is kept with the others."""

import json

import pytest

from fabric_desktop import cli
from fabric_desktop.local_schedules import LocalSchedules, task_xml
from fabric_desktop.service import Service
from tests.test_pipeline_runs import act, pipeline, wait
from tests.test_schedules import FakeScheduler

DAILY = {"kind": "daily", "times": ["06:00"]}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(Service, "pc_schedules", property(lambda self: LocalSchedules(self.root, fake, "C:/engine/python.exe")))
    s = Service(root=tmp_path / "app")
    s.fake_scheduler = fake
    return s


def folder(tmp_path, text, name="pl_daily"):
    path = tmp_path / "repo" / f"{name}.DataPipeline"
    path.mkdir(parents=True, exist_ok=True)
    (path / "pipeline-content.json").write_text(text, encoding="utf-8")
    return str(path)


def copy_then_mark(**extra):
    return pipeline(act("Ingest", "Copy", source={}, sink={}), wait("Mark", depends=["Ingest"]), name="Orders daily",
                    **extra)


def test_a_pipeline_is_scheduled_only_when_it_can_run_here(svc, tmp_path):
    path = folder(tmp_path, copy_then_mark())
    # No one is there to give a mock when the schedule fires, so it is asked for now.
    with pytest.raises(ValueError, match="can't run on this PC with these settings.*Ingest"):
        svc.add_pc_schedule({"kind": "pipeline", "path": path}, DAILY)
    added = svc.add_pc_schedule({"kind": "pipeline", "path": path, "mocks": {"Ingest": {"output": {"rowsCopied": 5}}}},
                                DAILY)["schedule"]
    assert added["target"] == {"kind": "pipeline", "path": path, "name": "Orders daily", "parameters": {},
                               "mocks": {"Ingest": {"output": {"rowsCopied": 5}}}, "notebooks": {},
                               "sql_projects": {}, "read_lakehouses": False, "workspace_id": ""}
    [row] = svc.pc_schedules_for("pipeline", path=path)
    assert row["id"] == added["id"] and svc.pc_schedules_for("pipeline", path=path + "x") == []
    assert "KratosForge: Pipeline: Orders daily" in task_xml(added, "p", "w")
    with pytest.raises(ValueError, match="Could not read the pipeline"):
        svc.add_pc_schedule({"kind": "pipeline", "path": str(tmp_path / "nope.DataPipeline")}, DAILY)


def test_a_schedule_never_keeps_a_secret_parameter(svc, tmp_path):
    path = folder(tmp_path, pipeline(wait("W"), parameters={"key": {"type": "SecureString", "defaultValue": "d"},
                                                             "region": {"type": "String"}}))
    with pytest.raises(ValueError, match="key is a SecureString"):
        svc.add_pc_schedule({"kind": "pipeline", "path": path, "parameters": {"key": "s3cret"}}, DAILY)
    added = svc.add_pc_schedule({"kind": "pipeline", "path": path, "parameters": {"region": "emea"}}, DAILY)
    assert "s3cret" not in json.dumps(svc.pc_schedules.listing()) and added["schedule"]["target"]["parameters"] == \
        {"region": "emea"}


def test_a_scheduled_run_runs_the_pipeline_and_is_kept_with_the_local_runs(svc, tmp_path):
    path = folder(tmp_path, copy_then_mark())
    sid = svc.add_pc_schedule({"kind": "pipeline", "path": path, "mocks": {"Ingest": {"output": {}}}},
                              DAILY)["schedule"]["id"]
    row = svc.run_schedule(sid)
    assert row["status"] == "ok" and row["label"] == "Pipeline: Orders daily", row
    assert row["message"] == "Orders daily succeeded: 2 activities, 1 mocked"
    assert svc.pipeline_runs()["runs"][0]["run_id"] == row["run_id"]
    assert svc.pc_schedule_runs()[0]["run_id"] == row["run_id"]


def test_a_failed_run_says_which_activity_failed(svc, tmp_path):
    path = folder(tmp_path, pipeline(act("Stop here", "Fail", message="No files today", errorCode="E1"), name="p"))
    sid = svc.add_pc_schedule({"kind": "pipeline", "path": path}, DAILY)["schedule"]["id"]
    row = svc.run_schedule(sid)
    assert row["status"] == "failed" and row["message"] == "p failed: 1 activity. Stop here: No files today", row


def test_a_pipeline_changed_since_scheduling_is_refused_before_it_runs(svc, tmp_path):
    path = folder(tmp_path, pipeline(wait("W")))
    sid = svc.add_pc_schedule({"kind": "pipeline", "path": path}, DAILY)["schedule"]["id"]
    folder(tmp_path, copy_then_mark())   # pulled from Git: now has a step that needs a mock
    row = svc.run_schedule(sid)
    assert row["status"] == "failed" and row["message"].startswith("The pipeline can't run on this PC now:"), row
    assert "run_id" not in row and svc.pipeline_runs()["runs"] == []


def test_the_cli_schedules_a_pipeline_folder(svc, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Service", lambda: svc)
    path = folder(tmp_path, copy_then_mark())
    mocks = tmp_path / "mocks.json"
    mocks.write_text(json.dumps({"Ingest": {"output": {}}}), encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        cli.main(["pipeline", "schedule", path, "--at", "06:00"])
    assert refused.value.code == 2 and "Ingest" in capsys.readouterr().out
    with pytest.raises(SystemExit) as done:
        cli.main(["pipeline", "schedule", path, "--at", "06:00", "--weekday", "Monday", "--mocks", str(mocks)])
    assert done.value.code == 0 and "Scheduled Orders daily on this PC" in capsys.readouterr().out
    [row] = svc.pc_schedules_for("pipeline", path=path)
    assert row["config"]["type"] == "Weekly" and row["config"]["weekdays"] == ["Monday"]


def test_the_cli_refuses_a_setting_without_a_value(svc, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Service", lambda: svc)
    path = folder(tmp_path, pipeline(wait("W")))
    with pytest.raises(SystemExit) as refused:
        cli.main(["pipeline", "schedule", path, "--at", "06:00", "--param", "region"])
    assert refused.value.code == 2 and "--param takes NAME=VALUE" in capsys.readouterr().out
    assert svc.pc_schedules_for("pipeline", path=path) == []
