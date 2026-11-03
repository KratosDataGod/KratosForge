"""Notice routes send by Teams webhook, Power Automate flow or email (workloads/deliver.py)."""

import smtplib
import ssl

import pytest

from fabric_desktop import platform_notify_service as pns
from fabric_desktop.workloads import deliver
from fabric_desktop.workloads.platform_notify import PlatformRoute

MAIL = deliver.MailServer("smtp.contoso.com", 587, "starttls", "kf@contoso.com", "svc-kf")


class FakeSMTP:
    """smtplib as the sender uses it: records what happened, refuses what it's told to."""
    log: list = []
    starttls_offered = True
    fail: Exception | None = None

    class SMTPAuthenticationError(smtplib.SMTPAuthenticationError):
        pass

    def __init__(self, host, port, timeout=None, context=None):
        self.log.append(("connect", host, port, type(self).__name__, context is not None))
        if self.fail:
            raise self.fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.log.append(("quit",))

    def ehlo(self):
        self.log.append(("ehlo",))

    def has_extn(self, name):
        return self.starttls_offered and name == "starttls"

    def starttls(self, context=None):
        assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        self.log.append(("starttls",))

    def login(self, user, password):
        self.log.append(("login", user, password))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.log.append(("send", from_addr, tuple(to_addrs), msg["Subject"], msg.get_content()))
        return {}


class SSLFake(FakeSMTP):
    pass


class Lib:
    SMTP = FakeSMTP
    SMTP_SSL = SSLFake


@pytest.fixture(autouse=True)
def fresh():
    FakeSMTP.log = []
    FakeSMTP.starttls_offered = True
    FakeSMTP.fail = None


def test_the_address_says_how_a_route_sends_and_old_teams_routes_still_work():
    assert deliver.kind("https://contoso.webhook.office.com/x") == "teams"
    assert deliver.kind("flow:https://prod-1.westus.logic.azure.com/w") == "flow"
    assert deliver.kind("mailto:a@contoso.com") == "email"
    assert deliver.check(" mailto:a@contoso.com; B@contoso.com,a@contoso.com ") == "mailto:a@contoso.com;B@contoso.com"
    for bad, why in [("mailto:", "at least one"), ("mailto:nope", "isn't an email"),
                     ("mailto:" + ";".join(f"p{i}@c.com" for i in range(21)), "at most 20"),
                     ("flow:http://x.com/a", "flow's address starts with https"),
                     ("http://x.com", "Teams webhook address starts with https"), ("https://u:p@x.com/", "user name")]:
        with pytest.raises(ValueError, match=why):
            deliver.check(bad)
    assert PlatformRoute.parse({"name": "ops", "url": "mailto:a@contoso.com"}).url == "mailto:a@contoso.com"
    with pytest.raises(ValueError, match="route ops: .*https://"):
        PlatformRoute.parse({"name": "ops", "url": "ftp://x"})
    assert deliver.label("mailto:a@c.com;b@c.com", MAIL) == "2 people, through smtp.contoso.com"
    assert deliver.label("flow:https://prod-1.westus.logic.azure.com/w?sig=SECRET") == "prod-1.westus.logic.azure.com"


def test_email_is_encrypted_signs_in_and_carries_only_one_line_text():
    post = deliver.poster(MAIL, teams=lambda *a: pytest.fail("not Teams"), password=lambda: "pw", smtp=Lib)
    post("mailto:ana@contoso.com;li@contoso.com", "Request waiting: Sales\r\nBcc: evil@x.com", ["Waits on ana", "Open it in KratosForge"])
    steps = [s[0] for s in FakeSMTP.log]
    assert steps == ["connect", "ehlo", "starttls", "ehlo", "login", "send", "quit"]
    _, sender, to, subject, body = FakeSMTP.log[5]
    assert sender == "kf@contoso.com" and to == ("ana@contoso.com", "li@contoso.com")
    assert "\n" not in subject and "\r" not in subject and subject.startswith("Request waiting: Sales")
    assert "Waits on ana" in body and body.rstrip().endswith("Sent by KratosForge.")
    msg = deliver.email("kf@contoso.com", ["ana@contoso.com", "li@contoso.com"], "t", [])
    assert msg["To"] == "kf@contoso.com" and "ana@contoso.com" not in msg.as_string()


