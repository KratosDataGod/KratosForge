"""Taking over pipeline requests from an agent that stopped reporting (FD-124 part 2, mailbox.py)."""

import dataclasses
import json
import threading
import time

import pytest

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.mailbox import TAKEOVER_GRACE, LocalControl
from fabric_desktop.meta import LocalStore
from fabric_desktop.meta.templates import init_project


def config_text(project, home, name, pipeline="enabled = true\npoll_seconds = 1\ntake_over = true"):
    return f"""
[agent]
name = "{name}"
home = {json.dumps(str(home))}
project = {json.dumps(str(project))}
environments = ["dev"]

[auth]
type = "user"

[retry]
max_attempts = 3
backoff_seconds = 0

[pipeline]
{pipeline}
"""


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    init_project(p, "3", "Chris", True)
    return p


def make(tmp_path, project, name, **kw):
    path = tmp_path / f"{name}.toml"
    path.write_text(config_text(project, tmp_path / f"home-{name}", name, **kw), encoding="utf-8")
    return Agent(agent_config.load(path))


def root(project):
    return project / ".local" / "dev" / "_control"


def write(project, key, body):
    path = root(project) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def status(project, rid):
    return json.loads((root(project) / "status" / f"{rid}.json").read_text(encoding="utf-8"))


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def record(project, name, stale_after):
    write(project, f"agents/{name}.json", {"kind": "agent_status", "contract": 1, "agent": name,
                                           "state": "running", "stale_after": iso(stale_after)})


def lost(project, name):
    record(project, name, time.time() - TAKEOVER_GRACE - 60)


def run(agent):
    agent._mailbox_at = 0
    agent.step()
    for f in list(agent._running.values()):
        f.result(timeout=120)


def claimed_by_a(tmp_path, project, rid="r1"):
    """etl-01 claims a request and queues it, then stops reporting."""
    a = make(tmp_path, project, "etl-01")
    write(project, f"requests/{rid}.json", {"env": "dev", "entities": ["bronze.orders"]})
    a.mailbox.poll()
    assert status(project, rid)["agent"] == "etl-01" and status(project, rid)["agents"] == ["etl-01"]
    return a


def test_a_request_held_by_a_lost_agent_is_taken_over_and_runs_once(tmp_path, project):
    a = claimed_by_a(tmp_path, project)
    lost(project, "etl-01")
    b = make(tmp_path, project, "etl-02")
    run(b)
    s = status(project, "r1")
    assert s["state"] == "succeeded" and s["agent"] == "etl-02" and s["taken_over_from"] == "etl-01"
    assert s["agents"] == ["etl-01", "etl-02"] and s["attempts"] == 1
    # etl-01 comes back: its own copy is cancelled, not run, and the status stays etl-02's
    run(a)
    [job] = a.queue.list()
    assert job["state"] == "cancelled" and "etl-02 took this request over" in job["error"]
    assert status(project, "r1") == s
    log = LocalStore(project / ".local" / "dev", "dev").meta("run_log")
    assert list(log["worker"]) == ["etl-02"]


@pytest.mark.parametrize("owner_record", ["live", "missing", "unreadable"])
def test_a_request_stays_with_its_agent_unless_that_agent_is_known_to_be_lost(tmp_path, project, owner_record):
    claimed_by_a(tmp_path, project)
    if owner_record == "live":
        record(project, "etl-01", time.time() + 120)
    elif owner_record == "unreadable":
        (root(project) / "agents").mkdir(parents=True, exist_ok=True)
        (root(project) / "agents" / "etl-01.json").write_text("{not json", encoding="utf-8")
    b = make(tmp_path, project, "etl-02")
    run(b)
    assert status(project, "r1")["agent"] == "etl-01" and sum(b.queue.counts().values()) == 0


def test_within_the_grace_for_clock_differences_nothing_is_taken_over(tmp_path, project):
    claimed_by_a(tmp_path, project)
    record(project, "etl-01", time.time() - TAKEOVER_GRACE / 2)
    b = make(tmp_path, project, "etl-02")
    b.mailbox.poll()
    assert status(project, "r1")["agent"] == "etl-01"


def test_takeover_is_off_unless_configured(tmp_path, project):
    claimed_by_a(tmp_path, project)
    lost(project, "etl-01")
    b = make(tmp_path, project, "etl-02", pipeline="enabled = true\npoll_seconds = 1")
    assert b.mailbox.take_over is False
    b.mailbox.poll()
    assert status(project, "r1")["agent"] == "etl-01"


def test_an_agent_does_not_take_over_a_request_it_would_refuse(tmp_path, project):
    claimed_by_a(tmp_path, project)
    lost(project, "etl-01")
    b = make(tmp_path, project, "etl-02", pipeline='enabled = true\ntake_over = true\nentities = ["silver.*"]')
    b.mailbox.poll()
    assert status(project, "r1")["agent"] == "etl-01"


