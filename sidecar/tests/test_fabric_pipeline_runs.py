"""Fabric pipeline runs for Monitor > Runs (Data Factory DF-1): job history and activity runs, reads only."""

import time

import pytest

from fabric_desktop import fabric_pipeline_runs_service as fpr
from fabric_desktop import schedule_service
from fabric_desktop.fabric_api import FabricClient
from fabric_desktop.service import Service

WS = "11111111-2222-3333-4444-555555555555"
JOB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"


class FakeFabric:
    calls: list = []

    def __init__(self, token):
        pass

    def list_workspaces(self):
        return [{"id": "ws1", "displayName": "Sales Dev"}, {"id": "ws2", "displayName": "Other"}]

    def list_items(self, ws):
        return {"ws1": [{"id": "pl1", "type": "DataPipeline", "displayName": "pl_customer_load"},
                        {"id": "nb1", "type": "Notebook", "displayName": "Load"}],
                "ws2": [{"id": "pl2", "type": "DataPipeline", "displayName": "pl_other"}]}[ws]

    def list_job_instances(self, ws, item):
        FakeFabric.calls.append(("runs", item))
        if item == "pl2":
            return [{"id": "j3", "status": "Completed", "startTimeUtc": "2026-10-24T07:00:00", "endTimeUtc": "2026-10-24T07:01:00"}]
        return [{"id": "j2", "status": "Failed", "invokeType": "Scheduled", "startTimeUtc": "2026-10-24T06:00:00.1234567Z",
                 "endTimeUtc": "2026-10-24T06:04:12.1234567Z",
                 "failureReason": {"message": f"Login failed with Bearer {TOKEN}"}},
                {"id": "j1", "status": "InProgress", "startTimeUtc": "2026-10-23T06:00:00Z", "endTimeUtc": None}]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    FakeFabric.calls = []
    for mod in (fpr, schedule_service):
        monkeypatch.setattr(mod, "FabricClient", FakeFabric)
    monkeypatch.setattr(Service, "auth", property(lambda self: type("A", (), {"fabric_token": lambda _s: "tok"})()))
    return Service(root=tmp_path / "app")


def _settled(svc, **kw):
    out = svc.fabric_pipeline_runs(**kw)
    for _ in range(200):
        if not out["pending"]:
            return out
        time.sleep(0.02)
        out = svc.fabric_pipeline_runs(**{k: v for k, v in kw.items() if k != "refresh"})
    raise AssertionError("scan never finished")


def test_runs_of_the_pinned_workspaces_pipelines_newest_first_with_secrets_removed(svc):
    svc.set_workspace_mark("ws1", "pins", True)
    out = _settled(svc)
    assert [r["job_id"] for r in out["runs"]] == ["j2", "j1"]  # only ws1, and only its pipeline
    failed, running = out["runs"]
    assert (failed["name"], failed["state"], failed["native_status"], failed["invoke"]) == (
        "pl_customer_load", "failed", "Failed", "Scheduled")
    assert failed["seconds"] == 252.0 and failed["workspace_name"] == "Sales Dev" and failed["where"] == "fabric"
    assert TOKEN not in failed["message"] and "[removed]" in failed["message"]
    assert running["state"] == "running" and running["seconds"] is None
    assert (out["workspaces"], out["pipelines"], out["missed"]) == (1, 1, 0)


def test_chosen_workspaces_and_all_workspaces(svc):
    assert [r["job_id"] for r in _settled(svc, workspace_ids=["ws2"])["runs"]] == ["j3"]
    assert [r["job_id"] for r in _settled(svc, all_workspaces=True)["runs"]] == ["j3", "j2", "j1"]


def test_the_scan_is_cached_until_refresh(svc):
    _settled(svc, all_workspaces=True)
    before = len(FakeFabric.calls)
    assert not svc.fabric_pipeline_runs(all_workspaces=True)["pending"]
    assert len(FakeFabric.calls) == before  # served from the cache
    _settled(svc, all_workspaces=True, refresh=True)
    assert len(FakeFabric.calls) == before + 2


def test_a_failed_pipeline_read_is_counted_not_raised(svc, monkeypatch):
    def broken(self, ws, item):
        raise RuntimeError("403")
    monkeypatch.setattr(FakeFabric, "list_job_instances", broken)
    out = _settled(svc, all_workspaces=True)
    assert out["runs"] == [] and out["missed"] == 2 and "error" not in out


