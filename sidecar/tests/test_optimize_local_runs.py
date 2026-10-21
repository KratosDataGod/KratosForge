"""This PC's notebook work, shared with Optimize as fdopt_local_execution rows."""

import json

import pytest
from test_optimize_service import svc  # noqa: F401 - the fixture

from fabric_desktop.optimize import local_runs

URI = "abfss://w1@onelake.dfs.fabric.microsoft.com/lh-sales/Tables/dbo/orders"
WRITE = {"job": "Notebook write to Sales", "table_uri": URI, "mode": "append", "rows": 120, "delta_version": 4,
         "seconds": 2.5, "finished_at": "2026-10-20T09:00:00Z"}
RUN = {"schedule_id": "s1", "label": "daily_load", "trigger": "schedule", "started": "2026-10-20T06:00:00+00:00",
       "ended": "2026-10-20T06:01:30+00:00", "seconds": 90.0, "status": "ok"}


def test_notebook_writes_and_scheduled_runs_become_rows():
    publish = {"job": "Publish orders.csv", "table_uri": URI, "finished_at": "2026-10-20T09:00:00Z"}
    [write] = local_runs.from_history([WRITE, publish], "inst")
    assert write["artifact_key"] == "w1/lh-sales" and write["project"] == "dbo.orders"
    assert write["finished_at"] == "2026-10-20T09:00:00.000000+00:00" and write["rows"] == 120
    assert write["workload_type"] == "notebook_write" and write["result"] == "succeeded"
    meta = dict(RUN, schedule_id="s2", label="Dev metadata: every table")
    running = dict(RUN, schedule_id="s3", ended=None)
    [run] = local_runs.from_schedule_runs([RUN, meta, running], {"daily_load"}, "inst")
    assert run["project"] == "daily_load" and run["artifact_key"] is None and run["seconds"] == 90.0
    assert run["result"] == "succeeded"
    # the same work gives the same id, so sharing again adds nothing; another PC's work doesn't collide
    assert local_runs.from_history([WRITE], "inst")[0]["execution_id"] == write["execution_id"]
    assert local_runs.from_history([WRITE], "other")[0]["execution_id"] != write["execution_id"]


def test_sharing_asks_first_and_adds_each_run_once(svc):  # noqa: F811
    with pytest.raises(ValueError, match="Connect this PC"):
        svc.optimize_share_local_runs()
    svc.optimize_create("w1", workspace_name="Platform")
    (svc.root / "history.jsonl").write_text(json.dumps(WRITE) + "\n", encoding="utf-8")
    svc.pc_schedules.log_run(RUN)
    svc.notebooks.save("daily_load", [{"type": "code", "source": "print(1)"}])
    with pytest.raises(PermissionError, match="Adds 2 runs from this PC.*No code or data.*Confirm") as asked:
        svc.optimize_share_local_runs()
    assert asked.value.would["notebook_writes"] == 1
    assert svc._optimize_shared().local_execution_ids() == set()  # asking wrote nothing
    out = svc.optimize_share_local_runs(approve=True)
    assert out["shared"] == 2 and out["notebook_runs"] == 1
    assert svc.optimize_share_local_runs() == {"new": 0, "notebook_writes": 0, "notebook_runs": 0, "already": 2,
                                               "shared": 0}
    rows = svc._optimize_shared().rows("local_execution")
    assert sorted(rows["workload_type"].to_list()) == ["notebook_run", "notebook_write"]
