"""The app's incident calls when a later step fails after the incident list was written: nothing
done is lost, and the next try finishes the job instead of skipping it."""

import sqlite3
import types

import pytest

from fabric_desktop import incident_service
from fabric_desktop.service import Service

from test_incidents import run


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def test_an_opening_isnt_lost_when_the_alert_outbox_is_busy(svc, monkeypatch):
    name = sorted(svc._project().entities)[0]
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [run(name, "failed", "r1")])
    posted = []
    monkeypatch.setattr("fabric_desktop.platform_notify_service.post_teams", lambda url, title, lines: posted.append(title))
    settings = svc._settings
    svc._settings = lambda: {**settings(), "teams_webhook": "https://hook"}
    real = incident_service.Outbox

    def busy(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(incident_service, "Outbox", busy)
    with pytest.raises(sqlite3.OperationalError):
        svc.incidents_evaluate("dev", notify=True)
    monkeypatch.setattr(incident_service, "Outbox", real)
    out = svc.incidents_evaluate("dev", notify=True)  # the next check
    assert [c["change"] for c in out["changes"]] == ["opened"]
    assert len(posted) == 1 and posted[0].startswith("New: ")

