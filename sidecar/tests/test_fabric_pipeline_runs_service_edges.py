"""Fabric pipeline runs: the scan's failure edges the main tests don't reach. The same fake Fabric stands in."""

from fabric_desktop import fabric_pipeline_runs_service as fpr
from tests.test_fabric_pipeline_runs import FakeFabric, svc  # noqa: F401 - the fixture is used by name

SCOPE = {"all_workspaces": False, "workspace_ids": ["ws1", "ws2"]}


def test_a_refresh_while_the_same_scan_is_going_returns_the_pending_one(svc, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(fpr.threading, "Thread", lambda **kw: started.append(kw))
    svc._fabric_pipeline_runs = {"pending": True, "runs": [], "scope": SCOPE}
    out = svc.fabric_pipeline_runs(refresh=True, workspace_ids=["ws2", "ws1"])
    assert out["pending"] is True and started == []


def test_workspaces_that_cant_be_listed_end_the_scan_with_the_error(svc, monkeypatch):  # noqa: F811
    def refuse(self):
        raise RuntimeError("503 busy")

    monkeypatch.setattr(FakeFabric, "list_workspaces", refuse)
    svc._scan_fabric_pipeline_runs("tok", SCOPE)
    out = svc._fabric_pipeline_runs
    assert out["pending"] is False and out["runs"] == [] and "503 busy" in out["error"]


def test_a_workspace_whose_items_cant_be_listed_is_counted_as_missed(svc, monkeypatch):  # noqa: F811
    real = FakeFabric.list_items

    def some(self, ws):
        if ws == "ws2":
            raise RuntimeError("403 Forbidden")
        return real(self, ws)

    monkeypatch.setattr(FakeFabric, "list_items", some)
    warned = []
    monkeypatch.setattr(fpr._log, "warning", lambda *a, **k: warned.append(a))
    svc._scan_fabric_pipeline_runs("tok", SCOPE)
    out = svc._fabric_pipeline_runs
    assert out["missed"] == 1 and out["items"] == 1 and len(out["runs"]) == 2
    assert warned and "items in workspace ws2" in warned[0][2]


def test_a_newer_scope_wins_over_an_older_scans_answer(svc):  # noqa: F811
    svc._fabric_pipeline_runs = {"pending": True, "runs": [], "scope": {"all_workspaces": True, "workspace_ids": []}}
    svc._scan_fabric_pipeline_runs("tok", SCOPE)
    assert svc._fabric_pipeline_runs["pending"] is True  # the older scope's answer is dropped
