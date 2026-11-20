"""opportunities/inventory.workloads: an item with no id is left out, and a definition that was read but
can't be understood is "not_read" with the reason. Pure; no Fabric."""

from __future__ import annotations

import base64
import datetime as dt
import json

from fabric_desktop.opportunities import inventory

NOW = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
WS = {"id": "ws1", "displayName": "Sales", "capacityId": "cap1"}


def _ipynb(cells):
    doc = {"cells": [{"cell_type": "code", "source": c} for c in cells], "metadata": {}}
    return {"definition": {"parts": [{"path": "notebook-content.ipynb", "payloadType": "InlineBase64",
                                      "payload": base64.b64encode(json.dumps(doc).encode()).decode()}]}}


def test_an_item_without_an_id_is_left_out_and_the_rest_are_sorted():
    items = [{"type": "Notebook", "displayName": "No id"}, {"id": "n2", "type": "Notebook", "displayName": "beta"},
             {"id": "n1", "type": "Notebook", "displayName": "Alpha"}, {"id": "r1", "type": "Report", "displayName": "R"}]
    out = inventory.workloads(WS, items, {"n1": _ipynb(["x = 1"]), "n2": _ipynb([])}, {}, now=NOW, days=7)
    assert [(w["type"], w["name"]) for w in out] == [("Notebook", "Alpha"), ("Notebook", "beta"), ("Report", "R")]
    assert out[0]["definition"] == "read" and out[0]["cells"] == ["x = 1"] and out[0]["default_lakehouse"] is None
    assert out[2]["definition"] == "unsupported" and out[2]["runs"]["read"] is False


def test_a_definition_that_cant_be_understood_is_not_read_with_why():
    items = [{"id": "n1", "type": "Notebook", "displayName": "Empty"},
             {"id": "p1", "type": "DataPipeline", "displayName": "Broken"},
             {"id": "n2", "type": "Notebook", "displayName": "Errored"}]
    defs = {"n1": {"definition": {"parts": [{"path": "other.json", "payload": ""}]}},
            "p1": {"definition": {"parts": "not a list"}},
            "n2": {"error": "403 from Fabric"}}
    out = {w["item_id"]: w for w in inventory.workloads(WS, items, defs, {"n1": []}, now=NOW, days=7)}
    assert out["n1"]["definition"] == "not_read"
    assert out["n1"]["problem"] == "The definition couldn't be read: the definition has no notebook-content part"
    assert out["p1"]["definition"] == "not_read" and out["p1"]["problem"].startswith("The definition couldn't be read: ")
    assert out["n2"]["problem"] == "403 from Fabric"
    assert out["n1"]["runs"]["runs"] == 0 and out["n1"]["workspace_name"] == "Sales"
