"""Fabric's job scheduler, the admin tenant scan and request errors (KUDP hardening): what the
Fabric client does when a job can't start, a poll never ends, Fabric refuses, or a retry would
repeat a write. No request reaches Fabric: every answer is a fake, and the organization's
allow_fabric_runs is set per test."""

from __future__ import annotations

import pytest

from fabric_desktop import app_settings
from fabric_desktop.fabric_api import API, POLICY_BLOCKED, POWERBI_ADMIN, FabricClient, FabricError
from fabric_desktop.retrying import RETRY_AFTER_CAP
from test_fabric_api import Resp


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def client(responses, poll_timeout=300):
    s = Session(responses)
    slept = []
    return FabricClient(lambda: "tok-secret", session=s, sleep=slept.append, poll_timeout=poll_timeout), s, slept


@pytest.fixture
def runs_allowed(monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: True if key == "allow_fabric_runs" else None)


@pytest.fixture
def runs_blocked(monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: False if key == "allow_fabric_runs" else None)


# ---------------------------------------------------------------- starting jobs


@pytest.mark.parametrize("start", [
    lambda c: c.run_item_job("ws1", "nb1", "RunNotebook"),
    lambda c: c.run_item_execute("ws1", "cj1", "CopyJob"),
    lambda c: c.run_table_maintenance("ws1", "lh1", "orders"),
    lambda c: c.create_schedule("ws1", "nb1", "RunNotebook", {"type": "Daily"}),
    lambda c: c.update_schedule("ws1", "nb1", "RunNotebook", "s1", True, {"type": "Daily"}),
])
def test_a_policy_that_keeps_work_off_fabric_stops_it_before_any_request(runs_blocked, start):
    c, s, _ = client([])
    with pytest.raises(FabricError) as exc:
        start(c)
    assert exc.value.code == POLICY_BLOCKED and s.calls == []


def test_turning_a_schedule_off_or_saving_it_off_is_allowed_under_that_policy(runs_blocked):
    c, s, _ = client([Resp(200, {"id": "s1"}), Resp(201, {"id": "s2"})])
    assert c.update_schedule("ws1", "nb1", "RunNotebook", "s1", False, {})["id"] == "s1"
    assert c.create_schedule("ws1", "nb1", "RunNotebook", {}, enabled=False)["id"] == "s2"
    assert [call["json"]["enabled"] for call in s.calls] == [False, False]


def test_a_job_gets_its_id_from_location_and_sends_its_type_and_data(runs_allowed):
    c, s, _ = client([Resp(202, headers={"Location": f"{API}/workspaces/ws1/items/nb1/jobs/instances/job-7/"})])
    assert c.run_item_job("ws1", "nb1", "RunNotebook", {"parameters": {"a": 1}}) == "job-7"
    call = s.calls[0]
    assert call["params"] == {"jobType": "RunNotebook"} and call["json"] == {"executionData": {"parameters": {"a": 1}}}


def test_an_execute_job_runs_at_its_own_items_path(runs_allowed):
    c, s, _ = client([Resp(202, headers={"Location": "x/jobs/execute/instances/j2"})])
    assert c.run_item_execute("ws1", "df1", "Dataflow") == "j2"
    assert s.calls[0]["url"] == f"{API}/workspaces/ws1/dataflows/df1/jobs/execute/instances"


@pytest.mark.parametrize("start, what", [
    (lambda c: c.run_item_job("ws1", "nb1", "RunNotebook"), "RunNotebook job"),
    (lambda c: c.run_item_execute("ws1", "cj1", "CopyJob"), "CopyJob run"),
])
def test_a_job_fabric_accepted_without_an_instance_says_so(runs_allowed, start, what):
    c, _, _ = client([Resp(202)])
    with pytest.raises(FabricError, match=f"accepted the {what} but returned no job instance"):
        start(c)


def test_starting_a_job_is_never_sent_twice_after_a_server_error(runs_allowed):
    c, s, _ = client([Resp(503, {}), Resp(202, headers={"Location": "x/j9"})])
    with pytest.raises(FabricError) as exc:
        c.run_item_job("ws1", "nb1", "RunNotebook")
    assert exc.value.status == 503 and len(s.calls) == 1


def test_a_throttled_start_is_tried_again_at_most_five_times(runs_allowed):
    c, s, slept = client([Resp(429, {}, headers={"Retry-After": "0"}) for _ in range(5)])
    with pytest.raises(FabricError) as exc:
        c.run_item_job("ws1", "nb1", "RunNotebook")
    assert exc.value.status == 429 and len(s.calls) == 5 and len(slept) == 4


def test_a_huge_retry_after_is_capped(runs_allowed):
    c, _, slept = client([Resp(429, {}, headers={"Retry-After": "86400"}),
                          Resp(202, headers={"Location": "x/j1"})])
    assert c.run_item_job("ws1", "nb1", "RunNotebook") == "j1"
    assert slept == [RETRY_AFTER_CAP]


def test_table_maintenance_sends_only_what_was_asked(runs_allowed):
    c, s, _ = client([Resp(202, headers={"Location": "x/j1"}), Resp(202, headers={"Location": "x/j2"})])
    c.run_table_maintenance("ws1", "lh1", "orders", schema="dbo", v_order=False, z_order_by=["id"])
    c.run_table_maintenance("ws1", "lh1", "orders", optimize=False, z_order_by=["id"])
    first, second = (call["json"]["executionData"] for call in s.calls)
    assert first == {"tableName": "orders", "schemaName": "dbo", "vacuumSettings": {"retentionPeriod": "7.01:00:00"},
                     "optimizeSettings": {"vOrder": "false", "zOrderBy": ["id"]}}
    assert second == {"tableName": "orders", "vacuumSettings": {"retentionPeriod": "7.01:00:00"}}
    assert s.calls[0]["url"] == f"{API}/workspaces/ws1/lakehouses/lh1/jobs/instances"


