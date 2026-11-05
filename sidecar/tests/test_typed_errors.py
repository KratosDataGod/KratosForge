"""Typed errors survive the wrappers that add advice, and classify_error decides by type first (#963)."""

import pytest
import requests

from fabric_desktop.central import requests_service
from fabric_desktop.central.client import PlatformKeys
from fabric_desktop.errors import NotSetUp
from fabric_desktop.service import Service
from fabric_desktop.workloads.model import classify_error

NOT_SET_UP = NotSetUp("Sign in to the platform's login workspace first: assign a workspace to Dev.")


# --------------------------------------------------------------------------- classify_error


@pytest.mark.parametrize("text", ["Assign a login workspace first", "Sign in to Fabric, then assign a workspace",
                                  "403 forbidden until a workspace is assigned", "timed out: no workspace assigned"])
def test_not_set_up_is_classified_by_its_type_whatever_its_words(text):
    assert classify_error(NotSetUp(text)) == ("not_set_up", text)


def test_a_permission_error_is_unauthorized_even_if_its_words_say_timeout():
    assert classify_error(PermissionError("timed out asking for a token"))[0] == "unauthorized"


def test_an_http_status_decides_before_the_words():
    resp = requests.Response()
    resp.status_code = 503
    assert classify_error(requests.HTTPError("please sign in again", response=resp))[0] == "offline"
    resp.status_code = 403
    assert classify_error(requests.HTTPError("login timed out", response=resp))[0] == "unauthorized"


@pytest.mark.parametrize("exc,state", [(RuntimeError("AADSTS: interaction_required, sign in"), "signed_out"),
                                       (RuntimeError("HTTP 403 Forbidden"), "unauthorized"),
                                       (RuntimeError("too many requests"), "throttled"),
                                       (RuntimeError("failed to establish a new connection"), "offline"),
                                       (RuntimeError("something else"), "error")])
def test_untyped_errors_still_fall_back_to_their_words(exc, state):
    assert classify_error(exc)[0] == state


# --------------------------------------------------------------------------- the wrappers


class Raising:
    def __init__(self, exc):
        self.exc = exc

    def call(self, op, **kwargs):
        raise self.exc


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def test_metadata_sources_keep_not_set_up(svc):
    svc._catalog = lambda: Raising(NOT_SET_UP)
    with pytest.raises(NotSetUp) as got:
        svc._read_platform_sources()
    assert got.value is NOT_SET_UP
    svc._catalog = lambda: Raising(LookupError("UDF_Platform wasn't found"))
    with pytest.raises(LookupError, match="Finish or repair") as got:
        svc._read_platform_sources()
    assert not isinstance(got.value, NotSetUp)


def test_shortcut_risk_calls_keep_not_set_up(svc):
    svc._catalog = lambda: Raising(NOT_SET_UP)
    with pytest.raises(NotSetUp) as got:
        svc._sc_risk_call("accepted_risks")
    assert got.value is NOT_SET_UP
    svc._catalog = lambda: Raising(LookupError("UDF_Platform wasn't found"))
    with pytest.raises(LookupError, match="has no accepted_risks function"):
        svc._sc_risk_call("accepted_risks")


def test_landing_zone_calls_keep_not_set_up(svc):
    svc._catalog = lambda: Raising(NOT_SET_UP)
    with pytest.raises(NotSetUp) as got:
        svc._zone_call("list_zones")
    assert got.value is NOT_SET_UP
    svc._catalog = lambda: Raising(LookupError("UDF_Platform wasn't found"))
    with pytest.raises(LookupError, match="has no list_zones function"):
        svc._zone_call("list_zones")


def test_call_or_repair_keeps_not_set_up():
    with pytest.raises(NotSetUp) as got:
        requests_service.call_or_repair(Raising(NOT_SET_UP), "list_requests", "take requests")
    assert got.value is NOT_SET_UP
    with pytest.raises(LookupError, match="older than this app"):
        requests_service.call_or_repair(Raising(LookupError("UDF_Platform wasn't found")), "list_requests", "take requests")


class Closing:
    """A platform whose close_keys raises; every other call goes through."""

    def __init__(self, client, exc):
        self.client, self.exc = client, exc

    def __getattr__(self, name):
        return getattr(self.client, name)

    def call(self, op, **kwargs):
        if op == "close_keys" and kwargs.get("reason"):
            raise self.exc
        return self.client.call(op, **kwargs)


@pytest.mark.parametrize("exc", [NOT_SET_UP, PermissionError("Only a platform admin closes keys")])
def test_moving_keys_keeps_not_set_up_and_permission_errors_and_reopens_the_keys(svc, monkeypatch, exc):
    for env in ("prod", "dev"):
        client = svc._platform(env)
        client.call("ensure_key_table", name="customer")
        PlatformKeys(client, env).issue("customer", ["ada"])
    real = svc._share_sources
    monkeypatch.setattr(svc, "_share_sources", lambda: [(env, c if i == 0 else Closing(c, exc))
                                                        for i, (env, c) in enumerate(real())])
    with pytest.raises(type(exc)) as got:
        svc.platform_share_keys(approve=True)
    assert got.value is exc
    PlatformKeys(svc._platform("prod"), "prod").issue("customer", ["grace"])  # the first one closed is open again


def test_moving_keys_from_an_older_platform_still_says_finish_or_repair(svc, monkeypatch):
    for env in ("prod", "dev"):
        client = svc._platform(env)
        client.call("ensure_key_table", name="customer")
        PlatformKeys(client, env).issue("customer", ["ada"])
    real = svc._share_sources
    monkeypatch.setattr(svc, "_share_sources", lambda: [(env, c if i == 0 else Closing(c, LookupError("no close_keys")))
                                                        for i, (env, c) in enumerate(real())])
    with pytest.raises(LookupError, match="Run Finish or repair") as got:
        svc.platform_share_keys(approve=True)
    assert not isinstance(got.value, NotSetUp)
