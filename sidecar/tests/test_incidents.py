"""Incidents: one record per problem whoever evaluates, recovery, manual handling, Teams and objectives."""

import datetime as dt
import json
import threading
import types

import pytest

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.leases import LocalBackend
from fabric_desktop.meta import objectives
from fabric_desktop.meta.project import ENVIRONMENTS
from fabric_desktop.meta.templates import init_project
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate
from fabric_desktop.workloads.incidents import KEY, IncidentLog
from fabric_desktop.workloads.notify import KEEP_TRYING, Outbox, messages, send

from test_agent import Clock, config_text


def run(entity, status, run_id, started="2026-10-16T10:00:00+00:00", message=""):
    return {"entity": entity, "status": status, "run_id": run_id, "started_at": started, "message": message}


def failed(entity="silver.orders", run_id="r1"):
    return evaluate.signals("sales", {entity: {}}, [run(entity, "failed", run_id, message="boom")], "prod")


@pytest.fixture
def backend(tmp_path):
    return LocalBackend(tmp_path / "control")


# ---------------------------------------------------------------------------- the incident log


def test_a_failed_run_opens_one_incident_and_seeing_it_again_writes_nothing(backend):
    clock = Clock()
    log = IncidentLog(backend, "prod", clock)
    [change] = log.sync(failed(), evaluated=True, by="agent etl-01")
    inc = change["incident"]
    assert change["change"] == "opened" and inc["kind"] == "run_failed" and inc["workload"] == "meta:sales:silver.orders"
    assert inc["evidence"]["run_id"] == "r1" and inc["state"] == "open" and inc["opened_by"] == "agent etl-01"
    etag = backend.read(KEY).etag
    clock.t += 60
    assert log.sync(failed(), evaluated=True, by="agent etl-01") == []
    assert backend.read(KEY).etag == etag  # a quiet evaluation doesn't rewrite the document
    clock.t += 1000
    assert log.sync(failed(), evaluated=True, by="x") == []
    assert log.active()[0]["last_seen_at"] != inc["last_seen_at"]


def test_a_new_failure_of_the_same_table_counts_as_another_occurrence(backend):
    log = IncidentLog(backend, "prod", Clock())
    log.sync(failed(run_id="r1"), evaluated=True, by="a")
    assert log.sync(failed(run_id="r2"), evaluated=True, by="a") == []
    [inc] = log.active()
    assert inc["occurrences"] == 2 and inc["evidence_id"] == "r2"


