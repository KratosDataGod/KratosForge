"""Pipelines straight from a Fabric workspace (Data Factory DF-2, engine): list and getDefinition, reads only."""

import json
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop.service import Service

HERE = Path(__file__).parent / "pipelines"
WS = "11111111-2222-3333-4444-555555555555"
PL = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ITEM_DEFINITION = json.loads((HERE / "orders_daily.item-definition.json").read_text(encoding="utf-8"))


class FakeFabric:
    def __init__(self):
        self.calls = []

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": "p2", "type": "DataPipeline", "displayName": "pl_zeta"},
                {"id": "nb", "type": "Notebook", "displayName": "Load"},
                {"id": "p1", "type": "DataPipeline", "displayName": "pl_Alpha", "description": "Loads", "folderId": "f1"}]

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", ws, item, fmt))
        return self.definition


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fake = FakeFabric()
    fake.definition = ITEM_DEFINITION
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    s = Service(root=tmp_path / "app")
    s.fake = fake
    return s


def test_a_workspaces_pipelines_by_name(svc):
    out = svc.workspace_pipelines(WS)
    assert [p["name"] for p in out["pipelines"]] == ["pl_Alpha", "pl_zeta"]
    assert out["pipelines"][0] == {"workspace_id": WS, "item_id": "p1", "name": "pl_Alpha", "description": "Loads",
                                   "folder_id": "f1"}


def test_a_definition_comes_back_as_text_the_other_pipeline_calls_take(svc, tmp_path):
    out = svc.workspace_pipeline_definition(WS, PL, name="pl_orders_daily")
    assert svc.fake.calls == [("get_item_definition", WS, PL, None)]
    assert out["ok"] and out["name"] == "pl_orders_daily" and out["pipeline"]["activity_count"] > 0
    secure = next(p for p in out["pipeline"]["parameters"] if p["name"] == "api_key")
    assert secure["default"] == "(hidden)"
    # The same text works with the existing calls, unchanged.
    assert svc.pipeline_inspect(text=out["text"])["name"] == "pl_orders_daily"
    assert "activities" in svc.pipeline_compatibility(text=out["text"])
    stored = [p for p in (tmp_path / "app").rglob("*") if p.is_file()]
    assert not any(b"not-a-real-secret" in p.read_bytes() for p in stored)  # nothing kept on disk


def test_a_definition_this_version_cant_read_says_why(svc):
    svc.fake.definition = {"definition": {"parts": [{"path": "something-else.json", "payload": "e30=", "payloadType": "InlineBase64"}]}}
    out = svc.workspace_pipeline_definition(WS, PL, name="pl_odd")
    assert out["ok"] is False and out["name"] == "pl_odd" and out["pipeline"]["diagnostics"]


@pytest.mark.parametrize("ws, item", [("", PL), (WS, ""), ("../x", PL), (WS, PL + "\n"), (WS, "p1/../../x")])
def test_ids_must_be_guids(svc, ws, item):
    with pytest.raises(ValueError):
        svc.workspace_pipeline_definition(ws, item)
    assert svc.fake.calls == []


def test_listing_needs_a_workspace_guid(svc):
    with pytest.raises(ValueError):
        svc.workspace_pipelines("not-a-guid")


def test_both_calls_are_read_only_and_not_quick():
    for name in ("workspace_pipelines", "workspace_pipeline_definition"):
        assert name in server.METHODS and name in audit.READ_ONLY and name not in server.QUICK
