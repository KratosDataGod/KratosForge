"""A locked alerts outbox never makes a send raise, and never sends a notice twice. notify.send says
"Never raises": an outbox file another process holds (SQLite gives up after its wait) used to raise out of
the trim, the flush or the keeping of a new message, losing the notice of an incident just written. Now
each new message is posted once, straight away, without being kept; one that can't be (quiet hours, a
route that fails) is counted in ``lost`` and audited. A message already posted, or sent with no answer,
whose row can't be deleted is never sent again (the notices at-most-once rule). Real SQLite locks with a
short wait, and fake transports only: nothing here reaches Teams."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import sqlite3

import pytest
import requests

from fabric_desktop import sqlitefile
from fabric_desktop.workloads import notify
from fabric_desktop.workloads.notify import Outbox, Quiet, Routing, Route, single

from test_agent import Clock
from test_incidents import agent_setup, run  # noqa: F401 - agent_setup is a fixture
from test_notice_failures import HOOK, Teams


def _change(criticality: str = ""):
    from test_incidents import change
    c = change()
    if criticality:
        c["incident"]["criticality"] = criticality
    return c


@pytest.fixture
def quick(monkeypatch):
    """SQLite waits a twentieth of a second for a lock here, not 30 seconds."""
    monkeypatch.setattr(Outbox, "_db", lambda self: sqlitefile.transaction(self.path, timeout=0.05))


@contextlib.contextmanager
def locked(path):
    """Another process holding the outbox file: every read and write of it waits, then fails."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN EXCLUSIVE")
    try:
        yield
    finally:
        db.execute("ROLLBACK")
        db.close()


class LockingTeams(Teams):
    """A webhook that takes the post, and meanwhile something else locks the outbox."""

    def __init__(self, path, fail=None):
        super().__init__(fail)
        self.path, self.lock = path, None

    def __call__(self, url, title, lines):
        if self.lock is None:
            self.lock = locked(self.path)
            self.lock.__enter__()
        super().__call__(url, title, lines)

    def release(self):
        if self.lock is not None:
            self.lock.__exit__(None, None, None)
            self.lock = None


def _box(tmp_path, t):
    return Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0])


def test_a_locked_outbox_doesnt_raise_and_a_new_notice_is_posted_once(tmp_path, quick, caplog):
    t = [1_000_000.0]
    outbox, teams = _box(tmp_path, t), Teams()
    with locked(outbox.path), caplog.at_level(logging.WARNING, logger="fabric_desktop.workloads.notify"):
        out = notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)
    assert out["sent"] == 1 and len(teams.posted) == 1
    assert "alerts.sqlite3 couldn't be used" in out["error"] and "SECRET" not in out["error"]
    assert "posted without being kept" in caplog.text
    t[0] += notify.BACKOFF_MAX + notify.SENDING_GRACE + 1
    again = notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert again["error"] is None and len(teams.posted) == 1 and outbox.count() == 0  # it was never kept


def test_a_notice_waiting_in_a_locked_outbox_stays_until_the_file_is_free(tmp_path, quick):
    t = [1_000_000.0]
    outbox, teams = _box(tmp_path, t), Teams(requests.exceptions.ConnectTimeout("connect timed out"))
    assert notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)["queued"] == 1
    teams.fail = None
    t[0] += notify.BACKOFF_FIRST + 1
    with locked(outbox.path):
        out = notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert out["sent"] == 0 and "couldn't be used" in out["error"] and teams.posted == []
    out = notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert out["sent"] == 1 and len(teams.posted) == 1 and outbox.count() == 0


def test_a_notice_posted_whose_row_cant_be_deleted_is_never_sent_again(tmp_path, quick):
    t = [1_000_000.0]
    outbox = _box(tmp_path, t)
    teams = LockingTeams(outbox.path)
    out = notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)
    assert out["sent"] == 1 and len(teams.posted) == 1  # posted; the row is still in the locked file
    for _ in range(3):  # later sends, past the stop-mid-send grace: still locked, then free
        t[0] += notify.SENDING_GRACE + notify.BACKOFF_MAX + 1
        notify.send([], single(HOOK), outbox, "KratosForge", teams)
        teams.release()
    assert len(teams.posted) == 1 and outbox.count() == 0  # deleted once the file was free, never re-sent


