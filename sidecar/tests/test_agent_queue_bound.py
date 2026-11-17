"""A bounded agent queue (agent.max_queued): when it backs up, new work is held back with its reason,
never dropped, and nothing already queued is removed to make room."""

import dataclasses
import json
import logging
import time

import pytest

from fabric_desktop import agent_config, agent_status, cli
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue, QueueFull
from fabric_desktop.meta.templates import init_project

from test_agent import Clock, config_text, setup  # noqa: F401 - setup is a fixture
from test_mailbox import config_text as pipeline_config_text
from test_mailbox import nb, root, status, write_request  # noqa: F401 - nb is a fixture


def _fill(queue: JobQueue, n: int) -> list[str]:
    return [queue.enqueue("meta", {"env": "dev", "entities": [f"t{i}"]}, source="cli")["id"] for i in range(n)]


def _agent(cfg, clock=None, **changes):
    cfg = dataclasses.replace(cfg, **changes)
    clock = clock or Clock()
    return Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)


def test_the_queue_refuses_a_new_job_past_its_limit_and_keeps_every_queued_one(tmp_path):
    queue = JobQueue(tmp_path / "q.sqlite3")
    ids = _fill(queue, 3)
    with pytest.raises(QueueFull, match=r"3 jobs are waiting \(agent.max_queued = 3\); nothing was queued"):
        queue.enqueue("meta", {"env": "dev"}, limit=3)
    assert queue.counts()["queued"] == 3 and all(queue.get(i)["state"] == "queued" for i in ids)
    # the same work already waiting is still returned, full or not: it isn't new work
    first = queue.enqueue("meta", {"env": "dev"}, dedupe_key="k")
    assert queue.enqueue("meta", {"env": "dev"}, dedupe_key="k", limit=1)["id"] == first["id"]
    # running jobs don't count against it: only those waiting
    queue.claim("me", 60)
    assert queue.enqueue("meta", {"env": "dev"}, limit=4)["state"] == "queued"


def test_below_the_limit_everything_queues_as_before(setup):  # noqa: F811
    agent = _agent(setup, max_queued=5)
    for i in range(5):
        assert agent.enqueue("dev", [f"t{i}"])["state"] == "queued"
    assert agent.queue.counts()["queued"] == 5 and agent.schedules_deferred == 0


def test_the_setting_is_read_and_checked(setup):  # noqa: F811
    assert setup.max_queued == 1000
    path = setup.path
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("poll_seconds = 0.1", "poll_seconds = 0.1\nmax_queued = 0"), encoding="utf-8")
    with pytest.raises(Exception, match="agent.max_queued: a number of at least 1"):
        agent_config.load(path)
    path.write_text(text.replace("poll_seconds = 0.1", "poll_seconds = 0.1\nmax_queued = 50"), encoding="utf-8")
    assert agent_config.load(path).max_queued == 50


def _cli(argv) -> int:
    try:
        return cli.main(argv) or 0
    except SystemExit as stop:
        return int(stop.code or 0)


def test_the_cli_says_why_it_didnt_queue(setup, capsys):  # noqa: F811
    path = setup.path
    path.write_text(path.read_text(encoding="utf-8").replace("poll_seconds = 0.1", "poll_seconds = 0.1\nmax_queued = 1"),
                    encoding="utf-8")
    assert _cli(["agent", "--config", str(path), "enqueue", "dev"]) == 0
    capsys.readouterr()
    assert _cli(["agent", "--config", str(path), "enqueue", "dev"]) == 1
    out = capsys.readouterr().out
    assert "Not queued: the agent's queue is full: 1 jobs are waiting (agent.max_queued = 1)" in out
    lines = [json.loads(line) for f in setup.home.rglob("*.jsonl") for line in f.read_text(encoding="utf-8").splitlines()
             if '"agent_enqueue"' in line]
    assert [(e["outcome"], e.get("reason")) for e in lines] == [("ok", None), ("error", "refused")]
    assert JobQueue(setup.home / "queue.sqlite3").counts()["queued"] == 1


def _scheduled(tmp_path, clock, max_queued):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "s.toml"
    text = config_text(project, tmp_path / "h", '[[schedule]]\nid = "q"\nenv = "dev"\nevery_minutes = 15')
    path.write_text(text.replace("poll_seconds = 0.1", f"poll_seconds = 0.1\nmax_queued = {max_queued}"), encoding="utf-8")
    cfg = agent_config.load(path)
    return Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)


