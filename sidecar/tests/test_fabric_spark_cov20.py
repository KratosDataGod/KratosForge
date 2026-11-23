"""Fabric Spark: the paths the other tests leave out (coverage pass 20). The app's own HTTP session by
default, an adapter with no Fabric sign-in, and a printed result that isn't the app's own."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import app_settings
from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import fabric_spark as fs
from fabric_desktop.engines.contract import SessionConfig, Unsupported
from tests.test_fabric_spark import LH, TOKEN, WS


@pytest.fixture(autouse=True)
def fabric_runs_allowed(monkeypatch):
    monkeypatch.setattr(app_settings, "value", lambda key: True if key == "allow_fabric_runs" else None)


def test_without_an_http_object_the_livy_client_uses_the_apps_shared_session(monkeypatch):
    from fabric_desktop import http_session

    shared = object()
    monkeypatch.setattr(http_session, "session", lambda: shared)
    client = fs.LivyClient(lambda: TOKEN, WS, LH)
    assert client._http is shared


def test_an_adapter_without_the_apps_sign_in_asks_to_sign_in_and_starts_nothing():
    adapter = fs.FabricSparkAdapter()
    config = SessionConfig("nb", options={"workspace_id": WS, "lakehouse_id": LH})
    with pytest.raises(Unsupported, match=r"^Fabric Spark sessions use the app's Fabric sign-in\. Sign in first\.$"):
        adapter.open_session(config, auth.confirmed("fabric-spark", f"{WS}/{LH}", "chris"))


@pytest.mark.parametrize("printed", [
    {"mark": "someone-else@1", "fields": [], "rows": [], "note": fs.RESULT_MARK},
    [fs.RESULT_MARK, "a list, not the app's object"],
])
def test_a_printed_line_with_the_mark_that_isnt_the_apps_result_is_refused(printed):
    with pytest.raises(fs.FabricSparkError, match=r"^Fabric Spark didn't send the result back in the expected form\.$"):
        fs._result_table("some output\n" + json.dumps(printed), 10)


def test_an_adapter_with_the_apps_sign_in_talks_to_livy_through_the_shared_session(monkeypatch):
    from fabric_desktop import http_session
    from tests.test_fabric_spark import FakeLivy

    livy = FakeLivy()
    monkeypatch.setattr(http_session, "session", lambda: livy)
    adapter = fs.FabricSparkAdapter(token=lambda: TOKEN, sleep=lambda s: None)
    config = SessionConfig("nb", options={"workspace_id": WS, "lakehouse_id": LH})
    session = adapter.open_session(config, auth.confirmed("fabric-spark", f"{WS}/{LH}", "chris"))
    try:
        assert session.id == "7" and not session.closed
        assert livy.calls[0][:2] == ("POST", "")   # the session was created with the app's token
    finally:
        session.close()
    assert livy.deleted == ["7"]
