"""Checks before a promote writes to Test or Prod (FD-130): does the person have a workspace role
that can write there, and, when the project asks for it, is someone other than whoever promoted
this version to Test promoting it to Prod.

Fabric and Microsoft Entra stay the source of truth: these checks only read Fabric's role
assignments and the project's own deployment records, so a person learns what's missing before
the promote starts instead of from a refusal halfway through. They are not a security boundary;
Fabric refuses a write the person's role doesn't allow whatever this module says.
"""

from __future__ import annotations

import getpass
import json
import logging
from typing import Any

from .access import WRITE_ROLES, principal
from .redact import describe

_log = logging.getLogger(__name__)


def role_problems(workspace: str, assignments: list[dict[str, Any]] | None, me: set[str],
                  error: str | None = None) -> tuple[list[str], list[str]]:
    """(errors, notes) about whether the person can write to ``workspace``. ``me`` holds their Entra
    object id and sign-in name, lower case.

    Only a role Fabric lists for the person themselves, with no groups that could add more, is an
    error. A role through a group, or a guest whose id differs in this tenant, can't be seen here,
    so those are notes."""
    if error is not None or assignments is None:
        return [], [f"Couldn't read who has access to {workspace} ({error}); Fabric refuses the promote "
                    "if you can't write there."]
    people = [principal(a.get("principal") or {}) | {"role": a.get("role")} for a in assignments]
    mine = [p["role"] for p in people if {str(p["id"] or "").lower(), p["email"].lower()} & me]
    if any(r in WRITE_ROLES for r in mine):
        return [], []
    if not mine:
        return [], [f"Couldn't find you in {workspace}'s access list; if your role there comes through a group "
                    "it isn't listed. Fabric refuses the promote if you can't write there."]
    if any(p["type"] == "Group" for p in people):
        return [], [f"You're a {mine[0]} in {workspace}; promoting needs Contributor or higher, unless a group "
                    "you're in gives you that."]
    admins = sorted(p["name"] for p in people if p["role"] == "Admin" and p["type"] == "User")
    ask = f" Ask one of its Admins ({', '.join(admins[:3])}) to make you a Contributor." if admins else ""
    return [f"You're a {mine[0]} in {workspace}; promoting writes tables there, which needs Contributor or "
            f"higher.{ask}"], []


def separation_problem(source_env: str, target_env: str, version: Any, promoted_by: str | None,
                       me: str | None, promoted_by_id: str | None = None, my_id: str | None = None) -> str | None:
    """With ``separate_promoter = true`` on the target environment: whoever promoted ``version``
    to ``source_env`` may not also promote it to ``target_env``.

    People are compared by their Microsoft Entra object ids when the release record has one and
    this person is signed in (EP-5), since two PCs can share a login name and one person can have
    two. Otherwise by PC login name, as releases recorded before object ids were."""
    if source_env == "dev":
        return None  # nothing was promoted into Dev; the rule is about the second pair of eyes after Test
    if not promoted_by:
        return (f"{target_env.title()} needs someone other than whoever promoted metadata {version} to "
                f"{source_env.title()}, and {source_env.title()}'s record doesn't say who that was. "
                f"Promote it to {source_env.title()} again so the record names them.")
    if promoted_by_id and my_id:
        same = promoted_by_id.lower() == my_id.lower()
    else:
        same = bool(me) and promoted_by.lower() == me.lower()  # type: ignore[union-attr]
    if same:
        return (f"You promoted metadata {version} to {source_env.title()}, so someone else has to promote it to "
                f"{target_env.title()} (separate_promoter in environments.toml).")
    return None


def separation_note(promoted_by_id: str | None, my_id: str | None) -> str | None:
    """Says so when the separate-promoter check fell back to PC login names."""
    if promoted_by_id and my_id:
        return None
    why = "you aren't signed in" if promoted_by_id else "its release record has no Microsoft Entra id"
    return ("The separate promoter check compared PC login names, because " + why + ". It's a team check, "
            "not a security boundary: the workspace roles decide who can write.")


def recorded_object_id(receipt: str | None) -> str | None:
    """The promoter's Entra object id in a deployment row's release receipt, if it has one."""
    try:
        oid = (json.loads(receipt or "{}").get("identity") or {}).get("object_id")
    except (ValueError, AttributeError):
        return None
    return oid if isinstance(oid, str) and oid else None


def current_user() -> str | None:
    """The login name a release record's ``by`` holds (meta/release.py records the same)."""
    try:
        return getpass.getuser()
    except Exception as exc:  # noqa: BLE001 - no login name (a service account without one)
        _log.info("No login name for this process: %s", describe(exc))
        return None
