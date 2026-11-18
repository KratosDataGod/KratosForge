"""Evidence records (FEA-101): what was seen, about which resource, from where, and how sure it is.

A record::

    {"schema": SCHEMA, "id": "ev-<16 hex>", "kind": "workspace.roles", "subject": {...},
     "source": "GET /v1/workspaces/{id}/roleAssignments", "confidence": "verified_configuration",
     "collected_at": "2026-11-17T23:00:00+00:00", "payload": {...}, "hash": "<sha256 of payload>"}

``subject`` names the resource: ``{"type": "workspace" | "capacity" | "item", "id", "name"}``, plus
``workspace_id`` and ``item_type`` for an item. ``confidence`` is one of:

- ``measured``: observed by a source that recorded it (job history, an imported Capacity Metrics
  export, a run on this PC).
- ``verified_configuration``: read from a definition or configuration API.
- ``inferred``: derived by a heuristic. Advisory: it doesn't count toward a score unless a control
  says it may (controls.Control.accepts_inferred).
- ``insufficient``: something was read, but not enough to decide. A control resting only on this is
  not evaluated.

The payload is run through redact.walk before it is hashed or kept, so a connection string a notebook
carries never reaches a snapshot. The id is built from kind, subject, source and hash, so the same
evidence read twice has the same id and a snapshot can be rescored exactly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .. import redact

SCHEMA = "kratosforge/excellence-evidence@1"
CONFIDENCE = ("measured", "verified_configuration", "inferred", "insufficient")
SCORED = frozenset({"measured", "verified_configuration"})
SUBJECT_TYPES = ("workspace", "capacity", "item")
_STRENGTH = {c: i for i, c in enumerate(CONFIDENCE)}


def canonical(value: Any) -> str:
    """JSON with sorted keys and no spacing: the same value always gives the same text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def subject(kind: str, id_: str, name: str | None = None, *, workspace_id: str | None = None,
            item_type: str | None = None) -> dict[str, Any]:
    """A subject dict, or ValueError. Items need their workspace and type."""
    if kind not in SUBJECT_TYPES:
        raise ValueError(f"unknown subject type {kind!r}")
    if not id_ or not isinstance(id_, str):
        raise ValueError("a subject needs an id")
    out: dict[str, Any] = {"type": kind, "id": id_, "name": name or id_}
    if kind == "item":
        if not workspace_id or not item_type:
            raise ValueError("an item subject needs its workspace_id and item_type")
        out["workspace_id"] = workspace_id
        out["item_type"] = item_type
    return out


def record(kind: str, subj: dict[str, Any], *, source: str, confidence: str, collected_at: str,
           payload: Any) -> dict[str, Any]:
    """One evidence record, or ValueError naming what's missing. Secrets in ``payload`` are removed."""
    if not kind or not isinstance(kind, str):
        raise ValueError("evidence needs a kind")
    if not source or not isinstance(source, str):
        raise ValueError("evidence needs a source")
    if confidence not in CONFIDENCE:
        raise ValueError(f"unknown confidence {confidence!r}")
    if not collected_at or not isinstance(collected_at, str):
        raise ValueError("evidence needs the time it was collected")
    subj = subject(subj.get("type", ""), subj.get("id", ""), subj.get("name"),
                   workspace_id=subj.get("workspace_id"), item_type=subj.get("item_type"))
    clean = redact.walk(payload)
    digest = content_hash(clean)
    ident = "ev-" + hashlib.sha256(canonical([kind, subj["type"], subj["id"], source, digest]).encode()).hexdigest()[:16]
    return {"schema": SCHEMA, "id": ident, "kind": kind, "subject": subj, "source": source,
            "confidence": confidence, "collected_at": collected_at, "payload": clean, "hash": digest}


def problems(rec: Any) -> list[str]:
    """Everything wrong with a stored record; empty when it can be trusted as is."""
    if not isinstance(rec, dict):
        return ["not a record"]
    out = [f"missing {k}" for k in ("schema", "id", "kind", "subject", "source", "confidence", "collected_at",
                                     "payload", "hash") if k not in rec]
    if out:
        return out
    if rec["schema"] != SCHEMA:
        out.append(f"unknown schema {rec['schema']!r}")
    if rec["confidence"] not in CONFIDENCE:
        out.append(f"unknown confidence {rec['confidence']!r}")
    if content_hash(rec["payload"]) != rec["hash"]:
        out.append("payload doesn't match its hash")
    return out


def strongest(records: list[dict[str, Any]]) -> str:
    """The best confidence class among ``records`` (``insufficient`` when there are none)."""
    if not records:
        return "insufficient"
    return min((r["confidence"] for r in records), key=_STRENGTH.__getitem__)


class Bundle:
    """Evidence records indexed by subject id and kind. Records that fail :func:`problems` are kept
    out and listed in ``rejected``, so tampered or malformed evidence can't decide a control."""

    def __init__(self, records: list[dict[str, Any]]):
        self.records: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self._by: dict[tuple[str, str], list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for rec in records:
            bad = problems(rec)
            if bad:
                self.rejected.append({"id": rec.get("id") if isinstance(rec, dict) else None, "problems": bad})
                continue
            if rec["id"] in seen:
                continue
            seen.add(rec["id"])
            self.records.append(rec)
            self._by.setdefault((rec["subject"]["id"], rec["kind"]), []).append(rec)
        self.records.sort(key=lambda r: r["id"])

    def get(self, subject_id: str, kind: str) -> list[dict[str, Any]]:
        return list(self._by.get((subject_id, kind), ()))

    def ids(self) -> set[str]:
        return {r["id"] for r in self.records}
