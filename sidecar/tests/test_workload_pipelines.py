"""Local pipelines in Monitor > Operations > Workloads (Preview): each pipeline this PC opened or
schedules is a workload, with health from its local runs, where a mocked success needs attention."""

import pytest

from fabric_desktop.workloads import model
from tests.test_pipeline_runs import act, pipeline, wait
from tests.test_pipeline_schedules import DAILY, copy_then_mark, folder, svc  # noqa: F401  (svc is a fixture)


def rows(svc):
    return {r["name"]: r for r in svc.workloads_list(view="all", limit=500)["rows"]}


def listed(svc, monkeypatch, *paths_and_names):
    pipelines = [{"path": p, "name": n, "repo": "repo", "relative": n + ".DataPipeline", "activity_count": 2}
                 for p, n in paths_and_names]
    monkeypatch.setattr(svc, "pipeline_list", lambda: {"pipelines": pipelines, "truncated": False})


def test_a_scheduled_pipeline_is_a_preview_workload_in_its_own_group(svc, tmp_path):
    path = folder(tmp_path, copy_then_mark())
    sid = svc.add_pc_schedule({"kind": "pipeline", "path": path, "mocks": {"Ingest": {"output": {}}}}, DAILY)["schedule"]["id"]
    page = svc.workloads_list(view="all", limit=500)
    row = rows(svc)["Orders daily"]
    assert (row["kind"], row["kind_label"], row["preview"], row["health"]) == ("pipeline", "Pipeline", True, "idle")
    assert row["group"] == {"type": "pipelines", "id": "pipelines", "label": "Local pipelines"}
    assert row["deployments"][0]["actions"] == ["run_schedule"]
    assert any(s["source"] == "Pipelines on this PC" and s["state"] == "live" for s in page["sources"])
    w = svc.workload_detail(model.pipeline_id(path))
    assert [a["schedule_id"] for a in w["available_actions"] if a["action"] == "run_schedule"] == [sid]


def test_a_success_with_mocks_needs_attention_and_never_reads_healthy(svc, tmp_path):
    path = folder(tmp_path, copy_then_mark())
    sid = svc.add_pc_schedule({"kind": "pipeline", "path": path, "mocks": {"Ingest": {"output": {}}}}, DAILY)["schedule"]["id"]
    ran = svc.run_schedule(sid)
    row = rows(svc)["Orders daily"]
    assert row["health"] == "attention" and row["health_label"] == "Needs attention"
    assert row["last_run"]["state"] == "ok" and row["last_run"]["mocked"] == 1
    assert row["last_run"]["open"] == {"kind": "pipeline_run", "run_id": ran["run_id"]}
    assert row["headline"]["title"] == "Last run in This PC used mocks"


def test_a_real_success_is_healthy_and_a_failure_is_failing_with_the_failed_activity(svc, tmp_path):
    ok = folder(tmp_path, pipeline(wait("W"), name="Just wait"), name="pl_wait")
    bad = folder(tmp_path, pipeline(act("Stop here", "Fail", message="No files today", errorCode="E1"), name="p"),
                 name="pl_fail")
    for path in (ok, bad):
        svc.run_schedule(svc.add_pc_schedule({"kind": "pipeline", "path": path}, DAILY)["schedule"]["id"])
    found = rows(svc)
    assert found["Just wait"]["health"] == "healthy"
    assert found["p"]["health"] == "failing"
    assert "Stop here" in found["p"]["headline"]["text"] and "No files today" in found["p"]["headline"]["text"]


