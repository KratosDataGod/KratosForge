"""Incidents: one record per problem, shared by everyone who can read the environment's LH_Meta.

Each environment keeps one document, ``incidents/incidents.json``, in its control area (next to
``agents/`` and the pipeline mailbox; a local environment keeps it in its ``.local`` folder). It
is only ever changed with a conditional write: read it with its etag, change it, write it back
only if nobody else wrote in between, otherwise read again and redo the change. So two
evaluators (the agent and the app, or two agents) that see the same failure at the same moment
open one incident, and only the one whose write landed reports it as opened.

Document, contract 1::

    {"contract": 1, "kind": "incidents", "env": "prod", "updated_at": "...Z",
     "incidents": [{
        "id": "inc-...", "key": "run_failed:meta:sales:silver.orders:prod", "kind": "run_failed",
        "workload": "meta:sales:silver.orders", "name": "silver.orders", "env": "prod",
        "title": "...", "text": "...", "evidence": {...}, "evidence_id": "<run id>",
        "state": "open" | "acknowledged" | "resolved",
        "opened_at", "last_seen_at", "occurrences", "opened_by",
        "owner", "acknowledged_by", "acknowledged_at",
        "resolved_at", "resolved_by", "resolution": "recovered" | "resolved" | "cleared",
        "team", "criticality", "classification": {"category", "label", "hint", "retry_helps"},
        "escalated_at", "escalated_to", "notes": [{"at", "by", "text"}],
        "number": "INC-PROD-0042", "source": "detected" | "reported",
        "priority": "P1".."P4", "priority_source": "policy" | "person",
        "events": [{"at", "by", "what", "text"}]}],
     "next_number": 43}

Kinds: ``run_failed``, ``needs_decision``, ``late``, ``objective`` (a workload problem),
``agent_offline`` (an agent that serves the environment stopped reporting), ``cant_evaluate`` (the evaluator couldn't read the environment: a telemetry problem, never a
workload one), and ``reported`` (a person reported it; only a person resolves it). An open incident whose signal is gone is resolved as ``recovered``, but only
after an evaluation that could read the environment. A person can resolve an incident by hand;
it opens again only on new evidence (a different failed run), never on the same one.
Fields may be added within contract 1; readers ignore fields they don't know.

Every incident has a number that stays the same for good (``INC-<ENV>-0001``, counted per
environment; records written before numbers existed get one on the next write, oldest first), a
priority P1 (many people blocked) to P4 (minor), and a timeline of ``events``. A detected
incident's priority follows the workload's criticality (``default_priority``) until a person sets
it; SLA targets per priority are applied when the incidents are read (``sla.py``), not stored.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from typing import Any, Callable

KEY = "incidents/incidents.json"
CONTRACT = 1
KINDS = ("run_failed", "needs_decision", "late", "objective", "agent_offline", "cant_evaluate", "reported")
KIND_LABELS = {"run_failed": "Run failed", "needs_decision": "Needs a decision", "late": "Late",
               "objective": "Objective missed", "agent_offline": "Agent offline", "cant_evaluate": "Can't evaluate",
               "reported": "Reported"}
PRIORITIES = ("P1", "P2", "P3", "P4")
PRIORITY_LABELS = {"P1": "Many people blocked, no workaround", "P2": "A team blocked, or due soon",
                   "P3": "Wrong or slow, has a workaround", "P4": "Minor, or a question"}
KEEP_EVENTS = 100  # per incident: the first is always kept, then the newest
ACTIVE = ("open", "acknowledged")
KEEP_RESOLVED = 200
ATTEMPTS = 8
SEEN_EVERY = 900  # seconds between "still happening" updates of an open incident


class Conflict(RuntimeError):
    """Someone else kept writing the document; nothing was changed."""


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _epoch(text: str | None) -> float:
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def signal_key(kind: str, workload: str | None, env: str) -> str:
    return f"{kind}:{workload or '-'}:{env}"


def _id() -> str:
    return "inc-" + uuid.uuid4().hex[:12]


def default_priority(inc: dict[str, Any]) -> str:
    """The priority a detected incident gets from what it is about, until a person sets one."""
    if inc.get("kind") == "cant_evaluate" or inc.get("env") == "dev":
        return "P4"
    crit = inc.get("criticality") or ""
    if crit == "critical":
        return "P1" if inc.get("kind") == "run_failed" and int(inc.get("occurrences") or 1) >= 3 else "P2"
    return {"high": "P2", "low": "P4"}.get(crit, "P3")


def number_of(env: str, n: int) -> str:
    return f"INC-{env.upper()}-{n:04d}"


def _event(inc: dict[str, Any], at: str, by: str, what: str, text: str = "") -> None:
    events = inc.setdefault("events", [])
    events.append({"at": at, "by": by, "what": what, "text": text[:2000]})
    if len(events) > KEEP_EVENTS:
        del events[1:len(events) - KEEP_EVENTS + 1]


def _follow_policy(inc: dict[str, Any], at: str, by: str) -> None:
    """Move a detected incident to its policy priority, unless a person set one."""
    if inc.get("priority_source") == "person" or inc.get("source") == "reported":
        return
    want = default_priority(inc)
    if inc.get("priority") != want:
        before = inc.get("priority")
        inc.update(priority=want, priority_source="policy")
        if before:
            _event(inc, at, by, "priority", f"{before} → {want} by policy")


def _numbered(doc: dict[str, Any]) -> None:
    """Give every incident without a number one, oldest first, from the document's counter."""
    missing = sorted((i for i in doc["incidents"] if not i.get("number")), key=lambda i: i.get("opened_at") or "")
    if not missing:
        return
    n = int(doc.get("next_number") or 1)
    for inc in missing:
        inc["number"] = number_of(inc.get("env") or doc.get("env") or "", n)
        n += 1
    doc["next_number"] = n