def test_a_server_without_starttls_gets_nothing_and_tls_uses_a_checked_context():
    FakeSMTP.starttls_offered = False
    with pytest.raises(RuntimeError, match="doesn't offer STARTTLS, so nothing was sent"):
        deliver.send_email(MAIL, "pw", ["a@contoso.com"], "t", [], smtp=Lib)
    assert not any(s[0] in ("login", "send") for s in FakeSMTP.log)
    FakeSMTP.log = []
    deliver.send_email(deliver.MailServer("smtp.contoso.com", 465, "tls", "kf@contoso.com"), None, ["a@contoso.com"], "t", [], smtp=Lib)
    assert FakeSMTP.log[0] == ("connect", "smtp.contoso.com", 465, "SSLFake", True)
    assert not any(s[0] == "login" for s in FakeSMTP.log)  # no user name: no sign-in


def test_failures_say_what_happened_without_the_password():
    FakeSMTP.fail = smtplib.SMTPAuthenticationError(535, b"5.7.3 bad credentials pw=hunter2")
    with pytest.raises(RuntimeError) as err:
        deliver.send_email(MAIL, "hunter2", ["a@contoso.com"], "t", [], smtp=Lib)
    assert "didn't accept the user name and password" in str(err.value) and "hunter2" not in str(err.value)
    FakeSMTP.fail = smtplib.SMTPDataError(550, b"5.1.1 <bob@contoso.com> unknown")
    with pytest.raises(RuntimeError) as err:
        deliver.send_email(MAIL, "pw", ["a@contoso.com"], "t", [], smtp=Lib)
    assert str(err.value) == "smtp.contoso.com answered 550 (5.1.1)"
    FakeSMTP.fail = ConnectionRefusedError("refused")
    with pytest.raises(RuntimeError, match="Couldn't reach smtp.contoso.com:587"):
        deliver.send_email(MAIL, "pw", ["a@contoso.com"], "t", [], smtp=Lib)
    FakeSMTP.fail = None
    with pytest.raises(RuntimeError, match="password isn't in Windows Credential Manager"):
        deliver.poster(MAIL, teams=print, password=lambda: None, smtp=Lib)("mailto:a@contoso.com", "t", [])
    with pytest.raises(RuntimeError, match="No company mail server is set"):
        deliver.poster(None, teams=print)("mailto:a@contoso.com", "t", [])


def test_a_flow_gets_json_once_and_teams_routes_go_to_teams():
    calls = []

    class Http:
        def post(self, url, json=None, timeout=None, allow_redirects=True):
            calls.append((url, json, allow_redirects))
            return type("R", (), {"status_code": 202})()

    teams = []
    post = deliver.poster(None, teams=lambda *a: teams.append(a), http=Http())
    post("flow:https://prod-1.westus.logic.azure.com/w", "New: orders late", ["line\none"])
    post("https://contoso.webhook.office.com/x", "t", ["l"])
    url, body, redirects = calls[0]
    assert url == "https://prod-1.westus.logic.azure.com/w" and redirects is False
    assert body == {"title": "New: orders late", "lines": ["line one"], "text": "New: orders late\nline one", "source": "KratosForge"}
    assert teams == [("https://contoso.webhook.office.com/x", "t", ["l"])]


