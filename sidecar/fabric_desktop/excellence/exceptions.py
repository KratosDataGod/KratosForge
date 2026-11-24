"""Accepted risk and exceptions (FEA-404): owner, reason, expiry; never changes the outcome.

An exception records that someone accepts the risk of one failed control, on one resource or on every
resource of that control (``subject`` ``"*"``), until a date:

    {"control_id": "FEA-SEC-003", "subject": "<resource id>" | "*", "owner": "...", "reason": "...",
     "expires": "YYYY-MM-DD", "recorded_at": "YYYY-MM-DD"}

``recorded_at`` may be left out and is then today. ``expires`` is required and falls after the day it
was recorded and at most 365 days after it. :func:`validate` refuses anything else with a ``ValueError``
that names the exception and the problem.

:func:`apply` never changes an outcome, a finding's status, a score, the assured score, coverage, a
rating or the Critical Risk flag. It only adds ``accepted_risk`` (owner, reason, expires, expired) to
the failed outcomes and the findings an exception matches, and ``result["exceptions"]`` with how many
are active and expired, the ones that matched nothing, and the records themselves so a rescore applies
them again. An exception is expired on and after its expiry date, counted against the assessment's
own date so the same snapshot always reads the same; an expired one is still shown, marked expired.

Pure: nothing here reads Fabric or files, and nothing is stored. The caller passes the exceptions in.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import unicodedata
from typing import Any

from . import findings as fx
from .controls import Registry

FIELDS = ("control_id", "subject", "owner", "reason", "expires", "recorded_at")
REQUIRED = ("control_id", "subject", "owner", "reason", "expires")
MAX_TEXT = 256
MAX_DAYS = 365
MAX_EXCEPTIONS = 1000
ALL = "*"
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# Control, format (zero-width, bidi), surrogate, private-use and unassigned characters, and line or
# paragraph separators: invisible or not text, so an id that reads right could match nothing.
_BAD = frozenset(("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))


def _text(i: int, key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"exception {i}: {key} must be text")
    s = value.strip()
    if not s:
        raise ValueError(f"exception {i}: {key} is empty")
    if len(s) > MAX_TEXT:
        raise ValueError(f"exception {i}: {key} is longer than {MAX_TEXT} characters")
    if any(unicodedata.category(ch) in _BAD for ch in s):
        raise ValueError(f"exception {i}: {key} has a control character")
    return s


def _date(i: int, key: str, value: Any) -> dt.date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(f"exception {i}: {key} must be a date written YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"exception {i}: {key} isn't a real date") from None


def check(raw: Any, i: int, registry: Registry, today: dt.date) -> dict[str, str]:
    """One exception, checked and normalized; ``i`` numbers it in messages (from 1)."""
    if not isinstance(raw, dict):
        raise ValueError(f"exception {i}: must be an object")
    unknown = sorted(str(k) for k in raw if k not in FIELDS)
    if unknown:
        raise ValueError(f"exception {i}: unknown field {', '.join(unknown)}")
    missing = [k for k in REQUIRED if k not in raw or raw[k] is None]
    if missing:
        raise ValueError(f"exception {i}: missing {', '.join(missing)}")
    cid = _text(i, "control_id", raw["control_id"])
    if cid not in registry.versions():
        raise ValueError(f"exception {i}: unknown control {cid}")
    subject = _text(i, "subject", raw["subject"])
    owner = _text(i, "owner", raw["owner"])
    reason = _text(i, "reason", raw["reason"])
    recorded = today if raw.get("recorded_at") is None else _date(i, "recorded_at", raw["recorded_at"])
    if recorded > today:
        raise ValueError(f"exception {i}: recorded_at is in the future")
    expires = _date(i, "expires", raw["expires"])
    if expires <= recorded:
        raise ValueError(f"exception {i}: expires must be after the day it was recorded ({recorded.isoformat()})")
    if (expires - recorded).days > MAX_DAYS:
        raise ValueError(f"exception {i}: expires more than {MAX_DAYS} days after it was recorded")
    return {"control_id": cid, "subject": subject, "owner": owner, "reason": reason,
            "expires": expires.isoformat(), "recorded_at": recorded.isoformat()}


def validate(raw: Any, registry: Registry, *, today: dt.date) -> list[dict[str, str]]:
    """The exceptions, checked; the first problem raises ``ValueError``. ``None`` is no exceptions.
    Two exceptions for the same control and resource are refused, so which one counts is never a guess."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("exceptions must be a list")
    if len(raw) > MAX_EXCEPTIONS:
        raise ValueError(f"at most {MAX_EXCEPTIONS} exceptions")
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for i, r in enumerate(raw, 1):
        e = check(r, i, registry, today)
        key = (e["control_id"], e["subject"].casefold())
        if key in seen:
            raise ValueError(f"exception {i}: another exception already covers {e['control_id']} on {e['subject']}")
        seen.add(key)
        out.append(e)
    return out


