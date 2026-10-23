"""Monitor > Agents: what the app reads from the status records agents write."""

import datetime as dt
import json
import types

from fabric_desktop import __version__
from fabric_desktop.local_schedules import local_configuration
from fabric_desktop.service import Service
from fabric_desktop.workloads.agents import agent_state, compare_version, fleet

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 17, 7, 46, tzinfo=UTC)


def rec(name, beat, stale, state="running", **extra):
    return {"contract": 1, "kind": "agent_status", "agent": name, "host": f"srv-{name}", "version": "0.21.0",
            "state": state, "heartbeat_at": beat, "stale_after": stale, **extra}


def test_an_agent_is_live_until_its_promised_report_passes():
    assert agent_state(rec("a", "2026-10-17T07:45:00Z", "2026-10-17T07:48:00Z"), NOW) == "live"
    assert agent_state(rec("a", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z"), NOW) == "offline"
    assert agent_state(rec("a", "2026-10-17T07:45:00Z", "2026-10-17T07:48:00Z", "stopped"), NOW) == "stopped"
    assert agent_state(rec("a", "x", "not a time"), NOW) == "offline"
    assert agent_state(rec("a", "x", "2026-10-17T09:00:00"), NOW) == "offline"  # no zone: can't tell


def test_versions_compare_numerically_and_unknown_stays_unknown():
    assert compare_version("0.21.0", "0.24.0") == "older"
    assert compare_version("0.10.0", "0.9.1") == "newer"
    assert compare_version("0.24.0", "0.24.0") == "same"
    assert compare_version(None, "0.24.0") == compare_version("dev", "0.24.0") == "unknown"


def test_one_row_per_agent_with_each_environment_s_own_jobs():
    by_env = {
        "test": [rec("etl-01", "2026-10-17T07:44:00Z", "2026-10-17T07:47:00Z", counts={"queued": 0}, recent=[])],
        "prod": [rec("etl-01", "2026-10-17T07:45:00Z", "2026-10-17T07:48:00Z", counts={"queued": 2},
                     running=[{"job_id": "j1"}], incidents={"enabled": True}),
                 rec("etl-02", "2026-10-17T07:40:00Z", "2026-10-17T07:43:00Z", schedules=[{"id": "nightly"}]),
                 {"kind": "agent_status"}],  # no name: skipped
    }
    rows = {r["agent"]: r for r in fleet(by_env, NOW, "0.24.0")}
    assert sorted(rows) == ["etl-01", "etl-02"]
    one = rows["etl-01"]
    assert one["state"] == "live" and one["heartbeat_at"] == "2026-10-17T07:45:00Z"
    assert sorted(one["envs"]) == ["prod", "test"] and one["envs"]["prod"]["counts"] == {"queued": 2}
    assert one["envs"]["prod"]["incidents"] == {"enabled": True} and one["envs"]["test"]["incidents"] is None
    assert rows["etl-02"]["state"] == "offline" and rows["etl-02"]["version_compare"] == "older"
    assert rows["etl-02"]["envs"]["prod"]["schedules"] == [{"id": "nightly"}]


def test_the_app_lists_agents_and_says_which_environments_it_could_not_read(tmp_path):
    svc = Service(root=tmp_path / "app")
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: None, storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    fresh = (dt.datetime.now(UTC) + dt.timedelta(minutes=3)).isoformat()
    svc._incident_log("dev").backend.create("agents/etl-01.json", json.dumps(
        {**rec("etl-01", "now", fresh), "version": __version__}).encode())
    out = svc.agents_list()
    envs = {e["env"]: e for e in out["envs"]}
    assert envs["dev"]["count"] == 1 and envs["dev"]["observation"]["state"] == "live"
    assert envs["test"]["count"] is None and envs["test"]["observation"]["state"] == "not_set_up"
    [a] = out["agents"]
    assert a["agent"] == "etl-01" and a["state"] == "live" and a["version_compare"] == "same"
    assert out["app_version"] == __version__


def test_the_agents_list_warns_when_this_pc_and_an_agent_schedule_the_same_table(tmp_path):
    svc = Service(root=tmp_path / "app")
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: None, storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    fresh = (dt.datetime.now(UTC) + dt.timedelta(minutes=3)).isoformat()
    when = {"kind": "cron", "every_minutes": 60}
    svc._incident_log("dev").backend.create("agents/etl-01.json", json.dumps(
        {**rec("etl-01", "now", fresh), "schedules": [{"id": "hourly", "entities": [], "when": when},
                                                       {"id": "old"}]}).encode())
    svc.pc_schedules._save({"schedules": [{"id": "pc1", "enabled": True,
                                           "config": local_configuration({"kind": "daily", "times": ["06:00"]}),
                                           "target": {"kind": "meta", "env": "dev", "entities": ["bronze_x"]}}]})
    out = svc.agents_list()
    assert out["pc_schedules_read"] is True
    [o] = out["overlaps"]
    assert o["env"] == "dev" and o["entities"] == ["bronze_x"]
    assert [s["schedule"] for s in o["schedules"]] == ["hourly", "pc1"]
    assert "06:00" in o["schedules"][1]["text"]
    assert [u["schedule"] for u in out["unknown"]] == ["old"]
