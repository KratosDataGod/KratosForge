"""Schedules shared by several agents through the mailbox (FD-124 part 1, shared_schedules.py)."""

import json
import random
import threading

import pytest

from fabric_desktop import agent_config, shared_schedules
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.mailbox import LocalControl
from fabric_desktop.meta.templates import init_project

T0 = 1_800_000_000.0  # 2027-01-15T08:00:00Z


def config_text(project, home, schedule="every_minutes = 60\nshared = true", pipeline="enabled = true\npoll_seconds = 1",
                name="etl-01"):
    return f"""
[agent]
name = "{name}"
home = {json.dumps(str(home))}
project = {json.dumps(str(project))}
environments = ["dev"]

[auth]
type = "user"

[retry]
backoff_seconds = 0

[pipeline]
{pipeline}

[[schedule]]
id = "dev-hourly"
env = "dev"
entities = ["bronze.orders"]
{schedule}
"""


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    init_project(p, "3", "Chris", True)
    return p


def load(tmp_path, project, **kw):
    path = tmp_path / f"{kw.get('name', 'etl-01')}.toml"
    path.write_text(config_text(project, tmp_path / f"home-{kw.get('name', 'etl-01')}", **kw), encoding="utf-8")
    return agent_config.load(path)


def control(project):
    return project / ".local" / "dev" / "_control"


def sched(when, sid="s", entities=("bronze.orders",)):
    return agent_config.Schedule(sid, "dev", list(entities), {}, "", when, True)


# ---- slots

def test_every_agent_names_the_same_slot_whatever_its_clock():
    hourly = {"kind": "cron", "every_minutes": 60}
    assert shared_schedules.next_aligned(hourly, T0 + 7) == T0 + 3600
    assert shared_schedules.next_aligned(hourly, T0 + 3599) == T0 + 3600
    for skew in (0, 3, 59, 900):
        assert shared_schedules.slot(hourly, T0 + skew) == ("20270115T0800Z", T0 + 3600)
    daily = {"kind": "daily", "times": ["02:00"]}
    assert shared_schedules.slot(daily, T0) == ("20270115T0800Z", T0 + 6 * 3600)
    weekly = {"kind": "cron", "every_minutes": 60 * 24 * 7}
    name, deadline = shared_schedules.slot(weekly, T0)
    assert deadline - shared_schedules.WINDOW <= T0  # a long interval still expires 6 hours after its slot starts
    rid = shared_schedules.request_id("x" * shared_schedules.MAX_ID, "20270115T0800Z")
    assert len(rid) <= 64