def test_runs_from_the_pipelines_page_count_by_name_but_a_shared_name_never_guesses(svc, tmp_path, monkeypatch):
    one = folder(tmp_path, pipeline(wait("W"), name="Same"), name="a")
    two = folder(tmp_path, pipeline(wait("W"), name="Other"), name="b")
    listed(svc, monkeypatch, (one, "Same"), (two, "Other"))
    started = svc.pipeline_run_start(path=one, wait_scale=0)
    while svc.pipeline_run_status(started["run_id"], values=False)["state"] in ("pending", "running"):
        pass
    assert rows(svc)["Same"]["health"] == "healthy"
    assert rows(svc)["Other"]["health"] == "idle"
    # Two folders, one name: neither takes a run its own schedule didn't start.
    listed(svc, monkeypatch, (one, "Same"), (two, "Same"))
    page = svc.workloads_list(view="all", limit=500)["rows"]
    assert sorted(r["health"] for r in page if r["name"] == "Same") == ["idle", "idle"]


def test_an_unscheduled_pipeline_has_no_run_action_and_the_owner_never_comes_from_a_run(svc, tmp_path, monkeypatch):
    path = folder(tmp_path, pipeline(wait("W"), name="Loose"), name="loose")
    listed(svc, monkeypatch, (path, "Loose"))
    svc.pipeline_run_start(path=path, wait_scale=0)
    row = rows(svc)["Loose"]
    assert row["deployments"][0]["actions"] == [] and row["unowned"] is True
    pre = svc.workload_preflight(model.pipeline_id(path), "run_schedule")
    assert pre["allowed"] is False


def test_run_the_schedule_now_says_what_it_does_and_goes_through_the_schedule(svc, tmp_path, monkeypatch):
    path = folder(tmp_path, copy_then_mark())
    sid = svc.add_pc_schedule({"kind": "pipeline", "path": path, "mocks": {"Ingest": {"output": {}}}}, DAILY)["schedule"]["id"]
    wid = model.pipeline_id(path)
    pre = svc.workload_preflight(wid, "run_schedule", assess=False)
    assert pre["allowed"] and pre["target"] == "the pipeline Orders daily on this PC" and pre["where"] == "This PC"
    assert "starts no Fabric job" in pre["consequence"] and "capacity" in pre["consequence"]
    started = []
    monkeypatch.setattr(svc, "run_pc_schedule_now", lambda schedule_id: started.append(schedule_id) or {"started": schedule_id})
    out = svc.workload_act(wid, "run_schedule", schedule_id=sid)
    assert started == [sid] and out["action"]["status"] == "submitted"


def test_a_pipeline_listing_that_fails_hides_nothing_else(svc, monkeypatch):
    svc.notebooks.save("Daily load", [{"type": "code", "source": "x = 1"}])
    monkeypatch.setattr(svc, "pipeline_list", lambda: (_ for _ in ()).throw(OSError("disk gone")))
    page = svc.workloads_list(view="all", limit=500)
    assert [r["name"] for r in page["rows"]] == ["Daily load"]
    src = next(s for s in page["sources"] if s["source"] == "Pipelines on this PC")
    assert src["state"] == "error" and "disk gone" in src["detail"]


@pytest.mark.parametrize("path", [r"C:\Users\chris\repo\pl_daily.DataPipeline", "/home/c/repo/pl.DataPipeline"])
def test_pipeline_ids_round_trip(path):
    assert model.parse_id(model.pipeline_id(path)) == {"kind": "pipeline", "path": path}


def test_run_the_schedule_now_refuses_a_schedule_of_another_workload(svc, tmp_path, monkeypatch):
    mine = folder(tmp_path, copy_then_mark(), name="mine")
    other = folder(tmp_path, pipeline(wait("W"), name="Other"), name="other")
    svc.add_pc_schedule({"kind": "pipeline", "path": mine, "mocks": {"Ingest": {"output": {}}}}, DAILY)
    theirs = svc.add_pc_schedule({"kind": "pipeline", "path": other}, DAILY)["schedule"]["id"]
    started = []
    monkeypatch.setattr(svc, "run_pc_schedule_now", lambda schedule_id: started.append(schedule_id))
    with pytest.raises(PermissionError, match="doesn't run Orders daily"):
        svc.workload_act(model.pipeline_id(mine), "run_schedule", schedule_id=theirs)
    assert started == []
    [action] = svc.workload_actions(model.pipeline_id(mine))
    assert action["status"] == "failed"
