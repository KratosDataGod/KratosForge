"""Tables scheduled in two places (workloads/overlap.py)."""

import datetime as dt

from fabric_desktop.workloads.overlap import overlaps

NOW = dt.datetime(2027, 1, 15, 8, 0, tzinfo=dt.timezone.utc)


def agent(name, schedules, state="running", stale="2027-01-15T08:01:30Z", beat="2027-01-15T07:59:30Z"):
    return {"kind": "agent_status", "agent": name, "state": state, "heartbeat_at": beat, "stale_after": stale,
            "schedules": schedules}


def sched(sid, entities=None, every=60):
    s = {"id": sid, "next": "2027-01-15T09:00:00Z", "when": {"kind": "cron", "every_minutes": every}}
    if entities is not None:
        s["entities"] = entities
    return s


def pc(sid, env, entities, enabled=True):
    return {"id": sid, "enabled": enabled, "target": {"kind": "meta", "env": env, "entities": entities},
            "text": "Every day at 06:00", "next_run": "2027-01-16T06:00:00"}


def test_an_agent_and_this_pc_running_the_same_table_overlap():
    out = overlaps({"prod": [agent("etl-01", [sched("hourly-silver", ["silver_orders", "silver_lines"])])]},
                   [pc("p1", "prod", ["silver_orders"]), pc("p2", "prod", ["gold_sales"])], NOW)
    [o] = out["overlaps"]
    assert o["env"] == "prod" and o["entities"] == ["silver_orders"]
    a, b = o["schedules"]
    assert (a["place"], a["agent"], a["schedule"]) == ("agent", "etl-01", "hourly-silver")
    assert a["text"] and (b["place"], b["schedule"]) == ("pc", "p1")
    assert out["unknown"] == []


def test_every_table_overlaps_anything_and_two_alls_say_every_table():
    out = overlaps({"prod": [agent("etl-01", [sched("all", [])]), agent("etl-02", [sched("all2", [])])]},
                   [pc("p1", "prod", ["gold_sales"])], NOW)
    shared = sorted((o["schedules"][0]["schedule"], o["schedules"][1]["schedule"], o["entities"]) for o in out["overlaps"])
    assert shared == [("all", "all2", None), ("all", "p1", ["gold_sales"]), ("all2", "p1", ["gold_sales"])]


def test_one_place_with_two_schedules_is_not_an_overlap():
    out = overlaps({"prod": [agent("etl-01", [sched("a", ["x"]), sched("b", ["x"])])]},
                   [pc("p1", "prod", ["y"]), pc("p2", "prod", ["y"])], NOW)
    assert out["overlaps"] == []


def test_other_environments_disabled_and_notebook_schedules_are_left_out():
    out = overlaps({"prod": [agent("etl-01", [sched("a", ["x"])])], "test": []},
                   [pc("p1", "test", ["x"]), pc("p2", "prod", ["x"], enabled=False),
                    {"id": "nb", "target": {"kind": "notebook", "name": "x"}}], NOW)
    assert out["overlaps"] == []


def test_a_cleanly_stopped_agent_runs_nothing_but_an_offline_one_still_counts():
    stopped = agent("etl-03", [sched("a", ["x"])], state="stopped")
    offline = agent("etl-02", [sched("b", ["x"])], stale="2027-01-15T07:43:00Z", beat="2027-01-15T07:40:00Z")
    out = overlaps({"prod": [stopped, offline]}, [pc("p1", "prod", ["x"])], NOW)
    [o] = out["overlaps"]
    assert o["schedules"][0]["agent"] == "etl-02" and o["schedules"][0]["state"] == "offline"


def test_an_older_agent_that_does_not_name_entities_is_unknown_not_every_table():
    old = agent("etl-old", [{"id": "nightly", "next": "2027-01-16T02:00:00Z"}])
    out = overlaps({"prod": [old]}, [pc("p1", "prod", ["x"])], NOW)
    assert out["overlaps"] == []
    [u] = out["unknown"]
    assert (u["agent"], u["schedule"], u["text"]) == ("etl-old", "nightly", "")


def test_the_newest_record_speaks_for_an_agent():
    older = agent("etl-01", [sched("a", ["x"])], beat="2027-01-15T07:00:00Z")
    newer = agent("etl-01", [sched("a", ["y"])])
    out = overlaps({"prod": [newer, older]}, [pc("p1", "prod", ["x"])], NOW)
    assert out["overlaps"] == []


def test_agents_sharing_one_schedule_through_the_mailbox_are_not_an_overlap():
    shared = {**sched("hourly", ["x"]), "shared": True}
    out = overlaps({"prod": [agent("etl-01", [shared]), agent("etl-02", [shared])]}, [], NOW)
    assert out["overlaps"] == []
    other = {**sched("hourly", ["x"]), "shared": False}
    out = overlaps({"prod": [agent("etl-01", [shared]), agent("etl-02", [other])]}, [], NOW)
    assert len(out["overlaps"]) == 1  # only one side shares it: both still run it
