"""Copy jobs from a workspace (Data Factory DF-6, engine): source, destination, tables, runs; reads only."""

import base64
import json

import pytest

from fabric_desktop import audit, server
from fabric_desktop.orchestration import copyjob
from fabric_desktop.service import Service

WS = "11111111-2222-3333-4444-555555555555"
CJ = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"

# Shapes from Microsoft's Copy job definition article (examples 2 and 3), trimmed.
LAKEHOUSE = {"properties": {"jobMode": "Batch",
    "source": {"type": "LakehouseTable", "connectionSettings": {"type": "Lakehouse", "typeProperties": {
        "workspaceId": WS, "artifactId": "aaaaaaaa-6666-7777-8888-bbbbbbbbbbbb", "rootFolder": "Tables"}}},
    "destination": {"type": "LakehouseTable", "connectionSettings": {"type": "Lakehouse", "typeProperties": {
        "workspaceId": WS, "artifactId": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb", "rootFolder": "Tables"}}},
    "policy": {"timeout": "0.12:00:00"}},
    "activities": [{"id": "e1", "properties": {
        "source": {"datasetSettings": {"table": "publicholidays"}},
        "destination": {"writeBehavior": "Append", "datasetSettings": {"table": "publicholidays"}},
        "translator": {"type": "TabularTranslator"}}}]}
SQL_CDC = {"properties": {"jobMode": "CDC",
    "source": {"type": "AzureSqlTable", "connectionSettings": {"type": "AzureSqlDatabase",
        "typeProperties": {"database": f"erp;Password={TOKEN}"},
        "externalReferences": {"connection": "5A1C7D2E-0000-4000-8000-000000000001"}}},
    "destination": {"type": "DelimitedText", "connectionSettings": {"type": "AzureBlobStorage",
        "externalReferences": {"connection": "1f41f4d4-f5fa-4221-a21b-f36dcdfedf41"}}},
    "policy": {"timeout": "0.12:00:00", "retry": 2}},
    "activities": [{"id": "t1", "properties": {
        "source": {"datasetSettings": {"schema": "primary", "table": "table1"},
                   "changeDataSettings": {"readMethod": "SnapshotPlusIncremental", "columns": [{"name": "id", "type": "Int32"}]}},
        "destination": {"datasetSettings": {"location": {"type": "AzureBlobStorageLocation", "container": "c1", "folderPath": "f1"}},
                        "writeBehavior": "Upsert", "upsertSettings": {"keys": ["id"]}, "tableOption": "autoCreate"},
        "enableStaging": True,
        "translator": {"type": "TabularTranslator", "mappings": [{"source": {"name": "id"}, "destination": {"name": "id2"}}]}}}]}


def _definition(content, path="copyjob-content.json"):
    text = content if isinstance(content, str) else json.dumps(content)
    return {"definition": {"parts": [{"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"},
                                     {"path": ".platform", "payload": base64.b64encode(b"{}").decode(), "payloadType": "InlineBase64"}]}}


class FakeFabric:
    def __init__(self):
        self.calls = []
        self.definition = _definition(LAKEHOUSE)

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": "c2", "type": "CopyJob", "displayName": "cj_zeta"}, {"id": "d1", "type": "Dataflow", "displayName": "df"},
                {"id": "c1", "type": "CopyJob", "displayName": "cj_Erp", "description": "ERP", "folderId": "f1"}]

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", ws, item))
        return self.definition

    def list_job_instances(self, ws, item):
        self.calls.append(("list_job_instances", ws, item))
        return [{"id": "j2", "status": "Failed", "invokeType": "Scheduled", "startTimeUtc": "2026-10-25T06:00:00Z",
                 "endTimeUtc": "2026-10-25T06:01:00Z", "failureReason": {"message": f"Bearer {TOKEN} expired"}},
                {"id": "j1", "status": "Completed", "startTimeUtc": "2026-10-24T06:00:00Z", "endTimeUtc": "2026-10-24T06:00:30Z"}]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    s = Service(root=tmp_path / "app")
    s.fake = fake
    return s


def test_a_workspaces_copy_jobs_by_name(svc):
    out = svc.workspace_copy_jobs(WS)
    assert [c["name"] for c in out["copy_jobs"]] == ["cj_Erp", "cj_zeta"]
    assert out["copy_jobs"][0] == {"workspace_id": WS, "item_id": "c1", "name": "cj_Erp", "description": "ERP", "folder_id": "f1"}


def test_a_lakehouse_to_lakehouse_batch_job_could_run_on_this_pc(svc):
    out = svc.workspace_copy_job_definition(WS, CJ, name="cj_holidays")
    assert out["ok"] and out["name"] == "cj_holidays" and out["problems"] == []
    assert (out["job_mode"], out["timeout"], out["retry"]) == ("Batch", "0.12:00:00", None)
    assert out["source"]["item_id"] == "aaaaaaaa-6666-7777-8888-bbbbbbbbbbbb" and out["source"]["root_folder"] == "Tables"
    [t] = out["tables"]
    assert (t["source"]["table"], t["destination"]["table"], t["write_behavior"], t["mapped_columns"]) == (
        "publicholidays", "publicholidays", "Append", 0)
    assert out["local_check"] == {"possible": True, "reasons": []}


def test_an_incremental_sql_job_lists_keys_and_runs_in_fabric_only(svc):
    svc.fake.definition = _definition(SQL_CDC)
    out = svc.workspace_copy_job_definition(WS, CJ)
    assert out["source"]["connection_id"] == "5a1c7d2e-0000-4000-8000-000000000001" and out["retry"] == 2
    [t] = out["tables"]
    assert (t["source"]["schema"], t["read_method"], t["incremental_columns"], t["keys"]) == (
        "primary", "SnapshotPlusIncremental", ["id"], ["id"])
    assert (t["destination"]["folder"], t["table_option"], t["staging"], t["mapped_columns"]) == ("c1/f1", "autoCreate", True, 1)
    check = out["local_check"]
    assert not check["possible"] and len(check["reasons"]) == 3 and "CDC" in check["reasons"][2]


def test_nothing_secret_comes_back(svc, tmp_path):
    svc.fake.definition = _definition(SQL_CDC)
    assert TOKEN not in json.dumps(svc.workspace_copy_job_definition(WS, CJ))
    assert TOKEN not in json.dumps(svc.fabric_copy_job_runs(WS, CJ))
    assert not any(TOKEN.encode() in p.read_bytes() for p in (tmp_path / "app").rglob("*") if p.is_file())


@pytest.mark.parametrize("definition, problem", [
    (_definition({}, path="other.json"), "copyjob-content.json"),
    (_definition("not json"), "copyjob-content.json"),
    (_definition("[1]"), "copyjob-content.json"),
    (_definition({"properties": {"jobMode": "Stream"}, "activities": []}), "Unknown job mode Stream"),
    (_definition({"properties": {"jobMode": "Batch"}, "activities": {"a": 1}}), "activities"),
])
def test_a_definition_it_cant_read_says_why(svc, definition, problem):
    svc.fake.definition = definition
    out = svc.workspace_copy_job_definition(WS, CJ)
    assert problem in out["problems"][0] and out["tables"] == []


def test_odd_values_dont_break_the_reader():
    out = copyjob.describe(_definition({"properties": {"jobMode": "Batch", "source": "x", "policy": {"retry": "2"}},
                                        "activities": [5, {"properties": {"destination": {"upsertSettings": {"keys": [1, "k"]},
                                                                                          "writeBehaviour": "Merge"}}}]}))
    assert out["ok"] and out["retry"] is None and out["source"]["type"] == ""
    [t] = out["tables"]
    assert (t["keys"], t["write_behavior"]) == (["k"], "Merge")


def test_runs_newest_first_in_the_runs_shape(svc):
    out = svc.fabric_copy_job_runs(WS, CJ, name="cj_erp")
    assert [(r["job_id"], r["state"], r["seconds"], r["kind"], r["name"]) for r in out["runs"]] == [
        ("j2", "failed", 60.0, "copy job", "cj_erp"), ("j1", "succeeded", 30.0, "copy job", "cj_erp")]


@pytest.mark.parametrize("call", ["workspace_copy_job_definition", "fabric_copy_job_runs"])
@pytest.mark.parametrize("ws, item", [("", CJ), (WS, ""), ("../x", CJ), (WS, CJ + "\n")])
def test_ids_must_be_guids(svc, call, ws, item):
    with pytest.raises(ValueError):
        getattr(svc, call)(ws, item)
    assert svc.fake.calls == []


def test_all_three_are_read_only_and_not_quick():
    for name in ("workspace_copy_jobs", "workspace_copy_job_definition", "fabric_copy_job_runs"):
        assert name in server.METHODS and name in audit.READ_ONLY and name not in server.QUICK


ODD = [None, 1, "x", True, 1.5, [], {}, [1], {"a": 1}]


@pytest.mark.parametrize("result", [None, 1, "x", [], {"definition": 1}, {"definition": {"parts": "x"}},
                                    {"definition": {"parts": [1, None]}},
                                    {"definition": {"parts": [{"path": "copyjob-content.json", "payload": 5}]}}])
def test_a_getdefinition_reply_of_any_shape_is_a_problem_not_a_crash(result):
    out = copyjob.describe(result)
    assert not out["ok"] and out["tables"] == [] and out["problems"]


@pytest.mark.parametrize("odd", ODD)
def test_odd_values_anywhere_in_a_copy_job_dont_break_the_reader(odd):
    for content in ({"properties": odd, "activities": odd},
                    {"properties": {"jobMode": odd, "source": odd, "destination": odd, "policy": odd}, "activities": [odd, {"properties": odd}]},
                    {"properties": {"source": {"connectionSettings": {"typeProperties": odd, "externalReferences": odd, "type": odd}}}},
                    {"activities": [{"id": odd, "properties": {"source": {"changeDataSettings": odd, "datasetSettings": {"location": odd}},
                                                               "destination": {"upsertSettings": odd, "datasetSettings": odd}}}]},
                    {"activities": [{"properties": {"source": {"changeDataSettings": {"columns": odd, "readMethod": odd}},
                                                    "destination": {"upsertSettings": {"keys": odd}}, "translator": {"mappings": odd}}}]}):
        out = copyjob.describe(_definition(content))
        assert isinstance(out["tables"], list) and isinstance(out["problems"], list)
        assert all(isinstance(t["keys"], list) and isinstance(t["incremental_columns"], list) for t in out["tables"])
