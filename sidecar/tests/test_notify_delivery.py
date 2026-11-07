"""Messages to Teams or a flow while the endpoint is down, throttled or slow: a failing route rests
between tries with a bounded backoff, the outbox can't grow without bound, a recovery doesn't
flood the channel, nothing leaves unsent without being counted, and the operator can see it."""

import pytest

from fabric_desktop import agent_status, dataops_service, retrying
from fabric_desktop.workloads import notify
from fabric_desktop.workloads.notify import (BACKOFF_FIRST, BACKOFF_MAX, MAX_PER_TRY, MAX_WAITING, Outbox, send,
                                             single)
from fabric_desktop.workloads.platform_notify import Ledger, PlatformRoute, PlatformRouting
from fabric_desktop.workloads.platform_notify import send as send_notices

from test_agent import Clock, setup  # noqa: F401 - setup is a fixture
from test_incidents import change
from test_platform_notify import event

HOOK = "https://contoso.webhook.office.com/hook"


class Teams:
    """A webhook that is down until ``up`` is set, counting every request it gets."""

    def __init__(self):
        self.up, self.calls, self.posted = False, 0, []

    def __call__(self, url, title, lines):
        self.calls += 1
        if not self.up:
            raise ConnectionError("Teams can't be reached")
        self.posted.append(title)


def test_a_failing_route_rests_between_tries_and_the_rest_doubles_up_to_a_cap(tmp_path):
    clock, teams = Clock(), Teams()
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    first = send([change()], single(HOOK), outbox, "agent", teams)
    assert first["queued"] == 1 and "ConnectionError" in first["error"] and teams.calls == 1
    # Resting: the next message waits without a request, and nothing new failed, so no error to log.
    res = send([change(name="b")], single(HOOK), outbox, "agent", teams)
    assert res["queued"] == 1 and res["error"] is None and teams.calls == 1 and res["waiting"] == 2
    clock.t += BACKOFF_FIRST
    assert send([], single(HOOK), outbox, "agent", teams)["error"] and teams.calls == 2
    clock.t += BACKOFF_FIRST  # the second failure rests twice as long
    send([], single(HOOK), outbox, "agent", teams)
    assert teams.calls == 2
    clock.t += BACKOFF_FIRST
    send([], single(HOOK), outbox, "agent", teams)
    assert teams.calls == 3
    for _ in range(20):  # a long outage: the rest stops growing at BACKOFF_MAX
        clock.t += BACKOFF_MAX
        send([], single(HOOK), outbox, "agent", teams)
    assert teams.calls == 23 and len(outbox.pending()) == 2


def test_a_route_that_recovers_sends_what_waited_and_starts_its_backoff_over(tmp_path):
    clock, teams = Clock(), Teams()
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    send([change()], single(HOOK), outbox, "agent", teams)
    clock.t += BACKOFF_FIRST
    send([], single(HOOK), outbox, "agent", teams)  # fails again: now rests twice as long
    clock.t += 2 * BACKOFF_FIRST
    teams.up = True
    res = send([], single(HOOK), outbox, "agent", teams)
    assert res["sent"] == 1 and res["waiting"] == 0 and teams.posted == ["Delayed: New: a failed in Prod (agent)"]
    teams.up = False
    send([change(name="c")], single(HOOK), outbox, "agent", teams)
    calls = teams.calls
    clock.t += BACKOFF_FIRST  # the first failure after a recovery rests the first wait again, not the doubled one
    send([], single(HOOK), outbox, "agent", teams)
    assert teams.calls == calls + 1


def test_a_new_address_is_tried_at_once_and_the_address_isnt_stored(tmp_path):
    clock, teams = Clock(), Teams()
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    send([change()], single(HOOK + "?sig=OLDSECRET"), outbox, "agent", teams)
    teams.up = True
    res = send([], single(HOOK + "?sig=NEWSECRET"), outbox, "agent", teams)  # the admin fixed the webhook
    assert res["sent"] == 1 and teams.calls == 2
    assert b"OLDSECRET" not in (tmp_path / "o.sqlite3").read_bytes()


def test_the_outbox_keeps_at_most_max_waiting_and_counts_what_it_drops(tmp_path):
    clock = Clock()
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    for i in range(MAX_WAITING + 10):
        outbox.add(f"m{i}", ["x"], "down")
    teams = Teams()
    outbox.route_failed("default", HOOK, "down")  # still resting: nothing is sent, only trimmed
    res = send([], single(HOOK), outbox, "agent", teams)
    assert res["dropped"] == 10 and res["waiting"] == MAX_WAITING and teams.calls == 0
    assert outbox.pending()[0]["title"] == "m10"  # the oldest went


