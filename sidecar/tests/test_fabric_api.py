import pytest

from fabric_desktop.fabric_api import FabricClient, FabricError, _operation_failed


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.content = b"x" if body is not None else b""
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json")))
        return self.responses.pop(0)


def client(responses):
    session = FakeSession(responses)
    return FabricClient(lambda: "tok", session=session, sleep=lambda s: None), session


def test_create_lakehouse_enables_schemas_and_follows_lro():
    loc = "https://api.fabric.microsoft.com/v1/operations/op1"
    c, s = client([
        Resp(202, headers={"Location": loc, "Retry-After": "1"}),
        Resp(200, {"status": "Running"}),
        Resp(200, {"status": "Succeeded"}),
        Resp(200, {"id": "lh1", "displayName": "Sales"}),
    ])
    assert c.create_lakehouse("ws1", "Sales")["id"] == "lh1"
    method, url, body = s.calls[0]
    assert (method, url) == ("POST", "https://api.fabric.microsoft.com/v1/workspaces/ws1/lakehouses")
    assert body == {"displayName": "Sales", "creationPayload": {"enableSchemas": True}}
    assert s.calls[-1][1] == f"{loc}/result"


def test_list_follows_continuation_and_retries_429():
    c, s = client([
        Resp(429, {}, headers={"Retry-After": "0"}),
        Resp(200, {"value": [{"id": "a"}], "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?ct=2"}),
        Resp(200, {"value": [{"id": "b"}]}),
    ])
    assert [w["id"] for w in c.list_workspaces()] == ["a", "b"]
    assert len(s.calls) == 3


def test_a_read_is_retried_on_503():
    c, s = client([Resp(503, {}, headers={"Retry-After": "0"}), Resp(200, {"id": "lh1"})])
    assert c.get_lakehouse("ws1", "lh1")["id"] == "lh1"
    assert len(s.calls) == 2


def test_a_read_gives_up_after_bounded_retries():
    c, s = client([Resp(503, {}) for _ in range(5)])
    with pytest.raises(FabricError) as exc:
        c.get_lakehouse("ws1", "lh1")
    assert exc.value.status == 503 and len(s.calls) == 5


def test_a_write_is_not_retried_on_503():
    c, s = client([Resp(503, {}), Resp(201, {"id": "w1"})])
    with pytest.raises(FabricError) as exc:
        c.create_workspace("Sales")
    assert exc.value.status == 503 and len(s.calls) == 1


def test_capacity_not_active_gets_a_plain_message():
    c, _ = client([Resp(404, {"errorCode": "CapacityNotActive", "message": "Capacity X is not active"})])
    try:
        c.list_lakehouses("ws1")
    except Exception as exc:
        assert exc.code == "CapacityNotActive"
        assert "capacity is paused" in str(exc)
    else:
        raise AssertionError("expected FabricError")


def test_create_workspace_puts_it_on_a_capacity_and_explains_a_taken_name():
    c, s = client([Resp(201, {"id": "ws9", "displayName": "Sales - Dev"})])
    assert c.create_workspace("Sales - Dev", "cap1", "Dev")["id"] == "ws9"
    assert s.calls[0] == ("POST", "https://api.fabric.microsoft.com/v1/workspaces",
                          {"displayName": "Sales - Dev", "capacityId": "cap1", "description": "Dev"})
    c, s = client([Resp(409, {"errorCode": "WorkspaceNameAlreadyExists"})])
    with pytest.raises(Exception, match="already exists. Pick it from the list"):
        c.create_workspace("Sales - Dev")
    assert s.calls[0][2] == {"displayName": "Sales - Dev"}


def test_create_lakehouse_refuses_a_name_fabric_would_reject_before_calling_fabric():
    c, s = client([])
    with pytest.raises(ValueError, match="For example TPC_H_Benchmark"):
        c.create_lakehouse("ws1", "TPC-H_Benchmark")
    with pytest.raises(ValueError, match="For example LH_2024_sales"):
        c.create_lakehouse("ws1", "2024 sales")
    assert s.calls == []


def test_roles_are_replaced_only_if_unchanged_since_they_were_read():
    c, s = client([Resp(200, {"value": [{"name": "DefaultReader"}]}, headers={"ETag": '"v1"'}),
                   Resp(200, {}), Resp(412, {"errorCode": "PreconditionFailed"})])
    headers = []
    request = s.request
    s.request = lambda method, url, **kw: headers.append(kw["headers"]) or request(method, url, **kw)
    roles, etag = c.list_data_access_roles_tagged("ws1", "lh1")
    assert roles == [{"name": "DefaultReader"}] and etag == '"v1"'
    c.put_data_access_roles("ws1", "lh1", roles, etag=etag)
    assert headers[1]["If-Match"] == '"v1"' and headers[1]["Authorization"] == "Bearer tok"
    with pytest.raises(FabricError) as exc:
        c.put_data_access_roles("ws1", "lh1", roles, etag='"v0"')
    assert exc.value.code == "PreconditionFailed" and len(s.calls) == 3  # a refused write isn't retried


