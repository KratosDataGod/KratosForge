"""Notices when delivery goes wrong: a webhook that times out, throttles or was revoked, and a process
stopped mid-send. No notice is lost or sent twice, every failure has an audit line, and the one-year
cleanup removes only closed dedupe keys, never a message still waiting or a notice still open."""

import json
import sqlite3
from contextlib import closing

import pytest
import requests

from fabric_desktop import dataops_service
from fabric_desktop import platform_notify_service as pns
from fabric_desktop.workloads import notify
from fabric_desktop.workloads.notify import BACKOFF_FIRST, SENDING_GRACE, Outbox, single
from fabric_desktop.workloads.platform_notify import Ledger, PlatformRoute, PlatformRouting, send

from test_agent import setup  # noqa: F401 - setup is a fixture
from test_platform_notify import NOON, event, raw, svc  # noqa: F401 - svc is a fixture

HOOK = "https://contoso.webhook.office.com/workflows/abc/invoke?api-version=1&sig=SECRET123"
NEW_HOOK = "https://contoso.webhook.office.com/workflows/def/invoke?api-version=1&sig=SECRET456"


class Teams:
    """A webhook that fails as told (an exception to raise, or a status code), recording what it took."""

    def __init__(self, fail=None):
        self.fail, self.calls, self.posted = fail, 0, []

    def __call__(self, url, title, lines):
        self.calls += 1
        if isinstance(self.fail, BaseException) or (isinstance(self.fail, type) and issubclass(self.fail, BaseException)):
            raise self.fail
        if isinstance(self.fail, int):
            raise RuntimeError(f"Teams answered {self.fail}")
        self.posted.append((url, title, lines))


def _box(tmp_path, t):
    path = tmp_path / "platform_outbox.sqlite"
    return Outbox(path, clock=lambda: t[0]), Ledger(path, clock=lambda: t[0])


def _routing(url=HOOK):
    return PlatformRouting([PlatformRoute("default", url)])


@pytest.mark.parametrize("fail", [requests.exceptions.ConnectTimeout("connect timed out"),
                                  requests.exceptions.ReadTimeout("read timed out"), 429, 403, 404, 410, 503])
def test_a_post_that_fails_any_way_keeps_the_notice_and_sends_it_once_later(tmp_path, fail):
    t = [1_000_000.0]
    outbox, ledger = _box(tmp_path, t)
    teams = Teams(fail)
    out = send([event()], _routing(), outbox, ledger, teams, now=NOON)
    assert (out["sent"], out["queued"], out["events"][0]["result"]) == (0, 1, "queued")
    assert out["error"] and "SECRET" not in out["error"] and "SECRET" not in json.dumps(outbox.pending())
    again = send([event()], _routing(), outbox, ledger, teams, now=NOON)  # the same notice reported again
    assert again["events"][0]["reason"] == "already posted and still open" and teams.calls == 1  # resting: no request
    teams.fail = None
    t[0] += BACKOFF_FIRST + 1
    send([], _routing(), outbox, ledger, teams, now=NOON)
    assert [title for _, title, _ in teams.posted] == ["Delayed: Waiting on you: Access to LH_Sales"]
    assert outbox.count() == 0
    send([event()], _routing(), outbox, ledger, teams, now=NOON)
    assert len(teams.posted) == 1  # once, however often it's reported


def test_a_teams_429_waits_a_capped_few_times_then_the_notice_waits_in_the_outbox(tmp_path, monkeypatch):
    class Throttled:
        posts = 0

        def post(self, url, json, timeout):
            Throttled.posts += 1
            return type("R", (), {"status_code": 429, "headers": {"Retry-After": "3600"}, "json": lambda self: {}})()

    monkeypatch.setattr(dataops_service.http_session, "session", lambda: Throttled())
    t, waits = [1_000_000.0], []
    outbox, ledger = _box(tmp_path, t)
    out = send([event()], _routing(), outbox, ledger,
               lambda u, ti, ls: dataops_service.post_teams(u, ti, ls, sleep=waits.append), now=NOON)
    assert out["queued"] == 1 and "429" in out["error"]
    assert Throttled.posts == dataops_service.TEAMS_ATTEMPTS and sum(waits) <= 2 * dataops_service.TEAMS_WAIT_CAP
    assert sum(waits) + 3 * sum(dataops_service.TEAMS_TIMEOUT) < SENDING_GRACE  # a slow post is never taken for a stopped one


