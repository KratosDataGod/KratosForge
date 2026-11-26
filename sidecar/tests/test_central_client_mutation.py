"""Mutation batch 373: how central/client.py reads UDF_Platform's answers and issues keys in batches."""

import json

import pytest
import requests

from fabric_desktop.central import client, core
from fabric_desktop.central.client import FabricPlatform, LocalPlatform, PlatformKeys


class Resp:
    def __init__(self, status=200, body=None, raw=None, reason=""):
        self.status_code, self.reason = status, reason
        self.content = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        self.text = self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)


def fabric(**kw):
    return FabricPlatform(lambda: "token", "ws", "fn", session=kw.pop("session", None), sleep=lambda s: None)


# --------------------------------------------------------------------------- reading an answer


@pytest.mark.parametrize("status", [401, 403])
def test_no_access_is_a_permission_error(status):
    with pytest.raises(PermissionError, match="Viewer"):
        fabric()._answer("list_domains", Resp(status, {"output": []}))


@pytest.mark.parametrize("status", [429, 500, 503])
def test_busy_or_throttled_is_an_http_error_naming_the_status_and_reason(status):
    with pytest.raises(requests.HTTPError, match=f"answered {status} Busy\\. Try again"):
        fabric()._answer("list_domains", Resp(status, {"output": []}, reason="Busy"))


def test_no_reason_adds_nothing_after_the_status():
    with pytest.raises(requests.HTTPError, match="answered 500\\. Try again"):
        fabric()._answer("list_domains", Resp(500, {"output": []}))


@pytest.mark.parametrize("status", [400, 499])
def test_a_client_error_with_a_json_object_is_the_platforms_refusal(status):
    with pytest.raises(core.PlatformError, match="couldn't list domains: no such domain"):
        fabric()._answer("list_domains", Resp(status, {"errors": [{"message": "no such domain"}]}))


def test_a_400_without_errors_is_still_refused_with_the_answers_text():
    with pytest.raises(core.PlatformError, match='couldn\'t list domains: \\{"output": "x"\\}'):
        fabric()._answer("list_domains", Resp(400, {"output": "x"}))


def test_an_answer_below_400_is_read():
    assert fabric()._answer("list_domains", Resp(399, {"output": ["a"]})) == ["a"]


def test_an_answer_that_isnt_json_says_so():
    with pytest.raises(core.PlatformError, match="something that isn't JSON: <html>busy</html>"):
        fabric()._answer("list_domains", Resp(200, raw=b"<html>busy</html>"))


def test_json_that_isnt_an_object_says_so():
    with pytest.raises(core.PlatformError, match="JSON that isn't an object: \\[1, 2\\]"):
        fabric()._answer("list_domains", Resp(200, [1, 2]))


def test_an_empty_answer_is_an_empty_object():
    assert fabric()._answer("list_domains", Resp(200)) == {}
    assert client._body(Resp(200)) == {}
    assert client._body(Resp(200, raw=b"nope")) is client._NOT_JSON


def test_each_call_is_posted_with_a_five_minute_timeout_and_the_token():
    seen = []

    class Session:
        def post(self, url, json=None, timeout=None, headers=None):
            seen.append((url, timeout, headers))
            return Resp(200, {"output": []})

    assert fabric(session=Session()).call("list_domains") == []
    url, timeout, headers = seen[0]
    assert url.endswith("/workspaces/ws/userDataFunctions/fn/functions/list_domains/invoke")
    assert timeout == 300 and headers == {"Authorization": "Bearer token"}


# --------------------------------------------------------------------------- keys


class Keys:
    """A key service stand-in that records each call."""

    def __init__(self, created=True, ensure_refused=False, pages=None):
        self.calls, self.created, self.ensure_refused, self.pages = [], created, ensure_refused, list(pages or [])
        self.next = 1

    def call(self, op, **kw):
        self.calls.append((op, kw))
        if op == "key_stats":
            return {"created": self.created, "name": kw["name"]}
        if op == "ensure_key_table":
            if self.ensure_refused:
                raise core.PlatformError("only platform admins")
            return {"made": True}
        if op == "lookup_key":
            return {"value": kw["value"], "key": 7}
        if op == "issue_keys":
            out = {}
            for b in kw["business_keys"]:
                out[b] = self.next
                self.next += 1
            return {"keys": out, "inserted": len(out)} if kw["business_keys"][0] != "old" else {"keys": out}
        if op == "export_keys":
            return self.pages.pop(0) if self.pages else []
        raise AssertionError(op)


