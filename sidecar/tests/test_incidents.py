"""Incidents: one record per problem whoever evaluates, recovery, manual handling, Teams and objectives."""

import datetime as dt
import json
import sqlite3
import threading
import types
from contextlib import closing

import pytest

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.errors import NotSetUp
from fabric_desktop.leases import LocalBackend
from fabric_desktop.meta import objectives
from fabric_desktop.meta.project import ENVIRONMENTS
from fabric_desktop.meta.templates import init_project
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate
from fabric_desktop.workloads.incidents import KEY, IncidentLog
from fabric_desktop.workloads.notify import KEEP_TRYING, Outbox, Quiet, Route, Routing, messages, send, single

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

    res = send([change()], single("https://hook"), outbox, "agent", post)
    assert res["queued"] == 1 and res["sent"] == 0 and "ConnectionError" in res["error"]
    res = send([change(name="b", title="b failed in Prod")], single("https://hook"), outbox, "agent", post)
    assert res["queued"] == 1 and len(outbox.pending()) == 2  # still down: the new one waits too
    down = False
    res = send([], single("https://hook"), outbox, "agent", post)
    assert res["sent"] == 2 and outbox.pending() == []
    assert all(t.startswith("Delayed: ") for t, _ in posted)
    assert send([change()], single(""), outbox, "agent", post)["skipped"]  # no webhook: nothing sent, nothing queued


def test_an_alert_waiting_too_long_is_dropped(tmp_path):
    clock = Clock()
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock)
    outbox.add("old", ["x"], "down")
    clock.t += KEEP_TRYING + 1
    posted = []
    assert send([], single("https://hook"), outbox, "agent", lambda *a: posted.append(a))["sent"] == 0
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


def test_an_environment_that_isnt_set_up_opens_no_incident_and_clears_a_cant_check_one(tmp_path, monkeypatch):
    svc = Service(root=tmp_path / "app")
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: None, storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    name = sorted(svc._project().entities)[0]
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [run(name, "failed", "r1")])
    svc.incidents_evaluate("dev")

    def offline(env, limit):
        raise ConnectionError("network is unreachable")

    monkeypatch.setattr(svc, "meta_runs", offline)
    assert [c["change"] for c in svc.incidents_evaluate("dev")["changes"]] == ["opened"]
    assert {i["kind"] for i in svc.incidents_list("dev")["incidents"] if i["state"] != "resolved"} == {"run_failed", "cant_evaluate"}

    def not_set_up(env, limit):
        raise NotSetUp("Assign a workspace to Dev first (Data mesh > Environments).")

    monkeypatch.setattr(svc, "meta_runs", not_set_up)
    out = svc.incidents_evaluate("dev")
    assert out["not_set_up"].startswith("Assign a workspace") and not out["evaluated"]
    # The "Can't check" incident clears; the workload's own incident stays, because nothing was read.
    assert [(c["change"], c["title"]) for c in out["changes"]] == [("recovered", "Can't check Dev: offline")]
    assert {i["kind"] for i in svc.incidents_list("dev")["incidents"] if i["state"] != "resolved"} == {"run_failed"}
    assert svc.incidents_evaluate("dev")["changes"] == [], "checking again opens nothing"


def test_the_agent_opens_no_incident_for_an_environment_that_isnt_set_up(agent_setup, monkeypatch):
    agent = Agent(agent_setup, clock=Clock())

    def not_set_up(env, limit):
        raise NotSetUp("Assign a workspace to Dev first.")

    monkeypatch.setattr(agent.runtime, "meta_runs", not_set_up)
    assert agent.evaluate_incidents() == {"dev": []}
    assert IncidentLog(agent._control("dev"), "dev").active() == []


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


# ---------------------------------------------------------------------------- stage 2.3: classification, agents, routes, quiet hours, escalation

from fabric_desktop.mailbox import LocalControl  # noqa: E402
from fabric_desktop.workloads.agents import read_status  # noqa: E402
from fabric_desktop.workloads.classify import classify  # noqa: E402

UTC = dt.timezone.utc