def test_a_schedule_due_in_a_full_queue_waits_and_runs_once_when_there_is_room(tmp_path, caplog):
    clock = Clock(1_800_000_000.0)
    agent = _scheduled(tmp_path, clock, max_queued=2)
    agent.tick_schedules()
    due = agent.queue.schedule_next("q")
    ids = _fill(agent.queue, 2)
    clock.t = due
    with caplog.at_level(logging.INFO):
        assert agent.tick_schedules() == []
        clock.t += 60
        assert agent.tick_schedules() == []  # still full: still waiting, logged once
    deferred = [r for r in caplog.records if r.getMessage().startswith("schedule deferred")]
    assert len(deferred) == 1 and agent.schedules_deferred == 1
    assert agent.queue.schedule_next("q") == due  # kept, not skipped to the next time
    assert all(agent.queue.get(i)["state"] == "queued" for i in ids)  # nothing removed for room
    record = agent_status.record(agent, "dev")
    assert record["schedules_deferred"] == 1 and record["max_queued"] == 2
    agent.queue.claim("w", 60)  # one starts: there is room
    clock.t += 60
    assert agent.tick_schedules() == ["q"]
    assert agent.tick_schedules() == []  # once
    assert agent.queue.find("schedule:q")["state"] == "queued"
    assert agent.queue.schedule_next("q") > clock.t


@pytest.fixture
def pipeline_cfg(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(pipeline_config_text(project, tmp_path / "home"), encoding="utf-8")
    return dataclasses.replace(agent_config.load(path), max_queued=1)


def test_a_pipeline_request_waits_with_its_reason_then_queues(pipeline_cfg):
    cfg = pipeline_cfg
    agent = Agent(cfg)
    ids = _fill(agent.queue, 1)
    write_request(cfg, "full-1", {"env": "dev", "entities": ["bronze.orders"]})
    agent.mailbox.poll()
    st = status(cfg, "full-1")
    assert st["state"] == "accepted" and "job_id" not in st
    assert st["waiting"] == "the agent's queue is full (agent.max_queued = 1); it is queued when there is room"
    later = agent.clock() + 120
    agent.clock = lambda: later  # a later poll, still full: the status isn't written again
    agent.mailbox.poll()
    assert status(cfg, "full-1")["updated_at"] == st["updated_at"]
    assert agent.queue.get(ids[0])["state"] == "queued"
    agent.queue.claim("w", 60)  # room
    agent.mailbox.poll()
    st = status(cfg, "full-1")
    assert "waiting" not in st and st["job_id"]
    assert agent.queue.get(st["job_id"])["source"] == "pipeline:full-1"
    assert root(cfg).exists()


def _iso_in(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def test_a_waiting_request_expires_when_its_deadline_passes_and_is_never_queued(pipeline_cfg):
    cfg = pipeline_cfg
    agent = Agent(cfg)
    _fill(agent.queue, 1)
    write_request(cfg, "full-late", {"env": "dev", "entities": ["bronze.orders"], "deadline": _iso_in(3600)})
    agent.mailbox.poll()
    assert status(cfg, "full-late")["waiting"]
    agent.clock = lambda: time.time() + 7200  # past the deadline, and still waiting for room
    agent.mailbox.poll()
    st = status(cfg, "full-late")
    assert st["state"] == "expired" and "passed before it started" in st["reason"]
    assert "waiting" not in st and "job_id" not in st
    agent.queue.claim("w", 60)  # room now, but the request has ended
    agent.mailbox.poll()
    assert agent.queue.find("pipeline:full-late") is None
    assert status(cfg, "full-late")["state"] == "expired"


def test_a_waiting_request_is_cancelled_by_its_cancel_file_and_is_never_queued(pipeline_cfg, nb):
    cfg = pipeline_cfg
    agent = Agent(cfg)
    _fill(agent.queue, 1)
    write_request(cfg, "full-cancel", {"env": "dev", "entities": ["bronze.orders"]})
    agent.mailbox.poll()
    assert status(cfg, "full-cancel")["waiting"]
    nb["cancel"](str(root(cfg)), "full-cancel")
    agent.mailbox.poll()
    st = status(cfg, "full-cancel")
    assert st["state"] == "cancelled" and st["reason"] == "a cancel file arrived before it was queued"
    assert "waiting" not in st and "job_id" not in st
    agent.queue.claim("w", 60)
    agent.mailbox.poll()
    assert agent.queue.find("pipeline:full-cancel") is None
