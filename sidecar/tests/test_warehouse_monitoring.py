"""Workspace monitoring views for a warehouse (WH-19): the views, the queryset, the database lookup."""

import base64
import json

import pytest
import requests

from fabric_desktop import app_settings, audit, server
from fabric_desktop import warehouse_monitoring as wm
from fabric_desktop import warehouse_monitoring_service as wms
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service

WS = "11111111-2222-3333-4444-555555555555"
OTHER_WS = "11111111-2222-3333-4444-666666666666"
WH = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
WH2 = "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"
DB = "bbbbbbbb-0000-1111-2222-333333333333"
DB_OTHER = "bbbbbbbb-0000-1111-2222-444444444444"
URI = "https://trd-abc123.z4.kusto.fabric.microsoft.com"


class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


def _table(columns, rows):
    return {"Tables": [{"TableName": "Table_0", "Columns": [{"ColumnName": c, "DataType": "String"} for c in columns],
                        "Rows": rows}]}


class FakePost:
    def __init__(self):
        self.sent = []
        self.answers = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.sent.append({"url": url, "body": json, "headers": headers})
        answer = self.answers.pop(0) if self.answers else Resp(200, _table(["LastSeen", "Items"], [["2026-10-26T20:00:00Z", 2]]))
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeFabric:
    def __init__(self):
        self.calls = []
        self.dbs = {DB: {"id": DB, "displayName": "Sales_Monitoring_Database", "properties": {"queryServiceUri": URI}},
                    DB_OTHER: {"id": DB_OTHER, "displayName": "Events", "properties": {"queryServiceUri": URI}}}
        self.querysets = []
        self.definitions = {}

    def list_kql_databases(self, ws):
        self.calls.append(("list_kql_databases", ws))
        return [self.dbs[DB_OTHER], self.dbs[DB]]

    def get_kql_database(self, ws, db):
        self.calls.append(("get_kql_database", ws, db))
        return self.dbs[db]

    def list_kql_querysets(self, ws):
        self.calls.append(("list_kql_querysets", ws))
        return list(self.querysets)

    def create_kql_queryset(self, ws, name, definition, description=""):
        self.calls.append(("create_kql_queryset", ws, name))
        self.definitions[name] = definition
        return {"id": "q-" + name}

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", ws, item))
        return {"definition": self.definitions[item]}

    def update_item_definition(self, ws, item, definition):
        self.calls.append(("update_item_definition", ws, item))
        self.definitions[item] = definition
        return {}


class Auth:
    def kusto_token(self):
        return "kusto-token"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake, post = FakeFabric(), FakePost()
    settings = {"wh_monitoring_views": True, "wh_monitoring_auto": []}
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    monkeypatch.setattr(Service, "auth", property(lambda self: Auth()))
    monkeypatch.setattr(wms, "session", lambda: type("S", (), {"post": staticmethod(post)})())
    real = app_settings.value
    monkeypatch.setattr(app_settings, "value", lambda key: settings[key] if key in settings else real(key))
    monkeypatch.setattr(app_settings, "save", lambda changes: settings.update(changes))
    s = Service(root=tmp_path / "app")
    s.fake, s.post, s.settings = fake, post, settings
    return s


def _decoded(definition):
    part = next(p for p in definition["parts"] if p["path"] == "RealTimeQueryset.json")
    return json.loads(base64.b64decode(part["payload"]))["queryset"]


# ---- the views


@pytest.mark.parametrize("view", [*wm.VIEWS, "summary"])
def test_every_view_reads_only_this_warehouse(view):
    kql = wm.view_kql(view, WH.upper(), "7d")
    assert kql.startswith("WarehouseExecutions") or kql.startswith("let Runs = WarehouseExecutions")
    assert f'ItemId == "{WH}"' in kql
    assert "{" not in kql.replace("{{", "")  # every placeholder filled
    assert not any(line.lstrip().startswith(".") for line in kql.splitlines()), "no control commands"


