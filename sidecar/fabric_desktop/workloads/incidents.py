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
        "notes": [{"at", "by", "text"}]}]}

Kinds: ``run_failed``, ``needs_decision``, ``late``, ``objective`` (a workload problem), and
``cant_evaluate`` (the evaluator couldn't read the environment: a telemetry problem, never a
workload one). An open incident whose signal is gone is resolved as ``recovered``, but only
after an evaluation that could read the environment. A person can resolve an incident by hand;
it opens again only on new evidence (a different failed run), never on the same one.
Fields may be added within contract 1; readers ignore fields they don't know.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from typing import Any, Callable

KEY = "incidents/incidents.json"
CONTRACT = 1
KINDS = ("run_failed", "needs_decision", "late", "objective", "cant_evaluate")
KIND_LABELS = {"run_failed": "Run failed", "needs_decision": "Needs a decision", "late": "Late",
               "objective": "Objective missed", "cant_evaluate": "Can't evaluate"}
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

    def sync(self, signals: list[dict[str, Any]], *, evaluated: bool, by: str) -> list[dict[str, Any]]:
        """Bring the document in line with what an evaluation saw.

        ``signals`` are the problems seen now ({kind, workload, name, title, text, evidence,
        evidence_id}). ``evaluated`` says whether the environment could be read: when it couldn't,
        nothing is resolved as recovered. Returns the changes this write made, each
        {"change": "opened" | "reopened" | "recovered", "incident": ...}, for notifying."""
        now = _iso(self.clock())

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
                                   title=s.get("title", cur["title"]))
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
                       "resolved_at": None, "resolved_by": None, "resolution": None, "notes": []}
                doc["incidents"].append(inc)
                by_key[key] = inc
                changes.append({"change": "reopened" if cur else "opened", "incident": inc})
            for key, inc in by_key.items():
                if key in seen or inc["state"] not in ACTIVE:
                    continue
                # A telemetry incident clears once the environment reads again; a workload one
                # recovers only after an evaluation that could read the environment.
                if inc["kind"] == "cant_evaluate" or evaluated:
                    inc.update(state="resolved", resolved_at=now, resolved_by=by,
                               resolution="cleared" if inc["kind"] == "cant_evaluate" else "recovered")
                    changes.append({"change": "recovered", "incident": inc})
            if not changes and not touched:
                return None
            return changes

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
            if note:
                inc.setdefault("notes", []).append({"at": now, "by": by, "text": note[:2000]})
        return self._manual(incident_id, fn)

    def assign(self, incident_id: str, owner: str, by: str) -> dict[str, Any]:
        now = _iso(self.clock())

        def fn(inc: dict[str, Any]) -> None:
            before = inc.get("owner") or "nobody"
            inc["owner"] = owner.strip()
            inc.setdefault("notes", []).append({"at": now, "by": by, "text": f"Assigned: {before} → {owner.strip() or 'nobody'}"})
        return self._manual(incident_id, fn)

    def note(self, incident_id: str, text: str, by: str) -> dict[str, Any]:
        now = _iso(self.clock())
        return self._manual(incident_id, lambda inc: inc.setdefault("notes", []).append(
            {"at": now, "by": by, "text": text[:2000]}))

    def resolve(self, incident_id: str, by: str, note: str = "") -> dict[str, Any]:
        now = _iso(self.clock())

        def fn(inc: dict[str, Any]) -> None:
            if inc["state"] == "resolved":
                raise ValueError("It's resolved already.")
            inc.update(state="resolved", resolved_at=now, resolved_by=by, resolution="resolved")
            if note:
                inc.setdefault("notes", []).append({"at": now, "by": by, "text": note[:2000]})
        return self._manual(incident_id, fn)
