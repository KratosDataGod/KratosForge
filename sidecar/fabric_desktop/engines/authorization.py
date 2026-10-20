"""Remote compute starts only when someone said so (ADR-003).

A remote session (Fabric Spark, Databricks Connect, a SQL warehouse) can start billable compute.
Its adapter's ``open_session`` takes a ``RemoteAuthorization`` and ``require`` refuses to go on
without a valid one for that exact engine and target. There are two ways to get one:

- ``confirmed``: the person confirmed in the app, for this engine and target, just now.
- ``policy``: the connection profile's execution policy allows this engine without asking
  (``remote_compute: "policy"`` and the engine in ``allowed_remote_engines``).

Nothing here ever picks a remote engine. A failed local run is reported as failed; it doesn't
move to the cloud.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from .capabilities import EngineDescriptor

# How long a confirmation lasts: long enough to run the notebook, short enough not to linger.
CONFIRM_MINUTES = 60
REMOTE_POLICIES = ("ask", "policy", "never")


class RemoteNotAuthorized(PermissionError):
    """A remote session was asked for without a valid authorization."""


@dataclass(frozen=True)
class RemoteAuthorization:
    engine: str
    target: str               # workspace, cluster or warehouse the person approved
    reason: str               # confirmed | policy
    by: str                   # who: the signed-in user, or "policy:<profile>"
    expires_at: dt.datetime

    def as_dict(self) -> dict[str, Any]:
        return {"engine": self.engine, "target": self.target, "reason": self.reason, "by": self.by,
                "expires_at": self.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def confirmed(engine: str, target: str, by: str, now: dt.datetime | None = None) -> RemoteAuthorization:
    """The person confirmed this engine and target in the app."""
    if not target:
        raise ValueError("Say which workspace, cluster or warehouse was approved.")
    return RemoteAuthorization(engine, target, "confirmed", by or "unknown",
                               (now or _now()) + dt.timedelta(minutes=CONFIRM_MINUTES))


def from_policy(policy: dict[str, Any], profile: str, engine: str, target: str,
                now: dt.datetime | None = None) -> RemoteAuthorization | None:
    """A standing authorization from a profile's execution policy, or None when it must ask.

    ``never`` raises: the profile forbids remote compute and asking wouldn't help."""
    mode = (policy or {}).get("remote_compute", "ask")
    if mode not in REMOTE_POLICIES:
        raise ValueError(f"Unknown remote_compute policy {mode!r}. Use one of: {', '.join(REMOTE_POLICIES)}.")
    if mode == "never":
        raise RemoteNotAuthorized(f"The profile {profile!r} doesn't allow remote compute.")
    if mode != "policy" or engine not in (policy.get("allowed_remote_engines") or []):
        return None
    return RemoteAuthorization(engine, target, "policy", f"policy:{profile}",
                               (now or _now()) + dt.timedelta(minutes=CONFIRM_MINUTES))


def require(engine: EngineDescriptor, target: str, authorization: Any,
            now: dt.datetime | None = None) -> RemoteAuthorization | None:
    """Check ``authorization`` before a session opens. Local engines need none."""
    if not engine.remote:
        return None
    if not isinstance(authorization, RemoteAuthorization):
        raise RemoteNotAuthorized(f"{engine.label} runs on remote compute that may be billed. "
                                  "Confirm it first, or allow it in the profile's execution policy.")
    if authorization.engine != engine.id or authorization.target != target:
        raise RemoteNotAuthorized(f"The approval was for {authorization.engine} on {authorization.target}, "
                                  f"not {engine.id} on {target}.")
    if (now or _now()) >= authorization.expires_at:
        raise RemoteNotAuthorized("The approval to use remote compute has expired. Confirm it again.")
    return authorization
