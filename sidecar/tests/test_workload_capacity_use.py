"""A workload's capacity use (cost allocation, plan item 46 follow-up on FinOps #153): measured where
an imported usage row matched the run, else estimated, else unknown, never made up."""

import datetime as dt

from fabric_desktop import measurement as ms
from tests.test_workloads import project, scan, signed_in, svc  # noqa: F401 - the fixtures are used by name


def ago(days):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")


def fabric_run(job, days):
    return {"job_id": job, "where": "fabric", "started_at": ago(days), "state": "succeeded"}


def cu(cls, job, seconds):
    extra = {"assumptions": ["test price"]} if cls == ms.ESTIMATED else {"collected_at": ago(0)}
    return ms.make(cls, "fabric_job_cu", ms.CU_SECONDS, seconds, run_id=job, method="test", source="test", **extra)


def use(svc, monkeypatch, runs, measured=(), estimated=(), problem=""):  # noqa: F811
    monkeypatch.setattr(svc, "_wl_record", lambda id, refresh=False: {"id": id, "runs": runs})
    seen = []

    def risk_cu(rs, memo=None):
        seen.append([r["job_id"] for r in rs])
        return list(measured), list(estimated), problem
    monkeypatch.setattr(svc, "_risk_cu", risk_cu)
    return svc.workload_capacity_use("fabric:ws1:pl1"), seen


def test_measured_runs_count_as_measured_and_the_rest_by_their_estimate(svc, monkeypatch):  # noqa: F811
    runs = [fabric_run("j1", 1), fabric_run("j2", 2), fabric_run("j3", 3),
            {"where": "this_pc", "started_at": ago(1), "state": "succeeded"}]
    out, seen = use(svc, monkeypatch, runs, measured=[cu(ms.MEASURED, "j1", 7200)], estimated=[
        cu(ms.ESTIMATED, "j1", 3600), cu(ms.ESTIMATED, "j2", 3600)])
    assert seen == [["j1", "j2", "j3"]]
    assert out["fabric_runs"] == 3 and out["pc_runs"] == 1 and out["measured_runs"] == 1
    assert out["measured"]["classification"] == ms.MEASURED and out["measured"]["value"] == 2.0  # CU hours
    total = out["total"]
    assert total["classification"] == ms.ESTIMATED and total["value"] == 3.0  # j1 measured + j2 estimated
    assert total["complete"] is False and total["unknown_parts"] == 1  # j3: no figure, not counted as 0


def test_runs_older_than_the_period_and_runs_on_this_pc_add_nothing(svc, monkeypatch):  # noqa: F811
    out, seen = use(svc, monkeypatch, [fabric_run("old", 45), {"where": "this_pc", "started_at": ago(2)}])
    assert seen == [[]] and out["fabric_runs"] == 0 and out["pc_runs"] == 1
    assert out["total"] is None and out["measured"] is None


def test_unreadable_imports_are_said_and_the_estimate_still_shows(svc, monkeypatch):  # noqa: F811
    out, _ = use(svc, monkeypatch, [fabric_run("j1", 1)], estimated=[cu(ms.ESTIMATED, "j1", 1800)],
                 problem="Imported usage couldn't be read (locked), so nothing is measured.")
    assert out["measured"] is None and out["total"]["value"] == 0.5 and "couldn't be read" in out["problem"]


def test_it_is_read_only_and_registered():
    from fabric_desktop import audit, server
    assert "workload_capacity_use" in audit.READ_ONLY and "workload_capacity_use" in server.METHODS
