"""One queue view across this PC, agents and Fabric (workloads/queue.py, queue_list)."""

import datetime as dt
import json
import types

from fabric_desktop import audit
from fabric_desktop.local_schedules import local_configuration
from fabric_desktop.service import Service
from fabric_desktop.workloads.queue import build

UTC = dt.timezone.utc
NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=UTC)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def agent(name, state="running", live=True, **kw):
    beat = NOW - (dt.timedelta(seconds=30) if live else dt.timedelta(minutes=20))
    return {"kind": "agent_status", "agent": name, "state": state, "heartbeat_at": iso(beat),
            "stale_after": iso(beat + dt.timedelta(minutes=3)), **kw}


def test_a_live_agent_shows_what_it_runs_queues_and_runs_next():
    rec = agent("etl-01", running=[{"job_id": "j1", "source": "schedule:hourly", "entities": ["gold_sales"],
                                    "started_at": iso(NOW - dt.timedelta(minutes=8))}],
                counts={"queued": 2, "running": 1},
                schedules=[{"id": "hourly", "next": iso(NOW + dt.timedelta(minutes=6)), "entities": ["silver_orders"]},
                           {"id": "weekly", "next": iso(NOW + dt.timedelta(days=3)), "entities": []}])
    rows = build({"prod": [rec]}, [], [], [], {}, NOW)
    assert [(r["state"], r["what"]) for r in rows] == [("running", "gold_sales"), ("queued", "2 jobs"), ("next", "silver_orders")]
    assert rows[0]["owner"] == "etl-01's queue" and rows[0]["started_by"] == "schedule hourly"
    assert rows[2]["owner"] == "etl-01's schedule hourly" and rows[2]["env"] == "prod"


def test_an_agent_that_stopped_reporting_is_unknown_and_its_due_schedule_missed():
    rec = agent("etl-02", live=False, running=[{"job_id": "j2", "source": "cli", "entities": ["silver_orders"]}],
                counts={"queued": 1},
                schedules=[{"id": "nightly", "next": iso(NOW - dt.timedelta(hours=6))}])
    rows = build({"prod": [rec]}, [], [], [], {}, NOW)
    assert [r["state"] for r in rows] == ["unknown", "unknown", "missed"]
    by = {r["what"]: r for r in rows}
    assert by["silver_orders"]["note"] == "From its last report. Picked up again when etl-02 is back."
    assert by["1 job"]["note"] == "From its last report"
    assert by["Tables not named (an older agent)"]["note"] == "Runs once when etl-02 is back"
    assert {r["as_of"] for r in rows} == {"2027-01-15T07:40:00Z"}


def test_a_cleanly_stopped_agent_shows_nothing():
    rec = agent("etl-03", state="stopped", schedules=[{"id": "x", "next": iso(NOW + dt.timedelta(hours=1))}])
    assert build({"test": [rec]}, [], [], [], {}, NOW) == []


def test_this_pc_and_fabric_rows_say_who_owns_them():
    pc = [{"id": "p1", "enabled": True, "next_run": (NOW + dt.timedelta(hours=2)).astimezone().replace(tzinfo=None).isoformat(),
           "text": "Daily at 10:00", "target": {"kind": "meta", "env": "dev", "entities": []}},
          {"id": "p2", "enabled": False, "next_run": None, "target": {"kind": "notebook", "name": "nb"}},
          {"id": "p3", "enabled": True, "next_run": (NOW + dt.timedelta(days=2)).astimezone().replace(tzinfo=None).isoformat(),
           "target": {"kind": "notebook", "name": "later"}}]
    runs = [{"kind": "Notebook run", "name": "nb_churn", "workspace_id": "ws-test", "workspace_name": "Sales Test",
             "status": "InProgress", "started_at": iso(NOW - dt.timedelta(minutes=2)), "url": "https://app.fabric.microsoft.com/x"},
            {"kind": "Metadata run", "name": "Run all", "env": "prod", "workspace_id": "ws-prod", "status": "Completed"},
            {"kind": "Notebook run", "name": "odd", "workspace_id": "ws-test", "status": "Weird"}]
    scheds = [{"enabled": True, "name": "nb_fabric_desktop_runner", "workspace_id": "ws-test", "workspace_name": "Sales Test",
               "text": "Daily at 22:00", "type": "Notebook"}, {"enabled": False, "name": "off", "workspace_id": "ws-test"}]
    rows = build({}, pc, runs, scheds, {"ws-test": "test"}, NOW)
    by = {r["what"]: r for r in rows}
    assert set(by) == {"Every table", "nb_churn", "odd", "nb_fabric_desktop_runner"}
    assert by["Every table"]["owner"] == "Windows Task Scheduler" and by["Every table"]["where"]["kind"] == "pc"
    assert by["nb_churn"]["state"] == "running" and by["nb_churn"]["env"] == "test" and by["nb_churn"]["owner"] == "Fabric"
    assert by["odd"]["state"] == "unknown" and "Weird" in by["odd"]["note"]
    assert by["nb_fabric_desktop_runner"]["at"] is None and by["nb_fabric_desktop_runner"]["owner"] == "Fabric scheduler"
    assert rows[-1]["what"] == "nb_fabric_desktop_runner"  # no next time: last


def test_queue_list_reads_without_calling_fabric_and_says_how_each_source_stands(tmp_path):
    svc = Service(root=tmp_path / "app")
    calls = []
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: calls.append("token"), storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    fresh = iso(dt.datetime.now(UTC) + dt.timedelta(minutes=3))
    soon = iso(dt.datetime.now(UTC) + dt.timedelta(minutes=30))
    svc._incident_log("dev").backend.create("agents/etl-01.json", json.dumps(
        {"kind": "agent_status", "agent": "etl-01", "state": "running", "heartbeat_at": iso(dt.datetime.now(UTC)),
         "stale_after": fresh, "schedules": [{"id": "hourly", "next": soon, "entities": []}]}).encode())
    svc.pc_schedules._save({"schedules": [{"id": "pc1", "enabled": True, "config": local_configuration({"kind": "cron", "every_minutes": 60}),
                                           "target": {"kind": "meta", "env": "dev", "entities": ["x"]}}]})
    out = svc.queue_list()
    assert calls == []  # no Fabric token asked for, so no Fabric call
    assert out["sources"]["fabric_schedules"] == {"state": "not_read"}
    assert out["sources"]["pc"]["state"] == "live"
    assert {e["env"]: e["count"] for e in out["sources"]["agents"]}["dev"] == 1
    assert sorted(r["owner"] for r in out["rows"]) == ["Windows Task Scheduler", "etl-01's schedule hourly"]


def test_the_queue_and_agent_reads_are_not_audited_as_changes():
    assert {"agents_list", "queue_list", "incidents_summary"} <= audit.READ_ONLY
