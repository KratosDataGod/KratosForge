"""A resource's claims challenge after a 401 (Continuous Access Evaluation), and how a client asks for
a new token with it.

With Continuous Access Evaluation, Fabric, Power BI and the other Microsoft APIs can refuse a token
before it expires (the account was disabled, its sessions were revoked, it moved to a blocked
location). Their 401 then carries ``WWW-Authenticate: Bearer ..., error="insufficient_claims",
claims="<base64 JSON>"``. A new token is asked for with those claims (auth.Authenticator.refreshed_token),
so Entra checks the sign-in again rather than handing back the token that was just refused.
Only the API the request went to sends them, and they go only to MSAL, never into a log or message.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

MAX_CLAIMS = 4096  # a real challenge is a few hundred characters
_CLAIMS = re.compile(r'\bclaims="([A-Za-z0-9+/_=-]{1,8192})"')


def challenge(headers: Mapping[str, str] | None) -> str:
    """The claims JSON a 401's WWW-Authenticate asks for, decoded; "" when it asks for none, or for
    something that isn't one JSON object."""
    header = str((headers or {}).get("WWW-Authenticate") or "")
    if "insufficient_claims" not in header:
        return ""
    found = _CLAIMS.search(header)
    if not found:
        return ""
    raw = found.group(1).replace("+", "-").replace("/", "_").rstrip("=")
    try:
        text = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        parsed = json.loads(text)
    except (binascii.Error, ValueError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return ""
    return text if isinstance(parsed, dict) and len(text) <= MAX_CLAIMS else ""


class Refresh:
    """A new token for ``scopes`` after a 401: ``renewed(scopes)``, or ``renewed(scopes, claims)`` when
    the API sent a claims challenge (auth.Authenticator.refreshed_token)."""

    takes_claims = True

    def __init__(self, renewed: Callable[..., str], scopes: list[str]):
        self.renewed, self.scopes = renewed, scopes

    def __call__(self, claims: str = "") -> str:
        return self.renewed(self.scopes, claims) if claims else self.renewed(self.scopes)


def renew(refresh: Callable[..., str], response: Any) -> str:
    """A new token after the 401 ``response``: with its claims challenge when it sent one and
    ``refresh`` can take it, otherwise as before."""
    claims = challenge(getattr(response, "headers", None))
    return refresh(claims) if claims and getattr(refresh, "takes_claims", False) else refresh()
