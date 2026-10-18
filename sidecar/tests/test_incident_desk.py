"""The incident desk: numbers, priorities, reported incidents, the timeline and SLA targets."""

import datetime as dt
import json
import types

import pytest

from fabric_desktop import app_settings
from fabric_desktop.leases import LocalBackend
from fabric_desktop.service import Service
from fabric_desktop.workloads import evaluate, sla
from fabric_desktop.workloads.incidents import KEY, IncidentLog, default_priority

from test_agent import Clock


def run(entity, status, run_id, started="2026-10-16T10:00:00+00:00"):
    return {"entity": entity, "status": status, "run_id": run_id, "started_at": started, "message": "boom"}


def failed(run_id="r1", criticality=None, entity="silver.orders"):
    return evaluate.signals("sales", {entity: {}}, [run(entity, "failed", run_id)], "prod",
                            criticality={entity: criticality} if criticality else None)


@pytest.fixture
def backend(tmp_path):
    return LocalBackend(tmp_path / "control")


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    def write(data):
        path = app_settings.policy_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return write


# ---------------------------------------------------------------------------- numbers


def test_incidents_get_numbers_per_environment_that_never_change(backend):
    log = IncidentLog(backend, "prod", Clock())
    log.sync(failed(entity="silver.orders"), evaluated=True, by="a")
    log.report("Sales report shows yesterday", "dana")
    log.sync(failed(entity="silver.orders", run_id="r2"), evaluated=True, by="a")  # same incident, seen again
    numbers = sorted(i["number"] for i in log.list())
    assert numbers == ["INC-PROD-0001", "INC-PROD-0002"]
    assert json.loads(backend.read(KEY).data)["next_number"] == 3


def test_records_written_before_numbers_get_one_oldest_first(backend):
    old = [{"id": f"inc-{n}", "key": f"late:w{n}:prod", "kind": "late", "env": "prod", "title": "t", "state": "open",
            "opened_at": f"2026-10-1{n}T00:00:00Z", "occurrences": 1} for n in (3, 1, 2)]
    backend.create(KEY, json.dumps({"contract": 1, "kind": "incidents", "env": "prod", "incidents": old}).encode())
    log = IncidentLog(backend, "prod", Clock())
    log.note("inc-3", "looking", "chris")
    assert {i["id"]: i["number"] for i in log.list()} == {"inc-1": "INC-PROD-0001", "inc-2": "INC-PROD-0002",
                                                          "inc-3": "INC-PROD-0003"}


# ---------------------------------------------------------------------------- priority


def test_detected_priority_follows_criticality_until_a_person_sets_it(backend):
    log = IncidentLog(backend, "prod", Clock())
    [c] = log.sync(failed(criticality="critical"), evaluated=True, by="a")
    inc = c["incident"]
    assert inc["priority"] == "P2" and inc["priority_source"] == "policy"
    log.sync(failed("r2", "critical"), evaluated=True, by="a")
    log.sync(failed("r3", "critical"), evaluated=True, by="a")
    [inc] = log.active()
    assert inc["priority"] == "P1"  # a critical workload failing three times
    assert any(e["what"] == "priority" and "P2 → P1" in e["text"] for e in inc["events"])
    log.set_priority(inc["id"], "P3", "chris", "Known source outage, workaround in place")
    log.sync(failed("r4", "critical"), evaluated=True, by="a")
    [inc] = log.active()
    assert inc["priority"] == "P3" and inc["priority_source"] == "person"


def test_setting_a_priority_needs_a_reason_and_a_real_priority(backend):
    log = IncidentLog(backend, "prod", Clock())
    inc = log.report("Broken", "dana")
    with pytest.raises(ValueError, match="why"):
        log.set_priority(inc["id"], "P1", "chris", " ")
    with pytest.raises(ValueError, match="P1, P2"):
        log.set_priority(inc["id"], "P0", "chris", "urgent")