class IncidentLog:
    """The incidents of one environment, over a control backend (create/read/replace with etags)."""

    def __init__(self, backend: Any, env: str, clock: Callable[[], float] = time.time):
        self.backend, self.env, self.clock = backend, env, clock

    # ---- reading

    def _load(self) -> tuple[dict[str, Any], str | None]:
        stored = self.backend.read(KEY)
        if stored is None:
            return {"contract": CONTRACT, "kind": "incidents", "env": self.env, "incidents": []}, None
        doc = json.loads(stored.data or b"{}")
        doc.setdefault("incidents", [])
        return doc, stored.etag

    def list(self, states: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        doc, _ = self._load()
        items = [i for i in doc["incidents"] if not states or i["state"] in states]
        active = sorted((i for i in items if i["state"] in ACTIVE), key=lambda i: i.get("opened_at") or "", reverse=True)
        done = sorted((i for i in items if i["state"] not in ACTIVE), key=lambda i: i.get("resolved_at") or "", reverse=True)
        return active + done

    def active(self) -> list[dict[str, Any]]:
        return self.list(ACTIVE)

    # ---- changing

    def _change(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        """Apply ``fn`` to a fresh copy of the document and write it back only if unchanged since read."""
        for attempt in range(ATTEMPTS):
            doc, etag = self._load()
            result = fn(doc)
            if result is None:
                return None  # nothing to write
            doc["updated_at"] = _iso(self.clock())
            _numbered(doc)
            resolved = [i for i in doc["incidents"] if i["state"] == "resolved"]
            if len(resolved) > KEEP_RESOLVED:
                resolved.sort(key=lambda i: i.get("resolved_at") or "")
                drop = {id(i) for i in resolved[:len(resolved) - KEEP_RESOLVED]}
                doc["incidents"] = [i for i in doc["incidents"] if id(i) not in drop]
            data = json.dumps(doc, indent=1).encode()
            ok = self.backend.create(KEY, data) if etag is None else self.backend.replace(KEY, data, etag)
            if ok:
                return result
            time.sleep(min(0.05 * (attempt + 1), 0.5))
        raise Conflict("The incident list kept changing while it was being updated; try again.")

    def sync(self, signals: list[dict[str, Any]], *, evaluated: bool | set[str] | frozenset[str],
             by: str) -> list[dict[str, Any]]:
        """Bring the document in line with what an evaluation saw.

        ``signals`` are the problems seen now ({kind, workload, name, title, text, evidence,
        evidence_id}). ``evaluated`` says whether the environment could be read: when it couldn't,
        nothing is resolved as recovered. It can also be the set of kinds that were evaluated, so a
        source that couldn't be read (the agent records, say) doesn't recover its incidents. Returns the changes this write made, each
        {"change": "opened" | "reopened" | "recovered", "incident": ...}, for notifying."""
        now = _iso(self.clock())
        checked = (lambda kind: evaluated) if isinstance(evaluated, bool) else (lambda kind: kind in evaluated)

        def apply(doc: dict[str, Any]) -> list[dict[str, Any]] | None:
            changes: list[dict[str, Any]] = []
            touched = False
            seen = set()
            by_key: dict[str, dict[str, Any]] = {}
            for inc in doc["incidents"]:  # newest record per key wins
                if inc["key"] not in by_key or (inc.get("opened_at") or "") >= (by_key[inc["key"]].get("opened_at") or ""):
                    by_key[inc["key"]] = inc
            for s in signals:
                key = signal_key(s["kind"], s.get("workload"), self.env)
                seen.add(key)
                cur = by_key.get(key)
                evidence_id = str(s.get("evidence_id") or "")
                if cur and cur["state"] in ACTIVE:
                    if evidence_id and evidence_id != cur.get("evidence_id"):
                        cur["occurrences"] = int(cur.get("occurrences") or 1) + 1
                        cur.update(evidence_id=evidence_id, evidence=s.get("evidence") or {}, text=s.get("text", ""),
                                   title=s.get("title", cur["title"]), classification=s.get("classification"))
                        _event(cur, now, by, "seen", f"Seen again ({cur['occurrences']} times)")
                        _follow_policy(cur, now, by)
                        touched = True
                    # Seen again: noted at most every few minutes, so a quiet evaluation writes nothing.
                    if self.clock() - _epoch(cur.get("last_seen_at")) >= SEEN_EVERY:
                        cur["last_seen_at"] = now
                        touched = True
                    continue
                if cur and cur["state"] == "resolved" and evidence_id and evidence_id == cur.get("evidence_id"):
                    continue  # resolved by hand on this same evidence: stays resolved
                inc = {"id": _id(), "key": key, "kind": s["kind"], "workload": s.get("workload"),
                       "name": s.get("name") or "", "env": self.env, "title": s.get("title", ""),
                       "text": s.get("text", ""), "evidence": s.get("evidence") or {}, "evidence_id": evidence_id,
                       "state": "open", "opened_at": now, "last_seen_at": now, "occurrences": 1, "opened_by": by,
                       "owner": s.get("owner") or "", "acknowledged_by": None, "acknowledged_at": None,
                       "resolved_at": None, "resolved_by": None, "resolution": None,
                       "team": s.get("team") or "", "criticality": s.get("criticality") or "",
                       "classification": s.get("classification"), "escalated_at": None, "escalated_to": None,
                       "notes": [], "source": "detected"}
                _follow_policy(inc, now, by)
                _event(inc, now, by, "reopened" if cur else "opened", s.get("title", ""))
                doc["incidents"].append(inc)
                by_key[key] = inc
                changes.append({"change": "reopened" if cur else "opened", "incident": inc})
            for key, inc in by_key.items():
                if key in seen or inc["state"] not in ACTIVE or inc["kind"] == "reported":
                    continue
                # A telemetry incident clears once the environment reads again; a workload one
                # recovers only after an evaluation that could read the environment.
                if inc["kind"] == "cant_evaluate" or checked(inc["kind"]):
                    inc.update(state="resolved", resolved_at=now, resolved_by=by,
                               resolution="cleared" if inc["kind"] == "cant_evaluate" else "recovered")
                    _event(inc, now, by, inc["resolution"])
                    changes.append({"change": "recovered", "incident": inc})
            if not changes and not touched:
                return None
            return changes

        return self._change(apply) or []

    def escalate(self, after_seconds: float, to: str, by: str,
                 applies: Callable[[dict[str, Any]], bool] = lambda inc: True) -> list[dict[str, Any]]:
        """Mark every open incident nobody acknowledged within ``after_seconds`` as escalated to
        ``to``, once. Only the write that lands reports it, so two evaluators escalate once."""
        now_t = self.clock()
        now = _iso(now_t)

        def apply(doc: dict[str, Any]) -> list[dict[str, Any]] | None:
            changes = []
            for inc in doc["incidents"]:
                if inc["state"] != "open" or inc.get("escalated_at") or inc["kind"] == "cant_evaluate":
                    continue
                if now_t - _epoch(inc.get("opened_at")) < after_seconds or not applies(inc):
                    continue
                inc.update(escalated_at=now, escalated_to=to)
                inc.setdefault("notes", []).append(
                    {"at": now, "by": by, "text": f"Escalated to {to}: not acknowledged in {after_seconds / 60:g} min"})
                _event(inc, now, by, "escalated", f"To {to}: not acknowledged in {after_seconds / 60:g} min")
                changes.append({"change": "escalated", "incident": inc})
            return changes or None

        return self._change(apply) or []

    def _manual(self, incident_id: str, fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        def apply(doc: dict[str, Any]) -> dict[str, Any]:
            inc = next((i for i in doc["incidents"] if i["id"] == incident_id), None)
            if inc is None:
                raise LookupError("That incident isn't in the list any more.")
            fn(inc)
            return inc
        return self._change(apply)

    def acknowledge(self, incident_id: str, by: str, note: str = "") -> dict[str, Any]:
        now = _iso(self.clock())

        def fn(inc: dict[str, Any]) -> None:
            if inc["state"] == "resolved":
                raise ValueError("It's resolved already.")
            inc.update(state="acknowledged", acknowledged_by=by, acknowledged_at=now)
            _event(inc, now, by, "acknowledged", note)
            if note:
                inc.setdefault("notes", []).append({"at": now, "by": by, "text": note[:2000]})
        return self._manual(incident_id, fn)

    def assign(self, incident_id: str, owner: str, by: str) -> dict[str, Any]:
        now = _iso(self.clock())

        def fn(inc: dict[str, Any]) -> None:
            before = inc.get("owner") or "nobody"
            inc["owner"] = owner.strip()
            inc.setdefault("notes", []).append({"at": now, "by": by, "text": f"Assigned: {before} → {owner.strip() or 'nobody'}"})
            _event(inc, now, by, "assigned", f"{before} → {owner.strip() or 'nobody'}")
        return self._manual(incident_id, fn)

    def note(self, incident_id: str, text: str, by: str) -> dict[str, Any]:
        now = _iso(self.clock())

        def fn(inc: dict[str, Any]) -> None:
            inc.setdefault("notes", []).append({"at": now, "by": by, "text": text[:2000]})
            _event(inc, now, by, "note", text)
        return self._manual(incident_id, fn)

    def resolve(self, incident_id: str, by: str, note: str = "") -> dict[str, Any]:
        now = _iso(self.clock())

        def fn(inc: dict[str, Any]) -> None:
            if inc["state"] == "resolved":
                raise ValueError("It's resolved already.")
            inc.update(state="resolved", resolved_at=now, resolved_by=by, resolution="resolved")
            _event(inc, now, by, "resolved", note)
            if note:
                inc.setdefault("notes", []).append({"at": now, "by": by, "text": note[:2000]})
        return self._manual(incident_id, fn)

    def set_priority(self, incident_id: str, priority: str, by: str, note: str) -> dict[str, Any]:
        """A person sets the priority, with a reason; policy no longer moves it."""
        if priority not in PRIORITIES:
            raise ValueError(f"Priority is one of {', '.join(PRIORITIES)}.")
        if not note.strip():
            raise ValueError("Say why the priority changes.")
        now = _iso(self.clock())

        def fn(inc: dict[str, Any]) -> None:
            if inc["state"] == "resolved":
                raise ValueError("It's resolved already.")
            before = inc.get("priority") or default_priority(inc)
            inc.update(priority=priority, priority_source="person")
            _event(inc, now, by, "priority", f"{before} → {priority}: {note.strip()}")
        return self._manual(incident_id, fn)

    def report(self, title: str, by: str, *, text: str = "", priority: str = "P3", workload: str | None = None,
               name: str = "", owner: str = "", team: str = "", criticality: str = "") -> dict[str, Any]:
        """A person reports a problem Fabric Desktop can't see (wrong numbers in a report, say).
        It opens like any incident but never recovers by itself: only a person resolves it."""
        title = " ".join(title.split())
        if not title:
            raise ValueError("Say what's wrong.")
        if priority not in PRIORITIES:
            raise ValueError(f"Priority is one of {', '.join(PRIORITIES)}.")
        now = _iso(self.clock())
        inc_id = _id()
        inc = {"id": inc_id, "key": signal_key("reported", inc_id, self.env), "kind": "reported",
               "workload": workload or None, "name": name, "env": self.env, "title": title[:200], "text": text[:4000],
               "evidence": {}, "evidence_id": "", "state": "open", "opened_at": now, "last_seen_at": now,
               "occurrences": 1, "opened_by": by, "owner": owner.strip(), "acknowledged_by": None,
               "acknowledged_at": None, "resolved_at": None, "resolved_by": None, "resolution": None,
               "team": team, "criticality": criticality, "classification": None, "escalated_at": None,
               "escalated_to": None, "notes": [], "source": "reported", "priority": priority,
               "priority_source": "person"}
        _event(inc, now, by, "reported", title[:200])

        def apply(doc: dict[str, Any]) -> dict[str, Any]:
            doc["incidents"].append(dict(inc, events=list(inc["events"])))
            _numbered(doc)
            return doc["incidents"][-1]
        return self._change(apply)