def test_a_recovery_sends_at_most_max_per_try_to_a_route_at_once(tmp_path):
    clock, teams = Clock(), Teams()
    teams.up = True
    outbox = Outbox(tmp_path / "o.sqlite3", clock)
    for i in range(MAX_PER_TRY + 5):
        outbox.add(f"m{i}", ["x"], "down")
    assert send([], single(HOOK), outbox, "agent", teams)["sent"] == MAX_PER_TRY
    res = send([], single(HOOK), outbox, "agent", teams)
    assert res["sent"] == 5 and res["waiting"] == 0
    assert teams.posted == [f"Delayed: m{i}" for i in range(MAX_PER_TRY + 5)]  # in order


def test_platform_notices_rest_the_same_way(tmp_path):
    clock, teams = Clock(), Teams()
    routing = PlatformRouting([PlatformRoute("default", HOOK)])
    outbox, ledger = Outbox(tmp_path / "p.sqlite", clock), Ledger(tmp_path / "p.sqlite", clock)
    first = send_notices([event()], routing, outbox, ledger, teams)
    assert first["events"][0]["result"] == "queued" and teams.calls == 1
    second = send_notices([event(id="r2")], routing, outbox, ledger, teams)
    assert second["events"][0]["result"] == "queued" and second["error"] is None and teams.calls == 1
    clock.t += BACKOFF_FIRST
    teams.up = True
    assert send_notices([], routing, outbox, ledger, teams)["sent"] == 2 and outbox.pending() == []


# ---- one Teams post never holds the caller long

class Throttled:
    def __init__(self, status=429, retry_after="3600"):
        self.status, self.retry_after, self.posts = status, retry_after, []

    def post(self, url, json, timeout):
        self.posts.append(timeout)
        return type("R", (), {"status_code": self.status, "headers": {"Retry-After": self.retry_after},
                              "json": lambda self: {}})()


def test_a_throttled_teams_post_waits_at_most_a_few_capped_times(monkeypatch):
    http, waits = Throttled(), []
    monkeypatch.setattr(dataops_service.http_session, "session", lambda: http)
    with pytest.raises(RuntimeError, match="Teams answered 429"):
        dataops_service.post_teams(HOOK, "t", ["l"], sleep=waits.append)
    assert len(http.posts) == dataops_service.TEAMS_ATTEMPTS
    assert waits == [dataops_service.TEAMS_WAIT_CAP] * (dataops_service.TEAMS_ATTEMPTS - 1)  # not an hour
    assert http.posts[0] == dataops_service.TEAMS_TIMEOUT  # connect and read are both bounded


def test_a_teams_5xx_is_not_sent_again_at_once(monkeypatch):
    http, waits = Throttled(status=503), []
    monkeypatch.setattr(dataops_service.http_session, "session", lambda: http)
    with pytest.raises(RuntimeError, match="Teams answered 503"):
        dataops_service.post_teams(HOOK, "t", ["l"], sleep=waits.append)
    assert len(http.posts) == 1 and waits == []  # the card may have been posted: the outbox decides


def test_send_with_retry_caps_each_wait():
    waits = []
    resp = type("R", (), {"status_code": 429, "headers": {"Retry-After": "120"}})()
    retrying.send_with_retry(lambda: resp, "POST", sleep=waits.append, attempts=3, wait_cap=7.0)
    assert waits == [7.0, 7.0]


# ---- the agent

def test_an_agent_alert_waits_without_a_request_while_teams_rests_and_the_record_shows_it(setup, monkeypatch):  # noqa: F811
    import dataclasses

    from fabric_desktop.agent import Agent
    from fabric_desktop.jobqueue import JobQueue
    clock, teams = Clock(), Teams()
    monkeypatch.setenv("FD_TEST_HOOK", HOOK)
    cfg = dataclasses.replace(setup, teams_webhook_env="FD_TEST_HOOK")
    agent = Agent(cfg, queue=JobQueue(cfg.home / "queue.sqlite3", clock=clock), clock=clock)
    monkeypatch.setattr(agent, "_post", teams)
    assert agent_status.record(agent, "dev")["messages_waiting"] == 0
    agent._notify("first", ["x"])
    agent._notify("second", ["x"])  # resting: no request, it waits
    assert teams.calls == 1 and len(agent._alert_outbox().pending()) == 2
    assert agent_status.record(agent, "dev")["messages_waiting"] == 2
    clock.t += BACKOFF_FIRST
    teams.up = True
    agent.send_waiting_alerts()
    assert teams.posted == ["Delayed: first", "Delayed: second"]
    assert agent_status.record(agent, "dev")["messages_waiting"] == 0


def test_the_backoff_settings_stay_bounded():
    assert 0 < notify.BACKOFF_FIRST <= notify.BACKOFF_MAX <= 3600
    assert MAX_PER_TRY <= MAX_WAITING