@pytest.mark.parametrize("inc, want", [
    ({"kind": "run_failed", "criticality": "critical", "occurrences": 1, "env": "prod"}, "P2"),
    ({"kind": "late", "criticality": "critical", "occurrences": 5, "env": "prod"}, "P2"),
    ({"kind": "late", "criticality": "high", "env": "prod"}, "P2"),
    ({"kind": "late", "criticality": "", "env": "prod"}, "P3"),
    ({"kind": "late", "criticality": "low", "env": "prod"}, "P4"),
    ({"kind": "run_failed", "criticality": "critical", "occurrences": 9, "env": "dev"}, "P4"),
    ({"kind": "cant_evaluate", "env": "prod"}, "P4"),
])
def test_default_priority(inc, want):
    assert default_priority(inc) == want


# ---------------------------------------------------------------------------- reported incidents


def test_a_reported_incident_stays_open_until_a_person_resolves_it(backend):
    log = IncidentLog(backend, "prod", Clock())
    inc = log.report("  Sales dashboard   shows yesterday ", "dana", text="Needed for 10:00", priority="P2",
                     workload="meta:sales:gold.revenue", owner="chris")
    assert inc["title"] == "Sales dashboard shows yesterday" and inc["source"] == "reported"
    assert inc["priority"] == "P2" and inc["owner"] == "chris" and inc["events"][0]["what"] == "reported"
    assert log.sync([], evaluated=True, by="agent") == []  # an evaluation that sees nothing doesn't recover it
    log.sync([], evaluated=set(evaluate.WORKLOAD_KINDS) | {"reported"}, by="agent")
    [still] = log.active()
    assert still["id"] == inc["id"]
    log.resolve(inc["id"], "chris", "Refreshed the semantic model")
    assert log.active() == []
    assert [e["what"] for e in log.list()[0]["events"]] == ["reported", "resolved"]


def test_reporting_needs_a_title(backend):
    with pytest.raises(ValueError, match="what's wrong"):
        IncidentLog(backend, "prod", Clock()).report("   ", "dana")


def test_the_timeline_records_every_change_and_stays_bounded(backend):
    log = IncidentLog(backend, "prod", Clock())
    [c] = log.sync(failed(), evaluated=True, by="agent")
    i = c["incident"]["id"]
    log.acknowledge(i, "chris", "on it")
    log.assign(i, "dana", "chris")
    log.note(i, "source late", "dana")
    log.sync([], evaluated=True, by="agent")
    whats = [e["what"] for e in log.list()[0]["events"]]
    assert whats == ["opened", "acknowledged", "assigned", "note", "recovered"]
    inc = log.report("x", "dana")
    for n in range(150):
        log.note(inc["id"], f"n{n}", "dana")
    events = next(x for x in log.list() if x["id"] == inc["id"])["events"]
    assert len(events) == 100 and events[0]["what"] == "reported" and events[-1]["text"] == "n149"


# ---------------------------------------------------------------------------- SLA


T0 = dt.datetime(2026, 10, 18, 6, 0, tzinfo=dt.timezone.utc)


def at(minutes):
    return T0 + dt.timedelta(minutes=minutes)


def inc(**kw):
    return {"kind": "run_failed", "env": "prod", "state": "open", "opened_at": "2026-10-18T06:00:00Z", "priority": "P1", **kw}


def test_sla_clocks_due_at_risk_breached_and_met():
    table = sla.DEFAULT_TARGETS
    s = sla.status(inc(), table, at(5))
    assert s["acknowledge"] == {"state": "due", "due": "2026-10-18T06:15:00Z", "left_minutes": 10}
    assert sla.status(inc(), table, at(13))["acknowledge"]["state"] == "at_risk"
    s = sla.status(inc(), table, at(27))
    assert s["state"] == "breached" and s["acknowledge"]["over_minutes"] == 12
    s = sla.status(inc(state="acknowledged", acknowledged_at="2026-10-18T06:10:00Z"), table, at(27))
    assert s["acknowledge"]["state"] == "met" and s["resolve"]["state"] == "due"
    late = inc(state="resolved", acknowledged_at="2026-10-18T06:20:00Z", resolved_at="2026-10-18T09:00:00Z")
    s = sla.status(late, table, at(600))
    assert s["acknowledge"]["state"] == "missed" and s["resolve"]["state"] == "met" and s["state"] == "missed"
    assert sla.status(inc(priority="P4"), table, at(10_000))["state"] == "none"
    assert sla.status(inc(priority=None, criticality="low"), table, at(1))["priority"] == "P4"