@pytest.mark.parametrize("item", ['x" or 1==1 //', "", None, WH + "0", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee\n"])
def test_a_warehouse_id_that_isnt_a_guid_never_reaches_the_kql(item):
    with pytest.raises(ValueError):
        wm.view_kql("activity", item)


def test_only_known_views_and_windows():
    with pytest.raises(ValueError):
        wm.view_kql("WarehouseExecutions | take 1", WH)
    with pytest.raises(ValueError):
        wm.view_kql("activity", WH, "ago(3650d)")


@pytest.mark.parametrize("uri", [URI, URI + "/", "https://TRD-ABC.Z4.KUSTO.FABRIC.MICROSOFT.COM"])
def test_a_fabric_eventhouse_is_a_cluster(uri):
    assert wm.cluster(uri) == uri.rstrip("/").lower()


@pytest.mark.parametrize("uri", ["http://trd-a.kusto.fabric.microsoft.com", "https://kusto.fabric.microsoft.com.evil.com",
                                 "https://evil.com", "https://trd-a.kusto.fabric.microsoft.com/db",
                                 "https://trd-a.kusto.fabric.microsoft.com:444", "https://me@trd-a.kusto.fabric.microsoft.com",
                                 "https://trd-a.kusto.fabric.microsoft.com?x=1", "https://help.kusto.windows.net", "", None,
                                 "https://evil.com\\.kusto.fabric.microsoft.com", "https://evil.com%2F.kusto.fabric.microsoft.com",
                                 "https://evil.com@trd-a.kusto.fabric.microsoft.com", "https://trd-a.kusto.fabric.microsoft.com.",
                                 "https://trd-a.kusto.fabric.microsoft.com#x", "https://trd_a.kusto.fabric.microsoft.com",
                                 "https://.kusto.fabric.microsoft.com", "https://trd-a.kusto.fabric.microsoft.com\n"])
def test_no_token_goes_anywhere_but_a_fabric_eventhouse(uri):
    with pytest.raises(ValueError):
        wm.cluster(uri)


def test_the_query_asks_for_a_read_only_run_and_caps_rows():
    post = FakePost()
    post.answers = [Resp(200, _table(["a"], [[i] for i in range(wm.MAX_ROWS + 5)]))]
    out = wm.query(post, URI, "Sales_Monitoring_Database", wm.view_kql("slowest", WH), "tok")
    sent = post.sent[0]
    assert sent["url"] == f"{URI}/v1/rest/query"
    assert json.loads(sent["body"]["properties"])["Options"]["request_readonly"] is True
    assert sent["headers"]["Authorization"] == "Bearer tok"
    assert len(out["rows"]) == wm.MAX_ROWS and out["truncated"] is True


def test_a_refusal_says_which_role_reads_monitoring():
    post = FakePost()
    post.answers = [Resp(403, {"error": {"@message": "Principal is not authorized"}})]
    with pytest.raises(RuntimeError, match="Contributor"):
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok")


def test_throttling_is_waited_out_as_retry_after_says():
    post, waits = FakePost(), []
    post.answers = [Resp(429, {}, {"Retry-After": "7"}), Resp(200, _table(["a"], [[1]]))]
    out = wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=waits.append)
    assert out["rows"] == [[1]] and waits == [7.0] and len(post.sent) == 2
    ids = [s["headers"]["x-ms-client-request-id"] for s in post.sent]
    assert ids[0] != ids[1]  # each try is its own request


@pytest.mark.parametrize("first", [Resp(503, {}), requests.Timeout("read timed out"),
                                   requests.ConnectionError("connection reset")])
def test_a_5xx_a_timeout_or_a_dropped_connection_is_tried_again(first):
    post = FakePost()
    post.answers = [first, Resp(200, _table(["a"], [[1]]))]
    assert wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None)["rows"] == [[1]]


def test_a_lasting_outage_says_try_again_and_keeps_kustos_message():
    post = FakePost()
    post.answers = [Resp(503, {"error": {"@message": "Service is overloaded"}})] * wm.QUERY_ATTEMPTS
    with pytest.raises(RuntimeError, match=r"busy or out of reach.*Try again in a minute.*Service is overloaded"):
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None)
    assert len(post.sent) == wm.QUERY_ATTEMPTS
    post.answers = [requests.ConnectionError("reset")] * wm.QUERY_ATTEMPTS
    with pytest.raises(RuntimeError, match="couldn't be reached after 3 tries"):
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None)


@pytest.mark.parametrize("status", [401, 403])
def test_a_refusal_is_not_tried_again(status):
    post = FakePost()
    post.answers = [Resp(status, {})]
    with pytest.raises(RuntimeError, match="Contributor"):
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None)
    assert len(post.sent) == 1


# ---- the queryset


