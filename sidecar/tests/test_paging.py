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


# ---- #968: the remaining list loops go through the same guard

def test_a_fabric_list_that_sends_its_first_page_back_stops_before_reading_it_twice():
    first = "https://api.fabric.microsoft.com/v1/workspaces"
    c, s = client([Resp(200, {"value": [{"id": "a"}], "continuationUri": first})] * 3)
    with pytest.raises(FabricError) as exc:
        c.list_workspaces()
    assert exc.value.code == "PagingStopped" and len(s.calls) == 1


ROLES_NEXT = "https://api.fabric.microsoft.com/v1/workspaces/ws1/items/lh1/dataAccessRoles?continuationToken=2"


def test_data_access_roles_stop_on_a_repeated_page():
    c, s = client([Resp(200, {"value": [{"name": "r1"}], "continuationUri": ROLES_NEXT})] * 3)
    with pytest.raises(FabricError) as exc:
        c.list_data_access_roles_tagged("ws1", "lh1")
    assert exc.value.code == "PagingStopped" and "data access roles" in str(exc.value) and len(s.calls) == 2


def test_data_access_roles_stop_at_the_cap(monkeypatch):
    monkeypatch.setattr(paging, "MAX_PAGES", 3)
    c, s = client([Resp(200, {"value": [{"name": str(i)}], "continuationUri": f"{ROLES_NEXT}{i}"}) for i in range(5)])
    with pytest.raises(FabricError) as exc:
        c.list_data_access_roles_tagged("ws1", "lh1")
    assert "isn't complete" in str(exc.value) and len(s.calls) == 3


def test_data_access_roles_over_two_pages_still_list():
    c, _ = client([Resp(200, {"value": [{"name": "r1"}], "continuationUri": ROLES_NEXT}),
                   Resp(200, {"value": [{"name": "r2"}]})])
    roles, _ = c.list_data_access_roles_tagged("ws1", "lh1")
    assert [r["name"] for r in roles] == ["r1", "r2"]


def key_vault(monkeypatch, next_links):
    import types

    from fabric_desktop import http_session
    sent = []
    links = iter(next_links)

    def get(url, **kw):
        sent.append(url)
        body = {"value": [{"id": f"https://kv.vault.azure.net/secrets/s{len(sent)}"}]}
        link = next(links, None)
        if link:
            body["nextLink"] = link
        return types.SimpleNamespace(status_code=200, json=lambda: body, raise_for_status=lambda: None)
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=get))
    return sent


def secrets_list():
    from test_dbutils import Kernel
    return Kernel(token=lambda audience: "tok").run("[s.key for s in dbutils.secrets.list('KV')]")


def stopped(out):
    assert out["status"] == "error" and out["outputs"][-1]["ename"] == "PagingError", out
    return out["outputs"][-1]["evalue"]


def test_key_vault_secrets_stop_on_a_repeated_next_link(monkeypatch):
    link = "https://kv.vault.azure.net/secrets?api-version=7.4&$skiptoken=x"
    sent = key_vault(monkeypatch, [link, link, link])
    assert "same next page twice" in stopped(secrets_list()) and len(sent) == 2


def test_key_vault_secrets_stop_at_the_cap(monkeypatch):
    monkeypatch.setattr(paging, "MAX_PAGES", 3)
    sent = key_vault(monkeypatch, [f"https://kv.vault.azure.net/secrets?$skiptoken={i}" for i in range(10)])
    assert "isn't complete" in stopped(secrets_list()) and len(sent) == 3


def test_key_vault_secrets_over_two_pages_still_list(monkeypatch):
    key_vault(monkeypatch, ["https://kv.vault.azure.net/secrets?$skiptoken=2"])
    from test_dbutils import _last
    assert _last(secrets_list()) == "['s1', 's2']"


class Catalogs:
    """A Unity Catalog listing that sends empty pages with the next tokens it's given."""

    def __init__(self, tokens, items=()):
        self.tokens, self.items, self.calls = list(tokens), list(items), 0

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        from test_databricks_sql import Resp as DbResp
        self.calls += 1
        body = {"catalogs": [self.items.pop(0)] if self.items else []}
        if self.tokens:
            body["next_page_token"] = self.tokens.pop(0)
        return DbResp(200, body)


def catalogs(ws):
    from test_databricks_sql import tokens

    from fabric_desktop.engines import databricks_sql as dbsql
    return dbsql.Client(tokens(), ws).list("/api/2.1/unity-catalog/catalogs", "catalogs", {})


def test_databricks_empty_pages_with_a_repeated_token_stop():
    from fabric_desktop.engines.databricks_sql import DatabricksError
    ws = Catalogs(["p2", "p2", "p2"])
    with pytest.raises(DatabricksError, match="same next page twice"):
        catalogs(ws)
    assert ws.calls == 2


def test_databricks_empty_pages_stop_at_the_cap(monkeypatch):
    from fabric_desktop.engines.databricks_sql import DatabricksError
    monkeypatch.setattr(paging, "MAX_PAGES", 3)
    ws = Catalogs([f"p{i}" for i in range(10)])
    with pytest.raises(DatabricksError, match="isn't complete"):
        catalogs(ws)
    assert ws.calls == 3


def test_databricks_catalogs_over_two_pages_still_list():
    assert [c["name"] for c in catalogs(Catalogs(["p2"], [{"name": "main"}, {"name": "samples"}]))] == ["main", "samples"]
