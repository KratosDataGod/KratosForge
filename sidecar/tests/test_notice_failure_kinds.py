"""Each way a Teams or flow post can fail, through the real transports (post_teams, post_flow) with a fake
HTTP answer, then notify.send's outbox and its audit line. Every failure is recorded (an audit line with
the error, never the webhook), and no notice ever reaches the channel twice: a post that may have
arrived (no answer, or 500, 502, 504) is never sent again, and one that was refused (429, 503, any 4xx)
or never sent goes once when the route works again. Nothing here reaches Teams or a flow."""

from __future__ import annotations

import pytest
import requests

from fabric_desktop import dataops_service, http_session
from fabric_desktop.workloads import deliver, notify
from fabric_desktop.workloads.notify import BACKOFF_MAX, Outbox, single

from test_notice_failures import HOOK

FLOW = "https://prod-00.westus.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?sig=SECRET999"


class Answer:
    def __init__(self, status):
        self.status_code, self.headers, self.text = status, {"Retry-After": "0"}, ""

    def json(self):
        raise ValueError("no body")


class Http:
    """An HTTP session that fails as told, then works; it counts the posts that reached the channel."""

    def __init__(self, fail):
        self.fail, self.calls, self.delivered = fail, 0, 0

    def post(self, url, json=None, timeout=None, allow_redirects=True):
        self.calls += 1
        fail = self.fail
        if fail is None:
            self.delivered += 1
            return Answer(200)
        if isinstance(fail, int):
            if fail in notify.MAY_HAVE_ARRIVED:
                self.delivered += 1  # the server failed after it took the post: it may well be in the channel
            return Answer(fail)
        if isinstance(fail, requests.exceptions.ReadTimeout):
            self.delivered += 1  # sent, and no answer came: it may have arrived
        raise fail


MAY_HAVE_ARRIVED = [500, 502, 504, requests.exceptions.ReadTimeout("read timed out")]
REFUSED = [429, 503, 400, 401, 403, 404, 410, requests.exceptions.ConnectTimeout("connect timed out"),
           requests.exceptions.ConnectionError("Failed to establish a new connection")]


def _change():
    from test_incidents import change
    return change()


def _transports(http):
    return {"teams": (HOOK, lambda url, title, lines: dataops_service.post_teams(url, title, lines, sleep=lambda s: None)),
            "flow": (FLOW, lambda url, title, lines: deliver.post_flow(url, title, lines, http=http))}


class Audit:
    def __init__(self):
        self.lines = []

    def record(self, action, outcome, seconds, params, **kw):
        self.lines.append((action, outcome, params, kw.get("error", "")))


@pytest.mark.parametrize("transport", ["teams", "flow"])
@pytest.mark.parametrize("fail", MAY_HAVE_ARRIVED + REFUSED, ids=str)
def test_every_failed_post_is_recorded_and_no_notice_reaches_the_channel_twice(tmp_path, monkeypatch, transport, fail):
    http = Http(fail)
    monkeypatch.setattr(http_session, "session", lambda: http)
    url, post = _transports(http)[transport]
    t = [1_000_000.0]
    outbox, audit = Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0]), Audit()
    out = notify.send([_change()], single(url), outbox, "KratosForge", post)
    notify.audit_trouble(audit, out, "alerts.sqlite3")
    [(action, outcome, params, error)] = audit.lines  # recorded, once
    assert (action, outcome) == (notify.NOT_DELIVERED, "error") and error
    assert "SECRET" not in error and "webhook.office.com" not in error and "logic.azure.com" not in error
    unsure = fail in MAY_HAVE_ARRIVED
    assert (params["unconfirmed"], params["queued"]) == ((1, 0) if unsure else (0, 1))
    http.fail = None
    for _ in range(4):  # the route works again; later sends, past every backoff
        t[0] += BACKOFF_MAX + 1
        notify.send([], single(url), outbox, "KratosForge", post)
    assert http.delivered == 1  # in the channel once: as it may have arrived, or sent once later
    assert outbox.count() == 0


@pytest.mark.parametrize("status, unsure", [(500, True), (502, True), (504, True), (503, False), (429, False),
                                            (404, False), (400, False)])
def test_an_answer_says_whether_the_post_may_have_arrived(status, unsure):
    assert notify.outcome_unknown(notify.answered("Teams", status)) is unsure
