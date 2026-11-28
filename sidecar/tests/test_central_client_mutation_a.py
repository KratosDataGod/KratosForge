"""central/client.py held to its exact numbers and answers: business keys go in batches of 20000, an
answer without "inserted" counted as none new, a key table that can't be made refused by name, any
4xx with a JSON object a refusal, and every call to UDF_Platform given 300 seconds. UDF_Platform is a
scripted session and the platform a fake client; nothing reaches a tenant."""

from __future__ import annotations

import json

import pytest
import requests

from fabric_desktop.central import core
from fabric_desktop.central.client import KEY_BATCH, FabricPlatform, PlatformKeys


def response(status, body=None):
    resp = requests.Response()
    resp.status_code, resp.encoding = status, "utf-8"
    resp._content = json.dumps(body).encode() if body is not None else b""
    resp._content_consumed = True
    return resp


class Session:
    def __init__(self, answers):
        self.answers, self.timeouts = list(answers), []

    def post(self, url, json=None, timeout=None, headers=None):  # noqa: A002 - requests' own name
        self.timeouts.append(timeout)
        return self.answers.pop(0)


class Client:
    """The platform's key operations, answered from what the test gives."""

    def __init__(self, created=True, ensure=True, inserted=True):
        self.created, self.ensure_answer, self.inserted = created, ensure, inserted
        self.calls: list = []

    def call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        if op == "key_stats":
            return {"created": self.created}
        if op == "ensure_key_table":
            if isinstance(self.ensure_answer, Exception):
                raise self.ensure_answer
            return self.ensure_answer
        if op == "issue_keys":
            keys = kwargs["business_keys"]
            out = {"keys": {k: str(i + 1) for i, k in enumerate(keys)}}
            if self.inserted:
                out["inserted"] = len(keys)
            return out
        raise AssertionError(op)


def test_business_keys_go_in_batches_of_20000():
    assert KEY_BATCH == 20000
    client = Client()
    keys = PlatformKeys(client, "dev")
    assert keys.batch == 20000
    wanted = [f"k{i:05d}" for i in range(20001)]
    found, inserted = keys.issue("customer", wanted)
    sizes = [len(kw["business_keys"]) for op, kw in client.calls if op == "issue_keys"]
    assert sizes == [20000, 1]
    assert inserted == 20001 and len(found) == 20001


def test_an_answer_without_inserted_counts_no_new_keys():
    keys = PlatformKeys(Client(inserted=False), "dev")
    found, inserted = keys.issue("customer", ["a", "b"])
    assert found == {"a": 1, "b": 2} and inserted == 0


@pytest.mark.parametrize("created, exists", [(True, True), (1, True), (False, False), (None, False)])
def test_exists_says_true_or_false(created, exists):
    assert PlatformKeys(Client(created=created), "dev").exists("customer") is exists


@pytest.mark.parametrize("answer, made", [(True, True), ({"made": 1}, True), (0, False), (None, False)])
def test_ensure_says_true_or_false(answer, made):
    assert PlatformKeys(Client(ensure=answer), "dev").ensure("customer") is made


def test_a_key_table_that_cant_be_made_is_refused_by_name_and_no_key_is_issued():
    client = Client(created=False, ensure=core.PlatformError("Only platform admins may do that."))
    with pytest.raises(core.PlatformError) as caught:
        PlatformKeys(client, "dev").issue("customer", ["a"])
    assert str(caught.value) == ("keys.customer isn't one of the platform's key tables yet. Ask a platform admin "
                                 "for it: Platform > Set up > Business keys > Request a key table")
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert [op for op, _ in client.calls] == ["key_stats", "ensure_key_table"]


@pytest.mark.parametrize("status", [400, 402, 499])
def test_a_4xx_with_a_json_object_and_no_errors_is_refused_with_its_text(status):
    s = Session([response(status, {"output": "kept"})])
    platform = FabricPlatform(lambda: "tok", "ws", "fn", session=s, sleep=lambda _s: None)
    read = sorted(core.READ_OPERATIONS)[0]
    with pytest.raises(core.PlatformError) as caught:
        platform.call(read)
    assert str(caught.value) == f'The platform couldn\'t {read.replace("_", " ")}: {{"output": "kept"}}'


def test_a_200_with_a_json_object_is_its_output():
    s = Session([response(200, {"output": "kept"})])
    platform = FabricPlatform(lambda: "tok", "ws", "fn", session=s, sleep=lambda _s: None)
    assert platform.call(sorted(core.READ_OPERATIONS)[0]) == "kept"


def test_every_call_to_udf_platform_waits_300_seconds():
    s = Session([response(200, {"output": 1}), response(200, {"output": {"result": 2}})])
    platform = FabricPlatform(lambda: "tok", "ws", "fn", session=s, sleep=lambda _s: None)
    change = next(op for op in sorted(core.OPERATIONS)
                  if op not in core.READ_OPERATIONS and op not in core.REPEATABLE_OPERATIONS)
    assert platform.call(sorted(core.READ_OPERATIONS)[0]) == 1
    assert platform.call(change) == 2
    assert s.timeouts == [300, 300]