@pytest.mark.parametrize("restart", [False, True])
def test_a_posted_notice_whose_row_cant_be_deleted_isnt_sent_again_by_a_new_outbox_or_after_a_restart(
        tmp_path, quick, monkeypatch, restart):
    t = [1_000_000.0]
    outbox = _box(tmp_path, t)
    teams = LockingTeams(outbox.path)
    assert notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)["sent"] == 1
    teams.release()
    if restart:  # a new process: only the file beside the outbox remembers
        monkeypatch.setattr(notify, "_GONE", {})
    assert (tmp_path / ("alerts.sqlite3" + notify.GONE_SUFFIX)).exists()
    t[0] += notify.SENDING_GRACE + notify.BACKOFF_MAX + 1
    fresh = _box(tmp_path, t)  # as the app's incident check and schedule alerts make one per call
    notify.send([], single(HOOK), fresh, "KratosForge", teams)
    assert len(teams.posted) == 1 and fresh.count() == 0
    assert not (tmp_path / ("alerts.sqlite3" + notify.GONE_SUFFIX)).exists()  # settled: nothing left to remember


def test_ids_remembered_for_an_outbox_file_that_was_replaced_filter_nothing(tmp_path, quick, monkeypatch):
    t = [1_000_000.0]
    outbox = _box(tmp_path, t)
    teams = LockingTeams(outbox.path)
    notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)
    teams.release()
    monkeypatch.setattr(notify, "_GONE", {})
    outbox.path.unlink()  # the outbox file was deleted; its ids would be reused by new rows
    fresh = _box(tmp_path, t)
    assert notify.send([], single(HOOK), fresh, "KratosForge", Teams(503))["sent"] == 0
    item = fresh.sending("New: x", ["a"])
    assert [r["id"] for r in fresh.pending()] == [item]  # not hidden by an old id


def test_a_row_id_reused_after_another_process_deleted_the_row_is_a_new_message(tmp_path, quick):
    t = [1_000_000.0]
    a = _box(tmp_path, t)
    teams = LockingTeams(a.path)
    notify.send([_change()], single(HOOK), a, "KratosForge", teams)  # posted; its row X couldn't be deleted
    teams.release()
    [(x, kept)] = notify._GONE[str(a.path.resolve())].items()
    assert kept is not None
    with sqlite3.connect(a.path) as db:  # another process deletes row X, and its next message reuses id X
        db.execute("DELETE FROM outbox WHERE id = ?", (x,))
    t[0] += 1
    other = _box(tmp_path, t)
    notify._GONE[str(a.path.resolve())][x] = kept  # still held for the old message
    assert other.sending("New: second", ["b"]) == x
    assert [r["title"] for r in a.pending()] == ["New: second"]  # not hidden, and not deleted by the settle
    assert a.count() == 1


def test_a_kept_ids_file_that_outlived_its_rows_never_hides_a_new_notice(tmp_path, quick, monkeypatch):
    t = [1_000_000.0]
    a = _box(tmp_path, t)
    teams = LockingTeams(a.path)
    notify.send([_change()], single(HOOK), a, "KratosForge", teams)
    teams.release()
    gone_file = tmp_path / ("alerts.sqlite3" + notify.GONE_SUFFIX)
    stale = gone_file.read_text(encoding="utf-8")
    a.pending()  # the row's delete commits...
    gone_file.write_text(stale, encoding="utf-8")  # ...but the app stopped before the file went
    monkeypatch.setattr(notify, "_GONE", {})
    t[0] += 60
    down = Teams(requests.exceptions.ConnectTimeout("connect timed out"))
    assert notify.send([_change()], single(HOOK), _box(tmp_path, t), "KratosForge", down)["queued"] == 1  # reuses id 1
    t[0] += notify.BACKOFF_MAX + 1
    up = Teams()
    out = notify.send([], single(HOOK), _box(tmp_path, t), "KratosForge", up)
    assert out["sent"] == 1 and len(up.posted) == 1  # the new notice went: the old pair matched nothing


