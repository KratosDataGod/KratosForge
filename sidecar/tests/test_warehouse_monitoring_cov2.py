"""Workspace monitoring for a warehouse (warehouse_monitoring.py): a queryset definition without a database
or without a queryset part, a 401 whose reply is closed before the fresh token's read, an answer with no
tables and an error reply that isn't JSON. The post is a fake: nothing reaches Fabric."""

import base64
import json

import pytest

from fabric_desktop import warehouse_monitoring as wm

WH = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
URI = "https://trd-abc123.z4.kusto.fabric.microsoft.com"


class Resp:
    def __init__(self, status, body=None, bad_json=False):
        self.status_code, self._body, self.bad_json = status, body, bad_json
        self.headers = {}
        self.closed = False

    def json(self):
        if self.bad_json:
            raise ValueError("not JSON")
        return self._body

    def close(self):
        self.closed = True


def _part(body, path="RealTimeQueryset.json"):
    return {
        "path": path,
        "payload": base64.b64encode(json.dumps(body).encode()).decode(),
    }


def test_queryset_definition_needs_a_database():
    with pytest.raises(ValueError, match="Pick the monitoring KQL database."):
        wm.queryset_definition(WH, URI, "  ")


def test_read_queryset_skips_other_parts_and_refuses_a_body_with_no_queryset_object():
    for definition in ({"definition": {"parts": [_part({}, path=".platform")]}},
                       {"parts": [_part({"queryset": ["not", "an", "object"]})]},
                       {"parts": [_part([1, 2])]}):
        with pytest.raises(ValueError, match="without its tabs"):
            wm.read_queryset(definition)
    kept = wm.read_queryset(
        {
            "parts": [
                _part({}, path="x"),
                _part({"queryset": {"tabs": [{"id": "t"}], "extra": 1}}),
            ]
        }
    )
    assert kept == {
        "version": "1.0.0",
        "dataSources": [],
        "tabs": [{"id": "t"}],
        "extra": 1,
    }


def test_query_closes_the_stale_reply_before_reading_with_a_fresh_token():
    stale = Resp(401, {})
    answers = [stale, Resp(200, {"Tables": []})]
    tokens = []

    def post(url, json=None, headers=None, timeout=None):
        tokens.append(headers["Authorization"])
        return answers.pop(0)

    out = wm.query(
        post, URI, "db", "print 1", "old", sleep=lambda s: None, refresh=lambda: "new"
    )  # pragma: allowlist secret (fake tokens)
    assert out == {"columns": [], "rows": [], "truncated": False}
    assert stale.closed is True
    assert tokens == ["Bearer old", "Bearer new"]


def test_an_error_reply_that_isnt_json_still_gives_its_status():
    def post(url, json=None, headers=None, timeout=None):
        return Resp(404, bad_json=True)

    with pytest.raises(RuntimeError) as raised:
        wm.query(post, URI, "db", "print 1", "tok", sleep=lambda s: None)
    assert str(raised.value) == "The monitoring database answered 404."