def test_a_revoked_webhook_keeps_every_notice_until_a_new_address_takes_them_once(tmp_path):
    t = [1_000_000.0]
    outbox, ledger = _box(tmp_path, t)
    teams = Teams(410)
    send([event(id="r1"), event(id="r2")], _routing(), outbox, ledger, teams, now=NOON)
    assert teams.calls == 1 and outbox.count() == 2  # the second waits without a request once the first failed
    t[0] += 3600
    send([], _routing(), outbox, ledger, teams, now=NOON)  # still revoked
    assert outbox.count() == 2
    teams.fail = None
    send([], _routing(NEW_HOOK), outbox, ledger, teams, now=NOON)  # the admin saved a new address: tried at once
    assert sorted(title for _, title, _ in teams.posted) == ["Delayed: Waiting on you: Access to LH_Sales"] * 2
    assert {url for url, _, _ in teams.posted} == {NEW_HOOK} and outbox.count() == 0


def test_a_stop_mid_send_loses_nothing_and_the_replay_says_it_may_be_a_repeat(tmp_path):
    t = [1_000_000.0]
    outbox, ledger = _box(tmp_path, t)
    with pytest.raises(KeyboardInterrupt):  # the process is stopped while the post is on its way
        send([event(id="r1")], _routing(), outbox, ledger, Teams(KeyboardInterrupt), now=NOON)
    teams = Teams()
    assert send([event(id="r1")], _routing(), outbox, ledger, teams, now=NOON)["events"][0]["result"] == "skipped"
    assert teams.posted == []  # within the grace another process could still be posting it
    t[0] += SENDING_GRACE + 1
    send([], _routing(), outbox, ledger, teams, now=NOON)
    [(_, title, lines)] = teams.posted
    assert title == "Delayed: Waiting on you: Access to LH_Sales" and "may have arrived already" in lines[0]
    send([], _routing(), outbox, ledger, teams, now=NOON)
    assert len(teams.posted) == 1 and outbox.count() == 0


def test_a_stop_after_the_first_of_two_notices_sends_each_once(tmp_path):
    t = [1_000_000.0]
    outbox, ledger = _box(tmp_path, t)
    first = Teams()

    def stops_on_the_second(url, title, lines):
        if first.calls:
            raise SystemExit(1)
        first(url, title, lines)

    with pytest.raises(SystemExit):
        send([event(id="r1"), event(id="r2", title="Access to LH_Finance")], _routing(), outbox, ledger,
             stops_on_the_second, now=NOON)
    t[0] += SENDING_GRACE + 1
    later = Teams()
    send([event(id="r1"), event(id="r2", title="Access to LH_Finance")], _routing(), outbox, ledger, later, now=NOON)
    assert [ti for _, ti, _ in first.posted] == ["Waiting on you: Access to LH_Sales"]
    assert [ti for _, ti, _ in later.posted] == ["Delayed: Waiting on you: Access to LH_Finance"]


def test_a_notice_and_its_key_are_kept_together_or_not_at_all(tmp_path, monkeypatch):
    t = [1_000_000.0]
    outbox, ledger = _box(tmp_path, t)
    real = Outbox.sending

    def breaks(self, *a, **kw):
        real(self, *a, **kw)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Outbox, "sending", breaks)
    with pytest.raises(sqlite3.OperationalError):
        send([event()], _routing(), outbox, ledger, Teams(), now=NOON)
    assert outbox.count() == 0 and ledger.get("request.waiting:r1") is None  # rolled back together
    monkeypatch.setattr(Outbox, "sending", real)
    teams = Teams()
    assert send([event()], _routing(), outbox, ledger, teams, now=NOON)["sent"] == 1 and len(teams.posted) == 1


def test_incident_alerts_are_kept_before_they_are_posted_too(tmp_path):
    from test_incidents import change
    t = [1_000_000.0]
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0])
    with pytest.raises(KeyboardInterrupt):
        notify.send([change()], single(HOOK), outbox, "KratosForge", Teams(KeyboardInterrupt))
    assert outbox.count() == 1
    teams = Teams()
    t[0] += SENDING_GRACE + 1
    notify.send([], single(HOOK), outbox, "KratosForge", teams)
    [(_, title, lines)] = teams.posted
    assert title.startswith("Delayed: ") and "may have arrived already" in lines[0] and outbox.count() == 0


# ---- the audit


def _audited(svc):
    rows = svc.audit_log(limit=50)["entries"]
    return [r for r in rows if r["action"] == notify.NOT_DELIVERED]


