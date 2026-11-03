"""Requests that make something in Fabric or the platform, mixed into ``Service``: a new workspace,
access to a workspace, a domain, and an exception to a governance policy.

The platform (core.py) checks a request when it's sent and records the decision. Approving one of
these makes what was asked for first, as the person approving, and only then approves it:

    workspace         creates the workspace on its capacity (or puts the one this request made back
                      on it), then makes the requested people and groups its admins
    workspace_access  gives the person or group the role in that workspace
    domain            creates the domain (or subdomain) in Fabric and keeps it in the platform linked to
                      it (platform_create_domain); one whose parent isn't in Fabric is saved in the
                      platform only. A retry finds a domain the platform already has, or one in
                      Fabric whose description carries this request's marker (#1042).
    exception         records the exception in the platform's catalog (core, through policy.py)
    data              nothing to make: accepting says the producing team will build the product or
                      change, and the note says how and when

If a step fails, the request stays waiting and the error names the step. What a workspace request
made is recorded on the request (record_request_progress) and in the workspace's description, so a
retry finds it instead of making it twice. Declining or withdrawing a request whose approval made
something before a later step failed changes nothing in Fabric: the answer's ``next`` says what is
still there, for the person to remove if it isn't wanted (left_in_fabric). Nothing here posts anything
but governance's own notices.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .. import access as access_mod
from ..errors import NotSetUp
from ..redact import describe
from . import core

_log = logging.getLogger(__name__)

NEW_KINDS = ("workspace", "workspace_access", "domain", "exception", "data")

# Fabric's longest description for what an approval makes: a workspace, and a domain or an item (Copy job)
WORKSPACE_DESCRIPTION, DOMAIN_DESCRIPTION, ITEM_DESCRIPTION = 4000, 256, 256
_MARKER_WORDS = re.compile(r"kratosforge\s*request", re.I)


def request_marker(req: dict[str, Any]) -> str:
    """What an approval puts in the description of what it makes, so approving again finds it."""
    return f"(KratosForge request {req['id']})"


def marked(typed: str, mark: str, limit: int) -> str:
    """A description for Fabric: the typed text, cut to fit ``limit`` with ``mark`` kept whole at its end.
    The typed text never keeps marker words, so no request's marker can be planted in it (#1069)."""
    while _MARKER_WORDS.search(typed):  # until none is left, so removing one can't leave another behind
        typed = _MARKER_WORDS.sub("", typed)
    typed = typed.strip()[:max(0, limit - len(mark) - 1)].rstrip()
    return f"{typed} {mark}".strip()
REPAIR = "After an update, a platform admin runs Platform > Settings > Finish or repair once."


class StepError(RuntimeError):
    """A step of carrying out a request failed: the request is still waiting."""


def _rank(role: str) -> int:
    return core.WORKSPACE_ROLES.index(role)


def _assigned(assignments: Any, who: dict[str, Any]) -> dict[str, Any] | None:
    """The role assignment given directly to ``who`` (a request's principal), if any."""
    found = None
    for r in assignments:
        p = access_mod.principal(r.get("principal") or {})
        if (who.get("id") and str(p["id"]).lower() == who["id"]) or (
                who.get("email") and str(p["email"]).lower() == who["email"]):
            found = r
    return found


def call_or_repair(catalog: Any, op: str, what: str, **kwargs: Any) -> Any:
    """Call a platform operation that came with an update. A UDF_Platform made before it has no such
    function (Fabric answers 404, read as LookupError), or refuses a request kind it doesn't know:
    say what to do instead. Mirrors the shortcut center's accepted-risk calls."""
    missing = f"The platform can't {what} yet: UDF_Platform is older than this app. {REPAIR}"
    try:
        return catalog.call(op, **kwargs)
    except NotSetUp:
        raise  # its own words say what to do; Finish or repair wouldn't help
    except LookupError as exc:
        _log.info("%s: %s", op, describe(exc))
        raise LookupError(missing) from exc
    except core.PlatformError as exc:
        text = str(exc)
        if (op in text and ("not found" in text.lower() or "no operation" in text.lower())) or "kind must be one of" in text:
            _log.info("%s: %s", op, describe(exc))
            raise LookupError(missing) from exc
        raise