def test_a_request_that_every_agent_lost_fails_after_max_attempts(tmp_path, project):
    write(project, "requests/r1.json", {"env": "dev", "entities": ["bronze.orders"]})
    write(project, "status/r1.json", {"contract": 1, "request_id": "r1", "env": "dev", "state": "running",
                                      "agent": "etl-03", "agents": ["etl-01", "etl-02", "etl-03"],
                                      "entities": ["bronze.orders"], "job_id": "job-x"})
    lost(project, "etl-03")
    d = make(tmp_path, project, "etl-04")
    d.mailbox.poll()
    s = status(project, "r1")
    assert s["state"] == "failed" and "3 agents took this request and stopped reporting: etl-01, etl-02, etl-03" in s["error"]
    assert "job_id" not in s and sum(d.queue.counts().values()) == 0


def test_an_older_status_without_an_agents_list_counts_its_owner(tmp_path, project):
    write(project, "requests/r1.json", {"env": "dev", "entities": ["bronze.orders"]})
    write(project, "status/r1.json", {"contract": 1, "request_id": "r1", "env": "dev", "state": "accepted",
                                      "agent": "etl-01", "entities": ["bronze.orders"]})
    lost(project, "etl-01")
    b = make(tmp_path, project, "etl-02")
    b.mailbox.poll()
    assert status(project, "r1")["agents"] == ["etl-01", "etl-02"]


def test_eight_agents_racing_to_take_over_one_request_leave_one_owner(tmp_path, project):
    claimed_by_a(tmp_path, project)
    lost(project, "etl-01")
    shared = LocalControl(root(project))  # one store for all of them, as OneLake is
    agents = [make(tmp_path, project, f"etl-1{i}") for i in range(8)]
    for ag in agents:
        ag.mailbox._controls["dev"] = shared
    go = threading.Barrier(len(agents))

    def poll(ag):
        go.wait()
        ag.mailbox.poll()

    threads = [threading.Thread(target=poll, args=(ag,)) for ag in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s = status(project, "r1")
    owners = [ag.config.name for ag in agents if sum(ag.queue.counts().values())]
    assert owners == [s["agent"]] and s["agents"] == ["etl-01", s["agent"]]


def test_a_paused_agent_that_comes_back_mid_run_stops_before_its_next_table(tmp_path, project, monkeypatch):
    a = claimed_by_a(tmp_path, project)
    b = make(tmp_path, project, "etl-02")
    real = a.runtime.run_environment

    def paused(*args, **kwargs):  # etl-01 freezes after starting; etl-02 takes the request over meanwhile
        lost(project, "etl-01")
        b.mailbox.poll()
        return real(*args, **kwargs)
    monkeypatch.setattr(a.runtime, "run_environment", paused)
    run(a)
    [job] = a.queue.list()
    assert job["state"] == "cancelled" and "etl-02 took this request over" in job["error"]
    assert status(project, "r1")["agent"] == "etl-02"
    run(b)
    assert status(project, "r1")["state"] == "succeeded"
    log = LocalStore(project / ".local" / "dev", "dev").meta("run_log")
    assert list(log["worker"]) == ["etl-02"]  # etl-01 wrote and recorded nothing
    audit = [json.loads(line) for p in (a.config.home / "audit").glob("*.jsonl") for line in p.read_text().splitlines()]
    assert [e["outcome"] for e in audit if e["action"] == "agent_job"] == ["handed_over"]


def test_a_refused_status_update_tells_the_agent_its_request_was_taken_over(tmp_path, project):
    a = claimed_by_a(tmp_path, project)
    s = status(project, "r1")
    write(project, "status/r1.json", s | {"agent": "etl-02", "agents": ["etl-01", "etl-02"]})
    job = a.queue.find("pipeline:r1")
    stale = s | {"state": "running"}  # what etl-01 read before etl-02 replaced it
    read = dataclasses.replace(a.mailbox.control("dev").read("status/r1.json"), etag="old")
    a.mailbox._sync("dev", "r1", stale, read)
    assert a.queue.get(job["id"])["state"] == "cancelled"


def test_an_agent_taken_over_from_and_then_given_the_request_back_runs_it_afresh(tmp_path, project):
    a = claimed_by_a(tmp_path, project)
    lost(project, "etl-01")
    b = make(tmp_path, project, "etl-02")
    b.mailbox.poll()
    a.mailbox.poll()  # etl-01 is back: its copy is cancelled
    record(project, "etl-01", time.time() + 120)
    lost(project, "etl-02")
    run(a)
    s = status(project, "r1")
    assert s["state"] == "succeeded" and s["agent"] == "etl-01" and s["agents"] == ["etl-01", "etl-02", "etl-01"]


def test_take_over_is_checked_in_agent_toml(tmp_path, project):
    with pytest.raises(agent_config.ConfigError) as exc:
        make(tmp_path, project, "etl-01", pipeline='enabled = true\ntake_over = "yes"')
    assert "pipeline.take_over: true or false" in str(exc.value)


def test_a_request_past_its_deadline_expires_rather_than_starting_again(tmp_path, project):
    a = make(tmp_path, project, "etl-01")
    write(project, "requests/r1.json", {"env": "dev", "entities": ["bronze.orders"], "deadline": iso(time.time() + 2)})
    a.mailbox.poll()
    lost(project, "etl-01")
    time.sleep(2.1)
    b = make(tmp_path, project, "etl-02")
    b.mailbox.poll()
    s = status(project, "r1")
    assert s["agent"] == "etl-02" and s["state"] == "expired"
