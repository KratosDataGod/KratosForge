"""Removing secrets and personal details from text that leaves the engine (FD-131).

Two levels:

``secrets(text)``   tokens, SAS signatures, keys and passwords. Used on every error message the
                    UI shows and every agent log line: nobody needs those to fix a problem.
``secret_key(name)`` whether a field called ``name`` holds a secret, so structured output (log
                    fields, capability results) blanks its value whatever the value looks like.
``for_support(text)`` also email addresses, the Windows user name in paths, and GUIDs (tenant,
                    workspace, item ids). Used on Report a problem and support bundles, which can
                    end up in a public GitHub issue.

The UI has the same rules in ``app/ui/settings.js`` (``redactReport``); both are tested against
``app/tests/redact-cases.json`` so they can't drift apart.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlsplit

REMOVED = "[removed]"

_SECRETS: list[tuple[re.Pattern[str], str]] = [
    # A JSON Web Token: what Entra ID access, refresh-able id and Fabric tokens look like.
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"), REMOVED),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1" + REMOVED),
    # Basic credentials (base64 of user:password), after "Authorization:" or as base64 a word isn't.
    (re.compile(r"(?i)(\bauthorization\s*[:=]\s*basic\s+)[A-Za-z0-9+/=]{4,}"), r"\1" + REMOVED),
    # Case matters in the lookahead: base64 has a digit, +, / or = or a lower-to-upper case change,
    # which a word after "Basic" ("Basic authentication") doesn't.
    (re.compile(r"(\b(?:basic|Basic|BASIC)\s+)(?=[A-Za-z0-9+/]*(?:[0-9+/=]|[a-z][A-Z]))[A-Za-z0-9+/]{8,}={0,2}"),
     r"\1" + REMOVED),
    # A shared access signature's signature, in a URL or on its own (sig=... after a space or a quote).
    (re.compile(r"(?i)(\bsig=)[^&\s\"'<>]+"), r"\1" + REMOVED),
    # The app's own tokens: an integration's local API token (fdt_) and a VS Code pairing token (kfp_).
    (re.compile(r"\b(?:fdt|kfp)_[A-Za-z0-9_\-]{20,}"), REMOVED),
    # A password given to a command as its own argument: --password value (--password=value is below).
    (re.compile(r"(?i)(--(?:password|passwd|pwd)\s+)(?:\"[^\"\n]*\"?|'[^'\n]*'?|[^\s\"']+)"), r"\1" + REMOVED),
    # A Cookie or Set-Cookie header's value, to the end of its line: a session cookie signs a person in.
    (re.compile(r"(?i)(\b(?:set-)?cookie\s*:(?![ \t]*\[removed\])[ \t]*)[^\r\n]+"), r"\1" + REMOVED),
    # An OAuth authorization code in a redirect's query or fragment (?code=...): it can be traded for tokens.
    (re.compile(r"(?i)([?&#]code=)[^&\s\"'<>#]+"), r"\1" + REMOVED),
    # A password or token in a URL's user info: https://user:token@dev.azure.com/...
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://)[^/\s:@\"'<>]+:[^@/\s\"'<>]+@"), r"\1" + REMOVED + "@"),
    # A Teams incoming webhook: anyone with the address can post to the channel. The legacy
    # Office 365 connector form lives under outlook.office.com/webhook/ (or office365.com).
    (re.compile(r"(?i)https://(?:[A-Za-z0-9.\-]+\.webhook\.office\.com/|outlook\.office(?:365)?\.com/webhook(?:b2)?/)"
                r"[^\s\"'<>]+"), REMOVED),
    # The same webhook's path on its own: a failed post's error names the host and the path apart
    # ("host='x.webhook.office.com' ... url: /webhookb2/<id>@<id>/IncomingWebhook/...").
    (re.compile(r"(?i)/webhook(?:b2)?/[^\s\"'<>/]+@[^\s\"'<>/]+/IncomingWebhook/[^\s\"'<>]+"), REMOVED),
    # Provider tokens and keys, as git/secrets.py finds them in files about to be committed.
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"), REMOVED),
    (re.compile(r"(?<![A-Za-z0-9_~.\-])[A-Za-z0-9_~.\-]{3}\dQ~[A-Za-z0-9_~.\-]{31,34}(?![A-Za-z0-9_~.\-])"), REMOVED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REMOVED),
    # AI model keys (App Studio model connections): Anthropic, OpenAI (project, service account, admin and
    # older keys) and Google AI Studio.
    (re.compile(r"\b(?:sk-ant-[A-Za-z0-9_\-]{20,}|sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}|AIza[0-9A-Za-z_\-]{35})"), REMOVED),
    # A Databricks personal access token (dapi + 32 hex, sometimes with a -N suffix).
    (re.compile(r"\bdapi[0-9a-f]{32}(?:-\d+)?\b"), REMOVED),
    # A Databricks OAuth secret (a service principal's client secret: dose + 32 hex).
    (re.compile(r"\bdose[0-9a-f]{32}\b"), REMOVED),
    # A PEM private key (a Snowflake key pair's .p8, an SSH or TLS key), whole or cut off before its end
    # line. Before the quoted-value rules, so a key inside quotes is still found by its BEGIN line.
    (re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----(?:[\s\S]*?-----END (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----|[\s\S]*)"),
     REMOVED),
    # A quoted secret value runs to its closing quote, spaces and all: JSON ("name": "value") and a
    # Python dict's repr ('name': 'value'). The names are the generic rule's, at the end. With a
    # closing quote the value runs to it, across lines; without one it stops at the end of its line,
    # so one stray quote doesn't blank the rest of a report.
    (re.compile(r"(?i)(\"(?:client_secret|(?:set-)?cookie|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)\"\s*:\s*\")(?:(?:[^\"\\]|\\.)*(?=\")|(?:[^\"\\\n]|\\.)*)"), r"\1" + REMOVED),
    (re.compile(r"(?i)('(?:client_secret|(?:set-)?cookie|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)'\s*:\s*')(?:(?:[^'\\]|\\.)*(?=')|(?:[^'\\\n]|\\.)*)"), r"\1" + REMOVED),
    # An unquoted name with a quoted value (password="two words", token='a b'): the value runs to its
    # closing quote too, so the generic rule below doesn't stop at the first space.
    (re.compile(r"(?i)\b((?:client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)\s*[=:]\s*\")(?:(?:[^\"\\]|\\.)*(?=\")|(?:[^\"\\\n]|\\.)*)"), r"\1" + REMOVED),
    (re.compile(r"(?i)\b((?:client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)\s*[=:]\s*')(?:(?:[^'\\]|\\.)*(?=')|(?:[^'\\\n]|\\.)*)"), r"\1" + REMOVED),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b"), REMOVED),
    # name=value or "name": "value" for names that hold secrets.
    # Any *_token or *-token name, and a bare token= (access_token, sas_token, x-auth-token, token).
    (re.compile(r"(?i)\b(client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token"
                r"|api[_-]?key|secret)(\"?\s*[=:]\s*\"?)[^\s\"'&;,]+"), r"\1\2" + REMOVED),
]
# Field names whose value is a secret, compared without case, "-" or "_" (Api-Key, client_secret).
# "sas" only at either end (sas_url, blob_sas), so names that merely contain the letters stay.
_SECRET_KEYS = ("password", "passwd", "passphrase", "secret", "token", "apikey", "connectionstring", "authorization")
# A guest's sign-in name carries "#EXT#" before the "@" (name_home.com#EXT#@tenant.onmicrosoft.com).
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-#]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
# The tenant's own domain, and the organization's name on a OneDrive for Business folder, name the company.
_TENANT_DOMAIN = re.compile(r"(?i)\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9][a-z0-9\-]*)*(\.onmicrosoft\.(?:com|us|de|cn))\b")
# contoso.sharepoint.com, contoso-my.sharepoint.com (OneDrive) and contoso-admin.sharepoint.com
_SHAREPOINT_HOST = re.compile(r"(?i)\b[a-z0-9][a-z0-9\-]*?(-my|-admin)?(\.sharepoint\.(?:com|us|de|cn))\b")
# a site or team name often carries the organization's: /sites/ContosoFinance. One with spaces
# (/sites/Contoso Finance/...) is hidden whole when its words run to a "/", a quote or the line's end.
_SHAREPOINT_SITE = re.compile(r"(?im)(\.sharepoint\.(?:com|us|de|cn)/(?:sites|teams)/)"
                              r"[^/\s\"'<>?#]+(?:(?: [^/\s\"'<>?#]+)+(?=[/\"'<>?#\r]|$))?")
# a OneDrive web address names its owner: /personal/ann_contoso_com/
_ONEDRIVE_PERSONAL = re.compile(r"(?i)(/personal/)[a-z0-9][a-z0-9_.\-]*")
_ONEDRIVE_ORG = re.compile(r"(OneDrive - )([^\\/\"'<>:\r\n]+)")
_SLASH = r"(?:\\\\|\\|/)+"
# Windows user names can hold spaces ("Ann Lee"), so a drive path's name runs to the next separator.
_WIN_USER_PATH = re.compile(rf"(?i)([a-z]:{_SLASH}(?:users|documents and settings){_SLASH})([^\\/\"'<>\r\n]+)")
_UNIX_USER_PATH = re.compile(r"(/home/|/Users/)([^\\/\s\"'<>]+)")
_GUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def secrets(text: str) -> str:
    """``text`` with anything credential-shaped replaced by [removed]."""
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def without_webhook(text: str, url: Any) -> str:
    """``text`` with the webhook's path and query replaced: a legacy Office 365 connector URL carries its
    secret in the path, which requests puts in connection errors and secrets() doesn't know (QA-016)."""
    if not isinstance(url, str) or not url:
        return text
    from requests.utils import requote_uri  # as requests writes the URL into its errors

    pieces: set[str] = set()
    for form in {url, requote_uri(url), quote(url, safe=":/?=&")}:  # quote: an @ in the path as %40
        parts = urlsplit(form)
        pieces |= {form, parts.path + ("?" + parts.query if parts.query else ""), parts.path, parts.query}
    for piece in sorted(pieces, key=len, reverse=True):
        if len(piece) > 1:  # percent-escapes may be written in either case (%2f, %2F)
            text = re.sub(re.escape(piece), "[webhook]", text, flags=re.IGNORECASE)
    return text


