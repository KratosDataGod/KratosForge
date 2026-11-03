"""How a notice route sends: a Teams webhook, a Power Automate flow, or email through the company
mail server. Platform notices (platform_notify.py) and incident alerts (notify.py) share it.

A route keeps one address, sealed in settings (sealed_settings.py), and its scheme says how it sends:

- ``https://...``: a Teams Workflows webhook, as routes always were, so every saved route still works.
- ``flow:https://...``: a Power Automate flow that starts "When an HTTP request is received". It gets
  ``{"title", "lines", "text", "source"}`` as JSON and sends the mail itself, as the tenant allows,
  so no app registration or Graph permission is needed.
- ``mailto:a@contoso.com;b@contoso.com``: email to up to ``MAX_TO`` addresses, through the
  ``MailServer`` set once per PC. The connection is always encrypted (STARTTLS, required, or TLS
  from the start) and the server's certificate is checked against Windows' store (net_trust.py).
  A password, when the relay needs one, is only in Windows Credential Manager (credman.py).

``poster(mail)`` gives the ``post(address, title, lines)`` both senders take. It raises
RuntimeError with a message free of addresses and secrets, so their outbox keeps the notice and
tries again later. What goes out is the same title and lines a Teams card shows: a request's
detail is never in them (platform_notify.message).
"""

from __future__ import annotations

import re
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any
from urllib.parse import urlparse

from ..redact import one_line

FLOW = "flow:"
MAILTO = "mailto:"
MAX_TO = 20
LINE_MAX = 500
MAX_LINES = 200  # lines kept from one message before any sender trims it further
PASSWORD_TARGET = "KratosForge/mail-server"  # the generic credential in Windows Credential Manager
SECURITY = ("starttls", "tls")
_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")

Post = Callable[[str, str, list[str]], None]


def kind(address: str) -> str:
    """teams, flow or email."""
    return "flow" if address.startswith(FLOW) else "email" if address.startswith(MAILTO) else "teams"


def recipients(address: str) -> list[str]:
    """The addresses in a ``mailto:`` route, each checked, no duplicates, at most ``MAX_TO``."""
    seen: dict[str, str] = {}
    for part in re.split(r"[;,\s]+", address[len(MAILTO):]):
        if not part:
            continue
        if not _EMAIL.match(part) or len(part) > 254:
            raise ValueError(f"{one_line(part, 80)} isn't an email address")
        seen.setdefault(part.lower(), part)
    if not seen:
        raise ValueError("Add at least one email address")
    if len(seen) > MAX_TO:
        raise ValueError(f"An email route sends to at most {MAX_TO} addresses; use a distribution list for more")
    return list(seen.values())


def check(address: str) -> str:
    """The address as it is kept, or ValueError saying what's wrong. Empty stays empty (not set)."""
    address = address.strip()
    if not address:
        return ""
    if address.startswith(MAILTO):
        return MAILTO + ";".join(recipients(address))
    url = address[len(FLOW):] if address.startswith(FLOW) else address
    what = "The flow's address" if address.startswith(FLOW) else "The Teams webhook address"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{what} starts with https://")
    if parsed.username or parsed.password:
        raise ValueError(f"{what} can't hold a user name or password")
    return address


def label(address: str, mail: "MailServer | None" = None) -> str:
    """What the UI shows for an address it never shows: the host, or how many people and through what."""
    if address.startswith(MAILTO):
        try:
            n = len(recipients(address))
        except ValueError:
            return "Email"
        who = "1 person" if n == 1 else f"{n} people"
        return f"{who}, through {mail.host}" if mail else f"{who}; set the company mail server to send"
    return urlparse(address[len(FLOW):] if address.startswith(FLOW) else address).hostname or ""


@dataclass(frozen=True)
class MailServer:
    """The company mail server (SMTP relay) every email route sends through. No password here: when
    ``username`` is set, the password is read from Windows Credential Manager when mail is sent."""
    host: str
    port: int = 587
    security: str = "starttls"
    sender: str = ""
    username: str = ""

    @staticmethod
    def parse(raw: Any) -> "MailServer | None":
        """From settings; None when none is set. ValueError for one that can't be used."""
        if not isinstance(raw, dict) or not str(raw.get("host") or "").strip():
            return None
        host = str(raw["host"]).strip().lower()
        if not _HOST.match(host):
            raise ValueError("The mail server is a host name, such as smtp.contoso.com")
        try:
            port = int(raw.get("port") or 587)
        except (TypeError, ValueError):
            raise ValueError("The port is a number, such as 587") from None
        if not 1 <= port <= 65535:
            raise ValueError("The port is a number from 1 to 65535")
        security = str(raw.get("security") or "starttls")
        if security not in SECURITY:
            raise ValueError("Encryption is STARTTLS or TLS; mail is never sent unencrypted")
        sender = str(raw.get("sender") or "").strip()
        if not _EMAIL.match(sender):
            raise ValueError("The From address is an email address, such as kratosforge@contoso.com")
        username = one_line(str(raw.get("username") or "").strip(), 256)
        return MailServer(host, port, security, sender, username)

    def as_settings(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "security": self.security, "sender": self.sender,
                "username": self.username}


