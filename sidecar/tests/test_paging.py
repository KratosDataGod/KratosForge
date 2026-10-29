"""Paged reads can't loop for ever (#940): Fabric's continuationUri and OneLake's x-ms-continuation."""

import json

import pytest
import requests
from test_fabric_api import Resp, client

from fabric_desktop import paging
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.mailbox import OneLakeControl
from fabric_desktop.onelake import FileStore

NEXT = "https://api.fabric.microsoft.com/v1/workspaces?continuationToken=2"


def test_a_repeated_continuation_uri_stops_the_fabric_list_with_an_error():
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": NEXT}),
                   Resp(200, {"value": [{"id": "b"}], "continuationUri": NEXT})])
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert exc.value.code == "PagingStopped" and "same next page twice" in str(exc.value)
    assert len(s.calls) == 2


def test_a_list_with_no_end_stops_at_the_cap_and_says_it_isnt_complete(monkeypatch):
    monkeypatch.setattr(paging, "MAX_PAGES", 3)
    pages = [Resp(200, {"value": [{"id": str(i)}], "continuationUri": f"{NEXT}{i}"}) for i in range(5)]
    c, s = client(pages)
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert "after 3 pages" in str(exc.value) and "isn't complete" in str(exc.value)
    assert len(s.calls) == 3


def test_normal_paging_is_unchanged():
    c, _ = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": NEXT}), Resp(200, {"value": [{"id": "b"}]})])
    assert [w["id"] for w in c.list_workspaces()] == ["a", "b"]


class Dfs:
    """A OneLake listing whose continuation never changes."""

    def __init__(self, tokens):
        self.tokens, self.calls = list(tokens), 0

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        self.calls += 1
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps({"paths": [{"name": f"lh/Files/f{self.calls}", "contentLength": "1"}]}).encode()
        token = self.tokens.pop(0) if self.tokens else None
        if token:
            r.headers["x-ms-continuation"] = token
        return r


def test_a_repeated_onelake_continuation_stops_the_listing(monkeypatch):
    dfs = Dfs(["t1", "t2", "t1", "t3"])
    store = FileStore(lambda: "tok")
    monkeypatch.setattr(store, "_read", lambda url, **kw: dfs.get(url, **kw))
    with pytest.raises(paging.PagingError, match="same next page twice"):
        store.list("ws", "lh")
    assert dfs.calls == 3
    dfs = Dfs(["t1", "t2"])
    monkeypatch.setattr(store, "_read", lambda url, **kw: dfs.get(url, **kw))
    assert len(store.list("ws", "lh")) == 3


def test_a_repeated_mailbox_continuation_stops_the_listing():
    dfs = Dfs(["t1", "t1"])
    control = OneLakeControl("ws", "lh", lambda: "tok", session=dfs, sleep=lambda s: None)
    with pytest.raises(paging.PagingError):
        control.list("requests")
    assert dfs.calls == 2