# ---------------------------------------------------------------- following runs


def test_job_runs_are_listed_newest_first_and_one_without_a_start_last():
    c, _, _ = client([Resp(200, {"value": [{"id": "a", "startTimeUtc": "2026-01-01"}, {"id": "b"},
                                          {"id": "c", "startTimeUtc": "2026-03-01"}]})])
    assert [r["id"] for r in c.list_job_instances("ws1", "nb1")] == ["c", "a", "b"]


@pytest.mark.parametrize("body, ids", [([{"id": "r1"}], ["r1"]), ({"value": [{"id": "r2"}]}, ["r2"]), ({}, [])])
def test_activity_runs_read_either_shape_fabric_answers_in(body, ids):
    c, s, _ = client([Resp(200, body)])
    assert [r["id"] for r in c.query_activity_runs("ws1", "job1", "a", "b")] == ids
    assert s.calls[0]["json"]["lastUpdatedAfter"] == "a"


def test_a_job_status_and_a_cancel_go_to_that_instance():
    c, s, _ = client([Resp(200, {"status": "InProgress"}), Resp(202)])
    assert c.job_instance("ws1", "nb1", "j1")["status"] == "InProgress"
    c.cancel_job("ws1", "nb1", "j1")
    assert [(x["method"], x["url"]) for x in s.calls] == [
        ("GET", f"{API}/workspaces/ws1/items/nb1/jobs/instances/j1"),
        ("POST", f"{API}/workspaces/ws1/items/nb1/jobs/instances/j1/cancel")]


# ---------------------------------------------------------------- the admin tenant scan


def test_a_scan_is_polled_until_it_succeeds_in_batches_of_100():
    ids = [f"w{i}" for i in range(150)]
    answers = []
    for scan in ("s1", "s2"):
        answers += [Resp(202, {"id": scan}), Resp(200, {"status": "Running"}), Resp(200, {"status": "Succeeded"}),
                    Resp(200, {"workspaces": [{"id": scan}]})]
    c, s, slept = client(answers)
    assert [w["id"] for w in c.admin_scan_workspaces(ids + ["w1", " "])] == ["s1", "s2"]
    batches = [call["json"]["workspaces"] for call in s.calls if call["url"].endswith("/getInfo")]
    assert [len(b) for b in batches] == [100, 50] and slept == [2, 2]
    assert s.calls[0]["params"]["datasourceDetails"] == "false"


def test_a_scan_that_doesnt_start_or_fails_says_so():
    c, _, _ = client([Resp(202, {})])
    with pytest.raises(FabricError, match="didn't start"):
        c.admin_scan_workspaces(["w1"])
    c, _, _ = client([Resp(202, {"id": "s1"}), Resp(200, {"status": "Failed", "error": "Throttled"})])
    with pytest.raises(FabricError, match="scan failed: Throttled"):
        c.admin_scan_workspaces(["w1"])


def test_a_scan_that_never_finishes_stops_after_the_poll_timeout():
    c, s, slept = client([Resp(202, {"id": "s1"})] + [Resp(200, {"status": "Running"}) for _ in range(5)],
                         poll_timeout=10)
    with pytest.raises(FabricError, match="didn't finish in time"):
        c.admin_scan_workspaces(["w1"], poll_seconds=2)
    assert len(s.calls) == 6 and slept == [2, 2, 2, 2]
    assert all(call["url"].startswith(POWERBI_ADMIN) for call in s.calls)


# ---------------------------------------------------------------- errors never carry the token or a query


def test_an_error_names_the_path_without_its_query_and_never_the_token():
    c, _, _ = client([Resp(403, {"errorCode": "InsufficientPrivileges", "message": "Not allowed"},
                           headers={"x-ms-request-id": "req-1"})])
    with pytest.raises(FabricError) as exc:
        c._request("GET", f"{API}/workspaces/ws1/items?continuationToken=sig-secret")
    text = str(exc.value)
    assert text.startswith("GET /workspaces/ws1/items failed with 403 InsufficientPrivileges")
    assert "sig-secret" not in text and "tok-secret" not in text and "Request id req-1" in text and exc.value.request_id == "req-1"


def test_an_error_body_that_isnt_an_object_gives_the_status_and_hint_only():
    c, s, _ = client([Resp(500, ["boom", "trace"], headers={"Retry-After": "0"}) for _ in range(5)])
    with pytest.raises(FabricError) as exc:
        c.get_workspace("ws1")
    assert str(exc.value) == ("GET /workspaces/ws1 failed with 500: Fabric had a problem on its side. Try again, and "
                              "give the request id to support if it keeps happening.")
    assert len(s.calls) == 5  # a read is tried again, at most five times


def test_an_operation_pointing_off_fabric_is_never_polled_with_the_token():
    c, s, slept = client([Resp(202, headers={"Location": "https://evil.example/operations/1"})])
    with pytest.raises(FabricError, match="another site"):
        c.refresh_sql_endpoint("ws1", "sql1")
    assert len(s.calls) == 1 and slept == []


def test_an_empty_role_update_reply_is_an_empty_result():
    c, _, _ = client([Resp(200)])
    assert c.update_role_assignment("ws1", "ra1", "Viewer") == {}