def left_in_fabric(req: dict[str, Any]) -> list[str]:
    """What approving this request made in Fabric before a later step failed (its ``detail.made``), as
    sentences saying it is still there. Declining or withdrawing deletes nothing: that stays the person's call."""
    d = req.get("detail") or {}
    made = d.get("made") or {}
    kind, out = req.get("kind"), []
    if kind == "workspace" and made.get("workspace_id"):
        name = d.get("name") or req.get("target") or made["workspace_id"]
        out.append(f"The workspace {name}, made by this request, is still in Fabric on its capacity: "
                   "delete it there if it isn't wanted")
    elif kind == "workspace_access" and made.get("assigned") and made.get("change") != "kept":
        who = d.get("principal") or {}
        label = who.get("name") or who.get("email") or who.get("id") or "They"
        whom = f"The group {label}" if who.get("type") == "Group" else label
        ws = d.get("workspace_name") or d.get("workspace_id") or "the workspace"
        role = d.get("role") or "role"
        if made.get("change") == "gave":
            out.append(f"{whom} keeps the {role} role in {ws} that approving this request gave: remove it in the "
                       "workspace's Manage access in Fabric if it isn't wanted")
        elif made.get("change") == "raised" and made.get("from"):
            out.append(f"{whom} keeps the {role} role in {ws} that approving this request raised from "
                       f"{made['from']}: set it back in the workspace's Manage access in Fabric if it isn't wanted")
        else:
            # The same role was there (they had it, or a retry found what this approval gave), or a request
            # recorded before #1207: say both, so no one removes access a person had before asking.
            higher = " or a higher one" if made.get("change") != "same" else ""
            out.append(f"{whom} has the {role} role{higher} in {ws}: they had it before this request, or approving it "
                       "gave it. Check which before removing it in the workspace's Manage access in Fabric")
    elif kind == "source" and made.get("copy_job_id"):
        name = made.get("copy_job_name") or made["copy_job_id"]
        runs = " and its daily schedule still runs it" if made.get("schedule_id") else ""
        out.append(f"The Copy job {name}, made by this request, is still in the landing workspace{runs}: "
                   "delete it there if it isn't wanted")
    elif kind == "shortcut" and made.get("shortcut_id"):
        name, lakehouse = d.get("name") or req.get("target"), d.get("lakehouse_name") or "its lakehouse"
        if made.get("found"):  # it was there already: possibly someone else's, so no removal advice
            out.append(f"The shortcut {name} in {lakehouse} is in OneLake: it was there before this request, or "
                       "approving it made it. Check which before removing it on OneLake > Shortcuts")
        else:
            out.append(f"The shortcut {name} in {lakehouse}, made by this request, is still in OneLake: remove it on "
                       "OneLake > Shortcuts if it isn't wanted")
    elif kind == "domain" and made.get("domain_id"):
        name = d.get("name") or req.get("target") or made["domain_id"]
        if made.get("found"):  # possibly someone else's: no removal advice
            out.append(f"The domain {name} is in the platform{' and Fabric' if made.get('fabric_domain_id') else ''}: "
                       "it was there before this request, or approving it made it. Check which before deleting it")
        elif made.get("fabric_domain_id"):
            out.append(f"The domain {name}, made by this request, is still in Fabric and the platform: delete it in "
                       "Fabric's admin portal, then on Data mesh > Domains, if it isn't wanted")
        else:
            out.append(f"The domain {name}, made by this request, is still in the platform: delete it on "
                       "Data mesh > Domains if it isn't wanted")
    return out