def test_the_queryset_has_one_tab_per_view_on_the_monitoring_database():
    q = _decoded(wm.queryset_definition(WH, URI, "Sales_Monitoring_Database"))
    assert q["dataSources"] == [{"id": q["tabs"][0]["dataSourceId"], "clusterUri": URI, "type": "AzureDataExplorer",
                                 "databaseName": "Sales_Monitoring_Database"}]
    assert [t["title"] for t in q["tabs"]] == [v[0] for v in wm.VIEWS.values()]
    assert all(f'ItemId == "{WH}"' in t["content"] for t in q["tabs"])


def test_a_rebuild_replaces_only_the_apps_tabs():
    first = wm.queryset_definition(WH, URI, "db")
    q = _decoded(first)
    q["tabs"].append({"id": "mine", "title": "My query", "content": "WarehouseExecutions | count", "dataSourceId": "s2"})
    q["dataSources"].append({"id": "s2", "clusterUri": URI, "type": "AzureDataExplorer", "databaseName": "other"})
    q["tabs"][0]["content"] = "edited by hand"
    existing = {"definition": {"parts": [{"path": "RealTimeQueryset.json", "payloadType": "InlineBase64",
                                          "payload": base64.b64encode(json.dumps({"queryset": q}).encode()).decode()}]}}
    again = _decoded(wm.queryset_definition(WH, URI, "db", existing))
    assert [t["id"] for t in again["tabs"]] == [t["id"] for t in _decoded(first)["tabs"]] + ["mine"]
    assert again["tabs"][0]["content"] == _decoded(first)["tabs"][0]["content"]
    assert [s["id"] for s in again["dataSources"]][1:] == ["s2"]


def test_two_warehouses_share_a_queryset_without_clashing():
    one = _decoded(wm.queryset_definition(WH, URI, "db"))
    existing = {"parts": [{"path": "RealTimeQueryset.json",
                           "payload": base64.b64encode(json.dumps({"queryset": one}).encode()).decode()}]}
    both = _decoded(wm.queryset_definition(WH2, URI, "db", existing))
    assert len(both["tabs"]) == 2 * len(wm.VIEWS) and len({t["id"] for t in both["tabs"]}) == len(both["tabs"])


def test_a_definition_it_cant_read_is_left_alone():
    with pytest.raises(ValueError, match="left as it is"):
        wm.queryset_definition(WH, URI, "db", {"parts": [{"path": "RealTimeQueryset.json", "payload": "!!not base64"}]})


@pytest.mark.parametrize("name, expected", [("WH_Gold", "WH_Gold_monitoring"), ("Sales DW (EU)", "Sales_DW__EU__monitoring"),
                                            ("", "warehouse_monitoring")])
def test_the_queryset_name_is_one_fabric_takes(name, expected):
    assert wm.queryset_name(name) == expected


# ---- the service


def test_status_finds_the_monitoring_database_in_the_workspace(svc):
    out = svc.wh_monitoring_status(WS)
    assert out["monitored"] is True and out["database"]["item_id"] == DB  # *_Database is tried first
    assert out["database"]["cluster"] == URI and out["database"]["items"] == 2
    assert f'WorkspaceId == "{WS}"' in svc.post.sent[0]["body"]["csl"]


def test_status_skips_a_database_without_the_table(svc):
    svc.post.answers = [Resp(400, {"error": {"@message": "SEM0100: Failed to resolve table expression named 'WarehouseExecutions'"}})]
    out = svc.wh_monitoring_status(WS)
    assert out["database"]["item_id"] == DB_OTHER and out["problems"] == []


def test_status_with_no_monitoring_database(svc):
    svc.fake.dbs[DB]["properties"]["queryServiceUri"] = "https://evil.com"
    svc.post.answers = [Resp(400, {"error": {"@message": "Failed to resolve table 'WarehouseExecutions'"}})]
    out = svc.wh_monitoring_status(WS)
    assert out["monitored"] is False and out["database"] is None
    assert len(svc.post.sent) == 1, "no query went to the address that isn't an Eventhouse"


def test_status_can_use_another_workspaces_monitoring_database(svc):
    out = svc.wh_monitoring_status(WS, OTHER_WS, DB)
    assert ("get_kql_database", OTHER_WS, DB) in svc.fake.calls
    assert ("list_kql_databases", WS) not in svc.fake.calls and out["monitored"] is True