def test_two_evaluators_racing_open_one_incident(tmp_path):
    backend = LocalBackend(tmp_path / "control")
    start = threading.Barrier(8)
    results = []

    def evaluator(i):
        log = IncidentLog(backend, "prod")  # one backend: its writes are conditional, as OneLake's If-Match
        start.wait()
        results.append(log.sync(failed(), evaluated=True, by=f"evaluator {i}"))

    threads = [threading.Thread(target=evaluator, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    doc = json.loads(backend.read(KEY).data)
    assert len(doc["incidents"]) == 1
    assert sum(len(r) for r in results) == 1  # only the write that landed reports it as opened


def test_recovery_closes_and_a_hand_resolved_incident_stays_resolved_on_the_same_evidence(backend):
    log = IncidentLog(backend, "prod", Clock())
    log.sync(failed(run_id="r1"), evaluated=True, by="a")
    [change] = log.sync([], evaluated=True, by="a")
    assert change["change"] == "recovered" and change["incident"]["resolution"] == "recovered"
    assert log.active() == []

    [change] = log.sync(failed(run_id="r2"), evaluated=True, by="a")
    assert change["change"] == "reopened"
    log.resolve(change["incident"]["id"], "chris@contoso.com", "Known upstream outage")
    assert log.sync(failed(run_id="r2"), evaluated=True, by="a") == []  # same failed run: stays resolved
    [again] = log.sync(failed(run_id="r3"), evaluated=True, by="a")  # a new failure: opens again
    assert again["change"] == "reopened" and len(log.list()) == 3


def test_an_unreadable_environment_never_recovers_workload_incidents(backend):
    log = IncidentLog(backend, "prod", Clock())
    log.sync(failed(), evaluated=True, by="a")
    cant = evaluate.cant_evaluate("prod", "offline", "Couldn't reach Fabric.")
    [change] = log.sync([cant], evaluated=False, by="a")
    assert change["change"] == "opened" and change["incident"]["kind"] == "cant_evaluate"
    assert change["incident"]["workload"] is None
    assert {i["kind"] for i in log.active()} == {"run_failed", "cant_evaluate"}
    # Readable again and the table is fine: both close, the telemetry one as cleared.
    changes = log.sync([], evaluated=True, by="a")
    assert {(c["incident"]["kind"], c["incident"]["resolution"]) for c in changes} == {
        ("run_failed", "recovered"), ("cant_evaluate", "cleared")}


def test_acknowledge_assign_and_note_are_kept_with_who_and_when(backend):
    log = IncidentLog(backend, "prod", Clock())
    [c] = log.sync(failed(), evaluated=True, by="a")
    i = c["incident"]["id"]
    log.acknowledge(i, "chris@contoso.com", "Looking")
    log.assign(i, "dana@contoso.com", "chris@contoso.com")
    inc = log.note(i, "Source file was late", "dana@contoso.com")
    assert inc["state"] == "acknowledged" and inc["owner"] == "dana@contoso.com"
    assert [n["by"] for n in inc["notes"]] == ["chris@contoso.com", "chris@contoso.com", "dana@contoso.com"]
    assert log.sync(failed(), evaluated=True, by="a") == []  # acknowledged is still active, not reopened
    with pytest.raises(LookupError):
        log.note("inc-nope", "x", "y")
    log.resolve(i, "dana@contoso.com")
    with pytest.raises(ValueError):
        log.acknowledge(i, "chris@contoso.com")


def test_resolved_incidents_are_trimmed_to_the_newest(backend, monkeypatch):
    from fabric_desktop.workloads import incidents
    monkeypatch.setattr(incidents, "KEEP_RESOLVED", 3)
    clock = Clock()
    log = IncidentLog(backend, "prod", clock)
    for n in range(6):
        clock.t += 10
        log.sync(failed(run_id=f"r{n}"), evaluated=True, by="a")
        clock.t += 10
        log.sync([], evaluated=True, by="a")
    assert len(log.list()) == 3


# ---------------------------------------------------------------------------- signals


def test_signals_come_from_the_latest_finished_run_and_ignore_placeholder_owners():
    runs = [run("a", "running", "r3"), run("a", "ok", "r2"), run("a", "failed", "r1"),
            run("b", "needs_decision", "r4"), run("c", "failed", "r5")]
    specs = {"a": {}, "b": {"owner": "me"}, "c": {"owner": "dana@contoso.com"}}
    got = {s["name"]: s for s in evaluate.signals("sales", specs, runs, "prod")}
    assert set(got) == {"b", "c"}  # a's newest finished run succeeded
    assert got["b"]["kind"] == "needs_decision" and got["b"]["owner"] == ""
    assert got["c"]["owner"] == "dana@contoso.com" and got["c"]["evidence_id"] == "r5"


def test_a_missed_freshness_promise_is_a_late_signal():
    spec = {"freshness": {"within_hours": 26, "envs": ["prod"]}}
    now = dt.datetime(2026, 10, 16, 9, 0).astimezone()
    old = run("a", "ok", "r1", started=(now - dt.timedelta(days=3)).isoformat())
    [s] = evaluate.signals("sales", {"a": spec}, [old], "prod", now=now)
    assert s["kind"] == "late" and s["evidence"]["source"] == "freshness promise"
    assert evaluate.signals("sales", {"a": spec}, [old], "test", now=now) == []  # not promised in Test


# ---------------------------------------------------------------------------- objectives


def test_objectives_are_validated():
    assert objectives.problems("a", {}, ENVIRONMENTS) == []
    errors = objectives.problems("a", {"objectives": {"criticality": "urgent", "success_rate": 120, "runs": 2,
                                                      "envs": ["qa"], "window": 1}}, ENVIRONMENTS)
    assert len(errors) == 5


def test_success_rate_against_the_objective():
    o = objectives.objective({"objectives": {"criticality": "high"}})
    assert (o["success_rate"], o["runs"], o["envs"], o["source"]) == (95.0, 20, ["test", "prod"], "entity file")
    assert objectives.objective({}) is None and objectives.objective({}, "low") is None
    assert objectives.objective({}, "critical")["source"] == "criticality"
    few = [run("a", "ok", f"r{i}") for i in range(3)]
    assert objectives.success(o, few)["status"] == "not_enough"
    runs = [run("a", "failed", "r0")] + [run("a", "ok", f"r{i}") for i in range(1, 10)] + [run("a", "skipped", "s")]
    res = objectives.success(o, runs)
    assert (res["status"], res["counted"], res["ok"], res["rate"]) == ("breached", 10, 9, 90.0)
    [s] = [x for x in evaluate.signals("sales", {"a": {"objectives": {"criticality": "standard"}}}, runs, "prod")
           if x["kind"] == "objective"] or [None]
    assert s is None  # 90 % meets standard's 90 %
    [s] = [x for x in evaluate.signals("sales", {"a": {}}, runs, "prod", criticality={"a": "critical"})
           if x["kind"] == "objective"]
    assert "9 of the last 10" in s["text"]


def test_the_project_validates_and_summarises_objectives(tmp_path):
    from fabric_desktop.meta.project import Project, validate
    init_project(tmp_path / "p", "3", "Chris", True)
    p = Project(tmp_path / "p")
    e = next(iter(p.entities.values()))
    text = e.path.read_text(encoding="utf-8") + '\n[objectives]\ncriticality = "fast"\n'
    e.path.write_text(text, encoding="utf-8")
    assert any("objectives criticality" in x for x in validate(Project(tmp_path / "p")))
    e.path.write_text(text.replace('"fast"', '"high"'), encoding="utf-8")
    p = Project(tmp_path / "p")
    assert not [x for x in validate(p) if "objectives" in x]
    s = p.entities[e.name].summary()
    assert s["criticality"] == "high" and s["objective"]["success_rate"] == 95.0


# ---------------------------------------------------------------------------- Teams


def change(kind="opened", name="a", title="a failed in Prod"):
    return {"change": kind, "incident": {"kind": "run_failed", "name": name, "env": "prod", "title": title,
                                         "text": "boom", "owner": ""}}


def test_many_changes_at_once_become_one_digest():
    assert len(messages([change() for _ in range(3)], "agent etl-01")) == 3
    [(title, lines)] = messages([change() for _ in range(5)] + [change("recovered")] * 2, "agent etl-01")
    assert title == "5 new problems, 2 recovered (agent etl-01)" and len(lines) == 7


def test_teams_unreachable_queues_and_the_next_try_sends_them_marked_delayed(tmp_path):
    clock = Clock()
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock)
    posted = []
    down = True

    def post(url, title, lines):
        if down:
            raise ConnectionError("no network")
        posted.append((title, lines))

    res = send([change()], "https://hook", outbox, "agent", post)
    assert res["queued"] == 1 and res["sent"] == 0 and "ConnectionError" in res["error"]
    res = send([change(name="b", title="b failed in Prod")], "https://hook", outbox, "agent", post)
    assert res["queued"] == 1 and len(outbox.pending()) == 2  # still down: the new one waits too
    down = False
    res = send([], "https://hook", outbox, "agent", post)
    assert res["sent"] == 2 and outbox.pending() == []
    assert all(t.startswith("Delayed: ") for t, _ in posted)
    assert send([change()], "", outbox, "agent", post)["skipped"]  # no webhook: nothing sent, nothing queued


def test_an_alert_waiting_too_long_is_dropped(tmp_path):
    clock = Clock()
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock)
    outbox.add("old", ["x"], "down")
    clock.t += KEEP_TRYING + 1
    posted = []
    assert send([], "https://hook", outbox, "agent", lambda *a: posted.append(a))["sent"] == 0
    assert posted == [] and outbox.pending() == []


