"""HD-058 (EN-12): a URL Fabric hands back is followed with the bearer token only when it's https on
the request's host or a Fabric, OneLake or Power BI host."""

import pytest

from fabric_desktop.fabric_api import FabricClient, FabricError, followable_url

API = "https://api.fabric.microsoft.com/v1"


class Resp:
    def __init__(self, status, body=None, headers=None, url=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.content = b"x" if body is not None else b""
        self.text = str(body)
        if url is not None:
            self.url = url

    def json(self):
        return self._body


class RecordingSession:
    """Answers in order and records every request with its headers."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        return self.responses.pop(0)


def client(responses):
    session = RecordingSession(responses)
    return FabricClient(lambda: "secret-token", session=session, sleep=lambda s: None), session


def hosts(session):
    return [url.split("/")[2] for _, url, _ in session.calls]


def test_continuation_on_another_host_is_refused_and_not_requested():
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": "https://evil.example.com/v1/workspaces?ct=2"}),
                   Resp(200, {"value": [{"id": "b"}]})])
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert exc.value.code == "UntrustedUrl"
    assert "evil.example.com" in str(exc.value) and "sign-in stays private" in str(exc.value)
    assert hosts(s) == ["api.fabric.microsoft.com"]  # nothing went to evil.example.com


def test_data_access_roles_continuation_on_another_host_is_refused_and_not_requested():
    c, s = client([Resp(200, {"value": [{"name": "DefaultReader"}], "continuationUri": "https://evil.example.com/roles?ct=2"},
                        headers={"ETag": '"v1"'}),
                   Resp(200, {"value": [{"name": "Other"}]})])
    with pytest.raises(FabricError) as exc:
        c.list_data_access_roles_tagged("ws1", "lh1")
    assert exc.value.code == "UntrustedUrl"
    assert hosts(s) == ["api.fabric.microsoft.com"]  # the token never went to evil.example.com


def test_data_access_roles_follow_a_continuation_on_the_same_host():
    c, s = client([Resp(200, {"value": [{"name": "A"}], "continuationUri": f"{API}/workspaces/ws1/items/lh1/dataAccessRoles?ct=2"},
                        headers={"ETag": '"v1"'}),
                   Resp(200, {"value": [{"name": "B"}]})])
    roles, etag = c.list_data_access_roles_tagged("ws1", "lh1")
    assert [r["name"] for r in roles] == ["A", "B"] and etag == '"v1"'
    assert hosts(s) == ["api.fabric.microsoft.com"] * 2


def test_continuation_on_a_lookalike_host_is_refused():
    c, s = client([Resp(200, {"value": [], "continuationUri": "https://evilfabric.microsoft.com.example.net/x"})])
    with pytest.raises(FabricError):
        c.list_workspaces()
    assert len(s.calls) == 1


def test_location_on_another_host_is_refused_and_not_polled():
    c, s = client([Resp(202, headers={"Location": "https://evil.example.com/v1/operations/op1"}),
                   Resp(200, {"status": "Succeeded"}), Resp(200, {"id": "lh1"})])
    with pytest.raises(FabricError) as exc:
        c.create_lakehouse("ws1", "Sales")
    assert exc.value.code == "UntrustedUrl"
    assert hosts(s) == ["api.fabric.microsoft.com"]


@pytest.mark.parametrize("url", ["http://api.fabric.microsoft.com/v1/operations/op1",
                                 "https://user:pw@api.fabric.microsoft.com/v1/operations/op1"])
def test_insecure_location_is_refused(url):
    c, s = client([Resp(202, headers={"Location": url})])
    with pytest.raises(FabricError) as exc:
        c.create_lakehouse("ws1", "Sales")
    assert "secure connection" in str(exc.value)
    assert len(s.calls) == 1


def test_http_continuation_is_refused():
    c, s = client([Resp(200, {"value": [], "continuationUri": "http://api.fabric.microsoft.com/v1/workspaces?ct=2"})])
    with pytest.raises(FabricError):
        c.list_workspaces()
    assert len(s.calls) == 1


def test_same_host_continuation_is_followed_with_the_token():
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": f"{API}/workspaces?ct=2"}),
                   Resp(200, {"value": [{"id": "b"}]})])
    assert [w["id"] for w in c.list_workspaces()] == ["a", "b"]
    assert s.calls[1][1] == f"{API}/workspaces?ct=2"
    assert s.calls[1][2]["Authorization"] == "Bearer secret-token"


def test_regional_operation_host_is_followed():
    loc = "https://wabi-west-europe-redirect.analysis.windows.net/v1/operations/op1"
    c, s = client([Resp(202, headers={"Location": loc}), Resp(200, {"status": "Succeeded"}), Resp(200, {"id": "lh1"})])
    assert c.create_lakehouse("ws1", "Sales")["id"] == "lh1"
    assert [u for _, u, _ in s.calls[1:]] == [loc, f"{loc}/result"]


def test_relative_continuation_and_location_are_resolved_against_the_request():
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": "/v1/workspaces?ct=2"}),
                   Resp(200, {"value": [{"id": "b"}]})])
    assert [w["id"] for w in c.list_workspaces()] == ["a", "b"]
    assert s.calls[1][1] == f"{API}/workspaces?ct=2"

    c, s = client([Resp(202, headers={"Location": "/v1/operations/op1"}),
                   Resp(200, {"status": "Succeeded"}), Resp(200, {"id": "lh1"})])
    assert c.create_lakehouse("ws1", "Sales")["id"] == "lh1"
    assert s.calls[1][1] == f"{API}/operations/op1"


def test_location_is_resolved_against_the_responses_own_url():
    c, s = client([Resp(202, headers={"Location": "operations/op1"}, url="https://api.powerbi.com/v1.0/myorg/x/"),
                   Resp(200, {"status": "Succeeded"}), Resp(200, {})])
    c._wait(c._request("POST", "https://api.powerbi.com/v1.0/myorg/x/"))
    assert s.calls[1][1] == "https://api.powerbi.com/v1.0/myorg/x/operations/op1"


@pytest.mark.parametrize("url,came_from", [
    ("https://onelake.dfs.fabric.microsoft.com/ws/lh/Files", API),
    ("https://api.powerbi.com/v1.0/myorg/admin/groups?$skip=100", API),
    ("https://custom.example.org/next?p=2", "https://custom.example.org/first"),  # same host
])
def test_known_and_same_hosts_are_allowed(url, came_from):
    assert followable_url(url, came_from) == url


@pytest.mark.parametrize("url", ["https://example.com/x", "https://fabric.microsoft.com.evil.net/x",
                                 "ftp://api.fabric.microsoft.com/x", "//evil.example.com/x"])
def test_other_hosts_and_schemes_are_refused(url):
    with pytest.raises(FabricError):
        followable_url(url, API)


@pytest.mark.parametrize("url", ["https://evil.example\\.fabric.microsoft.com/v1/workspaces?ct=2",
                                 "https://evil.example\\@api.fabric.microsoft.com/v1/workspaces",
                                 "https://api.fabric.microsoft.com\t.evil.example/v1/workspaces",
                                 "https://api.fabric.microsoft.com\n.evil.example/v1/workspaces",
                                 "https://evil.example\uff0f.fabric.microsoft.com/v1/workspaces"])
def test_addresses_read_differently_by_the_sender_are_refused_and_not_requested(url):
    # urlsplit reads the first as a fabric.microsoft.com subdomain, but requests connects to evil.example.
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": url}), Resp(200, {"value": [{"id": "b"}]})])
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert exc.value.code == "UntrustedUrl"
    assert hosts(s) == ["api.fabric.microsoft.com"]


def test_the_followed_url_is_the_one_checked():
    import requests
    from urllib3.util import parse_url

    url = followable_url("/v1/workspaces?continuationToken=a%20b", API + "/workspaces")
    assert parse_url(requests.Request("GET", url).prepare().url).host == "api.fabric.microsoft.com"
    assert url == "https://api.fabric.microsoft.com/v1/workspaces?continuationToken=a%20b"