def test_a_view_reads_the_database_fabric_names(svc):
    svc.post.answers = [Resp(200, _table(["Queries"], [[12]]))]
    out = svc.wh_monitoring_view(WS, DB, WH, "summary", "24h")
    assert out["rows"] == [[12]] and svc.post.sent[0]["url"].startswith(URI)


def test_a_view_before_any_warehouse_query_is_empty_not_an_error(svc):
    svc.post.answers = [Resp(400, {"error": {"@message": "SEM0100: Failed to resolve table 'WarehouseExecutions'"}})]
    out = svc.wh_monitoring_view(WS, DB, WH, "activity", "24h")
    assert out["rows"] == [] and "No warehouse query" in out["note"]


def test_build_needs_the_confirm(svc):
    with pytest.raises(PermissionError, match="confirm"):
        svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}])
    assert not [c for c in svc.fake.calls if c[0] in ("create_kql_queryset", "update_item_definition")]


def test_build_is_refused_when_the_organization_turned_it_off(svc):
    svc.settings["wh_monitoring_views"] = False
    with pytest.raises(PermissionError, match="organization"):
        svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}], confirm=True)
    assert svc.fake.calls == []


def test_build_creates_one_queryset_per_warehouse_and_updates_an_existing_one(svc):
    svc.fake.querysets = [{"id": "WH_Silver_monitoring", "displayName": "WH_Silver_monitoring",
                           "description": wms.QUERYSET_NOTE.format(name="WH_Silver")}]
    svc.fake.definitions["WH_Silver_monitoring"] = wm.queryset_definition(WH2, URI, "old")
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}, {"id": WH2, "name": "WH_Silver"}], confirm=True)
    assert [(r["queryset"], r["state"]) for r in out["results"]] == [("WH_Gold_monitoring", "created"),
                                                                     ("WH_Silver_monitoring", "updated")]
    assert _decoded(svc.fake.definitions["WH_Silver_monitoring"])["dataSources"][0]["databaseName"] == "Sales_Monitoring_Database"
    assert svc.settings["wh_monitoring_auto"] == []


def test_one_failure_doesnt_stop_the_rest(svc, monkeypatch):
    calls = {"n": 0}
    real = svc.fake.create_kql_queryset

    def flaky(ws, name, definition, description=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FabricError("ItemDisplayNameAlreadyInUse")
        return real(ws, name, definition, description)

    monkeypatch.setattr(svc.fake, "create_kql_queryset", flaky)
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "A"}, {"id": WH2, "name": "B"}], confirm=True)
    assert [r["state"] for r in out["results"]] == ["failed", "created"] and out["failed"] == 1


def test_auto_remembers_the_workspace(svc):
    svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}], confirm=True, auto=True)
    assert svc.settings["wh_monitoring_auto"] == [WS]
    assert svc.wh_monitoring_status(WS)["auto"] is True


@pytest.mark.parametrize("warehouses", [[], [{"id": "x", "name": "a"}], [{"id": WH, "name": ""}], ["WH_Gold"], "WH"])
def test_build_checks_what_it_is_given(svc, warehouses):
    with pytest.raises(ValueError):
        svc.wh_monitoring_build(WS, WS, DB, warehouses, confirm=True)


def test_methods_and_audit():
    assert wms.METHODS <= server.METHODS
    assert "wh_monitoring_status" in audit.READ_ONLY
    assert "wh_monitoring_view" not in audit.READ_ONLY and "wh_monitoring_build" not in audit.READ_ONLY
    assert not wms.METHODS & server.QUICK


def test_settings_have_safe_defaults():
    assert app_settings.DEFAULTS["wh_monitoring_views"] is True and app_settings.DEFAULTS["wh_monitoring_auto"] == []
    assert app_settings.RESTRICTED["wh_monitoring_views"] is False


def test_a_queryset_someone_else_named_the_same_is_left_alone(svc):
    svc.fake.querysets = [{"id": "theirs", "displayName": "WH_Gold_monitoring", "description": "Dana's queries"}]
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "WH_Gold"}], confirm=True)
    assert out["results"][0]["state"] == "failed" and "didn't make it" in out["results"][0]["error"]
    assert not [c for c in svc.fake.calls if c[0] in ("get_item_definition", "update_item_definition", "create_kql_queryset")]
    assert svc.wh_monitoring_status(WS)["querysets"] == {}, "only the app's own querysets count as built"