@pytest.mark.parametrize("message, category", [
    ("HTTP 403 Forbidden: AuthorizationPermissionMismatch", "access"),
    ("429 Too Many Requests", "throttled"),
    ("requests.exceptions.ConnectionError: Failed to establish a new connection", "network"),
    ("LeaseBusy: another writer holds silver.orders", "busy"),
    ("glob Files/landing/orders/*.parquet matched no files", "source_missing"),
    ("KeyError: 'region'", "schema"),
    ("2 checks failed; 14 rows quarantined", "data_quality"),
    ("ZeroDivisionError: division by zero", "code"),
    ("the moon is in the wrong phase", "unrecognized"),
    (None, "unrecognized"),
])
def test_a_failure_message_gets_a_likely_category(message, category):
    out = classify(message)
    assert out["category"] == category and out["label"] and out["hint"]


def test_a_failed_run_incident_carries_its_likely_category(backend):
    signals = evaluate.signals("sales", {"silver.orders": {}},
                               [run("silver.orders", "failed", "r1", message="KeyError: 'region'")], "prod")
    [c] = IncidentLog(backend, "prod", Clock()).sync(signals, evaluated=True, by="a")
    assert c["incident"]["classification"]["category"] == "schema"
    assert c["incident"]["classification"]["retry_helps"] is False


def status(name, heartbeat, stale_after, state="running", schedules=()):
    return {"contract": 1, "kind": "agent_status", "agent": name, "host": f"srv-{name}", "version": "0.21.0",
            "state": state, "heartbeat_at": heartbeat, "stale_after": stale_after,
            "schedules": [{"id": s} for s in schedules]}


