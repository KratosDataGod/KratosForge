"""Dataflow Gen2 from a workspace (Data Factory DF-7, engine): queries, destinations, refreshes; reads only."""

import base64
import json

import pytest

from fabric_desktop import audit, server
from fabric_desktop.orchestration import mashup
from fabric_desktop.service import Service

WS = "11111111-2222-3333-4444-555555555555"
DF = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LH = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"

MASHUP = f'''section Section1;
// A comment with shared Fake = 1; inside it
shared Rates = let
  Source = Web.Contents("https://fx.example.com/rates", [Headers = [Authorization = "Bearer {TOKEN}"]]),
  Text = "a ""quoted"" string; with a semicolon"
in
  Source;
[DataDestinations = {{[Definition = [Kind = "Reference", QueryName = "Orders_DataDestination", IsNewTarget = true],
  Settings = [Kind = "Manual", AllowCreation = true, ColumnSettings = [Mappings = {{}}], DynamicSchema = false,
  UpdateMethod = [Kind = "Replace"], TypeSettings = [Kind = "Table"]]]}}]
shared Orders = let
  Source = Sql.Database("erp.contoso.local", "erp"),
  Rows = Table.SelectRows(Source, each [Year] > 2020)
in
  Rows;
shared Orders_DataDestination = let
  Pattern = Lakehouse.Contents([CreateNavigationProperties = false, EnableFolding = false]),
  Navigation_1 = Pattern{{[workspaceId = "{WS}"]}}[Data],
  Navigation_2 = Navigation_1{{[lakehouseId = "{LH}"]}}[Data],
  TableNavigation = Navigation_2{{[Id = "orders", ItemKind = "Table"]}}?[Data]?
in
  TableNavigation;
shared #"Odd ""name""" = 1;
'''
META = {"formatVersion": "202502", "queriesMetadata": {
    "Rates": {"queryId": "q1", "queryName": "Rates", "loadEnabled": False},
    "Orders": {"queryId": "q2", "queryName": "Orders", "loadEnabled": True},
    "Orders_DataDestination": {"queryId": "q3", "queryName": "Orders_DataDestination", "isHidden": True, "loadEnabled": False}},
    "connections": [{"path": "erp.contoso.local;erp", "kind": "SQL",
                     "connectionId": json.dumps({"ClusterId": "c1", "DatasourceId": "5a1c7d2e-0000-4000-8000-000000000001"})},
                    {"path": "Lakehouse", "kind": "Lakehouse", "connectionId": "plain-id"}]}


def _b64(text):
    return base64.b64encode(text.encode()).decode()


DEFINITION = {"definition": {"parts": [
    {"path": "queryMetadata.json", "payload": _b64(json.dumps(META)), "payloadType": "InlineBase64"},
    {"path": "mashup.pq", "payload": _b64(MASHUP), "payloadType": "InlineBase64"},
    {"path": ".platform", "payload": _b64("{}"), "payloadType": "InlineBase64"}]}}


class FakeFabric:
    def __init__(self):
        self.calls = []
        self.definition = DEFINITION

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": "d2", "type": "Dataflow", "displayName": "df_zeta"}, {"id": "p1", "type": "DataPipeline", "displayName": "pl"},
                {"id": "d1", "type": "Dataflow", "displayName": "df_Budget", "description": "Excel", "folderId": "f1"}]

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", ws, item))
        return self.definition

    def list_job_instances(self, ws, item):
        self.calls.append(("list_job_instances", ws, item))
        return [{"id": "j2", "status": "Failed", "invokeType": "Manual", "startTimeUtc": "2026-10-25T06:00:00Z",
                 "endTimeUtc": "2026-10-25T06:02:00Z", "failureReason": {"message": f"Bearer {TOKEN} expired"}},
                {"id": "j1", "status": "Completed", "startTimeUtc": "2026-10-24T06:00:00Z", "endTimeUtc": "2026-10-24T06:01:30Z"}]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    s = Service(root=tmp_path / "app")
    s.fake = fake
    return s


def test_a_workspaces_dataflows_by_name(svc):
    out = svc.workspace_dataflows(WS)
    assert [d["name"] for d in out["dataflows"]] == ["df_Budget", "df_zeta"]
    assert out["dataflows"][0] == {"workspace_id": WS, "item_id": "d1", "name": "df_Budget", "description": "Excel", "folder_id": "f1"}


def test_queries_destinations_and_connections(svc):
    out = svc.workspace_dataflow_definition(WS, DF, name="df_orders")
    assert out["ok"] and out["name"] == "df_orders" and out["problems"] == []
    assert [q["name"] for q in out["queries"]] == ["Rates", "Orders", "Orders_DataDestination", 'Odd "name"']
    rates, orders, dest, _ = out["queries"]
    assert (rates["load_enabled"], rates["destination"]) == (False, None)
    assert 'a ""quoted"" string; with a semicolon' in rates["m"]  # a ; inside a string doesn't split
    assert dest["hidden"] and not orders["hidden"] and orders["m"].startswith("let") and orders["m"].endswith("Rows")
    assert orders["destination"] == {"query": "Orders_DataDestination", "kind": "Lakehouse", "workspace_id": WS,
                                     "item_id": LH, "schema": "", "table": "orders", "update_method": "Replace", "new_table": True}
    assert out["connections"] == [
        {"kind": "SQL", "path": "erp.contoso.local;erp", "connection_id": "5a1c7d2e-0000-4000-8000-000000000001"},
        {"kind": "Lakehouse", "path": "Lakehouse", "connection_id": "plain-id"}]


