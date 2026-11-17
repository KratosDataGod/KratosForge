"""workspace_copy_jobs_service.py path that mutation testing (batch 72, docs/quality/mutation-testing.md)
showed no test pinned: a copy job's runs list stops at the newest 50."""

from __future__ import annotations

import pytest

from fabric_desktop import workspace_copy_jobs_service as cjs
from fabric_desktop.service import Service

WS = "11111111-2222-3333-4444-555555555555"
CJ = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class Fabric:
    def __init__(self, n):
        self.jobs = [{"id": f"j{i:03d}", "status": "Completed", "startTimeUtc": "2026-10-24T06:00:00Z",
                      "endTimeUtc": "2026-10-24T06:00:30Z"} for i in range(n)]

    def list_job_instances(self, ws, item):
        return self.jobs


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def make(n):
        fake = Fabric(n)
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        return Service(root=tmp_path / "app")
    return make


def test_fifty_runs_are_listed():
    assert cjs.RUNS == 50


@pytest.mark.parametrize("n, listed", [(49, 49), (50, 50), (51, 50), (80, 50)])
def test_the_runs_list_stops_at_the_newest_fifty(svc, n, listed):
    runs = svc(n).fabric_copy_job_runs(WS, CJ)["runs"]
    assert [r["job_id"] for r in runs] == [f"j{i:03d}" for i in range(listed)]
