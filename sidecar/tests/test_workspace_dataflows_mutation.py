"""workspace_dataflows_service paths that mutation testing (batch 71, docs/quality/mutation-testing.md)
showed no test pinned: how many refreshes are listed."""

from __future__ import annotations

import pytest

from fabric_desktop.service import Service

WS = "11111111-2222-3333-4444-555555555555"
DF = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeFabric:
    def __init__(self, count):
        self.count = count

    def list_job_instances(self, ws, item):
        return [{"id": f"j{i}", "status": "Completed", "startTimeUtc": "2026-10-24T06:00:00Z",
                 "endTimeUtc": "2026-10-24T06:01:00Z"} for i in range(self.count)]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def make(count):
        fake = FakeFabric(count)
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        return Service(root=tmp_path / "app")

    return make


@pytest.mark.parametrize("count, listed", [(49, 49), (50, 50), (51, 50), (52, 50)])
def test_the_newest_fifty_refreshes_are_listed(svc, count, listed):
    out = svc(count).fabric_dataflow_refreshes(WS, DF)
    assert [r["job_id"] for r in out["refreshes"]] == [f"j{i}" for i in range(listed)]