# ---------------------------------------------------------------------------- the agent and the app


@pytest.fixture
def agent_setup(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(config_text(project, tmp_path / "home", '[incidents]\nenabled = true\nseconds = 60\n\n[alerts]\n'
                                'teams_webhook_env = "TEST_INCIDENT_HOOK"\n'), encoding="utf-8")
    return agent_config.load(path)


def test_incidents_config_defaults_and_checks(agent_setup, tmp_path):
    assert agent_setup.incidents.enabled and agent_setup.incidents.seconds == 60
    raw = {"agent": {"project": str(agent_setup.project), "environments": ["dev"]}, "auth": {"type": "user"},
           "incidents": {"seconds": 5, "enabled": "yes"}}
    with pytest.raises(agent_config.ConfigError) as err:
        agent_config.parse(raw)
    assert len(err.value.errors) == 2
    raw.pop("incidents")
    cfg = agent_config.parse(raw)
    assert not cfg.incidents.enabled and cfg.incidents.seconds == 300 and cfg.incidents.digest_over == 5


def test_the_agent_evaluates_on_its_timer_and_tells_teams(agent_setup, monkeypatch):
    monkeypatch.setenv("TEST_INCIDENT_HOOK", "https://hook")
    posted = []
    monkeypatch.setattr("fabric_desktop.agent.post_teams", lambda url, title, lines: posted.append(title))
    clock = Clock()
    agent = Agent(agent_setup, clock=clock)
    name = sorted(agent.runtime.entity_names())[0]
    runs = [run(name, "failed", "r1", message="boom")]
    monkeypatch.setattr(agent.runtime, "meta_runs", lambda env, limit: list(runs))
    agent.step()
    log = IncidentLog(agent._control("dev"), "dev")
    [inc] = log.active()
    assert inc["opened_by"] == "agent etl-01" and inc["workload"].endswith(f":{name}")
    assert posted == [f"New: {name} failed in Dev (agent etl-01)"]
    runs[:] = [run(name, "ok", "r2")]
    clock.t += 10
    agent.step()  # not due yet
    assert log.active()
    clock.t += 60
    agent.step()
    assert log.active() == [] and posted[-1] == f"Recovered: {name} (agent etl-01)"


def test_alerts_left_from_before_a_restart_go_out_with_the_next_evaluation(agent_setup, monkeypatch):
    monkeypatch.setenv("TEST_INCIDENT_HOOK", "https://hook")
    posted = []
    monkeypatch.setattr("fabric_desktop.agent.post_teams", lambda url, title, lines: posted.append(title))
    clock = Clock()
    Outbox(agent_setup.home / "alerts.sqlite3", clock).add("New: x failed in Dev (agent etl-01)", ["x"], "offline")
    agent = Agent(agent_setup, clock=clock)
    monkeypatch.setattr(agent.runtime, "meta_runs", lambda env, limit: [])
    assert agent.evaluate_incidents() == {"dev": []}
    assert posted == ["Delayed: New: x failed in Dev (agent etl-01)"]


def test_the_agent_reports_an_unreadable_run_log_without_crashing(agent_setup, monkeypatch):
    agent = Agent(agent_setup, clock=Clock())

    def broken(env, limit):
        raise ConnectionError("network is unreachable")

    monkeypatch.setattr(agent.runtime, "meta_runs", broken)
    changed = agent.evaluate_incidents()
    [c] = changed["dev"]
    assert c["incident"]["kind"] == "cant_evaluate" and c["incident"]["title"] == "Can't check Dev: offline"
    assert IncidentLog(agent._control("dev"), "dev").active()[0]["evidence"]["state"] == "offline"


def test_the_app_and_the_agent_share_one_incident_list(tmp_path, monkeypatch):
    svc = Service(root=tmp_path / "app")
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: None, storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    name = sorted(svc._project().entities)[0]
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [run(name, "failed", "r1")])
    out = svc.incidents_evaluate("dev")
    assert out["evaluated"] and [c["change"] for c in out["changes"]] == ["opened"]
    assert svc.incidents_evaluate("dev")["changes"] == []
    listed = svc.incidents_list("dev")
    [inc] = listed["incidents"]
    assert listed["observation"]["state"] == "live" and inc["kind_label"] == "Run failed"
    svc.incident_acknowledge("dev", inc["id"], "On it")
    svc.incident_resolve("dev", inc["id"])
    assert svc.incidents_list("dev", include_resolved=False)["incidents"] == []
    with pytest.raises(ValueError):
        svc.incident_note("dev", inc["id"], "  ")
    with pytest.raises(ValueError):
        svc.incidents_list("staging")
