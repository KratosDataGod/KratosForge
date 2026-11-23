"""Notice delivery edges the main tests don't pin down (mutation batch 135, docs/quality/mutation-testing.md):
limits at their exact boundaries, the mail server's defaults and port range, the message's headers, the
timeouts each sender passes, and a server's reply given as text."""

import dataclasses
import smtplib

import pytest

from fabric_desktop.workloads import deliver

MAIL = deliver.MailServer("smtp.contoso.com", 587, "starttls", "kf@contoso.com")


class Conn:
    """smtplib as send_email uses it, recording the connection's arguments."""
    seen: list = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        self.seen.append(("connect", host, port, timeout))
        if self.fail:
            raise self.fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def ehlo(self):
        pass

    def has_extn(self, name):
        return True

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.seen.append(("send", msg))
        return {}


class Lib:
    SMTP = Conn
    SMTP_SSL = Conn


@pytest.fixture(autouse=True)
def fresh():
    Conn.seen = []
    Conn.fail = None


def test_the_limits_are_the_documented_ones():
    assert deliver.LINE_MAX == 500 and deliver.MAX_LINES == 200 and deliver.MAX_TO == 20


def test_a_route_sends_at_most_two_hundred_lines_each_cut_to_five_hundred_characters():
    got = []
    deliver.poster(None, teams=lambda a, t, lines: got.append((t, lines)))("https://h.example.com/x", "t" * 600,
                                                                         ["x" * 600] * 250)
    title, lines = got[0]
    assert len(title) == 500 and len(lines) == 200 and len(lines[0]) == 500


def test_an_empty_address_stays_empty():
    assert deliver.check("   ") == ""


def test_a_webhook_with_only_a_user_name_or_only_a_password_is_refused():
    with pytest.raises(ValueError, match="user name or password"):
        deliver.check("https://user@contoso.webhook.office.com/x")
    with pytest.raises(ValueError, match="user name or password"):
        deliver.check("https://:pw@contoso.webhook.office.com/x")  # pragma: allowlist secret (fake URL password)


def test_an_address_of_exactly_254_characters_is_kept_and_255_is_refused():
    ok = "a" * (254 - len("@contoso.com")) + "@contoso.com"
    assert deliver.recipients("mailto:" + ok) == [ok]
    with pytest.raises(ValueError, match="isn't an email address"):
        deliver.recipients("mailto:a" + ok)


def test_a_bad_address_is_named_in_at_most_eighty_characters():
    with pytest.raises(ValueError) as err:
        deliver.recipients("mailto:" + "x" * 200)
    assert str(err.value) == "x" * 79 + "… isn't an email address"


def test_exactly_twenty_addresses_are_allowed():
    to = [f"p{n}@contoso.com" for n in range(20)]
    assert deliver.recipients("mailto:" + ";".join(to)) == to
    with pytest.raises(ValueError, match="at most 20"):
        deliver.recipients("mailto:" + ";".join(to + ["p20@contoso.com"]))


def test_the_mail_server_defaults_to_port_587_and_cant_be_changed_in_place():
    m = deliver.MailServer("smtp.contoso.com")
    assert m.port == 587
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.port = 25  # type: ignore[misc]


def parse(**kw):
    return deliver.MailServer.parse({"host": "smtp.contoso.com", "sender": "kf@contoso.com", **kw})


def test_the_port_is_one_to_65535_inclusive():
    assert parse(port=1).port == 1 and parse(port=65535).port == 65535
    for bad in (65536, -1):
        with pytest.raises(ValueError, match="from 1 to 65535"):
            parse(port=bad)


def test_the_user_name_is_cut_to_256_characters():
    assert len(parse(username="u" * 300).username) == 256


def test_the_message_subject_is_cut_to_200_and_its_id_is_on_the_senders_domain(monkeypatch):
    calls = []
    real = deliver.formatdate
    monkeypatch.setattr(deliver, "formatdate", lambda **kw: calls.append(kw) or real(**kw))
    msg = deliver.email("kf@mail.contoso.com", ["a@contoso.com"], "s" * 300, [])
    assert len(msg["Subject"]) == 200 and msg["Message-ID"].endswith("@mail.contoso.com>")
    assert calls == [{"localtime": True}]


def test_an_email_shows_twenty_lines_and_says_how_many_more():
    def body(n):
        return deliver.email("kf@contoso.com", [], "t", [f"line {i}" for i in range(n)]).get_content()
    assert "more in KratosForge" not in body(20) and "line 19" in body(20)
    assert "and 1 more in KratosForge" in body(21) and "line 20" not in body(21)


def test_the_mail_server_gets_twenty_seconds_by_default():
    deliver.send_email(MAIL, None, ["a@contoso.com"], "t", [], smtp=Lib)
    assert Conn.seen[0] == ("connect", "smtp.contoso.com", 587, 20)


def test_a_reply_given_as_text_still_gives_its_status_code():
    Conn.fail = smtplib.SMTPDataError(550, "5.1.1 <bob@contoso.com> unknown")
    with pytest.raises(RuntimeError) as err:
        deliver.send_email(MAIL, None, ["a@contoso.com"], "t", [], smtp=Lib)
    assert str(err.value) == "smtp.contoso.com answered 550 (5.1.1)"


class Http:
    def __init__(self, status):
        self.status, self.calls = status, []

    def post(self, url, json=None, timeout=None, allow_redirects=True):
        self.calls.append(timeout)
        return type("R", (), {"status_code": self.status})()


def test_a_flow_gets_five_seconds_to_connect_and_fifteen_to_answer_and_any_3xx_fails():
    ok = Http(299)
    deliver.post_flow("https://h.example.com/w", "t", [], http=ok)
    assert ok.calls == [(5, 15)]
    with pytest.raises(RuntimeError, match="answered 300"):
        deliver.post_flow("https://h.example.com/w", "t", [], http=Http(300))
