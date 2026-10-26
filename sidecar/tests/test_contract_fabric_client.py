"""Contract tests for the Fabric REST client's throttling, error and long-running-operation paths
(HD-053, finding TS-1).

They pin what ``fabric_api.FabricClient`` sends and how it reacts, through a fake transport under a
real ``requests.Session`` (tests/fake_http.py), with sleeps faked. Nothing reaches the network or a
tenant. The contracts:

* 429 is retried for any method (Fabric refused it unread) and the same request is sent again;
* 5xx on a read (GET) is retried and then succeeds, or ends in a FabricError carrying the status
  after a bounded number of tries;
* 5xx on a write (POST, PATCH, PUT, DELETE) is sent once and never repeated, since it may have
  happened; 4xx is never retried;
* a 202 long-running operation is posted once, polled at its Location until Succeeded, and its
  result read from <Location>/result; Failed surfaces Fabric's error; a stuck one times out;
* paged lists follow continuationUri until it runs out, with the token on every page.

Exact retry counts and sleep lengths are deliberately not pinned (HD-039 tunes them).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from fabric_desktop import fabric_api
from fabric_desktop.fabric_api import API, FabricClient, FabricError
from fake_http import MUTATING, Reply, session

WS = "11111111-1111-1111-1111-111111111111"
OP = f"{API}/operations/op-1"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(self, request, *args, **kwargs):
        raise AssertionError(f"real network call attempted: {request.method} {request.url}")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse)


def client(replies, **kwargs):
    s, transport = session(replies)
    sleeps: list[float] = []
    c = FabricClient(lambda: "tok", session=s, sleep=sleeps.append, **kwargs)
    return c, transport, sleeps


# ---- 429


def test_429_then_success_on_a_read():
    c, t, sleeps = client([Reply(429, {"errorCode": "TooManyRequests"}, {"Retry-After": "0"}),
                           Reply(200, {"id": "lh1"})])
    assert c.get_lakehouse(WS, "lh1") == {"id": "lh1"}
    assert [r.method for r in t.sent] == ["GET", "GET"]
    assert t.sent[0].url == t.sent[1].url == f"{API}/workspaces/{WS}/lakehouses/lh1"
    assert len(sleeps) == 1  # it waited before trying again


def test_429_then_success_on_a_write_sends_the_same_request_again():
    c, t, sleeps = client([Reply(429, {}, {"Retry-After": "0"}),
                           Reply(201, {"id": "w1", "displayName": "Sales"})])
    assert c.create_workspace("Sales", capacity_id="cap1")["id"] == "w1"
    assert [r.method for r in t.sent] == ["POST", "POST"]
    assert t.sent[0].url == t.sent[1].url == f"{API}/workspaces"
    assert t.sent[0].json() == t.sent[1].json() == {"displayName": "Sales", "capacityId": "cap1"}
    assert len(sleeps) == 1


def test_429_that_never_clears_ends_in_an_error_with_the_status():
    c, t, _ = client(lambda r: Reply(429, {"errorCode": "TooManyRequests"}, {"Retry-After": "0"}))
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert exc.value.status == 429 and exc.value.code == "TooManyRequests"
    assert 1 < len(t.sent) <= 10  # bounded


# ---- 5xx


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_on_a_get_is_retried_then_succeeds(status):
    c, t, _ = client([Reply(status, {}, {"Retry-After": "0"}), Reply(200, {"id": WS})])
    assert c.get_workspace(WS) == {"id": WS}
    assert {r.method for r in t.sent} == {"GET"} and len(t.sent) == 2


def test_5xx_on_a_get_that_never_clears_ends_in_an_error_with_the_status():
    c, t, _ = client(lambda r: Reply(503, {}, {"Retry-After": "0"}))
    with pytest.raises(FabricError) as exc:
        c.get_workspace(WS)
    assert exc.value.status == 503
    assert 1 < len(t.sent) <= 10 and {r.method for r in t.sent} == {"GET"}


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_on_a_post_is_not_repeated(status):
    c, t, sleeps = client([Reply(status, {}, {"Retry-After": "0"}), Reply(201, {"id": "w1"})])
    with pytest.raises(FabricError) as exc:
        c.create_workspace("Sales")
    assert exc.value.status == status
    assert [r.method for r in t.sent] == ["POST"]
    assert sleeps == []


@pytest.mark.parametrize("call, method", [
    (lambda c: c.update_role_assignment(WS, "ra1", "Member"), "PATCH"),
    (lambda c: c.put_data_access_roles(WS, "lh1", []), "PUT"),
    (lambda c: c.delete_lakehouse(WS, "lh1"), "DELETE"),
    (lambda c: c.create_shortcut(WS, "lh1", "Files", "s", {"oneLake": {}}), "POST"),
])
def test_5xx_on_any_write_is_sent_once(call, method):
    c, t, _ = client([Reply(500, {}), Reply(200, {})])
    with pytest.raises(FabricError):
        call(c)
    assert [r.method for r in t.sent] == [method]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_4xx_is_not_retried(status):
    c, t, _ = client([Reply(status, {"errorCode": "Nope"}), Reply(200, {})])
    with pytest.raises(FabricError) as exc:
        c.get_workspace(WS)
    assert (exc.value.status, exc.value.code) == (status, "Nope")
    assert len(t.sent) == 1


def test_every_request_carries_the_bearer_token():
    c, t, _ = client([Reply(429, {}, {"Retry-After": "0"}), Reply(200, {"id": WS})])
    c.get_workspace(WS)
    assert all(r.headers["Authorization"] == "Bearer tok" for r in t.sent)


# ---- long-running operations


def test_lro_202_is_posted_once_polled_to_succeeded_and_its_result_read():
    c, t, sleeps = client([
        Reply(202, None, {"Location": OP, "Retry-After": "0"}),
        Reply(200, {"status": "NotStarted"}),
        Reply(200, {"status": "Running"}),
        Reply(200, {"status": "Succeeded"}),
        Reply(200, {"id": "lh1", "displayName": "Sales"}),
    ])
    assert c.create_lakehouse(WS, "Sales") == {"id": "lh1", "displayName": "Sales"}
    assert [(r.method, r.url) for r in t.sent] == [
        ("POST", f"{API}/workspaces/{WS}/lakehouses"),
        ("GET", OP), ("GET", OP), ("GET", OP),
        ("GET", f"{OP}/result"),
    ]
    assert [r for r in t.sent if r.method in MUTATING] == t.sent[:1]
    assert len(sleeps) >= 3  # waits between polls
    assert all(r.headers["Authorization"] == "Bearer tok" for r in t.sent)


def test_lro_succeeded_with_an_empty_result_returns_an_empty_dict():
    c, t, _ = client([Reply(202, None, {"Location": OP}), Reply(200, {"status": "Succeeded"}), Reply(200)])
    assert c.refresh_sql_endpoint(WS, "sql1") == {}
    assert t.sent[0].json() == {} and t.sent[-1].url == f"{OP}/result"


def test_a_write_that_finishes_at_once_is_not_polled():
    c, t, _ = client([Reply(201, {"id": "lh1"})])
    assert c.create_lakehouse(WS, "Sales") == {"id": "lh1"}
    assert len(t.sent) == 1


@pytest.mark.parametrize("status", ["Failed", "Undefined"])
def test_lro_failed_surfaces_fabrics_error_and_is_not_reposted(status):
    error = {"status": status, "error": {"errorCode": "ItemDisplayNameAlreadyInUse",
                                         "message": "Sales is already in use"}}
    c, t, _ = client([Reply(202, None, {"Location": OP, "Retry-After": "0"}),
                      Reply(200, {"status": "Running"}), Reply(200, error)])
    with pytest.raises(FabricError) as exc:
        c.create_lakehouse(WS, "Sales")
    assert "Operation failed" in str(exc.value)
    assert "ItemDisplayNameAlreadyInUse" in str(exc.value) and "Sales is already in use" in str(exc.value)
    assert [r.method for r in t.sent] == ["POST", "GET", "GET"]
    assert not any(r.url.endswith("/result") for r in t.sent)


def test_lro_that_never_finishes_times_out_without_reposting(monkeypatch):
    now = [1000.0]

    def clock():
        now[0] += 10
        return now[0]

    # The client's own clock only; pytest keeps the real one.
    monkeypatch.setattr(fabric_api, "time", SimpleNamespace(monotonic=clock, sleep=lambda _: None))
    c, t, _ = client(lambda r: Reply(202, None, {"Location": OP, "Retry-After": "0"}) if r.method == "POST"
                     else Reply(200, {"status": "Running"}), poll_timeout=35)
    with pytest.raises(FabricError) as exc:
        c.create_lakehouse(WS, "Sales")
    assert "timed out" in str(exc.value) and OP in str(exc.value)
    assert [r.method for r in t.sent].count("POST") == 1
    assert 1 <= sum(r.url == OP for r in t.sent) <= 10


def test_lro_poll_that_errors_surfaces_the_error_and_is_not_reposted():
    c, t, _ = client([Reply(202, None, {"Location": OP, "Retry-After": "0"}),
                      Reply(404, {"errorCode": "OperationNotFound"})])
    with pytest.raises(FabricError) as exc:
        c.create_lakehouse(WS, "Sales")
    assert (exc.value.status, exc.value.code) == (404, "OperationNotFound")
    assert [r.method for r in t.sent] == ["POST", "GET"]


def test_lro_on_get_definition_posts_once_and_returns_the_result():
    parts = {"definition": {"parts": [{"path": "notebook-content.ipynb", "payload": "e30="}]}}
    c, t, _ = client([Reply(202, None, {"Location": OP}), Reply(200, {"status": "Succeeded"}), Reply(200, parts)])
    assert c.get_item_definition(WS, "nb1", fmt="ipynb") == parts
    assert (t.sent[0].method, t.sent[0].url) == ("POST", f"{API}/workspaces/{WS}/items/nb1/getDefinition?format=ipynb")


# ---- pagination


def test_pagination_follows_continuation_uri_until_it_runs_out():
    page2 = f"{API}/workspaces/{WS}/items?continuationToken=abc%3D%3D"
    page3 = f"{API}/workspaces/{WS}/items?continuationToken=def"
    c, t, _ = client([
        Reply(200, {"value": [{"id": "1"}], "continuationToken": "abc==", "continuationUri": page2}),
        Reply(200, {"value": [{"id": "2"}, {"id": "3"}], "continuationToken": "def", "continuationUri": page3}),
        Reply(200, {"value": [{"id": "4"}]}),
    ])
    assert [i["id"] for i in c.list_items(WS)] == ["1", "2", "3", "4"]
    assert [r.url for r in t.sent] == [f"{API}/workspaces/{WS}/items", page2, page3]
    assert t.sent[1].query == {"continuationToken": "abc=="}
    assert {r.method for r in t.sent} == {"GET"}
    assert all(r.headers["Authorization"] == "Bearer tok" for r in t.sent)


def test_pagination_stops_on_a_null_continuation_uri():
    c, t, _ = client([Reply(200, {"value": [{"id": "1"}], "continuationUri": None})])
    assert c.list_workspaces() == [{"id": "1"}]
    assert len(t.sent) == 1


def test_a_page_that_fails_mid_list_raises_instead_of_returning_part_of_the_list():
    page2 = f"{API}/workspaces?continuationToken=x"
    c, t, _ = client([Reply(200, {"value": [{"id": "1"}], "continuationUri": page2}),
                      Reply(403, {"errorCode": "Forbidden"})])
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert exc.value.status == 403
    assert len(t.sent) == 2


def test_a_page_that_is_throttled_is_retried_and_the_list_completes():
    page2 = f"{API}/workspaces?continuationToken=x"
    c, t, _ = client([Reply(200, {"value": [{"id": "1"}], "continuationUri": page2}),
                      Reply(429, {}, {"Retry-After": "0"}),
                      Reply(200, {"value": [{"id": "2"}]})])
    assert [w["id"] for w in c.list_workspaces()] == ["1", "2"]
    assert [r.url for r in t.sent][1:] == [page2, page2]


# ---- starting a copy job or a dataflow (their own "execute" job path)


@pytest.mark.parametrize("item_type,path", [("CopyJob", "copyJobs"), ("Dataflow", "dataflows")])
def test_execute_posts_once_to_the_items_own_path_and_returns_the_job_id(item_type, path):
    c, t, _ = client([Reply(202, None, {"Location": f"{API}/workspaces/{WS}/items/cj1/jobs/instances/job-7/"})])
    assert c.run_item_execute(WS, "cj1", item_type) == "job-7"
    assert [(r.method, r.url) for r in t.sent] == [("POST", f"{API}/workspaces/{WS}/{path}/cj1/jobs/execute/instances")]


def test_execute_without_a_location_is_an_error_not_a_blank_job():
    c, _, _ = client([Reply(202)])
    with pytest.raises(FabricError, match="no job instance"):
        c.run_item_execute(WS, "cj1", "CopyJob")


def test_execute_5xx_is_not_sent_twice():
    c, t, _ = client([Reply(500, {"errorCode": "InternalError"})])
    with pytest.raises(FabricError):
        c.run_item_execute(WS, "df1", "Dataflow")
    assert len(t.sent) == 1