def test_a_long_lived_outbox_sees_ids_another_process_kept_later(tmp_path, quick):
    t = [1_000_000.0]
    agent = _box(tmp_path, t)  # made first, and kept, as the agent keeps its outbox
    item = agent.sending("New: x", ["a"])
    created = agent.pending()[0]["created"]
    # Another process posted it and couldn't delete the row: it kept the pair in the file beside the outbox.
    (tmp_path / ("alerts.sqlite3" + notify.GONE_SUFFIX)).write_text(json.dumps([[item, created]]), encoding="utf-8")
    assert agent.pending() == [] and agent.count() == 0  # read again: that message went already


def test_a_bug_in_a_send_isnt_taken_for_a_busy_outbox(tmp_path, monkeypatch):
    outbox = _box(tmp_path, [1_000_000.0])
    monkeypatch.setattr(Outbox, "pending", lambda self: [{"id": 1}])  # a row missing its fields
    with pytest.raises(KeyError):
        notify.send([], single(HOOK), outbox, "KratosForge", Teams())


@pytest.mark.parametrize("waiting", [False, True])
def test_a_post_with_no_answer_while_the_outbox_is_locked_is_never_sent_again(tmp_path, quick, waiting):
    t = [1_000_000.0]
    outbox = _box(tmp_path, t)
    if waiting:  # the no-answer post is a retry from the outbox (flush), not a new message
        assert notify.send([_change()], single(HOOK), outbox, "KratosForge",
                           Teams(requests.exceptions.ConnectTimeout("connect timed out")))["queued"] == 1
        t[0] += notify.BACKOFF_FIRST + 1
    teams = LockingTeams(outbox.path, requests.exceptions.ReadTimeout("read timed out"))
    out = notify.send([] if waiting else [_change()], single(HOOK), outbox, "KratosForge", teams)
    assert out["unconfirmed"] == 1 and "may have arrived" in out["error"]
    teams.release()
    teams.fail = None
    for _ in range(3):
        t[0] += notify.SENDING_GRACE + notify.BACKOFF_MAX + 1
        notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert teams.calls == 1 and teams.posted == [] and outbox.count() == 0


def test_a_notice_held_for_quiet_hours_that_cant_be_kept_is_lost_counted_and_audited(tmp_path, quick):
    t = [dt.datetime(2026, 11, 18, 23, 0).timestamp()]
    outbox, teams = _box(tmp_path, t), Teams()
    routing = Routing([Route(notify.DEFAULT_ROUTE, HOOK)], Quiet.parse("22:00-07:00"))
    with locked(outbox.path):
        out = notify.send([_change()], routing, outbox, "KratosForge", teams,
                          now=dt.datetime.fromtimestamp(t[0]).astimezone())
    assert out["lost"] == 1 and out["held"] == 0 and teams.posted == []  # not posted during quiet hours
    assert "couldn't be used" in out["error"]
    lines = []

    class Audit:
        def record(self, action, outcome, seconds, params, **kw):
            lines.append((action, params))

    notify.audit_trouble(Audit(), out, "alerts.sqlite3")
    assert lines == [(notify.NOT_DELIVERED, {"outbox": "alerts.sqlite3", "queued": 0, "dropped": 0,
                                             "unconfirmed": 0, "waiting": 0, "lost": 1})]


