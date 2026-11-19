"""The app's incident calls (incident_service.py) as their callers see them: the defaults each call
takes, what the queue and agents pages say about their sources, which assignments set a priority,
when the product status goes to the catalog, and which openings an older app already posted. Each
test pins a behaviour that mutation testing (batch 109) found unchecked."""

import datetime as dt
import json
import types

import pytest

from fabric_desktop.meta import OneLakeStore, mesh
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate

UTC = dt.timezone.utc
OWNER = "chris@example.com"
PORT = [{"port": "customers", "entity": "silver.customers", "table": "customers", "columns": {"id": "int64"}}]


def iso(t):
    return t.isoformat(timespec="seconds")


def _run(entity, status, run_id):
    return {"entity": entity, "status": status, "run_id": run_id, "rows_read": 10, "quarantined": 0,
            "started_at": dt.datetime.now(UTC).isoformat(), "seconds": 0, "message": "boom"}


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.root.mkdir(parents=True, exist_ok=True)
    s._auth = types.SimpleNamespace(signed_in_user=lambda: OWNER, silent_token=lambda scopes: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def _agent(svc, env="dev", name="etl-01", live=True, checks=True):
    now = dt.datetime.now(UTC)
    beat = now if live else now - dt.timedelta(hours=2)
    svc._incident_log(env).backend.create(f"agents/{name}.json", json.dumps(
        {"kind": "agent_status", "agent": name, "state": "running", "heartbeat_at": iso(beat),
         "stale_after": iso(beat + dt.timedelta(minutes=3)), "schedules": [],
         "incidents": {"enabled": checks}}).encode())


def _failed(svc, env="dev"):
    name = mesh.project_name(svc._project())
    signals = evaluate.signals(name, {"silver.customers": {}}, [_run("silver.customers", "failed", "r1")], env)
    svc._incident_log(env).sync(signals, evaluated=True, by="agent")


def _publish(svc):
    svc.mesh_save_product({"name": "customers", "access": "public", "status": "published", "version": "1",
                           "ports": [{"name": "customers", "entity": "silver.customers"}]})
    svc._project().reload()
    svc._catalog().call("register_product", product={"project": mesh.project_name(svc._project()), "name": "customers",
                                                     "access": "public", "status": "published", "version": "1",
                                                     "owner": OWNER}, ports=PORT, env="prod")


# ---------------------------------------------------------------------------- what each call does by default


def test_the_list_shows_resolved_incidents_unless_asked_not_to(svc):
    inc = svc.incident_report("dev", "Fixed already")
    svc.incident_resolve("dev", inc["id"], "fixed")
    assert [i["title"] for i in svc.incidents_list("dev")["incidents"]] == ["Fixed already"]
    assert svc.incidents_list("dev", include_resolved=False)["incidents"] == []


def test_acknowledging_gives_back_the_acknowledged_incident(svc):
    inc = svc.incident_report("dev", "Slow page")
    out = svc.incident_acknowledge("dev", inc["id"], "on it")
    assert out["id"] == inc["id"] and out["state"] == "acknowledged"


def test_the_queue_looks_a_day_ahead_by_default(svc):
    assert svc.queue_list()["hours"] == 24


def test_a_check_tells_teams_only_when_asked(svc, monkeypatch):
    posted = []
    monkeypatch.setattr("fabric_desktop.platform_notify_service.post_teams", lambda url, title, lines: posted.append(title))
    settings = svc._settings
    svc._settings = lambda: {**settings(), "teams_webhook": "https://hook"}
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [_run("silver.customers", "failed", "r1")])
    out = svc.incidents_evaluate("dev")
    assert [c["change"] for c in out["changes"]] == ["opened"] and "notified" not in out
    watched = svc.incidents_watch()
    assert all("notified" not in e for e in watched["envs"]) and posted == []
    assert not (svc.root / "alerts_outbox.sqlite").exists()


def test_the_check_reads_the_latest_2000_runs(svc, monkeypatch):
    asked = []
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: asked.append((env, limit)) or [])
    svc.incidents_evaluate("dev")
    assert asked == [("dev", 2000)]


def test_the_queue_uses_the_last_100_fabric_jobs_as_last_checked(svc, monkeypatch):
    asked = []
    monkeypatch.setattr(svc, "fabric_runs", lambda limit=50, refresh=True: asked.append((limit, refresh)) or [])
    svc.queue_list()
    assert asked == [(100, False)]  # never asks Fabric for fresh statuses


# ---------------------------------------------------------------------------- the environment's control area


def test_a_onelake_environment_reads_its_control_area_with_the_silent_storage_token(svc, monkeypatch):
    store = OneLakeStore.__new__(OneLakeStore)
    store.workspace_id, store.lakehouse_ids = "ws-1", {"LH_Meta": "lh-meta"}
    monkeypatch.setattr(svc, "_store", lambda project, env: store)
    token = {"value": None}
    monkeypatch.setattr(svc, "_wl_token", lambda scopes: token["value"])
    backend = svc._incident_log("prod").backend
    assert (backend.workspace_id, backend.lakehouse_id) == ("ws-1", "lh-meta")
    assert backend._token() == ""  # no token: an empty one, never a sign-in window
    token["value"] = "tok"
    assert backend._token() == "tok"


# ---------------------------------------------------------------------------- the agents and the queue's sources


def test_the_incidents_list_says_its_agents_could_be_read(svc):
    _agent(svc)
    agents = svc.incidents_list("dev")["agents"]
    assert agents["readable"] is True and [a["agent"] for a in agents["agents"]] == ["etl-01"]


