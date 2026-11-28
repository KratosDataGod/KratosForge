"""central/udf.py paths that mutation testing (batch 17, docs/quality/mutation-testing.md) showed no test
checked: the definition's parts as Fabric's updateDefinition takes them."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop.central import udf


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_parts_carry_each_file_as_inline_base64_in_order():
    files = {"function_app.py": "print('é')\n", "definition.json": '{"a": 1}'}
    got = udf.parts(files)
    assert [p["path"] for p in got] == ["function_app.py", "definition.json"]
    assert all(p["payloadType"] == "InlineBase64" for p in got)
    assert [base64.b64decode(p["payload"]).decode("utf-8") for p in got] == list(files.values())


def test_parts_of_the_real_definition_decode_back_to_it():
    files = udf.definition("ws-1", "sql-1")
    got = {p["path"]: base64.b64decode(p["payload"]).decode("utf-8") for p in udf.parts(files)}
    assert got == files
    meta = json.loads(got["definition.json"])
    assert meta["connectedDataSources"] == [{"alias": "platform", "artifactId": "sql-1", "artifactType": "SqlDbNative",
                                              "workspaceId": "ws-1"}]


def test_parts_of_no_files_is_empty():
    assert udf.parts({}) == []
