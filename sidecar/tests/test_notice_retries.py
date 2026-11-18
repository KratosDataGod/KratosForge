"""Notice retries. A Teams, flow or mail post that fails is sent again later, each route resting between
tries with a backoff that doubles up to a cap, for at most three days. A post that was sent but got no
answer (a read timeout, a connection dropped after the request went, a mail server that stopped while
it took the message) may have arrived, so it is never sent again unless the message is idempotent. It
is counted as ``unconfirmed``, named in ``error`` and audited, never lost without a word. Fake
transports only: nothing here reaches Teams, a flow or a mail server."""

import http.client
import smtplib

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from fabric_desktop.workloads import deliver, notify
from fabric_desktop.workloads.notify import (BACKOFF_FIRST, BACKOFF_MAX, KEEP_TRYING, OutcomeUnknown, Outbox,
                                             outcome_unknown, single)
from fabric_desktop.workloads.platform_notify import Ledger, PlatformRoute, PlatformRouting
from fabric_desktop.workloads.platform_notify import send as platform_send

from test_notice_failures import HOOK, Teams
from test_platform_notify import NOON, event

ABORTED = requests.exceptions.ConnectionError(
    ProtocolError("Connection aborted.", http.client.RemoteDisconnected("Remote end closed connection without response")))
REFUSED = requests.exceptions.ConnectionError(
    MaxRetryError(None, "/workflows/x", NewConnectionError(None, "Failed to establish a new connection: refused")))
NO_ANSWER = [requests.exceptions.ReadTimeout("read timed out"), ABORTED,
             requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead"),
             OutcomeUnknown("mail.contoso.com stopped answering while it took the email (TimeoutError)")]
NOT_SENT = [requests.exceptions.ConnectTimeout("connect timed out"), REFUSED, RuntimeError("Teams answered 503"),
            RuntimeError("Teams answered 429"), RuntimeError("Teams answered 404"), OSError("network unreachable")]


def _change():
    from test_incidents import change
    return change()


@pytest.mark.parametrize("exc", NO_ANSWER)
def test_a_post_with_no_answer_may_have_arrived(exc):
    assert outcome_unknown(exc)


@pytest.mark.parametrize("exc", NOT_SENT)
def test_a_post_refused_or_never_sent_didnt_arrive(exc):
    assert not outcome_unknown(exc)


@pytest.mark.parametrize("exc", NO_ANSWER)
def test_an_alert_sent_with_no_answer_is_never_sent_twice_and_is_named(tmp_path, exc):
    t = [1_000_000.0]
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0])
    teams = Teams(exc)
    out = notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)
    assert (out["sent"], out["queued"], out["unconfirmed"], out["waiting"]) == (0, 0, 1, 0)
    assert "may have arrived; it isn't sent again" in out["error"] and "SECRET" not in out["error"]
    teams.fail = None
    for _ in range(5):  # later sends, well past every backoff: it never goes again
        t[0] += BACKOFF_MAX + 1
        notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert teams.calls == 1 and teams.posted == []


def test_an_idempotent_alert_sent_with_no_answer_waits_and_goes_once(tmp_path):
    t = [1_000_000.0]
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0])
    teams = Teams(requests.exceptions.ReadTimeout("read timed out"))
    out = notify.send([_change()], single(HOOK), outbox, "KratosForge", teams, idempotent=True)
    assert (out["queued"], out["unconfirmed"], out["waiting"]) == (1, 0, 1)
    assert outbox.pending()[0]["idempotent"]
    teams.fail = None
    t[0] += BACKOFF_FIRST + 1
    notify.send([], single(HOOK), outbox, "KratosForge", teams)
    t[0] += BACKOFF_MAX + 1
    notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert len(teams.posted) == 1 and teams.posted[0][1].startswith("Delayed: ") and outbox.count() == 0


def test_a_waiting_alert_whose_retry_gets_no_answer_is_not_tried_a_third_time(tmp_path):
    t = [1_000_000.0]
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0])
    teams = Teams(requests.exceptions.ConnectTimeout("connect timed out"))
    assert notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)["queued"] == 1  # never sent: waits
    teams.fail = requests.exceptions.ReadTimeout("read timed out")
    t[0] += BACKOFF_FIRST + 1
    out = notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert (out["unconfirmed"], out["waiting"]) == (1, 0) and "may have arrived" in out["error"]
    teams.fail = None
    t[0] += BACKOFF_MAX + 1
    notify.send([], single(HOOK), outbox, "KratosForge", teams)
    assert teams.calls == 2 and teams.posted == []


