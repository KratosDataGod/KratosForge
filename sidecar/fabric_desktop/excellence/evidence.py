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
import math
from typing import Any

from .. import redact

SCHEMA = "kratosforge/excellence-evidence@1"
CONFIDENCE = ("measured", "verified_configuration", "inferred", "insufficient")
SCORED = frozenset({"measured", "verified_configuration"})
SUBJECT_TYPES = ("workspace", "capacity", "item")
_STRENGTH = {c: i for i, c in enumerate(CONFIDENCE)}
MAX_NAME = 256  # characters kept of a name read from Fabric, so a hostile name can't bloat evidence
MAX_TEXT = 2000  # characters kept of a longer text (a description, a read problem)


def text(value: Any, limit: int = MAX_NAME) -> str:
    """``value`` as text of at most ``limit`` characters (``""`` for None, or when it can't be shown)."""
    if value is None:
        return ""
    try:
        out = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 - junk too deep or broken to print is no name at all
        return ""
    return out if len(out) <= limit else out[:limit - 1] + "…"


def canonical(value: Any) -> str:
    """JSON with sorted keys and no spacing: the same value always gives the same text.

    A mapping whose keys can't be sorted together (``{1: "a", "b": 2}``) or aren't JSON keys (a tuple)
    is written with its keys as JSON writes them, sorted by that text and then by their type's name, so
    it still has one text, and the same one as after a JSON round trip. Any other value keeps the plain
    ``json.dumps`` text, so the hashes of ordinary evidence never change."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    except TypeError:
        return _canonical_mixed(value)


def _json_key(k: Any) -> str:
    if isinstance(k, str):
        return k
    if k is None or isinstance(k, (bool, int, float)):
        return json.dumps(k)  # null, true, 1, 1.5, NaN: as json.dumps writes a key of that type
    return text(k, MAX_TEXT)


def _canonical_mixed(value: Any) -> str:
    if isinstance(value, dict):
        pairs = sorted(((_json_key(k), type(k).__name__, v) for k, v in value.items()), key=lambda p: (p[0], p[1]))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _canonical_mixed(v) for k, _, v in pairs) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_mixed(v) for v in value) + "]"
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    """The SHA-256 (hex) of ``value``'s canonical text. surrogatepass: a lone surrogate (valid in JSON
    text, not in UTF-8) hashes instead of raising."""
    return hashlib.sha256(canonical(value).encode("utf-8", "surrogatepass")).hexdigest()


def ok_id(value: Any) -> bool:
    """Whether ``value`` can be a resource id: text or a number, of at most MAX_NAME characters."""
    return isinstance(value, (str, int)) and not isinstance(value, bool) and 0 < len(text(value, MAX_NAME + 1)) <= MAX_NAME


def content_hash(payload: Any) -> str:
    return digest(payload)


def subject(kind: str, id_: str, name: str | None = None, *, workspace_id: str | None = None,
            item_type: str | None = None) -> dict[str, Any]:
    """A subject dict, or ValueError. Items need their workspace and type."""
    if kind not in SUBJECT_TYPES:
        raise ValueError(f"unknown subject type {kind!r}")
    if not id_ or not isinstance(id_, str):
        raise ValueError("a subject needs an id")
    if len(id_) > MAX_NAME:
        raise ValueError(f"a subject id is at most {MAX_NAME} characters")
    out: dict[str, Any] = {"type": kind, "id": id_, "name": text(name) or text(id_)}
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
    if not isinstance(subj, dict):
        raise ValueError("evidence needs a subject")
    subj = subject(subj.get("type", ""), subj.get("id", ""), subj.get("name"),
                   workspace_id=subj.get("workspace_id"), item_type=subj.get("item_type"))
    clean = redact.walk(payload)
    payload_hash = content_hash(clean)
    ident = "ev-" + digest([kind, subj["type"], subj["id"], source, payload_hash])[:16]
    return {"schema": SCHEMA, "id": ident, "kind": kind, "subject": subj, "source": source,
            "confidence": confidence, "collected_at": collected_at, "payload": clean, "hash": payload_hash}


def problems(rec: Any) -> list[str]:
    """Everything wrong with a stored record; empty when it can be trusted as is."""
    if not isinstance(rec, dict):
        return ["not a record"]
    out = [f"missing {k}" for k in ("schema", "id", "kind", "subject", "source", "confidence", "collected_at",
                                     "payload", "hash") if k not in rec]
    if out:
        return out
    if rec["schema"] != SCHEMA:
        out.append(f"unknown schema {text(rec['schema'], 64)!r}")
    if not isinstance(rec["confidence"], str) or rec["confidence"] not in CONFIDENCE:
        out.append(f"unknown confidence {text(rec['confidence'], 64)!r}")
    out += [f"{k} isn't text" for k in ("id", "kind", "source", "collected_at") if not isinstance(rec[k], str) or not rec[k]]
    subj = rec["subject"]
    if not isinstance(subj, dict) or subj.get("type") not in SUBJECT_TYPES or not isinstance(subj.get("id"), str) \
            or not subj["id"]:
        out.append("subject isn't a workspace, capacity or item with an id")
    try:
        if content_hash(rec["payload"]) != rec["hash"]:
            out.append("payload doesn't match its hash")
    except (ValueError, TypeError, RecursionError):
        out.append("payload can't be read")
    return out


# ---- payload shapes
#
# A catalog says how each kind's payload is shaped (catalog.PAYLOADS): a dict of key -> shape, where a
# shape is a type (``float``: any finite number), None, a tuple (any of), a one-element list (a list
# of), a dict (those keys, each required unless written ``"?key"``; others allowed), a Map (text keys
# to one shape) or a function of the value. A record of a listed kind, with confidence better than
# ``insufficient``, whose payload doesn't fit is set aside by the Bundle like a tampered one, so a
# control never reads a missing list as an empty one (no assignments: "no guests") and is not
# evaluated instead.

class Map:
    """A mapping of text keys to values of one shape (rule id -> its hits)."""

    def __init__(self, value: Any):
        self.value = value


def _fits(v: Any, spec: Any) -> bool:
    if spec is None:
        return v is None
    if isinstance(spec, type):
        if spec is float:  # a number: finite, int or float, never a flag
            return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        return isinstance(v, spec) and (spec is bool or not isinstance(v, bool))
    if isinstance(spec, tuple):  # any of
        return any(_fits(v, s) for s in spec)
    if isinstance(spec, list):  # a list of
        return isinstance(v, list) and all(_fits(x, spec[0]) for x in v)
    if isinstance(spec, Map):
        return isinstance(v, dict) and all(isinstance(k, str) and _fits(x, spec.value) for k, x in v.items())
    if isinstance(spec, dict):
        if not isinstance(v, dict):
            return False
        for key, want in spec.items():
            name = key.lstrip("?")
            if name not in v:
                if key.startswith("?"):
                    continue
                return False
            if not _fits(v[name], want):
                return False
        return True
    return bool(spec(v))  # a check of its own


def fits(payload: Any, spec: Any) -> bool:
    """Whether ``payload`` has the shape ``spec`` (see above)."""
    try:
        return _fits(payload, spec)
    except RecursionError:
        return False


def strongest(records: list[dict[str, Any]]) -> str:
    """The best confidence class among ``records`` (``insufficient`` when there are none)."""
    if not records:
        return "insufficient"
    return min((r["confidence"] for r in records), key=_STRENGTH.__getitem__)


class Bundle:
    """Evidence records indexed by subject id and kind. Records that fail :func:`problems` (tampered or
    malformed), and those whose payload doesn't fit its kind's shape in ``shapes`` (see above), are kept
    out and listed in ``rejected``, so they can't decide a control: the controls that need them are not
    evaluated. Anything but a list is no records."""

    def __init__(self, records: list[dict[str, Any]], shapes: dict[str, Any] | None = None):
        self.records: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self._by: dict[tuple[str, str], list[dict[str, Any]]] = {}
        seen: set[str] = set()
        good = []
        for rec in records if isinstance(records, list) else []:
            bad = problems(rec)
            spec = (shapes or {}).get(rec["kind"]) if not bad and rec["confidence"] != "insufficient" else None
            if spec is not None and not fits(rec["payload"], spec):
                bad = [f"payload isn't shaped as {text(rec['kind'], 64)} evidence"]
            if not bad:
                # Sorted by id and then the whole record, so the record kept for a repeated id, and the
                # first of a kind a control reads, don't depend on the order the evidence came in.
                try:
                    good.append(((rec["id"], canonical(rec)), rec))
                    continue
                except (ValueError, TypeError, RecursionError):
                    bad = ["record can't be read"]
            ident = rec.get("id") if isinstance(rec, dict) else None
            self.rejected.append({"id": ident if isinstance(ident, str) else None, "problems": bad})
        good.sort(key=lambda g: g[0])
        for _, rec in good:
            if rec["id"] in seen:
                continue
            seen.add(rec["id"])
            self.records.append(rec)
            self._by.setdefault((rec["subject"]["id"], rec["kind"]), []).append(rec)

    def get(self, subject_id: str, kind: str) -> list[dict[str, Any]]:
        return list(self._by.get((subject_id, kind), ()))

    def ids(self) -> set[str]:
        return {r["id"] for r in self.records}
