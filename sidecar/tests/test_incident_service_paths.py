"""The app's incident pages (incident_service.py) when a source can't be read: each says so and the
rest still shows. The happy paths are in test_incidents.py, test_workload_queue.py and test_run_as.py."""

import json
import types

import pytest

from fabric_desktop.service import Service

from test_incidents import run


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.root.mkdir(parents=True, exist_ok=True)
    s._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    return s


@pytest.fixture
def with_project(svc, tmp_path):
    svc.meta_init(str(tmp_path / "proj"), "3")
    return svc


def _boom(*a, **k):
    raise OSError("the control area can't be read")


def test_an_environment_that_isnt_one_is_refused(svc):
    with pytest.raises(ValueError, match="env is one of"):
        svc._incident_log("staging")


def test_a_list_that_cant_be_read_says_why_and_shows_nothing(svc, monkeypatch):
    monkeypatch.setattr(svc, "_incident_log", _boom)
    out = svc.incidents_list("dev")
    assert out["incidents"] == [] and out["observation"]["state"] != "live" and out["observation"]["detail"]
    agents = svc._incident_agents("dev")
    assert agents["readable"] is False and agents["agents"] == [] and agents["detail"]


def test_the_agents_page_says_when_this_pcs_schedules_couldnt_be_read(svc, monkeypatch):
    monkeypatch.setattr(type(svc), "pc_schedules", property(lambda self: types.SimpleNamespace(listing=_boom)))
    out = svc.agents_list()
    assert out["pc_schedules_read"] is False and out["agents"] == []


def test_the_queue_shows_what_it_can_when_sources_cant_be_read(svc, monkeypatch):
    monkeypatch.setattr(type(svc), "pc_schedules", property(lambda self: types.SimpleNamespace(listing=_boom)))
    monkeypatch.setattr(svc, "fabric_runs", _boom)
    out = svc.queue_list()
    assert out["rows"] == [] and out["sources"]["pc"]["state"] != "live" and out["sources"]["pc"]["detail"]


def test_without_a_project_fabric_rows_show_no_environment(svc):
    assert svc._env_of() == {}


def test_agent_records_that_cant_be_read_leave_their_incidents_as_they_are(with_project, monkeypatch, caplog):
    from fabric_desktop import incident_service
    name = sorted(with_project._project().entities)[0]
    monkeypatch.setattr(with_project, "meta_runs", lambda env, limit: [run(name, "failed", "r1")])
    monkeypatch.setattr(incident_service, "read_status", _boom)
    with caplog.at_level("WARNING"):
        out = with_project.incidents_evaluate("dev")
    assert [c["change"] for c in out["changes"]] == ["opened"]
    assert "Couldn't read agent records; their incidents stay as they are" in caplog.text


def test_no_product_status_is_reported_without_a_project(svc):
    svc._report_product_status("dev", [])  # nothing open: nothing to tell, and no error
    assert "_product_status_sent" not in svc.__dict__


@pytest.mark.parametrize("content", ["{torn", "[1, 2]"])
def test_an_older_alert_record_that_cant_be_used_lets_every_opening_through(svc, content):
    (svc.root / "freshness_alerts.json").write_text(content, encoding="utf-8")
    changes = [{"change": "opened", "incident": {"kind": "late", "name": "silver.orders", "evidence_id": "late:x"}}]
    assert svc._not_told_before("dev", changes) == changes  # a second post beats none


def test_an_opening_an_older_app_already_posted_isnt_posted_again(svc):
    (svc.root / "freshness_alerts.json").write_text(json.dumps({"failed:dev:silver.orders:r1": "2026-11-01"}),
                                                    encoding="utf-8")
    told = {"kind": "run_failed", "name": "silver.orders", "evidence_id": "r1"}
    changes = [{"change": "opened", "incident": told}, {"change": "recovered", "incident": told},
               {"change": "opened", "incident": told | {"evidence_id": "r2"}}]
    assert svc._not_told_before("dev", changes) == changes[1:]


def test_the_watch_reports_an_environment_it_cant_read_and_carries_on(with_project, monkeypatch):
    real = with_project._incident_log

    def log(env):
        if env == "prod":
            raise OSError("no workspace")
        return real(env)
    monkeypatch.setattr(with_project, "_incident_log", log)

    def evaluate(env, notify=False):
        if env == "test":
            raise ConnectionError("network is unreachable")
        return {"changes": [], "notified": {"sent": 0}}
    monkeypatch.setattr(with_project, "incidents_evaluate", evaluate)
    out = {e["env"]: e for e in with_project.incidents_watch(notify=True)["envs"]}
    assert out["prod"]["mode"] == "unreadable" and out["prod"]["incidents"] == []
    assert out["test"]["mode"] == "unreadable" and out["test"]["detail"]
    assert out["dev"]["mode"] == "app" and out["dev"]["notified"] == {"sent": 0}