def test_policy_targets_apply_and_bad_values_are_named():
    table, problems = sla.targets({"P1": {"acknowledge_minutes": 5, "resolve_minutes": 0},
                                   "P2": {"acknowledge_minutes": "soon"}, "P9": {}, "P3": {"ack": 1}})
    assert table["P1"] == {"acknowledge_minutes": 5, "resolve_minutes": None}
    assert table["P2"] == sla.DEFAULT_TARGETS["P2"]
    assert len(problems) == 3 and any("P9" in p for p in problems)
    assert sla.targets("x")[1] and sla.targets(None) == (sla.DEFAULT_TARGETS, [])


def test_the_policy_file_reports_bad_sla_targets(policy):
    policy({"incident_sla": {"P1": {"acknowledge_minutes": -1}}})
    assert any("incident_sla.P1.acknowledge_minutes" in p for p in app_settings.policy_problems())


# ---------------------------------------------------------------------------- the app


@pytest.fixture
def svc(tmp_path, monkeypatch, policy):
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda scopes: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    s.meta_init(str(tmp_path / "proj"), "3")
    return s


def test_the_board_adds_priority_sla_and_counts(svc, policy):
    policy({"incident_sla": {"P2": {"acknowledge_minutes": 1}}})
    svc.workload_store.assign("meta:sales:gold.revenue", {"technical_owner": "chris@contoso.com", "team": "Sales data",
                                                          "criticality": "high"})
    mine = svc.incident_report("dev", "Revenue looks doubled", priority="P2", workload="meta:sales:gold.revenue")
    assert mine["owner"] == "chris@contoso.com" and mine["team"] == "Sales data" and mine["number"] == "INC-DEV-0001"
    other = svc.incident_report("dev", "Slow page", priority="P3")
    assert other["opened_by"] == "chris@contoso.com"
    board = svc.incidents_board("dev")
    assert board["counts"] == {"open": 2, "mine": 1, "unassigned": 1, "at_risk": 0, "breached": 0}
    assert board["sla"]["source"] == "policy" and board["sla"]["targets"]["P2"]["acknowledge_minutes"] == 1
    by_title = {i["title"]: i for i in board["incidents"]}
    assert by_title["Slow page"]["sla"]["acknowledge"]["state"] == "due"
    svc.incident_set_priority("dev", other["id"], "P4", "Cosmetic")
    assert {i["title"]: i["priority"] for i in svc.incidents_board("dev")["incidents"]}["Slow page"] == "P4"


def test_the_sla_policy_call_says_where_targets_come_from(svc, policy):
    out = svc.incident_sla_policy()
    assert out["source"] == "default" and out["targets"] == sla.DEFAULT_TARGETS and len(out["priorities"]) == 4
    policy({"incident_sla": {"P1": {"resolve_minutes": 60}}})
    out = svc.incident_sla_policy()
    assert out["source"] == "policy" and out["targets"]["P1"]["resolve_minutes"] == 60 and out["problems"] == []


# ---------------------------------------------------------------------------- history in the central lakehouse


@pytest.fixture
def central(svc, tmp_path, monkeypatch):
    from fabric_desktop.optimize.lakehouse import LocalLakehouse
    from test_optimize_service import FakeFabric
    fabric = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    lakes = tmp_path / "onelake"
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None: LocalLakehouse(lakes / ws / lh, schemas is not False))
    return svc


def test_history_needs_the_central_lakehouse_and_says_why(svc, policy):
    assert svc.incidents_history_sync()["state"] == "not_connected"
    policy({"allow_optimize_lakehouse": False})
    assert svc.incidents_history_sync()["state"] == "off"
    assert svc.incidents_history()["incidents"] == []


