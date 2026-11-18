"""Workspace monitoring views: what the mutation run (batch 124, docs/quality/mutation-testing.md) showed no
test pinned. The row cap and the exact point it truncates, Kusto's columns and an empty reply, the request's
timeout, the queryset's name, version and other keys, and the 403 message with and without Kusto's words."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop import warehouse_monitoring as wm

WH = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
URI = "https://trd-abc123.z4.kusto.fabric.microsoft.com"


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers = status, body, {}

    def json(self):
        return self._body


class Post:
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.sent.append({"url": url, "body": json, "timeout": timeout})
        return self.answers.pop(0)


def run(*answers):
    post = Post(*answers)
    return wm.query(post, URI, "db", "WarehouseExecutions | take 1", "tok", sleep=lambda s: None), post


def table(rows, columns=None):
    t = {"Rows": rows}
    if columns is not None:
        t["Columns"] = columns
    return {"Tables": [t]}


def test_at_most_500_rows_and_kusto_is_asked_for_one_more():
    assert wm.MAX_ROWS == 500
    out, post = run(Resp(200, table([[1]] * 500, [])))
    props = json.loads(post.sent[0]["body"]["properties"])["Options"]
    assert props["truncationmaxrecords"] == 501
    assert len(out["rows"]) == 500 and out["truncated"] is False
    assert post.sent[0]["timeout"] == 90


def test_an_empty_reply_is_no_columns_no_rows_and_not_truncated():
    out, _ = run(Resp(200, {"Tables": []}))
    assert out == {"columns": [], "rows": [], "truncated": False}


def test_columns_take_datatype_or_columntype_and_may_be_missing():
    out, _ = run(Resp(200, table([["x", 1]], [{"ColumnName": "a", "DataType": "String"},
                                              {"ColumnName": "b", "ColumnType": "long"}])))
    assert out["columns"] == [{"name": "a", "type": "String"}, {"name": "b", "type": "long"}]
    out, _ = run(Resp(200, table([["x"]])))
    assert out["columns"] == [] and out["rows"] == [["x"]]


def test_a_403_adds_kustos_message_only_when_there_is_one():
    with pytest.raises(RuntimeError) as e:
        run(Resp(403, {"error": {"message": "Principal is not authorized"}}))
    assert str(e.value).endswith("shared with you. (Principal is not authorized)")
    with pytest.raises(RuntimeError) as e:
        run(Resp(403, {}))
    assert str(e.value).endswith("shared with you.")


def test_a_long_warehouse_name_is_cut_to_200_characters():
    assert wm.queryset_name("a" * 300) == "a" * 200 + "_monitoring"


def test_a_queryset_needs_a_database():
    with pytest.raises(ValueError, match="monitoring KQL database"):
        wm.queryset_definition(WH, URI, "  ")


def test_the_definition_is_json_indented_by_two():
    part = wm.queryset_definition(WH, URI, "db")["parts"][0]
    text = base64.b64decode(part["payload"]).decode()
    assert text.startswith('{\n  "queryset": {\n    "')


def _definition(queryset):
    payload = base64.b64encode(json.dumps({"queryset": queryset}).encode()).decode()
    return {"parts": [{"path": "RealTimeQueryset.json", "payload": payload}]}


def test_read_queryset_keeps_version_and_other_keys():
    got = wm.read_queryset(_definition({"tabs": [{"id": "t"}], "extra": 5}))
    assert got == {"version": "1.0.0", "dataSources": [], "tabs": [{"id": "t"}], "extra": 5}
    got = wm.read_queryset(_definition({"version": "2.0.0", "dataSources": [{"id": "s"}]}))
    assert got == {"version": "2.0.0", "dataSources": [{"id": "s"}], "tabs": []}


def test_a_definition_without_the_queryset_part_reads_as_an_empty_one():
    empty = {"version": "1.0.0", "dataSources": [], "tabs": []}
    assert wm.read_queryset({"parts": [{"path": "other.json", "payload": ""}]}) == empty
    assert wm.read_queryset({"definition": {"parts": []}}) == empty
