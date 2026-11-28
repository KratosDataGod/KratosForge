"""Who made an optimization: the signed-in token's tenant id and Entra object id (decision D3).

Decision D3 (Chris, 2026-11-27, with Security QC's conditions): a savings record names the person who
made a change by the pair ``{"tid", "oid"}`` of the app's own sign-in token, and never by email, UPN
or name.

- **Labels, not proof and not authority.** The ids are pseudonymous labels. The token's signature
  isn't checked here, and ``fdopt_`` rows can be edited by anyone who can write the lakehouse, so a
  pair on a row says who the app was signed in as, nothing more. It is never used for an access
  decision, and nothing presents it as proof of who did something: proof belongs in the audit trail.
- **Pseudonymous, not anonymous.** An object id finds the person in the tenant's directory. While
  contributor recognition is off, the ids are left out of exports, logs and support bundles (and
  share backups); nothing here logs them.
- **One source.** ``from_token`` keeps only ``tid`` and ``oid`` from the payload of the app's own
  Fabric token and drops every other claim (upn, email, name, ipaddr ...): they are never returned,
  stored or logged. ``current`` reads that token through the existing sign-in (``auth.Authenticator.
  silent_token``, the Fabric CLI app and Windows sign-in, WAM). Never request parameters, imported
  files, synced rows or the UI. No token, or offline, gives None; there is no fallback to the Windows
  user name or the machine name.
- **Strict format.** Both ids must be canonical lowercase GUIDs; anything else is refused, not
  repaired (uppercase is refused, not lowercased). The pair is the key: ``key`` and ``same`` compare
  (tid, oid), never the object id alone, since an object id is only unique within its tenant.
- **Every write and import path** goes through ``check``: None, or exactly ``{"tid", "oid"}``. A
  value with ``@`` or anything that looks like an email or UPN, an extra field, or an object id
  without its tenant id is refused with a message that never repeats the value.

Pure apart from ``current``, which asks the sign-in it is given for a cached token (no prompt).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

_log = logging.getLogger(__name__)

TID, OID = "tid", "oid"
KEYS = frozenset({TID, OID})
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")  # base64url, unpadded, as a JWT carries it
MAX_TOKEN = 64 * 1024  # characters; a sign-in token is a few KB
# Text that looks like an email or UPN: user@domain, its URL-encoded form, or DOMAIN\user.
_PERSON_MARKS = ("@", "%40", "\\")


def _guid(value: Any) -> bool:
    return isinstance(value, str) and _GUID.fullmatch(value) is not None


def from_token(access_token: str) -> dict[str, str] | None:
    """``{"tid", "oid"}`` from the payload of a JWT, or None when the token is missing, malformed or
    lacks either claim, or either isn't a canonical lowercase GUID.

    Only the payload is base64url-decoded: the signature isn't checked and nothing is fetched.
    Every other claim is dropped. Never raises and never logs, so no token text can leak."""
    if not isinstance(access_token, str) or not access_token or len(access_token) > MAX_TOKEN:
        return None
    parts = access_token.split(".")
    if len(parts) != 3 or not _SEGMENT.fullmatch(parts[1]):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, binascii.Error, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(claims, dict):
        return None
    tid, oid = claims.get(TID), claims.get(OID)
    del claims  # nothing else from the token is kept
    if not (_guid(tid) and _guid(oid)):
        return None
    return {TID: str(tid), OID: str(oid)}


def current(credential: Any) -> dict[str, str] | None:
    """The signed-in person's ``{"tid", "oid"}``, or None.

    ``credential``: the app's sign-in (``auth.Authenticator``, which the service holds as ``auth``).
    Its ``silent_token(FABRIC_SCOPES)`` gives the cached Fabric token without a prompt; None, an
    error (offline, signed out) or an app identity (the headless agent's service principal or
    managed identity, which is not a person) gives None. Never the Windows user or machine name."""
    if credential is None:
        return None
    from ..auth import FABRIC_SCOPES, Authenticator
    if not isinstance(credential, Authenticator):
        return None  # an app identity: its object id names an app, not the person who made the change
    try:
        token = credential.silent_token(FABRIC_SCOPES)
    except Exception as exc:  # noqa: BLE001 - offline or signed out means no identity, not a failed change
        _log.info("No sign-in token for the responsible person (%s); the record names none", type(exc).__name__)
        return None
    return from_token(token) if isinstance(token, str) else None


def _looks_like_person(value: Any, depth: int = 0) -> bool:
    if depth > 8:
        return True  # nothing legitimate nests this deep; refuse rather than walk it
    if isinstance(value, str):
        return any(mark in value for mark in _PERSON_MARKS)
    if isinstance(value, Mapping):
        return any(_looks_like_person(k, depth + 1) or _looks_like_person(v, depth + 1) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_looks_like_person(v, depth + 1) for v in value)
    return False


def check(value: Any) -> dict[str, str] | None:
    """None, or a copy of ``{"tid", "oid"}`` with both canonical lowercase GUIDs; ValueError otherwise.

    For every path that writes or imports a responsible person. The message never repeats the value."""
    if value is None:
        return None
    if _looks_like_person(value):
        raise ValueError("The responsible person looks like an email address or UPN. Records name a person only "
                         "by the sign-in token's tenant id and object id (D3); an email or UPN is never stored.")
    if not isinstance(value, Mapping):
        raise ValueError("The responsible person is a pair of tenant id and object id (tid, oid) from the sign-in "
                         "token, or none.")
    keys = {str(k) for k in value}
    if OID in keys and TID not in keys:
        raise ValueError("The responsible person has an object id without its tenant id. An object id is only "
                         "unique within its tenant, so both are needed.")
    if keys != KEYS or len(value) != len(KEYS):
        raise ValueError("The responsible person holds exactly tid and oid from the sign-in token, and no other "
                         "field.")
    if not (_guid(value[TID]) and _guid(value[OID])):
        raise ValueError("The responsible person's tid and oid must each be a GUID in canonical lowercase form "
                         "(8-4-4-4-12 hex digits).")
    return {TID: str(value[TID]), OID: str(value[OID])}


def key(value: Any) -> tuple[str, str] | None:
    """The (tid, oid) pair to compare or group by, never the object id alone; None for nobody."""
    pair = check(value)
    return None if pair is None else (pair[TID], pair[OID])


def same(a: Any, b: Any) -> bool:
    """Whether two responsible values name the same person: the same (tid, oid) pair.
    Two Nones are the same (no person recorded on either); a pair and None are not."""
    return key(a) == key(b)