def test_three_agents_racing_on_a_thousand_slots_make_one_request_each(tmp_path):
    backend = LocalControl(tmp_path / "ctl")
    s = sched({"kind": "cron", "every_minutes": 15})
    outcomes: dict[str, list[str]] = {}
    lock = threading.Lock()

    def agent(name):
        slots = list(range(1000))
        rng_local = random.Random(name)
        rng_local.shuffle(slots)
        for n in slots:
            due = T0 + n * 900 + rng_local.uniform(0, 30)  # each agent notices a little late
            rid, outcome = shared_schedules.submit(backend, s, due, due, name)
            with lock:
                outcomes.setdefault(rid, []).append(outcome)

    threads = [threading.Thread(target=agent, args=(f"etl-0{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(outcomes) == 1000
    assert all(sorted(o) == ["created", "exists", "exists"] for o in outcomes.values())
    assert len(backend.list("requests")) == 1000


def test_a_late_agent_does_not_bring_back_a_slot_past_its_deadline(tmp_path):
    backend = LocalControl(tmp_path / "ctl")
    s = sched({"kind": "cron", "every_minutes": 60})
    rid, outcome = shared_schedules.submit(backend, s, T0, T0 + 3601, "etl-03")
    assert outcome == "late" and backend.list("requests") == []
    rid, outcome = shared_schedules.submit(backend, s, T0, T0 + 10, "etl-01")
    body = json.loads(backend.read(f"requests/{rid}.json").data)
    assert outcome == "created" and body["deadline"] == "2027-01-15T09:00:00Z" and body["env"] == "dev"
    assert body["entities"] == ["bronze.orders"] and "written by agent etl-01" in body["submitted_by"]


# ---- agents

def test_two_agents_sharing_a_schedule_run_each_due_time_once(tmp_path, project):
    t = [T0 + 5]
    a_cfg, b_cfg = load(tmp_path, project), load(tmp_path, project, name="etl-02")
    a = Agent(a_cfg, queue=JobQueue(a_cfg.home / "q.sqlite3", clock=lambda: t[0]), clock=lambda: t[0])
    b = Agent(b_cfg, queue=JobQueue(b_cfg.home / "q.sqlite3", clock=lambda: t[0]), clock=lambda: t[0])
    for ag in (a, b):
        ag.tick_schedules()
        assert ag.queue.schedule_next("dev-hourly") == T0 + 3600  # aligned: the same on both
    t[0] = T0 + 3600 + 4
    assert a.tick_schedules() == ["dev-hourly"] and b.tick_schedules() == ["dev-hourly"]
    requests = sorted((control(project) / "requests").glob("*.json"))
    assert [p.name for p in requests] == ["sched-dev-hourly-20270115T0900Z.json"]
    b.mailbox.poll()
    a.mailbox.poll()
    queued = a.queue.counts()["queued"] + b.queue.counts()["queued"]
    assert queued == 1 and b.queue.counts()["queued"] == 1  # one run, on whichever claimed first
    assert a.queue.schedule_next("dev-hourly") == T0 + 7200


def test_a_busy_agent_leaves_requests_for_one_with_a_free_worker(tmp_path, project):
    pipeline = 'enabled = true\npoll_seconds = 1\nclaim = "when_free"'
    a_cfg, b_cfg = load(tmp_path, project, pipeline=pipeline), load(tmp_path, project, pipeline=pipeline, name="etl-02")
    a, b = Agent(a_cfg), Agent(b_cfg)
    a.enqueue("dev", ["bronze.orders"], source="cli")  # a's only worker has work waiting
    (control(project) / "requests").mkdir(parents=True, exist_ok=True)
    (control(project) / "requests" / "r1.json").write_text(json.dumps({"env": "dev", "entities": ["bronze.orders"]}))
    a.mailbox.poll()
    assert not (control(project) / "status" / "r1.json").exists()
    b.mailbox.poll()
    assert json.loads((control(project) / "status" / "r1.json").read_text())["agent"] == "etl-02"


def test_claim_always_is_still_the_default(tmp_path, project):
    cfg = load(tmp_path, project)
    assert cfg.pipeline.claim == "always" and Agent(cfg).mailbox.claim == "always"


def test_the_status_record_says_a_schedule_is_shared(tmp_path, project):
    from fabric_desktop.agent_status import record
    cfg = load(tmp_path, project)
    [s] = record(Agent(cfg), "dev")["schedules"]
    assert s["shared"] is True and s["entities"] == ["bronze.orders"]


@pytest.mark.parametrize("schedule, pipeline, error", [
    ("every_minutes = 60\nshared = true", "enabled = false", "enable [pipeline] for dev"),
    ("every_minutes = 60\nshared = \"yes\"", "enabled = true", "shared is true or false"),
    ("every_minutes = 60\nshared = true\nbackfill = \"full\"", "enabled = true", "needs pipeline.allow_backfill"),
    ("every_minutes = 60\nshared = true", 'enabled = true\nentities = ["silver.*"]', "isn't in pipeline.entities"),
    ("every_minutes = 60", 'enabled = true\nclaim = "sometimes"', 'pipeline.claim: "always" or "when_free"'),
    ("every_minutes = 1.5\nshared = true", "enabled = true", "whole number of at least 1"),
])
def test_shared_schedule_settings_are_checked(tmp_path, project, schedule, pipeline, error):
    with pytest.raises(agent_config.ConfigError) as exc:
        load(tmp_path, project, schedule=schedule, pipeline=pipeline)
    assert error in str(exc.value)


def test_a_long_shared_schedule_id_is_refused(tmp_path, project):
    text = config_text(project, tmp_path / "h").replace('id = "dev-hourly"', f'id = "{"x" * 41}"')
    path = tmp_path / "long.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(agent_config.ConfigError) as exc:
        agent_config.load(path)
    assert "at most 40 characters" in str(exc.value)


def test_an_unshared_schedule_runs_as_before(tmp_path, project):
    cfg = load(tmp_path, project, schedule="every_minutes = 60")
    t = [T0 + 5]
    agent = Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=lambda: t[0]), clock=lambda: t[0])
    agent.tick_schedules()
    t[0] = agent.queue.schedule_next("dev-hourly") + 1
    assert agent.tick_schedules() == ["dev-hourly"]
    assert agent.queue.counts()["queued"] == 1 and not (control(project) / "requests").exists()
