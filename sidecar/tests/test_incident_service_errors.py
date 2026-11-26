"""The app's own incident check (incidents_watch in incident_service.py) when an environment opens but
then fails: the check of it, or reading its incidents back. Test and Prod have no workspace in these
projects, so they fail before that point and only Dev reaches it."""

import datetime as dt
import json
import types

import pytest

from fabric_desktop.service import Service
from fabric_desktop.workloads.incidents import IncidentLog


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.root.mkdir(parents=True, exist_ok=True)
    s._auth = types.SimpleNamespace(
        signed_in_user=lambda: "chris@example.com",
        silent_token=lambda scopes: None,
        fabric_token=lambda: None,
        storage_token=lambda: None,
    )
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def _live_agent(svc):
    now = dt.datetime.now(dt.timezone.utc)
    svc._incident_log("dev").backend.create(
        "agents/etl-01.json",
        json.dumps(
            {
                "kind": "agent_status",
                "agent": "etl-01",
                "state": "running",
                "heartbeat_at": now.isoformat(),
                "stale_after": (now + dt.timedelta(minutes=3)).isoformat(),
                "schedules": [],
                "incidents": {"enabled": True},
            }
        ).encode(),
    )


def _dev(svc):
    out = {e["env"]: e for e in svc.incidents_watch()["envs"]}
    # No workspace: Test and Prod fail before the check.
    assert out["test"]["mode"] == out["prod"]["mode"] == "unreadable"
    return out["dev"]


def test_a_check_that_fails_shows_the_environment_unreadable_with_the_reason(
    svc, monkeypatch
):
    def evaluate(env, notify=False):
        raise ConnectionError("network is unreachable")

    monkeypatch.setattr(svc, "incidents_evaluate", evaluate)
    dev = _dev(svc)
    assert (
        dev["mode"] == "unreadable"
        and dev["state"] == "offline"
        and dev["incidents"] == []
    )
    assert dev["detail"] == "Couldn't reach Fabric. Check the network and try again."


def test_incidents_that_cant_be_read_back_after_the_check_show_unreadable(
    svc, monkeypatch
):
    monkeypatch.setattr(
        svc, "incidents_evaluate", lambda env, notify=False: {"changes": []}
    )

    def denied(self):
        raise PermissionError("no")

    monkeypatch.setattr(IncidentLog, "active", denied)
    dev = _dev(svc)
    assert (
        dev["mode"] == "unreadable"
        and dev["state"] == "unauthorized"
        and dev["incidents"] == []
    )


def test_an_agent_checked_environment_that_cant_be_read_tells_the_catalog_nothing(
    svc, monkeypatch
):
    _live_agent(svc)
    told = []
    monkeypatch.setattr(
        svc, "_report_product_status", lambda env, items: told.append(env)
    )
    monkeypatch.setattr(
        svc, "incidents_evaluate", lambda *a, **k: pytest.fail("an agent checks it")
    )

    def torn(self):
        raise ValueError("Expecting value: line 1 column 1")

    monkeypatch.setattr(IncidentLog, "active", torn)
    dev = _dev(svc)
    assert (
        dev["mode"] == "unreadable"
        and dev["agents"] == ["etl-01"]
        and dev["incidents"] == []
    )
    # A status from incidents it couldn't read would be wrong.
    assert told == []
