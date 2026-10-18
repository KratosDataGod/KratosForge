"""Removing secrets and personal details from text that leaves the engine (FD-131).

Two levels:

``secrets(text)``   tokens, SAS signatures, keys and passwords. Used on every error message the
                    UI shows and every agent log line: nobody needs those to fix a problem.
``for_support(text)`` also email addresses, the Windows user name in paths, and GUIDs (tenant,
                    workspace, item ids). Used on Report a problem and support bundles, which can
                    end up in a public GitHub issue.

The UI has the same rules in ``app/ui/settings.js`` (``redactReport``); both are tested against
``app/tests/redact-cases.json`` so they can't drift apart.
"""

from __future__ import annotations

import re

REMOVED = "[removed]"

_SECRETS: list[tuple[re.Pattern[str], str]] = [
    # A JSON Web Token: what Entra ID access, refresh-able id and Fabric tokens look like.
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"), REMOVED),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1" + REMOVED),
    # A shared access signature's signature, in a URL.
    (re.compile(r"(?i)([?&]sig=)[^&\s\"'<>]+"), r"\1" + REMOVED),
    # A password or token in a URL's user info: https://user:token@dev.azure.com/...
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://)[^/\s:@\"'<>]+:[^@/\s\"'<>]+@"), r"\1" + REMOVED + "@"),
    # A Teams incoming webhook: anyone with the address can post to the channel.
    (re.compile(r"https://[A-Za-z0-9.\-]+\.webhook\.office\.com/[^\s\"'<>]+"), REMOVED),
    # Provider tokens and keys, as git/secrets.py finds them in files about to be committed.
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"), REMOVED),
    (re.compile(r"(?<![A-Za-z0-9_~.\-])[A-Za-z0-9_~.\-]{3}\dQ~[A-Za-z0-9_~.\-]{31,34}(?![A-Za-z0-9_~.\-])"), REMOVED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REMOVED),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b"), REMOVED),
    # name=value or "name": "value" for names that hold secrets.
    (re.compile(r"(?i)\b(client_secret|password|passwd|pwd|accountkey|sharedaccesskey|access_token|refresh_token"
                r"|id_token|api[_-]?key|secret)(\"?\s*[=:]\s*\"?)[^\s\"'&;,]+"), r"\1\2" + REMOVED),
]
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
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


def for_support(text: str) -> str:
    """``text`` safe to paste into a public issue: no secrets, emails, user names in paths or ids."""
    text = secrets(text)
    text = _EMAIL.sub("[email]", text)
    text = _WIN_USER_PATH.sub(r"\1[user]", text)
    text = _UNIX_USER_PATH.sub(r"\1[user]", text)
    return _GUID.sub("[id]", text)
