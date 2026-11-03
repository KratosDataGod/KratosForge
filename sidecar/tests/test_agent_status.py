"""The agent's status records: agents/<name>.json in each environment's control area."""

import dataclasses
import json
import threading

import pytest

from fabric_desktop import __version__, agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.agent_status import publish
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.mailbox import LocalControl
from fabric_desktop.meta.templates import init_project


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(f"""
[agent]
name = "etl-01"
home = {json.dumps(str(tmp_path / "home"))}
project = {json.dumps(str(project))}
environments = ["dev"]
status_seconds = 30

[auth]
type = "user"

[pipeline]
enabled = true
entities = ["bronze.*"]

[[schedule]]
id = "dev-hourly"
env = "dev"
every_minutes = 60
""", encoding="utf-8")
    return agent_config.load(path)


def record(cfg, name="etl-01") -> dict:
    return json.loads((cfg.project / ".local" / "dev" / "_control" / "agents" / f"{name}.json").read_text(encoding="utf-8"))


def test_the_agent_publishes_what_it_is_doing(setup):
    t = [1_800_000_000.0]
    agent = Agent(setup, queue=JobQueue(setup.home / "q.sqlite3", clock=lambda: t[0]), clock=lambda: t[0])
    release, started = threading.Event(), threading.Event()
    real = agent.runtime.run_environment

    def held(*args, **kwargs):
        started.set()
        release.wait(60)
        return real(*args, **kwargs)
    agent.runtime.run_environment = held
    job = agent.enqueue("dev", ["bronze.orders"], source="cli")
    agent.step()
    started.wait(60)
    r = record(setup)
    assert r["contract"] == 1 and r["kind"] == "agent_status" and r["agent"] == "etl-01"
    assert r["version"] == __version__ and r["state"] == "running" and r["env"] == "dev"
    assert r["heartbeat_at"] == "2027-01-15T08:00:00Z" and r["stale_after"] == "2027-01-15T08:01:30Z"
    assert r["counts"]["running"] == 1 and r["running"][0]["job_id"] == job["id"]
    assert r["running"][0]["entities"] == ["bronze.orders"]
    assert r["pipeline"] == {"enabled": True, "entities": ["bronze.*"], "allow_backfill": False, "require_run": False}
    assert r["schedules"][0]["id"] == "dev-hourly"
    assert r["schedules"][0]["entities"] == []  # all entities: present and empty, not missing
    assert r["schedules"][0]["when"] == {"kind": "cron", "every_minutes": 60}
    assert r["owner"] is None and r["identity"] == "user"
    release.set()
    for f in list(agent._running.values()):
        f.result(timeout=120)
    agent.step()  # not due yet: the record isn't rewritten every pass
    assert record(setup)["counts"]["running"] == 1
    t[0] += 30
    agent.step()
    r = record(setup)
    assert r["counts"]["running"] == 0 and r["counts"]["succeeded"] == 1 and r["running"] == []
    assert r["recent"][0] == {"job_id": job["id"], "state": "succeeded", "source": "cli",
                              "finished_at": "2027-01-15T08:00:00Z", "error": None}


def test_a_record_only_shows_its_own_environment(setup, tmp_path):
    agent = Agent(setup)
    agent.queue.enqueue("meta", {"env": "prod", "entities": []}, source="other")
    agent.enqueue("dev", ["bronze.orders"])
    agent.publish_status()
    r = record(setup)
    assert r["counts"]["queued"] == 1 and sum(r["counts"].values()) == 1
    assert "prod" not in json.dumps(r["recent"]) and r["environments"] == ["dev"]


def test_a_clean_stop_says_so(setup):
    agent = Agent(dataclasses.replace(setup, poll_seconds=0.05))
    loop = threading.Thread(target=agent.run_forever, kwargs={"install_signals": False})
    loop.start()
    for _ in range(200):
        if (setup.project / ".local" / "dev" / "_control" / "agents" / "etl-01.json").exists():
            break
        threading.Event().wait(0.05)
    assert record(setup)["state"] == "running"
    agent.stop()
    loop.join(30)
    assert record(setup)["state"] == "stopped"