def email(sender: str, to: list[str], title: str, lines: list[str]) -> EmailMessage:
    """The message: the title as its subject, one paragraph per line. Every part is one line with
    credential-shaped text removed, so nothing in a title can add a header. ``To`` names only the
    sender: recipients are in the envelope alone, so they don't see each other's addresses."""
    msg = EmailMessage()
    msg["Subject"] = one_line(title, 200)
    msg["From"] = sender
    msg["To"] = sender  # the recipients go in the envelope only (send_message's to_addrs)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
    body = [one_line(line, LINE_MAX) for line in lines[:20]]
    if len(lines) > 20:
        body.append(f"and {len(lines) - 20} more in KratosForge")
    msg.set_content("\n\n".join([*body, "Sent by KratosForge."]) + "\n")
    return msg


def send_email(mail: MailServer, password: str | None, to: list[str], title: str, lines: list[str],
               smtp: Any = None, timeout: float = 20) -> None:
    """Send one message, encrypted, with the certificate checked. ``smtp`` is the smtplib module (tests)."""
    lib = smtp or smtplib
    context = ssl.create_default_context()
    msg = email(mail.sender, to, title, lines)
    try:
        if mail.security == "tls":
            conn = lib.SMTP_SSL(mail.host, mail.port, timeout=timeout, context=context)
        else:
            conn = lib.SMTP(mail.host, mail.port, timeout=timeout)
        with conn:
            if mail.security == "starttls":
                conn.ehlo()
                if not conn.has_extn("starttls"):
                    raise RuntimeError(f"{mail.host} doesn't offer STARTTLS, so nothing was sent; "
                                       "use TLS from the start (port 465) or a relay that encrypts")
                conn.starttls(context=context)
                conn.ehlo()
            if mail.username:
                if not password:
                    raise RuntimeError("The mail server's password isn't in Windows Credential Manager; "
                                       "enter it again in Settings > Notifications")
                conn.login(mail.username, password)
            refused = conn.send_message(msg, from_addr=mail.sender, to_addrs=to)
    except RuntimeError:
        raise
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(f"{mail.host} didn't accept the user name and password ({exc.smtp_code})") from None
    except smtplib.SMTPResponseException as exc:
        # Only the codes: a server's own text often repeats an address ("550 5.1.1 <bob@...> unknown").
        status = _STATUS.search(_text(exc.smtp_error))
        raise RuntimeError(f"{mail.host} answered {exc.smtp_code}{f' ({status.group(0)})' if status else ''}") from None
    except ssl.SSLCertVerificationError:
        raise RuntimeError(f"{mail.host}'s certificate isn't trusted by Windows, so nothing was sent") from None
    except (OSError, smtplib.SMTPException) as exc:
        raise RuntimeError(f"Couldn't reach {mail.host}:{mail.port} ({type(exc).__name__})") from None
    if refused:
        raise RuntimeError(f"{mail.host} refused {len(refused)} of {len(to)} addresses")


_STATUS = re.compile(r"\b[245]\.\d{1,3}\.\d{1,3}\b")  # an enhanced status code (RFC 3463), such as 5.1.1


def _text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def flow_body(title: str, lines: list[str]) -> dict[str, Any]:
    title, lines = one_line(title, LINE_MAX), [one_line(line, LINE_MAX) for line in lines[:20]]
    return {"title": title, "lines": lines, "text": "\n".join([title, *lines]), "source": "KratosForge"}


def post_flow(url: str, title: str, lines: list[str], http: Any = None) -> None:
    """One POST to the flow. A write: sent once; the caller's outbox decides whether to send it again."""
    if http is None:
        from .. import http_session
        http = http_session.session()
    r = http.post(url, json=flow_body(title, lines), timeout=15, allow_redirects=False)
    if r.status_code >= 300:
        raise RuntimeError(f"The flow answered {r.status_code}")


def poster(mail: MailServer | None, teams: Post, password: Callable[[], str | None] = lambda: None,
           smtp: Any = None, http: Any = None) -> Post:
    """``post(address, title, lines)`` for any route: ``teams`` for a webhook (dataops_service.post_teams),
    the flow, or email through ``mail``. ``password`` reads the mail server's password when it's needed."""
    def post(address: str, title: str, lines: list[str]) -> None:
        # Every kind gets the same text: one line each, credential-shaped values removed, bounded.
        # Each sender then shows at most 20 lines and says how many more there are.
        title, lines = one_line(title, LINE_MAX), [one_line(line, LINE_MAX) for line in lines[:MAX_LINES]]
        how = kind(address)
        if how == "teams":
            teams(address, title, lines)
        elif how == "flow":
            post_flow(address[len(FLOW):], title, lines, http)
        else:
            if mail is None:
                raise RuntimeError("No company mail server is set, so email routes wait; set one in Settings > Notifications")
            send_email(mail, password() if mail.username else None, recipients(address), title, lines, smtp)
    return post