def test_signed_out_says_so(svc, monkeypatch):
    def no_token(_s):
        raise RuntimeError("Sign in first")
    monkeypatch.setattr(Service, "auth", property(lambda self: type("A", (), {"fabric_token": no_token})()))
    out = svc.fabric_pipeline_runs()
    assert out["pending"] is False and "Sign in first" in out["error"]


class FakeSession:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def request(self, method, url, **kw):
        self.sent.append((method, url, kw))
        reply = self.reply

        class R:
            status_code = 200
            ok = True
            headers: dict = {}

            def json(self):
                return reply
        return R()


ACTIVITIES = [
    {"pipelineRunId": "j2", "activityName": "Copy customers", "activityType": "Copy", "activityRunId": "a2",
     "status": "Failed", "activityRunStart": "2026-10-24T06:00:14Z", "activityRunEnd": "2026-10-24T06:03:55Z",
     "durationInMs": 221000, "input": {"source": {"connectionString": "Server=x;Password=hunter2", "sas": "?sig=abc123"}},
     "output": {"rowsRead": 0}, "retryAttempt": None,
     "error": {"errorCode": "SqlFailedToConnect", "message": "Cannot connect", "failureType": "UserError"}},
    {"pipelineRunId": "j2", "activityName": "Get watermark", "activityType": "Lookup", "activityRunId": "a1",
     "status": "Succeeded", "activityRunStart": "2026-10-24T06:00:00Z", "activityRunEnd": "2026-10-24T06:00:14Z",
     "durationInMs": 14000, "input": {}, "output": {"firstRow": {"wm": "x" * 20000}},
     "error": {"errorCode": "", "message": "", "failureType": "", "target": "Get watermark"}},
]


def test_activity_runs_query_one_run_with_a_window_around_it():
    session = FakeSession(ACTIVITIES)
    client = FabricClient(lambda: "tok", session=session)
    out = client.query_activity_runs("ws1", "j2", "2026-10-24T05:55:00.000Z", "2026-10-24T06:09:12.000Z")
    [(method, url, kw)] = session.sent
    assert method == "POST" and url.endswith("/workspaces/ws1/datapipelines/pipelineruns/j2/queryactivityruns")
    assert kw["json"]["lastUpdatedAfter"] == "2026-10-24T05:55:00.000Z" and kw["json"]["filters"] == []
    assert len(out) == 2


def test_activity_rows_are_shaped_capped_and_redacted(svc, monkeypatch):
    seen = {}

    class Client:
        def query_activity_runs(self, ws, job, after, before):
            seen.update(ws=ws, job=job, after=after, before=before)
            return ACTIVITIES
    monkeypatch.setattr(Service, "fabric", property(lambda self: Client()))
    out = svc.fabric_pipeline_activity_runs(WS, JOB, "2026-10-24T06:00:00.1234567Z", "2026-10-24T06:04:12Z")
    assert seen == {"ws": WS, "job": JOB, "after": "2026-10-24T05:55:00.123Z", "before": "2026-10-24T06:09:12.000Z"}
    copy, lookup = out["activities"]
    assert (copy["name"], copy["type"], copy["state"], copy["ms"]) == ("Copy customers", "Copy", "failed", 221000)
    assert copy["error"] == {"code": "SqlFailedToConnect", "message": "Cannot connect", "failure_type": "UserError"}
    assert "abc123" not in copy["input"]["text"]
    assert lookup["state"] == "succeeded" and lookup["error"] is None and lookup["input"]["text"] == ""
    assert lookup["output"]["truncated"] and len(lookup["output"]["text"]) == fpr.TEXT_LIMIT


def test_activity_runs_need_a_run_with_guids(svc):
    for ws, job in ((WS, ""), ("", JOB), ("../ws", JOB), (WS, "j2/../../items"), (WS + "\n", JOB)):
        with pytest.raises(ValueError):
            svc.fabric_pipeline_activity_runs(ws, job)


def test_times_keep_their_offset_and_seven_digit_fractions():
    assert fpr._parse("2026-10-24T11:00:00.1234567+05:00") == fpr._parse("2026-10-24T06:00:00.123456Z")
    assert fpr._seconds("2026-10-24T06:00:00Z", "2026-10-24T06:00:01.5Z") == 1.5
    assert fpr._parse("not a time") is None and fpr._parse(None) is None


def test_both_calls_are_read_only_and_not_quick():
    from fabric_desktop import audit, server
    for name in ("fabric_pipeline_runs", "fabric_pipeline_activity_runs"):
        assert name in server.METHODS and name in audit.READ_ONLY and name not in server.QUICK
