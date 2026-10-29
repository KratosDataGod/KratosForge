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


# ---- the loops that followed next pages on their own (#968)

def _roles_client(pages):
    return client([Resp(200, body, {"ETag": '"v1"'}) for body in pages])


ROLES_NEXT = "https://api.fabric.microsoft.com/v1/workspaces/ws1/items/lh1/dataAccessRoles?continuationToken="


def test_data_access_roles_stop_on_a_repeated_page_or_the_cap(monkeypatch):
    c, s = _roles_client([{"value": [{"name": "a"}], "continuationUri": ROLES_NEXT + "2"},
                          {"value": [], "continuationUri": ROLES_NEXT + "2"}])
    with pytest.raises(FabricError) as exc:
        c.list_data_access_roles_tagged("ws1", "lh1")
    assert exc.value.code == "PagingStopped" and "same next page twice" in str(exc.value) and len(s.calls) == 2
    monkeypatch.setattr(paging, "MAX_PAGES", 3)
    c, s = _roles_client([{"value": [], "continuationUri": ROLES_NEXT + str(i)} for i in range(5)])
    with pytest.raises(FabricError) as exc:
        c.list_data_access_roles_tagged("ws1", "lh1")
    assert "isn't complete" in str(exc.value) and len(s.calls) == 3
    c, _ = _roles_client([{"value": [{"name": "a"}], "continuationUri": ROLES_NEXT + "2"}, {"value": [{"name": "b"}]}])
    assert c.list_data_access_roles_tagged("ws1", "lh1") == ([{"name": "a"}, {"name": "b"}], '"v1"')


class _Tokens:
    host = "https://adb-1.azuredatabricks.net"

    def headers(self):
        return {"Authorization": "Bearer tok"}


class _Catalogs:
    """Unity Catalog pages: each answer takes the next token from ``tokens``; an empty page past the first."""

    def __init__(self, tokens):
        self.tokens, self.calls = list(tokens), 0

    def request(self, method, url, headers=None, timeout=None, params=None):
        self.calls += 1
        r = requests.Response()
        r.status_code = 200
        token = self.tokens.pop(0) if self.tokens else None
        body = {"catalogs": [{"name": f"c{self.calls}"}] if self.calls < 3 else []}
        r._content = json.dumps(body | ({"next_page_token": token} if token else {})).encode()
        return r


def test_a_databricks_page_token_that_repeats_with_empty_pages_stops(monkeypatch):
    from fabric_desktop.engines.databricks_sql import Client, DatabricksError
    http = _Catalogs(["p2", "p3", "p3", "p3"])
    with pytest.raises(DatabricksError) as exc:
        Client(_Tokens(), http).list("/api/2.1/unity-catalog/catalogs", "catalogs", {})
    assert "same next page twice" in str(exc.value) and http.calls == 3
    monkeypatch.setattr(paging, "MAX_PAGES", 4)
    http = _Catalogs([f"p{i}" for i in range(10)])
    with pytest.raises(DatabricksError) as exc:
        Client(_Tokens(), http).list("/api/2.1/unity-catalog/catalogs", "catalogs", {})
    assert "isn't complete" in str(exc.value) and http.calls == 4
    http = _Catalogs(["p2"])
    assert [c["name"] for c in Client(_Tokens(), http).list("/x", "catalogs", {})] == ["c1", "c2"]


class _Vault:
    """Key Vault secret pages whose nextLink comes from ``links``."""

    def __init__(self, links):
        self.links, self.sent = list(links), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.sent.append(url)
        link = self.links.pop(0) if self.links else None
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps({"value": [{"id": f"https://kv.vault.azure.net/secrets/s{len(self.sent)}"}]}
                                | ({"nextLink": link} if link else {})).encode()
        return r


def test_key_vault_secret_pages_stop_on_a_repeated_link_or_the_cap(monkeypatch):
    from fabric_desktop import http_session
    from fabric_desktop.notebook import Kernel
    page = "https://kv.vault.azure.net/secrets?$skiptoken="
    vault = _Vault([page + "2", page + "2"])
    monkeypatch.setattr(http_session, "session", lambda: vault)
    out = Kernel(token=lambda audience: "tok").run("dbutils.secrets.list('kv')")
    assert out["status"] == "error" and "same next page twice" in out["outputs"][-1]["evalue"]
    assert len(vault.sent) == 2  # the token went out twice, not for ever
    monkeypatch.setattr(paging, "MAX_PAGES", 3)
    vault = _Vault([page + str(i) for i in range(10)])
    out = Kernel(token=lambda audience: "tok").run("dbutils.secrets.list('kv')")
    assert out["status"] == "error" and "isn't complete" in out["outputs"][-1]["evalue"] and len(vault.sent) == 3
    vault = _Vault([page + "2"])
    out = Kernel(token=lambda audience: "tok").run("print([s.key for s in dbutils.secrets.list('kv')])")
    assert out["status"] == "ok", out
    assert "['s1', 's2']" in json.dumps(out["outputs"])