def test_a_key_in_the_m_code_is_removed(svc, tmp_path):
    out = svc.workspace_dataflow_definition(WS, DF)
    assert TOKEN not in json.dumps(out) and "[removed]" in out["queries"][0]["m"]
    stored = [p for p in (tmp_path / "app").rglob("*") if p.is_file()]
    assert not any(TOKEN.encode() in p.read_bytes() for p in stored)  # nothing kept on disk


def test_a_definition_without_m_says_why(svc):
    svc.fake.definition = {"definition": {"parts": [{"path": "model.json", "payload": _b64("{}"), "payloadType": "InlineBase64"}]}}
    out = svc.workspace_dataflow_definition(WS, DF, name="df_gen1")
    assert out["ok"] is False and out["queries"] == [] and "mashup.pq" in out["problems"][0]


@pytest.mark.parametrize("meta", ["[1, 2]", "\"text\"", "3"])
def test_query_metadata_that_isnt_an_object_still_reads_the_queries(svc, meta):
    parts = [{"path": "queryMetadata.json", "payload": _b64(meta), "payloadType": "InlineBase64"},
             {"path": "mashup.pq", "payload": _b64(MASHUP), "payloadType": "InlineBase64"}]
    svc.fake.definition = {"definition": {"parts": parts}}
    out = svc.workspace_dataflow_definition(WS, DF)
    assert out["ok"] and len(out["queries"]) == 4 and out["connections"] == []
    assert "queryMetadata.json" in out["problems"][0]


def test_refreshes_newest_first_in_the_runs_shape(svc):
    out = svc.fabric_dataflow_refreshes(WS, DF, name="df_orders")
    assert [(r["job_id"], r["state"], r["seconds"], r["kind"]) for r in out["refreshes"]] == [
        ("j2", "failed", 120.0, "dataflow"), ("j1", "succeeded", 90.0, "dataflow")]
    assert TOKEN not in out["refreshes"][0]["message"] and out["refreshes"][0]["name"] == "df_orders"


@pytest.mark.parametrize("call", ["workspace_dataflow_definition", "fabric_dataflow_refreshes"])
@pytest.mark.parametrize("ws, item", [("", DF), (WS, ""), ("../x", DF), (WS, DF + "\n")])
def test_ids_must_be_guids(svc, call, ws, item):
    with pytest.raises(ValueError):
        getattr(svc, call)(ws, item)
    assert svc.fake.calls == []


def test_statements_skip_comments_and_nested_brackets():
    text = 'section S; /* a; b */ shared A = {1; 2}[x]; shared B = 2'
    assert [q["name"] for q in mashup.queries(text)] == ["A", "B"]


def test_all_three_are_read_only_and_not_quick():
    for name in ("workspace_dataflows", "workspace_dataflow_definition", "fabric_dataflow_refreshes"):
        assert name in server.METHODS and name in audit.READ_ONLY and name not in server.QUICK


ODD = [None, 1, "x", True, 1.5, [], {}, [1], {"a": 1}]
M = ('section Section1;\nshared Products = let a = 1 in a;\nshared Helper = 2;\n')


def _parts(m, meta):
    return {"definition": {"parts": [{"path": "mashup.pq", "payload": _b64(m)},
                                     {"path": "queryMetadata.json", "payload": _b64(json.dumps(meta))}]}}


@pytest.mark.parametrize("result", [None, 1, "x", [], {"definition": 1}, {"definition": {"parts": "x"}},
                                    {"definition": {"parts": [1, None]}}, {"definition": {"parts": [{"path": 1, "payload": 5}]}}])
def test_a_getdefinition_reply_of_any_shape_is_a_problem_not_a_crash(result):
    out = mashup.describe(result)
    assert not out["ok"] and out["queries"] == [] and out["problems"]


@pytest.mark.parametrize("odd", ODD)
def test_odd_query_metadata_doesnt_break_the_reader(odd):
    for meta in ({"queriesMetadata": odd, "connections": odd},
                 {"queriesMetadata": {"Products": odd}, "connections": [odd, {"kind": odd, "path": odd, "connectionId": odd}]},
                 {"queriesMetadata": {"Products": {"loadEnabled": odd, "isHidden": odd}}}):
        out = mashup.describe(_parts(M, meta))
        assert [q["name"] for q in out["queries"]] == ["Products", "Helper"]
        assert all(isinstance(c[k], str) for c in out["connections"] for k in ("kind", "path", "connection_id"))


def test_query_metadata_that_isnt_json_is_a_problem_and_the_scan_counts_it_missed():
    from fabric_desktop.fabric_connections_service import dataflow_connection_ids
    result = {"definition": {"parts": [{"path": "mashup.pq", "payload": _b64(M)},
                                       {"path": "queryMetadata.json", "payload": _b64("{not json")}]}}
    out = mashup.describe(result)
    assert out["ok"] and [q["name"] for q in out["queries"]] == ["Products", "Helper"]
    assert out["problems"] == ["queryMetadata.json isn't valid JSON, so load flags and connections can't be shown."]
    with pytest.raises(ValueError, match="isn't valid JSON"):
        dataflow_connection_ids(result)