class RequestsMixin:

    def _rq_fabric(self) -> Any:
        """Fabric as the signed-in person: the one approving."""
        return self.fabric  # type: ignore[attr-defined]

    def platform_request(self, kind: str, target: str, reason: str = "", detail: dict[str, Any] | None = None,
                         project: str = "") -> dict[str, Any]:
        if kind not in NEW_KINDS:
            return super().platform_request(kind, target, reason, detail, project)  # type: ignore[misc]
        detail = dict(detail or {})
        if kind == "workspace":
            self._rq_name_free(str(detail.get("name") or target))
        try:
            return super().platform_request(kind, target, reason, detail, project)  # type: ignore[misc]
        except core.PlatformError as exc:
            if "kind must be one of" in str(exc):
                _log.info("request %s: %s", kind, describe(exc))
                raise LookupError(f"The platform can't take this kind of request yet: UDF_Platform is older "
                                  f"than this app. {REPAIR}") from exc
            raise

    def platform_requests(self, status: str = "") -> list[dict[str, Any]]:
        """Each waiting workspace access request the viewer can decide says the role its person or group
        has in that workspace today (``today``: ``{"role": "Admin"}``, ``{"role": ""}`` for none of its
        own, or ``{"checked": False}``), read from Fabric as the viewer once per workspace and never
        stored, so the approver sees whether approving changes anything (#1108)."""
        out = super().platform_requests(status)  # type: ignore[misc]
        asks = [r for r in out if r.get("kind") == "workspace_access" and r.get("status") == "waiting"
                and r.get("can_decide")]
        roles: dict[str, list[Any] | None] = {}
        for r in asks:
            d = r.get("detail") or {}
            wid = str(d.get("workspace_id") or "")
            if wid not in roles:
                try:
                    roles[wid] = list(self._rq_fabric().list_role_assignments(wid))
                except Exception as exc:  # noqa: BLE001 - the row says it couldn't check; approving still works
                    _log.info("Couldn't read the roles in %s: %s", wid, describe(exc))
                    roles[wid] = None
            have = roles[wid]
            if have is None:
                r["today"] = {"checked": False}
            else:
                found = _assigned(have, d.get("principal") or {})
                r["today"] = {"role": str((found or {}).get("role") or "")}
        return out

    def _rq_name_free(self, name: str) -> None:
        """Refuse a new workspace whose name is taken, as far as the person asking can see."""
        try:
            taken = {str(w.get("displayName") or "").lower() for w in self._rq_fabric().list_workspaces()}
        except Exception as exc:  # noqa: BLE001 - the approver's check still stops a duplicate
            _log.info("Couldn't list workspaces to check %s is free: %s", name, describe(exc))
            return
        if name.strip().lower() in taken:
            raise core.PlatformError(f"A workspace named {name.strip()} already exists. Ask for access to it, "
                                     "or pick another name.")

    def platform_decide(self, id: str, approve: bool, note: str = "", capacity_id: str = "") -> dict[str, Any]:
        """Approving a workspace, workspace access or domain request makes it first, as the person
        approving. ``capacity_id`` is for a workspace request sent without one: the approver picks it."""
        if not approve:
            return self._rq_closed(super().platform_decide(id, approve, note))  # type: ignore[misc]
        req = next((r for r in self._catalog().call("list_requests", status="waiting")  # type: ignore[attr-defined]
                    if r["id"] == id), None)
        if req is None or req["kind"] not in NEW_KINDS:
            return super().platform_decide(id, approve, note)  # type: ignore[misc]
        if not req.get("can_decide"):
            raise PermissionError("Only a platform admin can approve this request")
        made: dict[str, Any] = {}
        if req["kind"] == "workspace":
            made = self._rq_make_workspace(req, capacity_id)
        elif req["kind"] == "workspace_access":
            made = self._rq_give_access(req)
        elif req["kind"] == "domain":
            made = self._rq_make_domain(req)
        out = super().platform_decide(id, approve, note)  # type: ignore[misc]
        if made.get("next"):
            out["next"] = made["next"]
        out["made"] = {k: v for k, v in made.items() if k != "next"}
        return out

    def platform_withdraw(self, id: str) -> dict[str, Any]:
        return self._rq_closed(super().platform_withdraw(id))  # type: ignore[misc]

    def _rq_closed(self, out: dict[str, Any]) -> dict[str, Any]:
        """A declined or withdrawn request says what its approval already made in Fabric. A domain
        request whose domain is saved but wasn't recorded on it (its progress record failed, or someone
        made it since) says it is there, with the careful wording: whose it is isn't known (#1270)."""
        made, view = (out.get("detail") or {}).get("made") or {}, out
        if out.get("kind") == "domain" and not made.get("domain_id"):
            try:
                have = next((x for x in self.platform_domains() if x.get("id") == out.get("target")), None)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - the request is closed either way; this only adds a note
                _log.info("Couldn't read the domains after closing %s: %s", out.get("id"), describe(exc))
                have = None
            if have:  # what the request records is left as it is: this only words the note
                view = dict(out, detail=dict(out.get("detail") or {}, made={
                    "domain_id": have["id"], "found": True,
                    **({"fabric_domain_id": have["fabric_domain"]} if have.get("fabric_domain") else {})}))
        left = left_in_fabric(view)
        if left:
            out["next"] = [*(out.get("next") or []), *left]
        return out

    # ---- carrying out each kind

    @staticmethod
    def _rq_step(step: str, run: Any) -> Any:
        try:
            return run()
        except Exception as exc:  # named and re-raised: the request stays waiting
            _log.info("%s failed: %s", step, describe(exc))
            raise StepError(f"{step} failed, so the request is still waiting: {describe(exc)}") from exc

    def _rq_progress(self, req: dict[str, Any], made: dict[str, Any]) -> None:
        self._rq_step("Record what was made", lambda: call_or_repair(
            self._catalog(), "record_request_progress", "carry out this request",  # type: ignore[attr-defined]
            id=req["id"], made=made))

    def _rq_make_domain(self, req: dict[str, Any]) -> dict[str, Any]:
        """Create the domain in Fabric as the approver, then keep it in the platform, as New domain
        with Also create it in Fabric does. A subdomain whose parent isn't in Fabric can't be there
        either, so it is saved in the platform only. A retry after a partial approval finds it."""
        d = req["detail"]
        name = str(d.get("name") or req["target"])
        known = {x["id"]: x for x in self.platform_domains()}  # type: ignore[attr-defined]
        if req["target"] in known:
            if not (d.get("made") or {}).get("domain_id"):
                self._rq_domain_found(req, known[req["target"]], known)
            return {}
        parent = known.get(str(d.get("parent_id") or ""))
        in_fabric = not parent or bool(parent.get("fabric_domain"))
        domain = {"id": req["target"], "name": name, "parent_id": d.get("parent_id") or "",
                  "description": d.get("description") or req.get("reason") or "", "owners": d.get("owners") or ""}

        def create() -> Any:
            try:
                return self._create_domain(domain, in_fabric, marker)  # type: ignore[attr-defined]
            except PermissionError as exc:
                raise PermissionError(f"{exc} A Fabric administrator can approve this request, or you can create "
                                      f"{name} in the platform only (New domain on Data mesh > Domains, without Also "
                                      "create it in Fabric) and approve it again.") from exc

        step = f"Create the domain {name} in Fabric" if in_fabric else f"Save the domain {name}"
        marker = request_marker(req)
        saved, adopted = self._rq_step(step, create)
        fabric_id = next((str(x.get("fabric_domain") or "") for x in saved or [] if x.get("id") == req["target"]), "")
        self._rq_progress(req, {"domain_id": req["target"], **({"fabric_domain_id": fabric_id} if fabric_id else {})})
        if adopted:
            return {"next": [f"{name} already existed in Fabric from this request, so it was kept"]}
        if in_fabric or parent is None:
            return {}
        return {"next": [f"{parent['name']} isn't in Fabric, so {name} was saved in the platform only. Create "
                         f"{parent['name']} and {name} in Fabric's admin portal, then choose Import from Fabric"]}

    def _rq_domain_found(self, req: dict[str, Any], have: dict[str, Any], known: dict[str, Any]) -> None:
        """A retry finds the domain already saved but not recorded on the request: its progress record
        failed, or someone made it since the request was sent (#1270). It's this request's when Fabric's
        domain carries the request's marker, or, for one in the platform only, the approver saved it
        after the request; otherwise it is recorded as found, so a decline says to check before deleting."""
        name = str(req["detail"].get("name") or req["target"])
        fid = str(have.get("fabric_domain") or "")
        if fid:
            parent_fid = str((known.get(str(have.get("parent_id") or "")) or {}).get("fabric_domain") or "")
            try:
                ours = self._domain_made_by(self._fabric_admin(), request_marker(req), name,  # type: ignore[attr-defined]
                                            parent_fid) is not None
            except Exception as exc:  # noqa: BLE001 - unread is recorded as found: the careful wording
                _log.info("Couldn't read Fabric's domains to tell who made %s: %s", name, describe(exc))
                ours = False
        else:
            try:
                me = str(self._catalog().call("whoami").get("user") or "").lower()  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - unread is recorded as found, as for Fabric above
                _log.info("Couldn't tell who is approving %s: %s", name, describe(exc))
                me = ""
            ours = bool(me) and str(have.get("updated_by") or "").lower() == me \
                and str(have.get("updated_at") or "") >= str(req.get("requested_at") or "")
        self._rq_progress(req, {"domain_id": req["target"], **({"fabric_domain_id": fid} if fid else {}),
                                **({} if ours else {"found": True})})

    def _rq_make_workspace(self, req: dict[str, Any], capacity_id: str = "") -> dict[str, Any]:
        d, api = req["detail"], self._rq_fabric()
        made = dict(d.get("made") or {})
        name = d["name"]
        capacity = (d.get("capacity_id") or capacity_id or "").strip()
        if not capacity:
            raise core.PlatformError("Pick a capacity for the workspace: the request didn't name one")
        marker = request_marker(req)

        def find_or_create() -> tuple[dict[str, Any], bool]:
            same = [w for w in api.list_workspaces() if str(w.get("displayName") or "").lower() == name.lower()]
            ours = next((w for w in same if w.get("id") == made.get("workspace_id")
                         or marker in str(w.get("description") or "")), None)
            if ours:
                return ours, False
            if same:
                raise core.PlatformError(f"A workspace named {name} already exists and this request didn't make "
                                         "it. Decline the request, or ask for another name.")
            why = str(req.get("reason") or "").strip()
            return api.create_workspace(name, capacity, marked(why, marker, WORKSPACE_DESCRIPTION)), True

        ws, created = self._rq_step(f"Create the workspace {name}", find_or_create)
        wid = ws["id"]
        if created or made.get("workspace_id") != wid:
            made["workspace_id"] = wid
            self._rq_progress(req, {"workspace_id": wid})
        if not created and str(ws.get("capacityId") or "").lower() != capacity.lower():
            self._rq_step(f"Put it on {d.get('capacity_name') or 'the capacity'}",
                          lambda: api.assign_to_capacity(wid, capacity))
        made["capacity_id"] = capacity
        done = {p.lower() for p in made.get("admins") or []}
        have: set[str] | None = None
        for a in d.get("admins") or []:
            key = (a.get("id") or a.get("email") or "").lower()
            if key in done:
                continue
            if have is None:
                have = self._rq_step("Read the workspace's roles", lambda: self._rq_admins(api, wid))
            if key not in have:
                self._rq_step(f"Make {a.get('name') or key} an admin", lambda a=a: self._rq_add(api, wid, a, "Admin"))
            done.add(key)
            made["admins"] = sorted(done)
            self._rq_progress(req, {"admins": made["admins"]})
        nxt = []
        if d.get("domain_id"):
            # Putting a workspace in a Fabric domain is a Fabric admin API (Tenant.ReadWrite.All), which
            # KratosForge doesn't ask for: a Fabric administrator does it in the admin portal.
            nxt.append(f"Put it in the {d.get('domain_name') or d['domain_id']} domain in Fabric's admin portal")
        return made | {"next": nxt} if nxt else made

    @staticmethod
    def _rq_admins(api: Any, workspace_id: str) -> set[str]:
        """The workspace's admins now, by object ID and by UPN, lower case."""
        out: set[str] = set()
        for r in api.list_role_assignments(workspace_id):
            if r.get("role") == "Admin":
                p = access_mod.principal(r.get("principal") or {})
                out |= {str(p["id"] or "").lower(), str(p["email"] or "").lower()} - {""}
        return out

    def _rq_add(self, api: Any, workspace_id: str, who: dict[str, Any], role: str) -> None:
        if who.get("type") == "Group":
            api.add_role_assignment(workspace_id, who["id"], "Group", role)
        else:
            self._add_user_by_email(workspace_id, who["email"], role)  # type: ignore[attr-defined]

    def _rq_give_access(self, req: dict[str, Any]) -> dict[str, Any]:
        d, api = req["detail"], self._rq_fabric()
        who, role, wid = d["principal"], d["role"], d["workspace_id"]
        label = who.get("name") or who.get("email") or who.get("id")

        def give() -> tuple[str, str]:
            """What approving did (gave, raised, kept a higher role, or found the same one) and the role before."""
            current = _assigned(api.list_role_assignments(wid), who)
            if current is None:
                self._rq_add(api, wid, who, role)
                return "gave", ""
            have = str(current.get("role") or "")
            # A request asks for access, so approving one never lowers a role: a higher one is kept.
            if have == role:
                return "same", have
            if have in core.WORKSPACE_ROLES and _rank(have) > _rank(role):
                return "kept", have
            api.update_role_assignment(wid, current["id"], role)
            return "raised", have

        change, before = self._rq_step(f"Give {label} the {role} role in {d.get('workspace_name') or wid}", give)
        earlier = (d.get("made") or {}).get("change")
        # A retry finds the role this approval already gave or raised: what it recorded then still holds.
        record = ({"change": earlier, "from": (d.get("made") or {}).get("from", "")} if earlier in ("gave", "raised")
                  else {"change": change, "from": before})
        self._rq_progress(req, {"assigned": True} | record)
        kept = before if change in ("kept", "same") else ""
        if not kept:
            return {"assigned": True}
        whom = f"the group {label}" if who.get("type") == "Group" else label
        # The same role may be this approval's own work on a retry, so that only says where things stand.
        note = f"{whom} has {kept} there" if kept == role else f"{whom} already had {kept}, so it was kept"
        return {"assigned": True, "kept": kept, "next": [note]}