def secret_key(name: object) -> bool:
    """Whether a field called ``name`` holds a secret: its text value is blanked, not pattern-matched."""
    key = str(name).lower().replace("-", "").replace("_", "")
    return any(part in key for part in _SECRET_KEYS) or key.startswith("sas") or key.endswith("sas")


def walk(value: Any, *, stringify: bool = False) -> Any:
    """``value`` with every string run through secrets() and the text of every field named like a
    secret (secret_key) blanked; dicts, lists and tuples are walked (a tuple comes back as a list).
    Numbers, flags and None stay. Anything else stays as it is, or with ``stringify`` becomes its
    str() with secrets removed, for a writer that would print it anyway (json default=str)."""
    if isinstance(value, str):
        return secrets(value)
    if isinstance(value, dict):
        return {k: REMOVED if secret_key(k) and isinstance(v, str) else walk(v, stringify=stringify)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [walk(v, stringify=stringify) for v in value]
    if not stringify or isinstance(value, (int, float, bool)) or value is None:
        return value
    return secrets(str(value))


def for_support(text: str) -> str:
    """``text`` safe to paste into a public issue: no secrets, emails (a guest's too), the tenant's
    onmicrosoft.com and SharePoint names, the organization on OneDrive folders, user names in paths or ids."""
    text = secrets(text)
    text = _EMAIL.sub("[email]", text)
    text = _TENANT_DOMAIN.sub(r"[tenant]\1", text)
    text = _SHAREPOINT_HOST.sub(r"[tenant]\1\2", text)
    text = _SHAREPOINT_SITE.sub(r"\1[site]", text)
    text = _ONEDRIVE_PERSONAL.sub(r"\1[user]", text)
    text = _ONEDRIVE_ORG.sub(r"\1[organization]", text)
    text = _WIN_USER_PATH.sub(r"\1[user]", text)
    text = _UNIX_USER_PATH.sub(r"\1[user]", text)
    return _GUID.sub("[id]", text)


DESCRIBE_MAX = 1000  # characters of an exception's message that describe keeps


def one_line(text: Any, limit: int = 300) -> str:
    """``text`` as it goes into a message: credential-shaped values removed, control characters and
    line breaks folded to single spaces, and cut to ``limit`` characters, so text from a file or a
    reply can't carry a token or start a line of its own."""
    # Removed again once folded: a control character between a keyword and its value hides the pair
    # from the first pass, and folding joins them.
    folded = secrets(" ".join("".join(c if c.isprintable() else " " for c in secrets(str(text))).split()))
    return folded if len(folded) <= limit else folded[:limit - 1] + "…"


def describe(exc: BaseException) -> str:
    """``exc`` as one log-safe line, ``TypeName: message``, with anything credential-shaped removed."""
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001 - an exception whose message can't be read still gets its type logged
        text = "(message unreadable)"
    text = one_line(text, DESCRIBE_MAX)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