def test_a_stopping_agent_keeps_its_record_fresh_until_its_jobs_finish(setup):
    agent = Agent(dataclasses.replace(setup, poll_seconds=0.05, status_seconds=0.05))
    release, started = threading.Event(), threading.Event()
    agent.runtime.run_environment = lambda *a, **k: (started.set(), release.wait(60), [])[2]
    states = []
    real = agent.publish_status
    agent.publish_status = lambda state="running": (states.append(state), real(state))[1]
    agent.enqueue("dev", ["bronze.orders"], source="cli")
    loop = threading.Thread(target=agent.run_forever, kwargs={"install_signals": False})
    loop.start()
    assert started.wait(30)
    agent.stop()
    for _ in range(200):
        if states.count("stopping") >= 3:
            break
        threading.Event().wait(0.05)
    assert states.count("stopping") >= 3 and "stopped" not in states  # beats while the job still runs
    release.set()
    loop.join(30)
    assert states[-1] == "stopped" and record(setup)["state"] == "stopped"


def test_a_failed_publish_is_logged_and_the_loop_goes_on(setup, caplog):
    agent = Agent(setup)

    def broken(env):
        raise PermissionError("can't write LH_Meta")
    agent.runtime.control_backend = broken
    agent.mailbox._control_for = broken
    agent.step()
    assert "couldn't publish the agent status" in caplog.text


def test_publish_replaces_the_previous_record(tmp_path):
    c = LocalControl(tmp_path)
    publish(c, "etl-01", {"n": 1})
    publish(c, "etl-01", {"n": 2})
    assert json.loads((tmp_path / "agents" / "etl-01.json").read_text())["n"] == 2

    class Racing(LocalControl):
        def replace(self, key, data, etag):
            return None  # someone else changes it every time
    with pytest.raises(RuntimeError, match="another agent called etl-01"):
        publish(Racing(tmp_path), "etl-01", {"n": 3})


def test_status_seconds_is_checked(setup):
    raw = {"agent": {"project": str(setup.project), "environments": ["dev"], "status_seconds": 1}, "auth": {"type": "user"}}
    with pytest.raises(agent_config.ConfigError, match="agent.status_seconds"):
        agent_config.parse(raw)
    assert agent_config.parse({**raw, "agent": raw["agent"] | {"status_seconds": 120}}).status_seconds == 120


def test_the_record_names_the_owner_and_what_each_schedule_runs(setup):
    cfg = dataclasses.replace(setup, owner="Sales data team", schedules=[
        agent_config._schedule({"id": "dev-gold", "env": "dev", "daily": ["02:00"], "entities": ["gold.sales"]}, 0, []),
        agent_config._schedule({"id": "dev-weekly", "env": "dev", "weekly": {"days": ["Monday"], "times": ["06:00"]}}, 1, [])])
    from fabric_desktop.agent_status import record as status_record
    agent = Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3"))
    r = status_record(agent, "dev")
    assert r["owner"] == "Sales data team" and r["identity"] == "user"
    assert [(s["id"], s["entities"], s["when"]) for s in r["schedules"]] == [
        ("dev-gold", ["gold.sales"], {"kind": "daily", "times": ["02:00"]}),
        ("dev-weekly", [], {"kind": "weekly", "times": ["06:00"], "weekdays": ["Monday"]})]
    assert "certificate_path" not in json.dumps(r) and "client_id" not in json.dumps(r)


def test_owner_comes_from_agent_toml(setup):
    text = setup.path.read_text(encoding="utf-8").replace('name = "etl-01"', 'name = "etl-01"\nowner = "  Sales data team "')
    setup.path.write_text(text, encoding="utf-8")
    assert agent_config.load(setup.path).owner == "Sales data team"