def test_a_failing_route_rests_longer_each_time_up_to_the_cap_and_then_its_alert_is_dropped_and_counted(tmp_path):
    t = [1_000_000.0]
    outbox = Outbox(tmp_path / "alerts.sqlite3", clock=lambda: t[0])
    tried = []

    def teams(url, title, lines):
        tried.append(t[0])
        raise RuntimeError("Teams answered 503")

    start = t[0]
    notify.send([_change()], single(HOOK), outbox, "KratosForge", teams)
    while t[0] - start <= KEEP_TRYING:  # the agent's loop: a send every five minutes
        t[0] += 300
        out = notify.send([], single(HOOK), outbox, "KratosForge", teams)
    gaps = [b - a for a, b in zip(tried, tried[1:], strict=False)]
    for n, gap in enumerate(gaps):  # each rest at least the backoff, doubling from a minute, capped
        assert min(BACKOFF_FIRST * 2 ** n, BACKOFF_MAX) <= gap <= min(BACKOFF_FIRST * 2 ** n, BACKOFF_MAX) + 300, n
    assert max(gaps) <= BACKOFF_MAX + 300 and len(tried) < KEEP_TRYING / BACKOFF_MAX + 10  # bounded tries
    assert out["dropped"] == 1 and outbox.count() == 0  # three days on: dropped, and counted for the audit


def test_a_platform_notice_sent_with_no_answer_says_unconfirmed_and_its_key_stops_a_repeat(tmp_path):
    t = [1_000_000.0]
    path = tmp_path / "platform_outbox.sqlite"
    outbox, ledger = Outbox(path, clock=lambda: t[0]), Ledger(path, clock=lambda: t[0])
    routing = PlatformRouting([PlatformRoute("default", HOOK)])
    teams = Teams(ABORTED)
    out = platform_send([event()], routing, outbox, ledger, teams, now=NOON)
    assert (out["sent"], out["queued"], out["unconfirmed"], out["events"][0]["result"]) == (0, 0, 1, "unconfirmed")
    teams.fail = None
    t[0] += BACKOFF_MAX + 1
    again = platform_send([event()], routing, outbox, ledger, teams, now=NOON)
    assert again["events"][0]["result"] == "skipped" and teams.posted == [] and outbox.count() == 0


def test_an_unconfirmed_notice_has_an_audit_line_that_says_so():
    lines = []

    class Audit:
        def record(self, action, outcome, seconds, params, **kw):
            lines.append((action, outcome, params, kw["error"], kw["reason"]))

    notify.audit_trouble(Audit(), {"error": notify.UNCONFIRMED.format(error=f"ReadTimeout: no answer from {HOOK}"),
                                   "unconfirmed": 1, "waiting": 0}, "alerts.sqlite3")
    [(action, outcome, params, error, reason)] = lines
    assert (action, outcome, reason) == (notify.NOT_DELIVERED, "error", "failed")
    assert params == {"outbox": "alerts.sqlite3", "queued": 0, "dropped": 0, "unconfirmed": 1, "waiting": 0}
    assert "may have arrived" in error and "SECRET" not in error and "webhook.office.com" not in error
    lines.clear()
    notify.audit_trouble(Audit(), {"unconfirmed": 1}, "alerts.sqlite3")  # even with no error text
    assert len(lines) == 1


# ---- the mail transport tells the two apart itself


class Smtp:
    """smtplib as send_email uses it, failing at the step it's told to."""
    at = ""

    def __init__(self, host, port, timeout=None, context=None):
        if self.at == "connect":
            raise TimeoutError("timed out")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return True

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if self.at == "login":
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        if self.at == "send":
            raise TimeoutError("timed out")
        if self.at == "send-dropped":
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        if self.at == "send-refused":
            raise smtplib.SMTPDataError(451, b"4.3.0 try again later")
        return {}


class Lib:
    SMTP = Smtp
    SMTP_SSL = Smtp


MAIL = deliver.MailServer(host="mail.contoso.com", port=587, sender="kf@contoso.com", security="starttls",
                          username="kf")


@pytest.mark.parametrize("at, unknown", [("connect", False), ("login", False), ("send-refused", False),
                                         ("send", True), ("send-dropped", True)])
def test_a_mail_server_that_stops_while_it_takes_the_message_may_have_delivered_it(monkeypatch, at, unknown):
    monkeypatch.setattr(Smtp, "at", at)
    with pytest.raises(RuntimeError) as err:
        deliver.send_email(MAIL, "pw", ["ops@contoso.com"], "Title", ["line"], smtp=Lib)
    assert outcome_unknown(err.value) is unknown, at
    assert "ops@contoso.com" not in str(err.value)
