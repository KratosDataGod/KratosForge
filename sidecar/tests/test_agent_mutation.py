"""agent.py paths that mutation testing (batch 17, docs/quality/mutation-testing.md) showed no test
checked: how long a schedule may wait, a repeated hour when the clocks go back, the clock the agent
ages incidents on, its threads, its housekeeping logs, and how schedules are reconciled and ticked.
No test reaches Fabric, OneLake, Teams or the network: the runtime and the senders are fakes."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time

import pytest

from fabric_desktop import agent as agent_mod
from fabric_desktop import agent_config
from fabric_desktop.agent import Agent, _DAY, _from_wall, _longest_wait
from fabric_desktop.meta.templates import init_project

from test_agent import Clock, config_text


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def cfg(tmp_path):
    project = tmp_path / "proj"
    init_project(project, "3", "Chris", True)
    path = tmp_path / "agent.toml"
    path.write_text(config_text(project, tmp_path / "agent-home"), encoding="utf-8")
    return agent_config.load(path)


def make(cfg, clock=None, **changes):
    return Agent(dataclasses.replace(cfg, **changes) if changes else cfg, clock=clock or Clock())


# ---- how long a schedule may wait

def test_a_cron_schedule_waits_at_most_its_interval_and_an_hour():
    assert _longest_wait({"kind": "cron", "every_minutes": 15}) == 15 * 60 + 3600


def test_a_cron_schedule_without_an_interval_waits_at_most_a_day_and_an_hour():
    assert _longest_wait({"kind": "cron"}) == _DAY + 3600


def test_a_weekly_schedule_waits_at_most_a_week_and_an_hour():
    assert _longest_wait({"kind": "weekly"}) == 7 * _DAY + 3600
    assert _longest_wait({"kind": "daily"}) == _DAY + 3600


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset to set this process's time zone")
def test_the_second_time_a_repeated_hour_comes_round_is_an_hour_later(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        # 2026-11-01: New York's clocks go back at 02:00, so 01:30 comes round twice.
        assert _from_wall("2026-11-01T01:30+fold") - _from_wall("2026-11-01T01:30") == 3600
    finally:
        monkeypatch.undo()
        time.tzset()


# ---- the clock

def test_incident_ages_run_on_the_monotonic_clock_unless_a_clock_is_handed_in(cfg):
    assert Agent(cfg).monotonic is time.monotonic
    clock = Clock()
    assert make(cfg, clock).monotonic is clock


# ---- status

def test_status_lists_the_latest_twenty_jobs(cfg):
    a = make(cfg)
    for i in range(21):
        a.enqueue("dev", [f"silver.t{i}"])
    assert len(a.status()["jobs"]) == 20


# ---- threads

def test_the_shortcut_check_runs_on_a_daemon_thread(cfg):
    a = make(cfg)
    release = threading.Event()
    a.check_shortcuts = lambda: release.wait(10)
    try:
        t = a.start_shortcut_check()
        assert t is not None and t.daemon and t.name == "shortcut-health"
    finally:
        release.set()
        t.join(10)


def test_a_job_renews_its_lease_on_a_daemon_thread(cfg):
    a = make(cfg)
    seen = []

    def run_environment(*args, **kwargs):
        seen.extend(t for t in threading.enumerate() if "_heartbeat" in t.name)
        return []

    a.runtime.run_environment = run_environment
    a.enqueue("dev")
    a.run_job(a.queue.claim(cfg.name, cfg.job_lease_seconds))
    assert seen and all(t.daemon for t in seen)


# ---- housekeeping logs say something only when something went

class _Gone:
    def __init__(self, gone):
        self.gone = gone

    def prune(self, days):
        return self.gone


def test_pruning_logs_only_when_jobs_were_removed(cfg, caplog):
    a = make(cfg)
    a.queue = _Gone([])
    with caplog.at_level(logging.INFO, logger="fabric_desktop.agent"):
        a.prune_queue()
    assert "finished jobs removed from the queue" not in caplog.text
    a.queue = _Gone(["j1", "j2"])
    with caplog.at_level(logging.INFO, logger="fabric_desktop.agent"):
        a.prune_queue()
    assert "finished jobs removed from the queue" in caplog.text


@pytest.mark.parametrize("removed, logged", [([], False), (["cache/x"], True)])
def test_the_home_sweep_logs_only_when_copies_were_removed(cfg, caplog, monkeypatch, removed, logged):
    from fabric_desktop import retention
    monkeypatch.setattr(retention, "sweep", lambda root, days: {"removed": removed, "bytes": 10 * len(removed)})
    with caplog.at_level(logging.INFO, logger="fabric_desktop.agent"):
        make(cfg).sweep_home()
    assert ("local copies removed from the agent's home" in caplog.text) is logged


@pytest.mark.parametrize("files, logged", [([], False), (["2020-01.jsonl"], True)])
def test_the_audit_cleanup_logs_only_when_months_were_removed(cfg, caplog, monkeypatch, files, logged):
    from fabric_desktop import retention
    monkeypatch.setattr(retention, "prune_audit", lambda folder, days, record, app: {"files": files, "lines": 3})
    with caplog.at_level(logging.INFO, logger="fabric_desktop.agent"):
        make(cfg).prune_audit_trail()
    assert ("old months removed from the agent's audit trail" in caplog.text) is logged


# ---- where messages can go

HOOK = "https://example.invalid/hook"


def test_an_empty_address_is_never_sent_to(cfg):
    assert make(cfg)._can_send("default", "") is False


def test_a_bad_address_is_refused_and_warned_about_once(cfg, caplog):
    a = make(cfg)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        assert a._can_send("sales", "http://example.invalid/hook") is False
        assert a._can_send("sales", "http://example.invalid/hook") is False
    assert caplog.text.count("an incident route's address can't be used") == 1


def test_an_email_route_is_refused_on_an_agent_and_warned_about_once(cfg, caplog):
    a = make(cfg)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        assert a._can_send("ops", "mailto:ops@example.com") is False
        assert a._can_send("ops", "mailto:ops@example.com") is False
    assert caplog.text.count("Email routes are sent from the KratosForge app") == 1


def test_a_teams_address_can_be_sent_to(cfg):
    assert make(cfg)._can_send("default", HOOK) is True


def test_incident_routing_uses_each_route_whose_variable_is_set(cfg, monkeypatch, caplog):
    monkeypatch.setenv("SALES_HOOK", HOOK)
    monkeypatch.delenv("OPS_HOOK", raising=False)
    route = {"teams": [], "owners": [], "criticality": [], "kinds": [], "envs": []}
    inc = dataclasses.replace(cfg.incidents, escalate_after_minutes=45, routes=(
        {"name": "sales", "webhook_env": "SALES_HOOK", **route}, {"name": "ops", "webhook_env": "OPS_HOOK", **route}))
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        routing = make(cfg, incidents=inc).incident_routing()
    assert [r.name for r in routing.routes] == ["sales"]
    assert routing.escalate_after == 45 * 60
    warned = [r for r in caplog.records if "webhook variable isn't set" in r.getMessage()]
    assert len(warned) == 1


# ---- schedules

SCHEDULE = """
[[schedule]]
id = "dev-hourly"
env = "dev"
entities = ["silver.orders"]
every_minutes = 60
"""


@pytest.fixture
def scheduled(tmp_path, cfg):
    path = tmp_path / "scheduled.toml"
    path.write_text(config_text(cfg.project, tmp_path / "agent-home", SCHEDULE), encoding="utf-8")
    return agent_config.load(path)


def test_a_schedule_keeps_the_local_time_it_runs_next_at(scheduled):
    a = make(scheduled)
    a.tick_schedules()
    wall = a.queue.schedule_wall("dev-hourly")
    assert wall is not None and _from_wall(wall) == a.queue.schedule_next("dev-hourly")


def test_a_kept_time_a_minute_off_its_local_time_moves_with_the_time_zone(scheduled, caplog):
    clock = Clock()
    a = make(scheduled, clock)
    wall = "2027-01-15T06:00"
    a.queue.set_schedule_next("dev-hourly", _from_wall(wall) - 60, wall)
    clock.t = _from_wall(wall) - 3000  # before it: nothing runs
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        assert a.tick_schedules() == []
    assert "the PC's time zone changed" in caplog.text
    assert a.queue.schedule_next("dev-hourly") == _from_wall(wall)


def test_a_waiting_first_run_of_an_edited_schedule_takes_its_new_tables_without_cascade(scheduled):
    a = make(scheduled)
    job = a.queue.enqueue("meta", {"env": "dev", "entities": ["gold.old"], "backfill": "full", "cascade": True},
                          source="schedule:dev-hourly", dedupe_key="schedule:dev-hourly")
    out = a.reconcile_schedules()
    assert out["updated"] == [job["id"]]
    assert a.queue.get(job["id"])["payload"] == {"env": "dev", "entities": ["silver.orders"], "backfill": "",
                                                 "cascade": False}



def test_reconciling_logs_only_the_kinds_of_change_it_made(scheduled, caplog):
    a = make(scheduled)
    a.queue.enqueue("meta", {"env": "dev", "entities": ["gold.old"], "backfill": "full", "cascade": True},
                    source="schedule:dev-hourly", dedupe_key="schedule:dev-hourly")
    with caplog.at_level(logging.INFO, logger="fabric_desktop.agent"):
        a.reconcile_schedules()
    said = [r.getMessage() for r in caplog.records if "schedules changed" in r.getMessage()]
    assert said == ["schedules changed in agent.toml: updated"]

def test_a_deferred_schedule_says_so_once_it_is_queued(scheduled, caplog):
    a = make(scheduled)
    s = scheduled.schedules[0]
    with caplog.at_level(logging.INFO, logger="fabric_desktop.agent"):
        assert a._enqueue_due(s, 100.0) is True
    assert "deferred schedule queued" not in caplog.text
    a._deferred[s.id] = 50.0
    a.queue.cancel(a.queue.queued_with_key("schedule:dev-hourly")[0]["id"], "test")
    with caplog.at_level(logging.INFO, logger="fabric_desktop.agent"):
        assert a._enqueue_due(s, 100.0) is True
    assert "deferred schedule queued" in caplog.text and s.id not in a._deferred


# ---- running a job

def result(entity, status="ok", **more):
    return {"entity": entity, "status": status, "message": more.pop("message", ""), **more}


def claimed(a, payload=None, source="cli"):
    a.queue.enqueue("meta", payload or {"env": "dev", "entities": [], "backfill": "", "cascade": False},
                    source=source, max_attempts=a.config.max_attempts)
    return a.queue.claim(a.config.name, a.config.job_lease_seconds)


def test_a_job_passes_its_backfill_on(cfg):
    a = make(cfg)
    got = {}

    def run_environment(env, entities, backfill, cascade, job=None, guard=None):
        got.update(env=env, entities=entities, backfill=backfill, cascade=cascade)
        return []

    a.runtime.run_environment = run_environment
    a.run_job(claimed(a, {"env": "dev", "entities": ["silver.orders"], "backfill": "full", "cascade": True}))
    assert got == {"env": "dev", "entities": ["silver.orders"], "backfill": "full", "cascade": True}


class TakenMailbox:
    """A pipeline mailbox that says every job was taken over, and records what it was told."""

    def __init__(self, cancel=False):
        self.changed, self.cancel = [], cancel

    def taken_over(self, job):
        return "etl-02"

    def cancel_requested(self, job):
        return self.cancel

    def job_changed(self, job):
        self.changed.append(job)


def test_only_a_pipeline_job_is_checked_for_a_take_over(cfg):
    a = make(cfg)
    a.mailbox = TakenMailbox()
    ran = []

    def run_environment(env, entities, backfill, cascade, job=None, guard=None):
        guard()
        ran.append(entity := "silver.orders")
        return [result(entity)]

    a.runtime.run_environment = run_environment
    job = claimed(a, source="cli")
    a.run_job(job)
    assert ran == ["silver.orders"] and a.queue.get(job["id"])["state"] == "succeeded"


def test_a_job_whose_pipeline_cancelled_it_tells_the_mailbox_it_was_cancelled(cfg):
    a = make(cfg)
    a.mailbox = TakenMailbox(cancel=True)
    job = claimed(a, source="pipeline:r1")
    a._guarded(job)
    assert [j["state"] for j in a.mailbox.changed] == ["cancelled"]


def test_a_finished_pipeline_job_tells_the_mailbox_how_it_ended(cfg):
    a = make(cfg)
    box = TakenMailbox()
    box.taken_over = lambda job: ""
    a.mailbox = box
    a.runtime.run_environment = lambda *args, **kwargs: [result("silver.orders")]
    job = claimed(a, source="pipeline:r1")
    a._guarded(job)
    assert box.changed[-1]["state"] == "succeeded"


def test_a_job_counts_what_finished(cfg):
    assert Agent._summary([result("a"), result("b", "skipped"), result("c", "failed", rows_written=4, run_id="r3")]) \
        == {"tables": 3, "ok": 1, "failed": 1, "rows_written": 4, "run_ids": ["r3"]}


@pytest.fixture
def audited(cfg, monkeypatch):
    a = make(cfg)
    lines = []
    monkeypatch.setattr(a.audit, "record", lambda action, outcome, seconds, params, **kw: lines.append(
        (outcome, seconds, kw)))
    return a, lines


def test_a_failed_job_audits_how_long_it_ran(audited):
    a, lines = audited

    def run_environment(*args, **kwargs):
        raise ValueError("bad table")

    a.runtime.run_environment = run_environment
    a.run_job(claimed(a))
    assert [(o, kw["error"]) for o, _, kw in lines] == [("error", "ValueError: bad table")]
    assert 0 <= lines[0][1] < 60


def test_a_job_audits_its_time_to_a_tenth_of_a_second(audited, monkeypatch):
    a, lines = audited
    ticks = iter([1000.0, 1001.26])
    monkeypatch.setattr(agent_mod, "time", type("T", (), {"monotonic": staticmethod(lambda: next(ticks)),
                                                          "sleep": staticmethod(time.sleep)}))
    a.runtime.run_environment = lambda *args, **kwargs: [result("silver.orders")]
    a.run_job(claimed(a))
    assert lines[0][0] == "ok" and lines[0][1] == 1.3


def test_a_retryable_error_with_tries_left_retries_without_an_alert(audited, monkeypatch, caplog):
    a, _ = audited
    alerts = []
    monkeypatch.setattr(a, "_alert", lambda job, error, crashed=False: alerts.append(error))

    def run_environment(*args, **kwargs):
        raise ConnectionError("network down")

    a.runtime.run_environment = run_environment
    job = claimed(a)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        a.run_job(job)
    assert alerts == [] and "job will retry" in caplog.text and "job gave up" not in caplog.text
    assert a.queue.get(job["id"])["state"] == "queued"


def test_a_retryable_error_on_the_last_try_gives_up_and_alerts(audited, monkeypatch, caplog):
    a, _ = audited
    alerts = []
    monkeypatch.setattr(a, "_alert", lambda job, error, crashed=False: alerts.append(error))

    def run_environment(*args, **kwargs):
        raise ConnectionError("network down")

    a.runtime.run_environment = run_environment
    a.queue.enqueue("meta", {"env": "dev", "entities": [], "backfill": "", "cascade": False}, max_attempts=1)
    job = a.queue.claim(a.config.name, a.config.job_lease_seconds)
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        a.run_job(job)
    assert len(alerts) == 1 and "job gave up" in caplog.text and "job will retry" not in caplog.text


def test_busy_tables_retry_without_an_alert_while_tries_are_left(audited, monkeypatch):
    a, _ = audited
    alerts = []
    monkeypatch.setattr(a, "_alert", lambda job, error, crashed=False: alerts.append(error))
    a.runtime.run_environment = lambda *args, **kwargs: [result("silver.a", "failed", retryable=True, message="busy")]
    job = claimed(a)
    a.run_job(job)
    assert alerts == [] and a.queue.get(job["id"])["state"] == "queued"


def test_a_cascading_job_retries_with_all_its_tables(audited):
    a, _ = audited
    a.runtime.run_environment = lambda *args, **kwargs: [result("silver.a"), result("silver.b", "failed", retryable=True)]
    job = claimed(a, {"env": "dev", "entities": ["silver.a", "silver.b"], "backfill": "", "cascade": True})
    a.run_job(job)
    assert a.queue.get(job["id"])["payload"]["entities"] == ["silver.a", "silver.b"]


# ---- the loop

def test_a_running_jobs_claim_is_kept_for_renewing_its_lease(cfg):
    a = make(cfg)
    release = threading.Event()

    def run_environment(*args, **kwargs):
        release.wait(30)
        return []

    a.runtime.run_environment = run_environment
    for name in ("tick_schedules", "publish_status", "send_waiting_alerts", "prune_queue", "sweep_home",
                 "prune_audit_trail", "check_policy"):
        setattr(a, name, lambda *args, **kwargs: None)
    a.enqueue("dev")
    try:
        a.step()
        (job_id,) = a._running
        claim = a._claims[job_id]
        a.step()
        assert a._claims == {job_id: claim} and claim
    finally:
        release.set()
        for f in list(a._running.values()):
            f.result(timeout=60)


# ---- alerts waiting from before

def test_waiting_alerts_make_no_outbox_when_none_ever_waited(cfg, monkeypatch):
    monkeypatch.setenv("FD_HOOK", HOOK)
    a = make(cfg, teams_webhook_env="FD_HOOK")
    sent = []
    monkeypatch.setattr(agent_mod, "send", lambda *args, **kwargs: sent.append(args) or {})
    a.send_waiting_alerts()
    assert sent == [] and not (cfg.home / "job_alerts.sqlite3").exists()


def test_waiting_alerts_are_sent_when_an_outbox_was_left_from_before(cfg, monkeypatch):
    monkeypatch.setenv("FD_HOOK", HOOK)
    a = make(cfg, teams_webhook_env="FD_HOOK")
    a._alert_outbox()  # made by an earlier start
    a._alert_box = None
    sent = []
    monkeypatch.setattr(agent_mod, "send", lambda *args, **kwargs: sent.append(args) or {})
    a.send_waiting_alerts()
    assert len(sent) == 1


# ---- the shortcut check

def test_an_environment_with_no_workspace_is_skipped_by_the_shortcut_check(cfg, monkeypatch):
    from fabric_desktop import shortcut_watch
    monkeypatch.setenv("FD_SHORTCUTS", HOOK)
    sh = dataclasses.replace(cfg.shortcut_health, enabled=True, environments=["dev"], webhook_env="FD_SHORTCUTS")
    a = make(cfg, shortcut_health=sh)
    monkeypatch.setattr(a.runtime, "environment_config", lambda env: {})
    monkeypatch.setattr(shortcut_watch, "env_workspaces", lambda config: [])
    monkeypatch.setattr(agent_mod, "send_notices", lambda *args, **kwargs: {})
    assert a.check_shortcuts() == {"dev": {"skipped": "no workspace"}}