# ---- long-running operations (#921)

LOC = "https://api.fabric.microsoft.com/v1/operations/op1"


def started(*after):
    return [Resp(202, headers={"Location": LOC, "x-ms-operation-id": "op1", "Retry-After": "0"}), *after]


def test_a_succeeded_operation_follows_the_result_location_fabric_gives():
    result = "https://api.fabric.microsoft.com/v1/workspaces/ws1/lakehouses/lh1"  # not the usual {operation}/result
    c, s = client(started(Resp(200, {"status": "Succeeded"}, headers={"Location": result}), Resp(200, {"id": "lh1"})))
    assert c.create_lakehouse("ws1", "Sales") == {"id": "lh1"}
    assert [u for _, u, _ in s.calls[1:]] == [LOC, result]


def test_an_operation_with_no_result_is_done_not_an_error():
    c, s = client(started(Resp(200, {"status": "Succeeded"}),
                          Resp(400, {"errorCode": "OperationHasNoResult", "message": "No result"})))
    assert c._wait(c._request("POST", "/workspaces/ws1/items")) == {}
    assert s.calls[-1][1] == f"{LOC}/result"


def test_a_result_that_fails_for_another_reason_still_raises():
    c, _ = client(started(Resp(200, {"status": "Succeeded"}), Resp(403, {"errorCode": "Forbidden"})))
    with pytest.raises(FabricError) as exc:
        c._wait(c._request("POST", "/workspaces/ws1/items"))
    assert exc.value.status == 403


def test_a_result_location_on_another_host_is_refused_without_the_token():
    c, s = client(started(Resp(200, {"status": "Succeeded"}, headers={"Location": "https://evil.example.com/r"})))
    with pytest.raises(FabricError) as exc:
        c._wait(c._request("POST", "/workspaces/ws1/items"))
    assert exc.value.code == "UntrustedUrl" and len(s.calls) == 2


@pytest.mark.parametrize("state, says, code", [
    ({"status": "Failed", "error": {"errorCode": "ItemDisplayNameAlreadyInUse", "message": "Name already in use.",
                                    "moreDetails": [{"message": "Pick another name."}], "requestId": "req-9"}},
     "Fabric's operation op1 failed: Name already in use. Pick another name (ItemDisplayNameAlreadyInUse). Request id req-9.",
     "ItemDisplayNameAlreadyInUse"),
    ({"status": "Failed", "error": None}, "Fabric's operation op1 failed and gave no reason.", None),
    ({"status": "Undefined"}, "Fabric's operation op1 ended in an unknown state and gave no reason.", None),
    ({"status": "Failed", "error": {"errorCode": "CapacityNotActive", "message": "x"}}, "capacity is paused", "CapacityNotActive"),
])
def test_a_failed_operation_says_why_in_fabrics_words(state, says, code):
    c, _ = client(started(Resp(200, state)))
    with pytest.raises(FabricError) as exc:
        c._wait(c._request("POST", "/workspaces/ws1/items"))
    assert says in str(exc.value) and exc.value.code == code
    assert "{" not in str(exc.value), "no raw JSON"


def test_an_operation_that_never_finishes_times_out_naming_it():
    c, _ = client(started(*[Resp(200, {"status": "Running"}) for _ in range(5)]))
    c._poll_timeout = -1
    with pytest.raises(FabricError) as exc:
        c._wait(c._request("POST", "/workspaces/ws1/items"))
    assert exc.value.code == "OperationTimedOut" and "op1" in str(exc.value) and "Running" in str(exc.value)


def test_a_lakehouse_created_without_a_result_is_looked_up_by_name():
    listed = Resp(200, {"value": [{"id": "lh9", "displayName": "Sales"}]})
    c, s = client(started(Resp(200, {"status": "Succeeded"}), Resp(404, {"errorCode": "NotFound"}), listed))
    assert c.create_lakehouse("ws1", "Sales") == {"id": "lh9", "displayName": "Sales"}
    assert s.calls[-1][1].endswith("/workspaces/ws1/lakehouses")
    c, _ = client(started(Resp(200, {"status": "Succeeded"}), Resp(404, {"errorCode": "NotFound"}), Resp(200, {"value": []})))
    with pytest.raises(FabricError) as exc:
        c.create_lakehouse("ws1", "Sales")
    assert exc.value.code == "LakehouseNotFoundAfterCreate"


def test_a_definition_with_no_result_is_a_clear_error():
    c, _ = client(started(Resp(200, {"status": "Succeeded"}), Resp(404, {"errorCode": "NotFound"})))
    with pytest.raises(FabricError) as exc:
        c.get_item_definition("ws1", "nb1")
    assert exc.value.code == "NoDefinition"


def test_more_details_that_isnt_a_list_is_ignored():
    err = _operation_failed("op1", {"status": "Failed", "error": {"message": "Bad", "moreDetails": "oops"}})
    assert str(err) == "Fabric's operation op1 failed: Bad."
