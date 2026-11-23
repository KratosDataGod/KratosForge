"""Run as edges the main tests don't pin down (mutation batch 147, docs/quality/mutation-testing.md):
expiry at exactly now and at exactly the warning days, times with an offset or none, older agents,
the order of records, and defaults for a Fabric schedule's missing names."""

import datetime as dt

from fabric_desktop.workloads import run_as
from fabric_desktop.workloads.run_as import build

UTC = dt.timezone.utc
NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=UTC)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def agent(name, beat=None, **kw):
    beat = beat or NOW - dt.timedelta(seconds=30)
    return {"kind": "agent_status", "agent": name, "state": "running", "heartbeat_at": iso(beat), "host": "vm",
            "stale_after": iso(beat + dt.timedelta(minutes=3)), **kw}


def state(credential, identity="service_principal"):
    [row] = [r for r in build("", {"prod": [agent("a", identity=identity, credential=credential)]}, [], {}, NOW)["rows"]
             if r["place"]["kind"] == "agent"]
    return row["credential"]


def test_a_certificate_expiring_at_exactly_now_is_expired_and_at_exactly_the_warning_days_is_expiring():
    assert run_as.EXPIRING_DAYS == 30
    assert state({"kind": "certificate", "expires_at": iso(NOW)})["state"] == "expired"
    warn = NOW + dt.timedelta(days=30)
    assert state({"kind": "certificate", "expires_at": iso(warn)})["state"] == "expiring"
    later = warn + dt.timedelta(hours=1)
    assert state({"kind": "certificate", "expires_at": iso(later)})["state"] == "ok"  # no token yet: a certificate is ok
    assert build("", {}, [], {}, NOW)["expiring_days"] == 30


def test_an_expiry_with_an_offset_keeps_it_and_one_without_is_read_as_utc():
    # 09:00 at +02:00 is 07:00 UTC, an hour before now.
    assert state({"kind": "certificate", "expires_at": "2027-01-15T09:00:00+02:00"})["state"] == "expired"
    assert state({"kind": "certificate", "expires_at": "2027-01-15T07:00:00"})["state"] == "expired"
    assert state({"kind": "certificate", "expires_at": "2027-01-15T09:00:00"})["state"] == "expiring"


def test_a_certificate_whose_dates_couldnt_be_read_is_unknown_and_a_secret_without_a_token_too():
    assert state({"kind": "certificate", "expires_at": None, "last_token_at": iso(NOW)})["state"] == "unknown"
    assert state({"kind": "secret"})["state"] == "unknown"
    assert state({"kind": "secret", "last_token_at": iso(NOW)}) == {
        "kind": "secret", "expires_at": None, "last_token_at": iso(NOW), "error": "", "state": "ok"}


def test_an_older_agents_credential_kind_follows_its_identity():
    assert state(None, identity="managed_identity")["kind"] == "managed_identity"
    assert state(None, identity="service_principal")["kind"] == ""


def test_the_newest_record_speaks_whichever_comes_first_and_on_a_tie_the_first_one():
    new = agent("a", identity="service_principal", credential={"kind": "secret", "last_token_at": iso(NOW)})
    old = agent("a", beat=NOW - dt.timedelta(hours=1), identity="managed_identity", credential={"kind": "managed_identity"})
    out = build("", {"prod": [new], "test": [old]}, [], {}, NOW)
    [row] = [r for r in out["rows"] if r["place"]["kind"] == "agent"]
    assert row["runs_as"]["kind"] == "service_principal" and "Prod, Test OneLake" in row["boundary"]
    tie = agent("a", identity="managed_identity", credential={"kind": "managed_identity"})
    out = build("", {"prod": [new], "test": [tie]}, [], {}, NOW)
    [row] = [r for r in out["rows"] if r["place"]["kind"] == "agent"]
    assert row["runs_as"]["kind"] == "service_principal"


def test_an_agent_in_one_environment_names_it():
    [row] = [r for r in build("", {"prod": [agent("a")]}, [], {}, NOW)["rows"] if r["place"]["kind"] == "agent"]
    assert row["env"] == "prod"


def test_this_pc_fabric_jobs_and_fabric_schedules_are_live_and_a_schedule_without_names_says_so():
    out = build("me@contoso.com", {}, [{"enabled": True, "workspace_id": "ws"}], {"ws": "test"}, NOW)
    pc, jobs, sched = out["rows"]
    assert pc["live"] is True and jobs["live"] is True and sched["live"] is True
    assert sched["place"]["name"] == "Fabric · item" and sched["detail"] == ""
    assert sched["boundary"] == "Runs on the capacity of workspace its workspace; data stays in Fabric"
