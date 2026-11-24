"""central/client.py: what mutation testing found unchecked. How each UDF_Platform answer is read (its
status, an empty or non-JSON body, a gateway's reason), what each call sends, and PlatformKeys' counts,
its one look for a key table, and export from the unknown key on. UDF_Platform is a scripted session or a
SQLite platform; nothing reaches a tenant."""

from __future__ import annotations

import pytest
import requests

from fabric_desktop.central import core
from fabric_desktop.central.client import KEY_BATCH, FabricPlatform, LocalPlatform, PlatformKeys
from tests.test_central_client_cov3 import READ, fabric, response

ADMIN, DEV = "ana@contoso.com", "dev@contoso.com"


def answered(resp, times=1):
    client, s = fabric([resp] * times)
    return client, s


def test_a_401_or_403_is_a_permission_error():
    for status in (401, 403):
        client, _ = answered(response(status, {"errors": [{"message": "no"}]}))
        with pytest.raises(PermissionError, match="at least Viewer on the Platform workspace"):
            client.call(READ)


@pytest.mark.parametrize("status", [429, 500])
def test_a_429_or_any_5xx_is_an_http_error_naming_the_status(status):
    client, s = answered(response(status, {"errors": [{"message": "busy"}]}), times=3)
    with pytest.raises(requests.HTTPError) as err:
        client.call(READ)
    assert str(err.value) == (f"UDF_Platform couldn't be reached to {READ.replace('_', ' ')}: Fabric answered "
                              f"{status}. Try again in a few minutes.")
    assert err.value.response.status_code == status and len(s.sent) == 3


def test_a_gateways_reason_is_named_after_the_status():
    resp = response(503, raw=b"<html>busy</html>")
    resp.reason = "Service Unavailable"
    client, _ = answered(resp, times=3)
    with pytest.raises(requests.HTTPError, match="answered 503 Service Unavailable. Try again"):
        client.call(READ)


def test_a_400_without_errors_is_still_a_refusal_with_the_bodys_text():
    client, _ = answered(response(400, {"output": "made it"}))
    with pytest.raises(core.PlatformError, match=r'couldn\'t .*: \{"output": "made it"\}$'):
        client.call(READ)


def test_an_empty_body_is_an_empty_answer():
    client, _ = answered(response(200))
    assert client.call(READ) == {}


def test_a_body_that_isnt_json_or_isnt_an_object_is_refused_saying_which():
    client, _ = answered(response(200, raw=b"<html>gateway</html>"))
    with pytest.raises(core.PlatformError, match="answered 200 with something that isn't JSON: <html>gateway</html>$"):
        client.call(READ)
    client, _ = answered(response(200, [1, 2]))
    with pytest.raises(core.PlatformError, match=r"answered 200 with JSON that isn't an object: \[1, 2\]$"):
        client.call(READ)


def test_each_call_waits_up_to_five_minutes_as_the_signed_in_person():
    seen = []

    class Session:
        def post(self, url, json=None, timeout=None, headers=None):  # noqa: A002 - requests' own name
            seen.append((url, timeout, headers))
            return response(200, {"output": 1})

    client = FabricPlatform(lambda: "tok", "ws", "fn", session=Session(), sleep=lambda _s: None)
    assert client.call(READ) == 1
    assert seen == [(f"https://api.fabric.microsoft.com/v1/workspaces/ws/userDataFunctions/fn/functions/{READ}/invoke",
                     300, {"Authorization": "Bearer tok"})]


# ----------------------------------------------------------------------------- PlatformKeys


class Recorder:
    def __init__(self, inner):
        self.inner, self.ops = inner, []

    def call(self, op, **kwargs):
        self.ops.append(op)
        return self.inner.call(op, **kwargs)


def test_keys_go_in_batches_of_twenty_thousand_by_default(tmp_path):
    assert KEY_BATCH == 20000
    assert PlatformKeys(LocalPlatform(tmp_path / "p.db"), "dev").batch == 20000


def test_a_key_table_is_looked_for_once_and_made_only_when_missing(tmp_path):
    local = Recorder(LocalPlatform(tmp_path / "p.db", lambda: ADMIN))
    keys = PlatformKeys(local, "dev", batch=2)
    assert keys.exists("customer") is False
    assert keys.issue("customer", ["b", "a", None, "c", "a"]) == ({"a": 1, "b": 2, "c": 3}, 3)
    assert keys.issue("customer", ["a", "d"]) == ({"a": 1, "d": 4}, 1)
    assert keys.issue("customer", ["a"]) == ({"a": 1}, 0)
    assert keys.exists("customer") is True
    assert local.ops == ["key_stats", "key_stats", "ensure_key_table", "issue_keys", "issue_keys", "issue_keys",
                         "issue_keys", "key_stats"]
    other = Recorder(local.inner)
    assert PlatformKeys(other, "dev").issue("customer", ["a"]) == ({"a": 1}, 0)
    assert other.ops == ["key_stats", "issue_keys"]   # there already: not made again


def test_someone_who_isnt_an_admin_is_told_to_request_the_key_table(tmp_path):
    LocalPlatform(tmp_path / "p.db", lambda: ADMIN)
    dev = Recorder(LocalPlatform(tmp_path / "p.db", lambda: DEV))
    with pytest.raises(core.PlatformError, match=r"keys.customer isn't one of the platform's key tables yet\. "):
        PlatformKeys(dev, "dev").issue("customer", ["a"])
    assert dev.ops == ["key_stats", "ensure_key_table"]


def test_an_answer_without_inserted_counts_none_new():
    class Client:
        def call(self, op, **kwargs):
            return {"created": True} if op == "key_stats" else {"keys": {b: 7 for b in kwargs["business_keys"]}}

    assert PlatformKeys(Client(), "dev").issue("customer", ["a"]) == ({"a": 7}, 0)


def test_export_starts_below_the_unknown_key_so_it_comes_back_too(tmp_path):
    keys = PlatformKeys(LocalPlatform(tmp_path / "p.db", lambda: ADMIN), "dev")
    keys.issue("customer", ["a", "b"])
    assert [r[:2] for r in keys.export("customer")][:3] == [[core.UNKNOWN_KEY, core.UNKNOWN_BK], [1, "a"], [2, "b"]]