@pytest.mark.parametrize("cached, source", [
    (None, {"state": "not_read"}),
    ({"pending": True}, {"state": "reading"}),
    ({"error": "Fabric said no"}, {"state": "error", "detail": "Fabric said no"}),
    ({"items": [], "scanned": "2026-11-18T10:00:00+00:00"}, {"state": "read", "at": "2026-11-18T10:00:00+00:00"}),
])
def test_the_queue_says_how_the_fabric_schedules_stand(svc, cached, source):
    if cached is not None:
        svc._fabric_schedules = cached
    assert svc.queue_list()["sources"]["fabric_schedules"] == source
    assert svc.run_as_list()["sources"]["fabric_schedules"] == source


def _offline(env):
    raise ConnectionError("no network in this test")


def test_a_fabric_schedule_shows_the_environment_whose_workspace_it_is_in(svc, monkeypatch):
    project = svc._project()
    envs = {k: dict(v) for k, v in project.environments.items()}
    envs["test"] = {"workspace_id": "ws-test"}
    svc._write_environments(project, envs)
    real = svc._incident_log
    monkeypatch.setattr(svc, "_incident_log", lambda env: _offline(env) if env == "test" else real(env))
    svc._fabric_schedules = {"scanned": "2026-11-18T10:00:00+00:00", "items": [
        {"enabled": True, "workspace_id": "ws-test", "workspace_name": "Test", "name": "Nightly"}]}
    rows = [r for r in svc.run_as_list()["rows"] if r["place"]["kind"] == "fabric_schedule"]
    assert [(r["place"]["name"], r["env"]) for r in rows] == [("Test · Nightly", "test")]


# ---------------------------------------------------------------------------- priority from criticality


def test_only_this_projects_assignments_set_how_critical_a_table_is(svc, monkeypatch):
    name = mesh.project_name(svc._project())
    svc.workload_store.assign("meta:another:silver.customers", {"criticality": "critical"})
    svc.workload_store.assign(f"meta:{name}:silver.orders", {"criticality": "high"})
    svc.workload_store.assign(f"meta:{name}:gold.fact_sales", {"team": "Sales data"})  # no criticality
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [_run("silver.customers", "failed", "r1"),
                                                             _run("silver.orders", "failed", "r2")])
    assert len(svc.incidents_evaluate("dev")["changes"]) == 2
    crit = {i["name"]: i.get("criticality") or "" for i in svc._incident_log("dev").active()}
    assert crit == {"silver.customers": "", "silver.orders": "high"}


# ---------------------------------------------------------------------------- product status


def test_when_an_agent_checks_the_watch_still_tells_the_catalog(svc):
    _publish(svc)
    _agent(svc)
    _failed(svc)
    [dev] = [e for e in svc.incidents_watch()["envs"] if e["env"] == "dev"]
    assert dev["mode"] == "agent" and dev["agents"] == ["etl-01"]
    [row] = svc._catalog().call("list_product_status")
    assert (row["env"], row["status"]) == ("dev", "red")


def test_an_agent_offline_with_no_other_agent_live_is_red(svc):
    _publish(svc)
    _agent(svc, name="pc-1", live=False)
    svc._report_product_status("dev", [{"kind": "agent_offline", "name": "pc-1", "state": "open", "events": [],
                                        "evidence": {}}])
    [row] = svc._catalog().call("list_product_status")
    assert row["status"] == "red"


# ---------------------------------------------------------------------------- what an older app already posted


def test_a_late_table_without_evidence_is_posted_even_when_an_older_record_looks_alike(svc):
    (svc.root / "freshness_alerts.json").write_text(json.dumps({"late:dev:silver.orders:": "2026-11-01"}),
                                                    encoding="utf-8")
    changes = [{"change": "opened", "incident": {"kind": "late", "name": "silver.orders", "evidence_id": ""}},
               {"change": "opened", "incident": {"kind": "promise", "name": "silver.orders", "evidence_id": "x"}}]
    assert svc._not_told_before("dev", changes) == changes


@pytest.mark.parametrize("refusal, said", [(LookupError("no such function"), "not asking again this session"),
                                           (ConnectionError("network is unreachable"), None),
                                           (ValueError("Expecting value: line 1 column 1"), "trying again next check")])
def test_the_log_says_whether_the_platform_will_be_asked_again(svc, monkeypatch, caplog, refusal, said):
    _publish(svc)
    real = type(svc._catalog()).call

    def answer(self, op, **kw):
        if op == "report_product_status":
            raise refusal
        return real(self, op, **kw)
    monkeypatch.setattr(type(svc._catalog()), "call", answer)
    with caplog.at_level("INFO"):
        svc._report_product_status("dev", [{"kind": "run_failed", "name": "silver.customers", "state": "open",
                                            "events": [], "evidence": {}}])
    if said:
        assert f"won't take {mesh.project_name(svc._project())}'s product status in dev; {said}" in caplog.text
    else:
        assert "Couldn't report" in caplog.text and "won't take" not in caplog.text


# ---------------------------------------------------------------------------- who checks an environment


@pytest.mark.parametrize("live, checks", [(True, False), (False, True)])
def test_only_a_live_agent_that_checks_incidents_leaves_the_check_to_it(svc, monkeypatch, live, checks):
    _agent(svc, live=live, checks=checks)
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [])
    [dev] = [e for e in svc.incidents_watch()["envs"] if e["env"] == "dev"]
    assert dev["mode"] == "app" and dev["agents"] == [] and "changes" in dev  # this app checked it


def test_agents_that_cant_be_read_say_why_in_words(svc, monkeypatch):
    def denied(env):
        raise PermissionError("no")
    monkeypatch.setattr(svc, "_incident_log", denied)
    agents = svc._incident_agents("dev")
    assert agents["readable"] is False and "Ask for access" in agents["detail"]