def test_the_default_batch_is_twenty_thousand_keys():
    assert client.KEY_BATCH == 20000
    k = Keys()
    keys, new = PlatformKeys(k, "prod").issue("customer", [f"b{i:05d}" for i in range(20001)])
    sizes = [len(kw["business_keys"]) for op, kw in k.calls if op == "issue_keys"]
    assert sizes == [20000, 1] and len(keys) == 20001 and new == 20001


def test_keys_are_issued_in_batches_once_each_and_none_is_left_out():
    k = Keys()
    keys, new = PlatformKeys(k, "prod", batch=2).issue("customer", ["c", "a", None, "b", "a", "d", "e"])
    batches = [kw["business_keys"] for op, kw in k.calls if op == "issue_keys"]
    assert batches == [["a", "b"], ["c", "d"], ["e"]]
    assert sorted(keys) == ["a", "b", "c", "d", "e"] and all(isinstance(v, int) for v in keys.values())
    assert new == 5


def test_a_batch_without_a_count_adds_no_new_keys():
    k = Keys()
    keys, new = PlatformKeys(k, "prod").issue("customer", ["old"])
    assert keys == {"old": 1} and new == 0


def test_no_business_keys_issues_nothing_and_says_none_were_new():
    k = Keys()
    assert PlatformKeys(k, "prod").issue("customer", [None]) == ({}, 0)
    assert [op for op, _ in k.calls] == ["key_stats"]


def test_a_table_is_checked_once_and_made_only_when_missing():
    k = Keys(created=True)
    pk = PlatformKeys(k, "prod")
    pk.issue("customer", ["a"])
    pk.issue("customer", ["b"])
    assert [op for op, _ in k.calls] == ["key_stats", "issue_keys", "issue_keys"]
    missing = Keys(created=False)
    PlatformKeys(missing, "prod").issue("orders", ["a"])
    assert [op for op, _ in missing.calls] == ["key_stats", "ensure_key_table", "issue_keys"]


def test_someone_who_cant_make_the_table_is_told_to_request_it():
    with pytest.raises(core.PlatformError, match="Request a key table"):
        PlatformKeys(Keys(created=False, ensure_refused=True), "prod").issue("orders", ["a"])


def test_the_key_table_calls_return_what_the_platform_says():
    k = Keys(created=False)
    pk = PlatformKeys(k, "prod")
    assert pk.ensure("orders", "order_id", "Orders") is True
    assert ("ensure_key_table", {"name": "orders", "business_key": "order_id", "description": "Orders"}) in k.calls
    assert pk.exists("orders") is False and PlatformKeys(Keys(), "prod").exists("orders") is True
    assert pk.stats("orders") == {"created": False, "name": "orders"}
    assert pk.lookup("orders", "A1") == {"value": "A1", "key": 7}


def test_export_reads_every_page_from_after_the_unknown_key():
    k = Keys(pages=[[[1, "a"], [2, "b"]], [[3, "c"]]])
    assert PlatformKeys(k, "prod").export("orders") == [[1, "a"], [2, "b"], [3, "c"]]
    afters = [kw["after"] for op, kw in k.calls if op == "export_keys"]
    assert afters == [core.UNKNOWN_KEY - 1, 2, 3]


def test_export_of_an_empty_table_is_empty():
    assert PlatformKeys(Keys(), "prod").export("orders") == []


# --------------------------------------------------------------------------- the local platform


def test_a_refused_change_is_recorded_and_a_refused_read_is_not(tmp_path):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: "ana@contoso.com")
    with pytest.raises(core.PlatformError):
        p.call("save_domain", domain={"name": ""})
    refused = [a for a in p.call("audit") if a["action"].startswith("refused")]
    assert refused
    with pytest.raises(core.PlatformError):
        p.call("get_policy", id="nope")
    assert [a for a in p.call("audit") if a["action"].startswith("refused")] == refused