def _expired(e: dict[str, str], on: dt.date) -> bool:
    return on >= dt.date.fromisoformat(e["expires"])


def _matches(e: dict[str, str], control_id: str, subject_id: str) -> bool:
    return e["control_id"] == control_id and (e["subject"] == ALL or e["subject"].casefold() == subject_id.casefold())


def _failed(o: dict[str, Any]) -> bool:
    return o["outcome"] == "fail" or bool((o.get("advisory") or {}).get("outcome") == "fail")


def _pick(found: list[dict[str, str]], on: dt.date) -> dict[str, str]:
    """When several match: an active one before an expired one, a named resource before ``*``, then the
    latest expiry, then the order given."""
    return min(found, key=lambda e: (_expired(e, on), e["subject"] == ALL, -dt.date.fromisoformat(e["expires"]).toordinal()))


def apply(result: dict[str, Any], exceptions: list[dict[str, str]]) -> dict[str, Any]:
    """A copy of ``result`` with ``exceptions`` (already validated) shown on what they match. Every
    outcome, finding status, score, count and flag stays exactly as it was."""
    return annotate(copy.deepcopy(result), exceptions)


def annotate(out: dict[str, Any], exceptions: list[dict[str, str]]) -> dict[str, Any]:
    """:func:`apply` in place on a result no one else holds (assess builds one). Any earlier
    annotation is replaced, so applying twice reads the same as applying once."""
    for x in (*out["outcomes"], *out["findings"]):
        x.pop("accepted_risk", None)
    on = dt.date.fromisoformat(str(out["assessed_at"])[:10])
    used: set[int] = set()
    by_finding: dict[str, dict[str, Any]] = {}
    for o in out["outcomes"]:
        if not _failed(o):
            continue
        found = [(n, e) for n, e in enumerate(exceptions) if _matches(e, o["control_id"], str(o["subject"]["id"]))]
        if not found:
            continue
        used.update(n for n, _ in found)
        e = _pick([e for _, e in found], on)
        note = {"owner": e["owner"], "reason": e["reason"], "expires": e["expires"], "expired": _expired(e, on)}
        o["accepted_risk"] = note
        by_finding[fx.finding_id(o["control_id"], str(o["subject"]["id"]))] = note
    for f in out["findings"]:
        if f["id"] in by_finding:
            f["accepted_risk"] = dict(by_finding[f["id"]])
    expired = [e for e in exceptions if _expired(e, on)]
    out["exceptions"] = {
        "active": sum(1 for n, e in enumerate(exceptions) if n in used and not _expired(e, on)),
        "expired": len(expired),
        "unmatched": [{"control_id": e["control_id"], "subject": e["subject"]}
                      for n, e in enumerate(exceptions) if n not in used and not _expired(e, on)],
        "records": [dict(e) for e in exceptions],
    }
    return out
