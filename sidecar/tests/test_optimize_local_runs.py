"""This PC's notebook work, shared with Optimize as fdopt_local_execution rows."""

import json

import pytest
from test_optimize_service import svc  # noqa: F401 - the fixture

from fabric_desktop.optimize import local_runs

def share(svc):
    """Share the way the app does: ask, then confirm with what the ask showed."""
    try:
        return svc.optimize_share_local_runs()
    except PermissionError as asked:
        return svc.optimize_share_local_runs(approve=True, confirm=asked.would["confirm"])  # type: ignore[attr-defined]


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


LOG = {"run_id": "r1", "batch_id": "b1", "entity": "silver.orders", "env": "dev", "status": "ok",
       "started_at": "2026-10-20T07:00:00+00:00", "seconds": 4.5, "rows_read": 300, "rows_written": 250}
PROOF = {"id": "p1", "name": "daily_load", "state": "done", "started_at": "2026-10-20T08:00:00+00:00",
         "finished_at": "2026-10-20T08:05:00+00:00",
         "pc": {"status": "ok", "seconds": 12.0, "cpu_seconds": 9.5, "peak_memory_mb": 512.0,
                "measured": "the notebook's own kernel process on this PC"}}


def test_metadata_runs_on_this_pc_become_rows():
    fabric = dict(LOG, run_id="r2", batch_id="fabric-123")
    agent = dict(LOG, run_id="r3", worker="agent-1", host="srv")
    waiting = dict(LOG, run_id="r4", status="needs_decision")
    failed = dict(LOG, run_id="r5", status="failed", rows_written=0)
    rows = local_runs.from_run_log([LOG, fabric, agent, waiting, failed], "crm", "inst")
    assert [r["result"] for r in rows] == ["succeeded", "failed"]  # Fabric's, an agent's and a waiting run are left out
    run = rows[0]
    assert run["project"] == "crm.silver.orders" and run["workload_type"] == "metadata_run"
    assert run["started_at"] == "2026-10-20T07:00:00.000000+00:00"
    assert run["finished_at"] == "2026-10-20T07:00:04.500000+00:00" and run["seconds"] == 4.5 and run["rows"] == 250
    assert run["cpu_seconds"] is None and run["peak_memory_bytes"] is None  # not measured: empty, never 0
    assert local_runs.from_run_log([dict(LOG, env="test")], "crm", "inst")[0]["execution_id"] != run["execution_id"]


def test_proof_runs_carry_cpu_and_memory_only_where_measured():
    [run] = local_runs.from_proofs([PROOF], "inst")
    assert run["workload_type"] == "proof_run" and run["project"] == "daily_load" and run["seconds"] == 12.0
    assert run["cpu_seconds"] == 9.5 and run["peak_memory_bytes"] == 512 * 1024 ** 2
    assert run["started_at"] is None and run["finished_at"] == "2026-10-20T08:05:00.000000+00:00"
    unmeasured = dict(PROOF, id="p2", pc={"status": "ok", "seconds": 3.0, "cpu_seconds": 0, "peak_memory_mb": None})
    going = dict(PROOF, id="p3", state="pc", finished_at=None)
    cancelled = dict(PROOF, id="p4", state="cancelled", pc=dict(PROOF["pc"], status="cancelled"))
    rows = local_runs.from_proofs([unmeasured, going, cancelled], "inst")
    assert [(r["cpu_seconds"], r["peak_memory_bytes"], r["result"]) for r in rows] == [
        (None, None, "succeeded"), (9.5, 512 * 1024 ** 2, "cancelled")]


def test_sharing_adds_metadata_runs_kept_on_this_pc_and_proofs(svc, tmp_path):  # noqa: F811
    svc.meta_init(str(tmp_path / "repo" / "crm"), "3")
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6  # Dev, kept on this PC
    (svc.root / "proofs.jsonl").write_text(json.dumps(PROOF) + "\n", encoding="utf-8")
    svc.optimize_create("w1", workspace_name="Platform")
    with pytest.raises(PermissionError, match=r"Adds 7 runs.*6 metadata runs, 1 proof run.*CPU time and peak memory") as asked:
        svc.optimize_share_local_runs()
    assert asked.value.would["metadata_runs"] == 6 and asked.value.would["proof_runs"] == 1
    assert share(svc)["shared"] == 7
    rows = svc._optimize_shared().rows("local_execution")
    assert sorted(set(rows["project"].to_list()))[0].startswith("crm.")
    proof = rows.filter(rows["workload_type"] == "proof_run").row(0, named=True)
    assert proof["cpu_seconds"] == 9.5 and proof["peak_memory_bytes"] == 512 * 1024 ** 2
    assert svc.optimize_share_local_runs()["new"] == 0  # each once


def test_a_damaged_project_or_proof_leaves_only_that_out(svc, tmp_path):  # noqa: F811
    svc.meta_init(str(tmp_path / "repo" / "crm"), "3")
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    (tmp_path / "repo" / "crm" / "layers.toml").write_text("this isn't [ TOML", encoding="utf-8")
    (svc.root / "proofs.jsonl").write_text("{not json\n" + json.dumps(PROOF) + "\n", encoding="utf-8")
    (svc.root / "history.jsonl").write_text(json.dumps(WRITE) + "\n", encoding="utf-8")
    svc.optimize_create("w1", workspace_name="Platform")
    with pytest.raises(PermissionError) as asked:
        svc.optimize_share_local_runs()
    would = asked.value.would
    assert (would["notebook_writes"], would["metadata_runs"], would["proof_runs"]) == (1, 0, 1)


def test_a_run_that_lands_after_the_ask_isnt_shared_without_a_new_confirm(svc):  # noqa: F811
    svc.optimize_create("w1", workspace_name="Platform")
    (svc.root / "history.jsonl").write_text(json.dumps(WRITE) + "\n", encoding="utf-8")
    with pytest.raises(PermissionError) as asked:
        svc.optimize_share_local_runs()
    seen = asked.value.would["confirm"]
    svc.pc_schedules.log_run(RUN)
    svc.notebooks.save("daily_load", [{"type": "code", "source": "print(1)"}])
    with pytest.raises(PermissionError, match="changed since you looked.*Adds 2 runs") as again:
        svc.optimize_share_local_runs(approve=True, confirm=seen)
    assert svc._optimize_shared().local_execution_ids() == set()  # nothing written
    with pytest.raises(PermissionError, match="^Adds"):
        svc.optimize_share_local_runs(approve=True)  # approving without what was shown asks too
    out = svc.optimize_share_local_runs(approve=True, confirm=again.value.would["confirm"])
    assert out["shared"] == 2


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
    out = share(svc)
    assert out["shared"] == 2 and out["notebook_runs"] == 1
    assert svc.optimize_share_local_runs() == {"new": 0, "notebook_writes": 0, "notebook_runs": 0, "metadata_runs": 0,
                                               "proof_runs": 0, "already": 2, "waiting": 0, "shared": 0}
    rows = svc._optimize_shared().rows("local_execution")
    assert sorted(rows["workload_type"].to_list()) == ["notebook_run", "notebook_write"]
