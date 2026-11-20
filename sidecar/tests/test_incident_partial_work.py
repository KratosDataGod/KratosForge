"""The app's incident calls when a later step fails after the incident list was written: nothing
done is lost, and the next try finishes the job instead of skipping it."""

import sqlite3
import types

import pytest

from fabric_desktop import incident_service
from fabric_desktop.service import Service
from fabric_desktop.workloads.incidents import Conflict, IncidentLog

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


def _impacted(svc, monkeypatch):
    from fabric_desktop import platform_notify_service as pns
    posts = []
    monkeypatch.setattr(pns, "post_teams", lambda url, title, lines: posts.append(title))
    cat = svc._catalog()
    cat.call("register_product", product={"project": "sales", "name": "orders", "owner": "olu@contoso.com",
                                          "access": "public", "status": "published", "domain": "sales"},
             ports=[{"port": "daily", "entity": "silver.orders"}], env="dev")
    svc.platform_notify_save([{"name": "default", "url": "https://contoso.webhook.office.com/x"}])
    return svc.incident_report("dev", "Orders late", workload="meta:sales:silver.orders"), posts


def test_telling_again_records_who_was_told_when_the_note_couldnt_be_written(svc, monkeypatch):
    inc, posts = _impacted(svc, monkeypatch)
    real = IncidentLog.note

    def lost(self, *a, **k):
        raise Conflict("The incident list kept changing while it was being updated; try again.")

    monkeypatch.setattr(IncidentLog, "note", lost)
    with pytest.raises(Conflict):
        svc.incident_tell_impacted("dev", inc["id"], ["olu@contoso.com"])
    assert len(posts) == 1  # it went out
    monkeypatch.setattr(IncidentLog, "note", real)
    again = svc.incident_tell_impacted("dev", inc["id"], ["olu@contoso.com"])
    assert again["told"] is False and again["reason"] == "already posted" and len(posts) == 1
    told = svc.incident_impact("dev", inc["id"])["told"]
    assert told is not None and told["text"].endswith("olu@contoso.com")
    svc.incident_tell_impacted("dev", inc["id"], ["olu@contoso.com"])  # a third press adds no second note
    notes = [n for n in svc._incident("dev", inc["id"])["notes"] if n["text"].startswith("Told downstream")]
    assert len(notes) == 1
