"""The agent when the machine sleeps or its clock jumps (NTP, a time-zone change, someone setting
it by hand): nothing runs twice, a job still running here isn't taken back, periodic work doesn't
stop for as long as the clock went back, and a failing route doesn't rest for the jump."""

import dataclasses
from concurrent.futures import Future

from fabric_desktop import agent_config
from fabric_desktop.agent import Agent
from fabric_desktop.jobqueue import JobQueue
from fabric_desktop.workloads.notify import Outbox

from test_agent import Clock, config_text, setup  # noqa: F401 - setup is a fixture

HOUR = 3600.0


def _agent(cfg, clock, **changes):
    cfg = dataclasses.replace(cfg, **changes) if changes else cfg
    return Agent(cfg, queue=JobQueue(cfg.home / "queue.sqlite3", clock=clock, backoff_seconds=10), clock=clock)


def _counting(agent, monkeypatch):
    published = []
    monkeypatch.setattr(agent, "publish_status", lambda state="running": published.append(agent.clock()))
    return published


def _running_here(agent):
    """A job this agent claimed and is still running (its worker thread is busy)."""
    agent.enqueue("dev")
    job = agent.queue.claim(agent.config.name, agent.config.job_lease_seconds)
    agent._running[job["id"]] = Future()  # not done
    agent._claims[job["id"]] = job["claim_id"]
    return job


# ---- sleep, or the clock jumping forward

def test_a_job_still_running_here_isnt_taken_back_after_a_sleep(setup, monkeypatch):  # noqa: F811
    clock = Clock()
    agent = _agent(setup, clock)
    _counting(agent, monkeypatch)
    job = _running_here(agent)
    agent.stopping.set()  # claim nothing new: only recovery is under test
    agent.step()
    clock.t += 3 * agent.config.job_lease_seconds  # the laptop slept; the job's thread is still there
    agent.step()
    after = agent.queue.get(job["id"])
    assert after["state"] == "running" and after["attempts"] == 1 and after["claim_id"] == job["claim_id"]
    assert after["lease_until"] == clock.t + agent.config.job_lease_seconds


def test_a_job_whose_worker_is_gone_is_still_recovered(setup, monkeypatch):  # noqa: F811
    clock = Clock()
    agent = _agent(setup, clock)
    _counting(agent, monkeypatch)
    agent.enqueue("dev")
    job = agent.queue.claim("another-process", agent.config.job_lease_seconds)  # not running here
    agent.stopping.set()
    clock.t += 3 * agent.config.job_lease_seconds
    agent.step()
    assert agent.queue.get(job["id"])["state"] == "queued"


def test_after_a_sleep_the_status_is_published_before_anything_slower(setup, monkeypatch):  # noqa: F811
    clock = Clock()
    agent = _agent(setup, clock)
    published = _counting(agent, monkeypatch)
    order = []
    monkeypatch.setattr(agent.queue, "recover", lambda max_lease=None: order.append(("recover", len(published))) or [])
    agent.stopping.set()
    agent.step()
    first = len(published)
    clock.t += 5 * HOUR
    agent.step()
    assert len(published) == first + 1  # once: the regular beat doesn't publish again in the same pass
    assert order[-1] == ("recover", first + 1)  # published before recovery ran


# ---- the clock going back

def test_periodic_work_doesnt_stop_for_as_long_as_the_clock_went_back(setup, monkeypatch):  # noqa: F811
    clock = Clock()
    agent = _agent(setup, clock)
    published = _counting(agent, monkeypatch)
    agent.stopping.set()
    agent.step()
    n = len(published)
    clock.t -= 2 * HOUR  # NTP, or a time-zone fix by hand
    agent.step()
    assert len(published) == n + 1  # was: no status record for two hours, so peers saw it offline
    clock.t += agent.config.status_seconds
    agent.step()
    assert len(published) == n + 2  # and the beat carries on from the new time


def _every_15(tmp_path, clock):
    path = tmp_path / "s.toml"
    project = tmp_path / "proj"
    from fabric_desktop.meta.templates import init_project
    init_project(project, "3", "Chris", True)
    path.write_text(config_text(project, tmp_path / "h", '[[schedule]]\nid = "q"\nenv = "dev"\nevery_minutes = 15'),
                    encoding="utf-8")
    cfg = agent_config.load(path)
    return Agent(cfg, queue=JobQueue(cfg.home / "q.sqlite3", clock=clock), clock=clock)


def test_a_schedule_isnt_stopped_by_the_clock_going_back_and_doesnt_run_twice(tmp_path):
    clock = Clock(1_800_000_000.0)  # on a 15-minute boundary
    agent = _every_15(tmp_path, clock)
    agent.tick_schedules()
    clock.t = agent.queue.schedule_next("q")
    assert agent.tick_schedules() == ["q"]  # ran
    clock.t -= 3 * HOUR
    assert agent.tick_schedules() == []  # not again at once for the same time
    nxt = agent.queue.schedule_next("q")
    assert clock.t < nxt <= clock.t + 15 * 60  # was: three hours away, so it stopped for three hours
    clock.t = nxt
    assert agent.tick_schedules() == ["q"]


def test_a_small_step_back_doesnt_run_a_daily_schedule_again(tmp_path):
    clock = Clock()
    agent = _every_15(tmp_path, clock)
    agent.queue.set_schedule_next("q", clock.t + 24 * HOUR)  # as after a daily run
    agent.config.schedules[0] = dataclasses.replace(agent.config.schedules[0], when={"kind": "daily", "times": ["06:00"]})
    clock.t -= 30 * 60
    assert agent.tick_schedules() == []
    assert agent.queue.schedule_next("q") == clock.t + 30 * 60 + 24 * HOUR  # left as it was


def test_a_retry_set_before_the_clock_went_back_isnt_held_for_the_jump(tmp_path):
    clock = Clock()
    queue = JobQueue(tmp_path / "q.sqlite3", clock=clock, backoff_seconds=60, max_backoff_seconds=1800)
    job = queue.enqueue("meta", {})
    claimed = queue.claim("etl-01", 300)
    queue.retry_later(job["id"], "etl-01", "network", claim=claimed["claim_id"])
    clock.t -= 5 * HOUR
    again = queue.claim("etl-01", 300)
    assert again is not None and again["id"] == job["id"]
    assert queue.claim("etl-01", 300) is None


def test_a_job_queued_in_the_future_by_delay_isnt_started_early(tmp_path):
    clock = Clock()
    queue = JobQueue(tmp_path / "q.sqlite3", clock=clock, max_backoff_seconds=1800)
    queue.enqueue("meta", {}, delay=5 * HOUR)  # never tried: its time stands
    assert queue.claim("etl-01", 300) is None


def test_a_lease_renewed_before_the_clock_went_back_runs_out_when_the_worker_is_gone(tmp_path):
    clock = Clock()
    queue = JobQueue(tmp_path / "q.sqlite3", clock=clock)
    job = queue.enqueue("meta", {})
    queue.claim("gone", 300)
    clock.t -= 5 * HOUR
    assert queue.recover() == []  # without a bound it would wait out the five hours
    assert queue.recover(max_lease=300) == [job["id"]]


def test_a_failing_route_doesnt_rest_for_as_long_as_the_clock_went_back(tmp_path):
    clock = Clock()
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    hook = "https://contoso.webhook.office.com/hook"
    outbox.route_failed("default", hook, "down")
    assert outbox.resting({"default": hook})
    clock.t -= 5 * HOUR
    assert outbox.resting({"default": hook}) == {}