def test_a_definition_nested_too_deep_fails_that_warehouse_only(svc):
    svc.fake.querysets = [{"id": "q1", "displayName": "A_monitoring", "description": wms.QUERYSET_NOTE.format(name="A")}]
    deep = "[" * 100000 + "]" * 100000
    svc.fake.definitions["q1"] = {"parts": [{"path": "RealTimeQueryset.json", "payload": base64.b64encode(deep.encode()).decode()}]}
    out = svc.wh_monitoring_build(WS, WS, DB, [{"id": WH, "name": "A"}, {"id": WH2, "name": "B"}], confirm=True)
    assert [r["state"] for r in out["results"]] == ["failed", "created"]


# ---- a token that expires mid-session, and replies that aren't a KQL result (#1440)

class _NotJson(Resp):
    def json(self):
        raise ValueError("Expecting value")


def test_a_401_gets_a_fresh_token_once_and_returns_the_rows():
    post = FakePost()
    post.answers = [Resp(401, {"error": {"@message": "token expired"}}), Resp(200, _table(["a"], [[1]]))]
    out = wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None, refresh=lambda: "new")
    assert out["rows"] == [[1]]
    assert [s["headers"]["Authorization"] for s in post.sent] == ["Bearer tok", "Bearer new"]
    ids = [s["headers"]["x-ms-client-request-id"] for s in post.sent]
    assert ids[0] != ids[1]


@pytest.mark.parametrize("refresh", [None, lambda: "tok", lambda: "", lambda: (_ for _ in ()).throw(RuntimeError())])
def test_without_a_new_token_a_401_is_sent_once_and_keeps_its_message(refresh):
    post = FakePost()
    post.answers = [Resp(401, {})]
    with pytest.raises(RuntimeError, match="Contributor"):
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None, refresh=refresh)
    assert len(post.sent) == 1


def test_a_second_401_with_the_new_token_is_not_tried_a_third_time():
    post = FakePost()
    post.answers = [Resp(401, {}), Resp(401, {})]
    with pytest.raises(RuntimeError, match="Contributor") as err:
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None, refresh=lambda: "new")
    assert len(post.sent) == 2 and "new" not in str(err.value)


@pytest.mark.parametrize("answer", [_NotJson(200, None), Resp(200, [1, 2]), Resp(200, None), Resp(200, {"Tables": "x"}),
                                    Resp(200, {"Tables": [1]}), _NotJson(500, None)])
def test_a_reply_that_isnt_a_kql_result_fails_in_one_plain_line(answer):
    post = FakePost()
    post.answers = [answer] * wm.QUERY_ATTEMPTS
    with pytest.raises(RuntimeError) as err:
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok", sleep=lambda s: None)
    assert "\n" not in str(err.value) and "Expecting" not in str(err.value)


@pytest.mark.parametrize("body", [{}, {"Tables": None}, {"Tables": []}])
def test_a_reply_with_no_tables_is_an_empty_result(body):
    post = FakePost()
    post.answers = [Resp(200, body)]
    out = wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok")
    assert out == {"columns": [], "rows": [], "truncated": False}


def test_a_long_kusto_message_stays_one_bounded_line():
    post = FakePost()
    post.answers = [Resp(400, {"error": {"@message": "Semantic error\r\n\x1b[31m" + "y" * 2000}})]
    with pytest.raises(RuntimeError) as err:
        wm.query(post, URI, "db", wm.view_kql("summary", WH), "tok")
    text = str(err.value)
    assert "\n" not in text and "\x1b" not in text and "Semantic error" in text and "…" in text
    assert len(text) < 300 + 100


def test_the_service_hands_monitoring_a_kusto_refresh(svc, monkeypatch):
    seen = {}
    monkeypatch.setattr(svc.auth.__class__, "refreshed_token", lambda self, scopes: seen.setdefault("s", scopes) and "new",
                        raising=False)
    monkeypatch.setattr(wm, "query", lambda *a, **kw: seen.update(refresh=kw["refresh"]()) or {"rows": []})
    svc._wm_query({"cluster": URI, "name": "db"}, "x")
    from fabric_desktop.auth import KUSTO_SCOPES
    assert seen["refresh"] == "new" and seen["s"] == KUSTO_SCOPES