def test_a_notice_teams_didnt_take_has_an_audit_line_without_its_address(svc, monkeypatch):  # noqa: F811
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    monkeypatch.setattr(pns, "post_teams", Teams(requests.exceptions.ReadTimeout(f"read timed out for {HOOK}")))
    svc.platform_notify([raw()])
    [line] = _audited(svc)
    assert (line["outcome"], line["reason"]) == ("error", "failed")
    assert line["target"] | {} == {"outbox": "platform_outbox.sqlite", "queued": 1, "dropped": 0, "waiting": 1}
    assert "ReadTimeout" in line["error"] and "SECRET" not in json.dumps(line) and "webhook.office.com" not in json.dumps(line)
    svc.platform_notify([raw(id="r2", title="Another")])  # resting: nothing was sent, so nothing new to record
    assert len(_audited(svc)) == 1


def test_a_notice_dropped_unsent_has_an_audit_line(svc, monkeypatch):  # noqa: F811
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    box = Outbox(svc.root / "platform_outbox.sqlite", clock=lambda: 0.0)  # added long ago
    box.add("Waiting on you: old", ["x"], "ConnectionError: no network")
    svc.platform_notify([raw()])
    [line] = _audited(svc)
    assert line["outcome"] == "error" and line["target"]["dropped"] == 1


def test_an_audit_line_that_cant_be_written_never_stops_the_notice(tmp_path, caplog):
    class Broken:
        def record(self, *a, **kw):
            raise OSError("disk full")

    notify.audit_trouble(Broken(), {"error": "ConnectionError: x", "queued": 1}, "alerts.sqlite3")
    assert "couldn't be recorded in the audit log" in caplog.text


# ---- the one-year cleanup


def test_the_yearly_cleanup_leaves_waiting_messages_and_open_notices(svc, monkeypatch):  # noqa: F811
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    monkeypatch.setattr(pns, "post_teams", Teams(503))
    decided = {k: v for k, v in raw(kind="request.decided", id="r9").items() if k != "dedupe"}
    svc.platform_notify([raw(), decided])
    path = svc.root / "platform_outbox.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE sent SET at = at - ?", (pns.SENT_KEEP_SECONDS + 60,))  # a year on
    svc._pn_tidy()
    ledger = Ledger(path)
    assert ledger.get("request.waiting:r1") is not None  # still waiting on someone: kept
    assert ledger.get("request.decided:r9") is None  # a one-off a year old: gone
    assert Outbox(path).count() == 2  # messages Teams hasn't taken are never cleaned up here
    assert [r["key"] for r in svc.platform_notify_open()["open"]] == ["request.waiting:r1"]


def test_the_agents_failed_and_dropped_alerts_have_audit_lines(setup, monkeypatch):  # noqa: F811
    import dataclasses

    from fabric_desktop.agent import Agent
    from fabric_desktop.jobqueue import JobQueue

    from test_agent import Clock
    clock = Clock()
    monkeypatch.setenv("FD_TEST_HOOK", HOOK)
    cfg = dataclasses.replace(setup, teams_webhook_env="FD_TEST_HOOK")
    agent = Agent(cfg, queue=JobQueue(cfg.home / "queue.sqlite3", clock=clock, backoff_seconds=0), clock=clock)
    monkeypatch.setattr(agent, "_post", Teams(requests.exceptions.ConnectTimeout(f"timed out for {HOOK}")))
    agent._notify("Job failed", ["line"])
    assert [p["title"] for p in agent._alert_outbox().pending()] == ["Job failed"]  # kept for the next try
    clock.t += 3 * 86400 + 1
    agent.send_waiting_alerts()
    lines = [r for r in agent.audit.recent(20, notify.NOT_DELIVERED)]
    assert sorted((r["target"]["queued"], r["target"]["dropped"]) for r in lines) == [(0, 1), (1, 0)]
    assert "SECRET" not in json.dumps(lines) and "webhook.office.com" not in json.dumps(lines)


def test_an_email_address_in_a_mail_error_is_kept_out_of_the_audit():
    lines = []

    class Audit:
        def record(self, *a, **kw):
            lines.append(kw["error"])

    notify.audit_trouble(Audit(), {"error": "SMTPRecipientsRefused: {'ops.team@contoso.com': (550, b'no such user')}",
                                   "queued": 1}, "platform_outbox.sqlite")
    assert lines == ["SMTPRecipientsRefused: {'<address>': (550, b'no such user')}"]