def test_history_copies_each_change_once_and_keeps_what_the_live_list_drops(central):
    central.optimize_create("w1", workspace_name="Platform")
    inc = central.incident_report("dev", "Revenue looks doubled", text="token=abc123secret", priority="P2")
    ask = central.incidents_history_sync()  # the incident tables don't exist yet: ask first
    assert ask["state"] == "needs_confirm" and ask["message"].endswith("Confirm to continue.")
    assert central.incidents_history()["state"] == "empty"
    out = central.incidents_history_sync(approve=True)
    dev = next(e for e in out["envs"] if e["env"] == "dev")
    assert out["state"] == "copied" and out["lakehouse"] == "FabricDesktop_Central"
    assert dev["events"] == 1 and dev["snapshots"] == 1
    assert {e["env"]: e["state"] for e in out["envs"]}["dev"] == "copied"
    again = next(e for e in central.incidents_history_sync()["envs"] if e["env"] == "dev")
    assert again["events"] == 0 and again["snapshots"] == 0  # nothing new: nothing sent
    central.incident_set_priority("dev", inc["id"], "P1", "Board meeting at 10")
    central.incident_resolve("dev", inc["id"], "Fixed the measure")
    dev = next(e for e in central.incidents_history_sync()["envs"] if e["env"] == "dev")
    assert dev["events"] == 2 and dev["snapshots"] == 1
    [row] = central.incidents_history("dev")["incidents"]
    assert row["number"] == "INC-DEV-0001" and row["state"] == "resolved" and row["priority"] == "P1"
    # The live list forgets it; the history doesn't.
    log = central._incident_log("dev")
    log.backend.replace(KEY, json.dumps({"contract": 1, "kind": "incidents", "env": "dev", "incidents": []}).encode(),
                        log.backend.read(KEY).etag)
    assert [r["incident_id"] for r in central.incidents_history()["incidents"]] == [inc["id"]]


def test_history_on_a_read_only_central_lakehouse_says_why(central):
    central.optimize_create("w1", workspace_name="Platform")
    reg = central.optimize_registry
    reg.add(reg.active() | {"access": "read"})
    out = central.incidents_history_sync(approve=True)
    assert out["state"] == "refused" and "Contributor" in out["message"]


def test_history_rows_are_stable_across_pcs_and_redact_secrets():
    from fabric_desktop.workloads.incident_history import event_rows, snapshot_rows
    inc = {"id": "inc-1", "number": "INC-PROD-0001", "kind": "reported", "state": "open", "priority": "P2",
           "opened_at": "2026-10-18T06:00:00Z", "title": "password=hunter2 in the log",
           "events": [{"at": "2026-10-18T06:00:00Z", "by": "dana", "what": "reported", "text": "x"}]}
    same = dict(inc, events=[dict(inc["events"][0], at="2026-10-18T06:00:00+00:00")])
    assert event_rows([inc], "prod", "w")[0]["record_id"] == event_rows([same], "prod", "w")[0]["record_id"]
    assert snapshot_rows([inc], "prod", "w")[0]["record_id"] == snapshot_rows([same], "prod", "w")[0]["record_id"]
    assert "hunter2" not in snapshot_rows([inc], "prod", "w")[0]["title"]
    moved = dict(inc, state="acknowledged", acknowledged_at="2026-10-18T06:05:00Z")
    assert snapshot_rows([moved], "prod", "w")[0]["record_id"] != snapshot_rows([inc], "prod", "w")[0]["record_id"]


def test_an_open_incident_acknowledged_late_counts_as_breached(svc, policy):
    svc.incident_report("dev", "Late ack", priority="P3")  # acknowledge within 4 h, resolve within 2 days
    log = svc._incident_log("dev")
    doc = json.loads(log.backend.read(KEY).data)
    opened = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)
    iso = lambda t: t.isoformat(timespec="seconds").replace("+00:00", "Z")  # noqa: E731
    doc["incidents"][0].update(opened_at=iso(opened), state="acknowledged",
                               acknowledged_at=iso(opened + dt.timedelta(hours=4, minutes=10)))
    log.backend.replace(KEY, json.dumps(doc).encode(), log.backend.read(KEY).etag)
    board = svc.incidents_board("dev")
    [inc] = board["incidents"]
    assert inc["sla"]["acknowledge"]["state"] == "missed" and inc["sla"]["resolve"]["state"] == "due"
    assert board["counts"]["breached"] == 1