def test_with_no_outbox_at_all_each_notice_is_posted_once_and_a_failure_is_named(tmp_path):
    teams = Teams()
    out = notify.send([_change()], single(HOOK), None, "KratosForge", teams)
    assert out["sent"] == 1 and out["error"] is None and len(teams.posted) == 1
    teams.fail = 503
    out = notify.send([_change()], single(HOOK), None, "KratosForge", teams)
    assert out["lost"] == 1 and "503" in out["error"] and "SECRET" not in out["error"]
    teams.fail = requests.exceptions.ReadTimeout("read timed out")
    out = notify.send([_change()], single(HOOK), None, "KratosForge", teams)
    assert out["unconfirmed"] == 1 and "lost" not in out and "may have arrived" in out["error"]


def test_a_locked_outbox_doesnt_stop_the_app_opening_an_incident(tmp_path, monkeypatch, quick):
    import types

    from fabric_desktop.service import Service
    svc = Service(root=tmp_path / "app")
    svc._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda s: None,
                                      fabric_token=lambda: None, storage_token=lambda: None)
    svc.meta_init(str(tmp_path / "proj"), "3")
    name = sorted(svc._project().entities)[0]
    monkeypatch.setattr(svc, "meta_runs", lambda env, limit: [run(name, "failed", "r1")])
    posted = []
    monkeypatch.setattr("fabric_desktop.platform_notify_service.post_teams", lambda url, title, lines: posted.append(title))
    settings = svc._settings
    svc._settings = lambda: {**settings(), "teams_webhook": "https://hook"}
    from fabric_desktop import incident_service
    real = incident_service.send

    def send_while_held(*a, **k):  # the incident is written; then another process holds the outbox
        with locked(svc.root / "alerts_outbox.sqlite"):
            return real(*a, **k)

    monkeypatch.setattr(incident_service, "send", send_while_held)
    out = svc.incidents_evaluate("dev", notify=True)
    monkeypatch.setattr(incident_service, "send", real)
    assert [c["change"] for c in out["changes"]] == ["opened"]
    assert out["notified"]["sent"] == 1 and len(posted) == 1 and posted[0].startswith("New: ")
    assert svc.incidents_evaluate("dev", notify=True)["changes"] == [] and len(posted) == 1  # once
    assert any(e["action"] == notify.NOT_DELIVERED for e in svc.audit.recent(20))


def test_the_agent_posts_its_incidents_when_its_outbox_cant_be_opened(agent_setup, monkeypatch, caplog):  # noqa: F811
    from fabric_desktop.agent import Agent
    monkeypatch.setenv("TEST_INCIDENT_HOOK", "https://hook")
    posted = []
    monkeypatch.setattr("fabric_desktop.agent.post_teams", lambda url, title, lines: posted.append(title))

    def unopenable(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("fabric_desktop.agent.Outbox", unopenable)
    agent = Agent(agent_setup, clock=Clock())
    name = sorted(agent.runtime.entity_names())[0]
    monkeypatch.setattr(agent.runtime, "meta_runs", lambda env, limit: [run(name, "failed", "r1", message="boom")])
    with caplog.at_level(logging.WARNING, logger="fabric_desktop.agent"):
        [c] = agent.evaluate_incidents()["dev"]
    assert c["change"] == "opened" and posted == [f"New: {name} failed in Dev (agent etl-01)"]
    assert "couldn't open the alerts outbox" in caplog.text


def test_an_environment_the_agent_cant_evaluate_becomes_its_own_incident_not_a_crash(agent_setup, monkeypatch):  # noqa: F811
    from fabric_desktop.agent import Agent
    monkeypatch.setenv("TEST_INCIDENT_HOOK", "https://hook")
    posted = []
    monkeypatch.setattr("fabric_desktop.agent.post_teams", lambda url, title, lines: posted.append(title))

    def broken(*a, **k):
        raise KeyError("promises")

    monkeypatch.setattr("fabric_desktop.agent.evaluate.signals", broken)
    agent = Agent(agent_setup, clock=Clock())
    [c] = agent.evaluate_incidents()["dev"]  # before: the KeyError left evaluate_incidents, nothing posted
    assert c["change"] == "opened" and len(posted) == 1