def test_mail_server_settings_check_everything_and_never_hold_the_password():
    with pytest.raises(ValueError, match="never sent unencrypted"):
        deliver.MailServer.parse({"host": "smtp.contoso.com", "security": "none", "sender": "a@contoso.com"})
    with pytest.raises(ValueError, match="host name"):
        deliver.MailServer.parse({"host": "smtp.contoso.com:25", "sender": "a@contoso.com"})
    with pytest.raises(ValueError, match="From address"):
        deliver.MailServer.parse({"host": "smtp.contoso.com", "sender": "kratosforge"})
    assert deliver.MailServer.parse({}) is None
    m = deliver.MailServer.parse({"host": "SMTP.contoso.com", "port": "465", "security": "tls", "sender": "a@contoso.com",
                                  "password": "nope"})
    assert m is not None and m.host == "smtp.contoso.com" and "password" not in m.as_settings()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from fabric_desktop.service import Service

    s = Service(root=tmp_path / "app")
    vault: dict[str, tuple[str, str]] = {}
    monkeypatch.setattr(pns.credman, "write", lambda t, u, p: vault.__setitem__(t, (u, p)))
    monkeypatch.setattr(pns.credman, "read", lambda t: vault[t][1] if t in vault else None)
    monkeypatch.setattr(pns.credman, "delete", lambda t: vault.pop(t, None))
    sent = []
    monkeypatch.setattr(deliver, "send_email", lambda mail, pw, to, title, lines, smtp=None: sent.append((mail.host, pw, to, title)))
    s.vault, s.sent = vault, sent
    return s


def test_the_mail_server_is_tested_before_it_is_saved_and_its_password_stays_in_credential_manager(svc):
    assert svc.mail_server_settings() == {"set": False}
    with pytest.raises(ValueError, match="Enter the password"):
        svc.mail_server_save("smtp.contoso.com", "kf@contoso.com", username="svc-kf")
    svc.mail_server_save("smtp.contoso.com", "kf@contoso.com", username="svc-kf", password="hunter2", test_to="ana@contoso.com")
    assert svc.sent == [("smtp.contoso.com", "hunter2", ["ana@contoso.com"], "KratosForge can send email")]
    assert svc.vault[deliver.PASSWORD_TARGET] == ("svc-kf", "hunter2")
    assert "hunter2" not in (svc.settings_file).read_text()
    shown = svc.mail_server_settings()
    assert shown["password_saved"] is True and "hunter2" not in str(shown)
    svc.mail_server_save("smtp.contoso.com", "kf@contoso.com", username="svc-kf")  # empty password keeps it
    assert svc.vault[deliver.PASSWORD_TARGET][1] == "hunter2"
    svc.platform_notify_save([{"name": "default", "url": "mailto:ana@contoso.com"}], test="default")
    assert svc.sent[-1][1:3] == ("hunter2", ["ana@contoso.com"])
    assert svc.platform_notify_settings()["routes"][0]["host"] == "1 person, through smtp.contoso.com"
    assert "ana@contoso.com" not in str(svc.platform_notify_settings()["routes"])
    svc.mail_server_forget()
    assert svc.mail_server_settings() == {"set": False} and deliver.PASSWORD_TARGET not in svc.vault


def test_a_failed_test_saves_nothing(svc, monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("smtp.contoso.com answered 550")
    monkeypatch.setattr(deliver, "send_email", refuse)
    with pytest.raises(RuntimeError, match="550"):
        svc.mail_server_save("smtp.contoso.com", "kf@contoso.com", username="svc-kf", password="pw", test_to="a@contoso.com")
    assert svc.mail_server_settings() == {"set": False} and svc.vault == {}


def test_mail_server_methods_are_served_and_the_password_is_never_audited():
    from fabric_desktop import audit, server

    assert {"mail_server_save", "mail_server_forget"} <= server.METHODS and "mail_server_settings" not in server.METHODS
    assert audit.audited("mail_server_save") and audit.audited("mail_server_forget")
    assert audit.target({"host": "smtp.contoso.com", "password": "hunter2"}).get("password") != "hunter2"


def test_every_kind_gets_text_without_secrets_on_one_line():
    got = []
    post = deliver.poster(None, teams=lambda url, title, lines: got.append((title, lines)))
    post("https://contoso.webhook.office.com/x", "New\nincident password=hunter2", ["a\r\nb"] * 300)
    title, lines = got[0]
    assert "\n" not in title and "hunter2" not in title and len(lines) == deliver.MAX_LINES and lines[0] == "a b"