def test_an_agent_past_its_stale_time_is_an_agent_offline_signal():
    now = dt.datetime(2026, 10, 17, 7, 46, tzinfo=UTC)
    records = [status("etl-02", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z", schedules=["nightly"]),
               status("etl-03", "2026-10-17T07:45:00Z", "2026-10-17T07:48:00Z"),           # still fresh
               status("etl-04", "2026-10-17T06:00:00Z", "2026-10-17T06:03:00Z", "stopped"),  # said it stopped
               status("etl-01", "2026-10-17T06:00:00Z", "2026-10-17T06:03:00Z"),            # the evaluator itself
               status("etl-05", "x", "not a time"), status("etl-06", "x", "2026-10-17T06:00:00")]  # naive: unknown
    [s] = evaluate.agent_signals(records, "prod", now, me="etl-01")
    assert s["kind"] == "agent_offline" and s["workload"] == "agent:etl-02"
    assert s["title"] == "Agent etl-02 stopped reporting in Prod" and "nightly" in s["text"]
    assert s["evidence"]["source"] == "agents/etl-02.json"


def test_agent_offline_opens_recovers_and_unreadable_records_recover_nothing(tmp_path):
    control = LocalControl(tmp_path / "control")
    control.create("agents/etl-02.json", json.dumps(
        status("etl-02", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z")).encode())
    control.create("agents/notes.json", b"not json")
    records = read_status(control)
    assert [r["agent"] for r in records] == ["etl-02"]
    log = IncidentLog(control, "prod", Clock())
    now = dt.datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    [c] = log.sync(evaluate.agent_signals(records, "prod", now), evaluated={"agent_offline"}, by="a")
    assert c["change"] == "opened" and c["incident"]["kind"] == "agent_offline"
    # the run log was read but the agent records weren't: the agent incident stays open
    assert log.sync([], evaluated=evaluate.WORKLOAD_KINDS, by="a") == []
    assert log.active()[0]["kind"] == "agent_offline"
    # the agent reported again
    fresh = [status("etl-02", "2026-10-17T08:00:00Z", "2026-10-17T08:03:00Z")]
    [c] = log.sync(evaluate.agent_signals(fresh, "prod", now), evaluated={"agent_offline"}, by="a")
    assert c["change"] == "recovered" and log.active() == []


def test_an_unacknowledged_incident_escalates_once_whoever_evaluates(backend):
    clock = Clock()
    first, second = IncidentLog(backend, "prod", clock), IncidentLog(backend, "prod", clock)
    first.sync(failed() + failed("silver.customers", "r9"), evaluated=True, by="agent etl-01")
    clock.t += 1800
    assert first.escalate(3600, "on-call", "agent etl-01") == []  # not yet
    [ack] = [i for i in first.active() if i["name"] == "silver.customers"]
    first.acknowledge(ack["id"], "dana")
    clock.t += 1801
    [c] = first.escalate(3600, "on-call", "agent etl-01")
    assert c["change"] == "escalated" and c["incident"]["escalated_to"] == "on-call"
    assert c["incident"]["name"] == "silver.orders"  # the acknowledged one isn't escalated
    assert second.escalate(3600, "on-call", "agent etl-02") == []
    assert "escalated" in evaluate.describe(c).lower()


def inc_change(kind="opened", team="", criticality="", owner="", env="prod", name="a"):
    return {"change": kind, "incident": {"kind": "run_failed", "name": name, "env": env, "title": f"{name} failed",
                                         "text": "boom", "owner": owner, "team": team, "criticality": criticality,
                                         "escalated_to": "on-call" if kind == "escalated" else ""}}


def test_changes_go_to_matching_team_routes_or_the_default():
    routing = Routing([Route("default", "https://default"), Route("sales", "https://sales", teams=("Sales",)),
                       Route("crit", "https://crit", criticality=("critical",), envs=("prod",)),
                       Route("on-call", "https://oncall")], escalate_to="on-call")
    names = lambda c: [r.name for r in routing.for_change(c)]  # noqa: E731
    assert names(inc_change(team="sales")) == ["sales"]
    assert names(inc_change(team="sales", criticality="Critical")) == ["sales", "crit"]
    assert names(inc_change(criticality="critical", env="dev")) == ["default"]
    assert names(inc_change(team="finance")) == ["default"]  # the escalation route isn't a catch-all
    assert names(inc_change("escalated", team="sales")) == ["on-call"]
    assert Routing([Route("sales", "https://sales", teams=("sales",))]).for_change(inc_change()) == []


def test_quiet_hours_hold_messages_until_they_end_except_critical_and_escalations(tmp_path):
    quiet = Quiet.parse("22:00-06:00")
    night = dt.datetime(2026, 10, 17, 23, 30, tzinfo=UTC)
    assert quiet.until(night, "high") == dt.datetime(2026, 10, 18, 6, 0, tzinfo=UTC)
    assert quiet.until(night.replace(hour=3), "") == dt.datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    assert quiet.until(night, "critical") is None and quiet.until(night.replace(hour=12), "") is None
    assert Quiet.parse("09:00-17:00").until(night.replace(hour=10), "") is not None

    clock = Clock(night.timestamp())
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock)
    routing = Routing([Route("default", "https://hook"), Route("on-call", "https://oncall")], quiet,
                      escalate_after=3600, escalate_to="on-call")
    posted = []
    post = lambda url, title, lines: posted.append((url, title, lines))  # noqa: E731
    res = send([inc_change(name="a"), inc_change(name="b", criticality="critical"),
                inc_change("escalated", name="c")], routing, outbox, "agent", post, now=night)
    assert res["held"] == 1 and res["sent"] == 2
    assert sorted(t for _, t, _ in posted) == ["Escalated: c failed (agent)", "New: b failed (agent)"]
    posted.clear()
    clock.t += 3600  # still quiet
    assert send([], routing, outbox, "agent", post, now=night + dt.timedelta(hours=1))["sent"] == 0
    clock.t = dt.datetime(2026, 10, 18, 6, 1, tzinfo=UTC).timestamp()
    assert send([], routing, outbox, "agent", post)["sent"] == 1
    [(url, title, lines)] = posted
    assert url == "https://hook" and title == "New: a failed (agent)" and lines[0].startswith("Held during quiet hours")


def test_an_outbox_from_before_routes_is_upgraded_in_place(tmp_path):
    import sqlite3
    path = tmp_path / "alerts.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE outbox (id INTEGER PRIMARY KEY, created REAL, title TEXT, lines TEXT, "
                   "attempts INTEGER DEFAULT 0, error TEXT)")
        db.execute("INSERT INTO outbox (created, title, lines, attempts, error) VALUES (?, 'old', '[\"x\"]', 1, 'down')",
                   (Clock()(),))
    [item] = Outbox(path, Clock()).pending()
    assert item["route"] == "default" and item["not_before"] == 0.0


def incidents_raw(project, **inc):
    return {"agent": {"project": str(project), "environments": ["prod"]}, "auth": {"type": "user"},
            "incidents": {"enabled": True, **inc}}


def test_routes_quiet_hours_and_escalation_are_validated(agent_setup):
    tmp_path = agent_setup.project
    ok = agent_config.parse(incidents_raw(
        tmp_path, quiet_hours="22:00-06:00", escalate_after_minutes=60, escalate_to="on-call",
        route=[{"name": "sales", "webhook_env": "SALES_HOOK", "teams": ["sales"]},
               {"name": "on-call", "webhook_env": "ONCALL_HOOK", "criticality": ["critical"]}]))
    assert [r["name"] for r in ok.incidents.routes] == ["sales", "on-call"]
    assert ok.incidents.routes[0]["teams"] == ("sales",) and ok.incidents.quiet_except == ("critical",)
    bad = [
        {"quiet_hours": "late"},
        {"escalate_after_minutes": 60},
        {"escalate_to": "nobody", "route": [{"name": "a", "webhook_env": "A"}]},
        {"route": [{"name": "a", "url": "https://hook"}]},
        {"route": [{"name": "default", "webhook_env": "A"}]},
        {"route": [{"name": "a"}]},
        {"route": [{"name": "a", "webhook_env": "A", "colour": "red"}]},
        {"route": [{"name": "a", "webhook_env": "A"}, {"name": "a", "webhook_env": "B"}]},
        {"route": ["a"]},
    ]
    for inc in bad:
        with pytest.raises(agent_config.ConfigError):
            agent_config.parse(incidents_raw(tmp_path, **inc))


def test_the_agent_reports_other_agents_that_went_quiet_and_routes_by_team(agent_setup, monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_INCIDENT_HOOK", "https://default")
    posted = []
    monkeypatch.setattr("fabric_desktop.agent.post_teams", lambda url, title, lines: posted.append((url, title)))
    agent = Agent(agent_setup, clock=Clock(dt.datetime(2026, 10, 17, 12, 0, tzinfo=UTC).timestamp()))
    monkeypatch.setattr(agent.runtime, "meta_runs", lambda env, limit: [])
    agent._control("dev").create("agents/etl-02.json", json.dumps(
        status("etl-02", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z")).encode())
    [c] = agent.evaluate_incidents()["dev"]
    assert c["incident"]["kind"] == "agent_offline"
    assert posted == [("https://default", "New: Agent etl-02 stopped reporting in Dev (agent etl-01)")]


def test_the_app_lists_agents_serving_an_environment_and_sums_incidents(tmp_path, monkeypatch):
    svc = Service(root=tmp_path / "app")
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: None, storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    name = sorted(svc._project().entities)[0]
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [run(name, "failed", "r1")] if env == "dev" else [])
    backend = svc._incident_log("dev").backend
    fresh = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=3)).isoformat()
    backend.create("agents/etl-01.json", json.dumps({**status("etl-01", "now", fresh),
                                                     "incidents": {"enabled": True, "seconds": 300}}).encode())
    backend.create("agents/etl-02.json", json.dumps(status("etl-02", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z")).encode())
    svc.incidents_evaluate("dev")
    listed = svc.incidents_list("dev")
    assert listed["user"] == "chris@contoso.com"
    agents = {a["agent"]: a for a in listed["agents"]["agents"]}
    assert agents["etl-01"]["state"] == "live" and agents["etl-01"]["incidents"]["enabled"]
    assert agents["etl-02"]["state"] == "offline" and agents["etl-02"]["incidents"] is None
    kinds = sorted(i["kind"] for i in listed["incidents"])
    assert kinds == ["agent_offline", "run_failed"]
    summary = {e["env"]: e for e in svc.incidents_summary()["envs"]}
    assert summary["dev"]["count"] == 2
    assert summary["test"]["count"] is None and summary["test"]["observation"]["state"] == "not_set_up"
    mine = svc.incidents_summary(workload=f"meta:{listed['incidents'][0]['workload'].split(':')[1]}:{name}")
    assert [i["kind"] for i in {e["env"]: e for e in mine["envs"]}["dev"]["incidents"]] == ["run_failed"]


def test_the_agent_status_record_says_how_it_checks_incidents_without_webhooks(agent_setup, monkeypatch):
    from fabric_desktop import agent_status
    raw = incidents_raw(agent_setup.project, route=[{"name": "sales", "webhook_env": "SALES_HOOK", "teams": ["sales"]}],
                        escalate_after_minutes=30, escalate_to="sales")
    cfg = agent_config.parse(raw)
    out = agent_status.incidents_summary(cfg.incidents)
    assert out["enabled"] and out["escalate_to"] == "sales" and out["routes"] == [
        {"name": "sales", "teams": ["sales"], "owners": [], "criticality": [], "kinds": [], "envs": []}]
    assert "SALES_HOOK" not in json.dumps(out)


def test_the_app_checks_only_environments_no_running_agent_checks(tmp_path, monkeypatch):
    svc = Service(root=tmp_path / "app")
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: None, storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    name = sorted(svc._project().entities)[0]
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [run(name, "failed", "r1")])
    posted = []
    monkeypatch.setattr("fabric_desktop.incident_service.post_teams", lambda url, title, lines: posted.append(title))
    settings = svc._settings
    svc._settings = lambda: {**settings(), "teams_webhook": "https://hook"}
    out = {e["env"]: e for e in svc.incidents_watch(notify=True)["envs"]}
    assert out["dev"]["mode"] == "app", out["dev"]
    assert [c["change"] for c in out["dev"]["changes"]] == ["opened"]
    assert out["test"]["mode"] == "unreadable" and out["test"]["state"] == "not_set_up"
    assert len(posted) == 1 and posted[0].startswith("New: ")
    assert svc.incidents_watch(notify=True)["envs"][0]["changes"] == [] and len(posted) == 1  # once each
    # a running agent checks Dev now: the app only reads it, and says nothing to Teams
    fresh = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=3)).isoformat()
    svc._incident_log("dev").backend.create("agents/etl-01.json", json.dumps(
        {**status("etl-01", "now", fresh), "incidents": {"enabled": True, "seconds": 300}}).encode())
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [run(name, "ok", "r2")])
    dev = svc.incidents_watch(notify=True)["envs"][0]
    assert dev["mode"] == "agent" and dev["agents"] == ["etl-01"] and "changes" not in dev
    assert [i["kind"] for i in dev["incidents"]] == ["run_failed"]  # recovery is the agent's to record
    assert len(posted) == 1


def _alerting_agent(tmp_path, monkeypatch, url):
    import json as _json
    from fabric_desktop import agent_config
    from fabric_desktop.agent import Agent
    from fabric_desktop.meta.templates import init_project
    init_project(tmp_path / "proj", "3", "Chris", True)
    monkeypatch.setenv("FD_HOOK", url)
    path = tmp_path / "agent.toml"
    path.write_text(f"""
[agent]
name = "etl-01"
home = {_json.dumps(str(tmp_path / "home"))}
project = {_json.dumps(str(tmp_path / "proj"))}
environments = ["dev"]

[auth]
type = "user"

[alerts]
teams_webhook_env = "FD_HOOK"
""", encoding="utf-8")
    agent = Agent(agent_config.load(path))
    logged = []
    monkeypatch.setattr(agent.log, "warning", lambda msg, extra=None: logged.append((msg, extra)))
    return agent, logged


JOB = {"id": "j1", "payload": {"env": "prod"}, "source": "schedule"}


def test_an_agents_failed_job_alert_logs_no_webhook_signature(tmp_path, monkeypatch):
    def down(url, title, lines):
        raise ConnectionError(f"Max retries exceeded with url: {url.split('.com', 1)[1]}")
    monkeypatch.setattr("fabric_desktop.agent.post_teams", down)
    agent, logged = _alerting_agent(tmp_path, monkeypatch, "https://x.webhook.office.com/workflows/a/invoke?sv=1.0&sig=SECRET123")
    agent._alert(JOB, "boom")
    assert logged and "SECRET123" not in str(logged) and "sig=[removed]" in str(logged)


def test_a_job_alert_teams_didnt_take_goes_out_later_marked_delayed(tmp_path, monkeypatch):
    # QA-021: the "job didn't finish" alert went straight to Teams, once; a network blip lost it.
    posted, up = [], {"ok": False}

    def teams(url, title, lines):
        if not up["ok"]:
            raise ConnectionError("Teams can't be reached")
        posted.append((title, lines))
    monkeypatch.setattr("fabric_desktop.agent.post_teams", teams)
    agent, logged = _alerting_agent(tmp_path, monkeypatch, "https://x.webhook.office.com/workflows/a/invoke?sig=S")
    agent._alert(JOB, "boom")
    assert posted == [] and "it goes with the next try" in logged[-1][0]
    agent.send_waiting_alerts()  # still down: it stays
    assert posted == [] and len(agent._alert_outbox().pending()) == 1
    up["ok"] = True
    agent._status_at = 0
    agent.step()  # the next status round sends it
    [(title, lines)] = posted
    assert title == "Delayed: KratosForge agent etl-01: a Prod job didn't finish"
    assert lines[0].startswith("From ") and lines[1:] == ["Job j1 (schedule)", "boom"]
    assert agent._alert_outbox().pending() == []
    agent.send_waiting_alerts()
    assert len(posted) == 1  # sent once


def test_waiting_job_alerts_go_before_a_new_one(tmp_path, monkeypatch):
    posted, up = [], {"ok": False}

    def teams(url, title, lines):
        if not up["ok"]:
            raise ConnectionError("down")
        posted.append(lines[-1])
    monkeypatch.setattr("fabric_desktop.agent.post_teams", teams)
    agent, _ = _alerting_agent(tmp_path, monkeypatch, "https://x.webhook.office.com/workflows/a/invoke?sig=S")
    agent._alert(JOB, "first")
    up["ok"] = True
    agent._alert(JOB | {"id": "j2"}, "second")
    assert posted == ["first", "second"]


def test_a_legacy_connector_webhook_stays_out_of_a_waiting_job_alert(tmp_path, monkeypatch):
    legacy = ("https://127.0.0.1:9/webhookb2/11111111-2222-3333-4444-555555555555@66666666-7777-8888-9999-000000000000"
              "/IncomingWebhook/abcdef0123456789abcdef0123456789/11111111-2222-3333-4444-555555555555/V2SECRETSECRET0123")
    agent, logged = _alerting_agent(tmp_path, monkeypatch, legacy)
    agent._alert(JOB, "boom")  # the real post_teams: a ConnectionError naming the URL's path
    [item] = agent._alert_outbox().pending()
    assert item["error"].startswith("ConnectionError: ")  # what went wrong is kept
    for secret in ("V2SECRETSECRET0123", "abcdef0123456789abcdef0123456789", "webhookb2"):
        assert secret not in item["error"] and secret not in str(logged)  # the webhook isn't, however it's masked


def test_a_credential_in_a_run_message_reaches_neither_teams_nor_the_outbox(tmp_path):
    secret = "Login failed: password=hunter2\nAuthorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123 " + "x" * 2000
    c = {"change": "opened", "incident": {"kind": "run_failed", "name": "silver.orders", "env": "prod",
                                          "title": "silver.orders failed in Prod\n" + secret, "text": secret, "owner": ""}}
    posted, down = [], [True]

    def post(url, title, lines):
        if down[0]:
            raise ConnectionError("no network")
        posted.append((title, lines))
    outbox = Outbox(tmp_path / "outbox.sqlite")
    assert send([c], single("https://hook"), outbox, "KratosForge", post)["queued"] == 1
    with closing(sqlite3.connect(tmp_path / "outbox.sqlite")) as db:
        stored = json.dumps(db.execute("SELECT title, lines FROM outbox").fetchall())
    down[0] = False
    send([], single("https://hook"), outbox, "KratosForge", post)
    for text in (stored, json.dumps(posted)):
        assert "hunter2" not in text and "eyJhbGciOiJIUzI1NiJ9" not in text and "[removed]" in text
    from fabric_desktop.dataops_service import teams_card
    for title, lines in posted:  # as the card goes to Teams ("Delayed: " is added to the title)
        blocks = teams_card(title, lines)["attachments"][0]["content"]["body"]
        assert all("\n" not in b["text"] and len(b["text"]) <= 500 for b in blocks)
    # a row an older build queued unredacted is cleaned when it's sent
    card = json.dumps(teams_card("New: " + secret, [secret]))
    assert "hunter2" not in card and "eyJhbGciOiJIUzI1NiJ9" not in card and "\\n" not in card
