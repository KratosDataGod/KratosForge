"""Coverage per item and per item type (FEA-306).

Every item in a workspace's inventory gets one state:

- ``inspected``: at least one control passed or failed on it. ``controls`` names those controls and
  ``evidence_kinds`` the kinds of evidence they cited.
- ``not_inspected``: controls cover its type but none could decide. ``reason`` says why in plain
  words: the read that failed or was skipped (a cap, no sign-in), the evidence that is missing, or
  that no control applies to it.
- ``unsupported``: no registered control names its type in ``item_types``. Unsupported never reads
  as passing.

A control covers a type simply by naming it in ``Control.item_types``: register it and its items
move from unsupported to inspected or not inspected, with nothing to change here.

:func:`by_type` sums the items per type. A type's ``state`` is the worst of its items' states, so a
type with one item not inspected is ``not_inspected`` even when the others were: the counts say how
many of each. Types are sorted by name; reasons by how many items they hold, then by text. Every
text is cut at MAX_TEXT characters and every list at its cap, so a workspace with thousands of items
or a very long item name can't blow up a result. An item type nobody has heard of is just another
unsupported type.

Nothing here reads Fabric or writes anything.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import controls as ct

STATES = ("inspected", "not_inspected", "unsupported")
MAX_TEXT = 256  # characters kept of an item name, item type or reason
MAX_REASONS = 10  # distinct reasons kept per type; ``more_reasons`` counts the rest
MAX_LIST = 50  # controls, evidence kinds and unlisted workspaces kept per list
UNSUPPORTED = "No control covers this kind of item yet."
NOT_LISTED = "The workspace's items couldn't be listed, so no item type in it can be reported."


def cut(value: Any) -> str:
    """``value`` as text, at most MAX_TEXT characters (the last one an ellipsis when cut)."""
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


def covering(registry: ct.Registry) -> dict[str, list[str]]:
    """Item type -> the ids of the controls that name it, sorted."""
    out: dict[str, list[str]] = {}
    for c in registry.controls():
        for t in c.item_types:
            out.setdefault(t, []).append(c.id)
    return {t: sorted(ids) for t, ids in sorted(out.items())}


def _problems(records: list[dict[str, Any]]) -> list[str]:
    """What the item's own evidence says went wrong in reading it (a failed or skipped read)."""
    out = []
    for r in records:
        payload = r.get("payload")
        if r.get("confidence") == "insufficient" and isinstance(payload, dict) and payload.get("problem"):
            out.append(cut(payload["problem"]))
    return out


def items(inv: list[dict[str, Any]], outcomes: list[dict[str, Any]], registry: ct.Registry,
          records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One coverage row per item of ``inv``: ``{"item", "state", "reason", "controls", "evidence_kinds"}``."""
    covers = covering(registry)
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for o in outcomes:
        by_subject.setdefault(str(o["subject"]["id"]), []).append(o)
    evidence: dict[str, list[dict[str, Any]]] = {}
    kind_of: dict[str, str] = {}
    for r in records:
        evidence.setdefault(str(r["subject"]["id"]), []).append(r)
        kind_of[r["id"]] = r["kind"]
    out = []
    for it in inv:
        mine = by_subject.get(str(it["id"]), [])
        decided = [o for o in mine if o["outcome"] in ("pass", "fail")]
        row: dict[str, Any] = {"item": it, "state": "", "reason": None, "controls": [], "evidence_kinds": []}
        if it["item_type"] not in covers:
            row.update(state="unsupported", reason=UNSUPPORTED)
        elif decided:
            row.update(state="inspected",
                       controls=sorted({o["control_id"] for o in decided})[:MAX_LIST],
                       evidence_kinds=sorted({kind_of[i] for o in decided for i in o["evidence_ids"]
                                              if i in kind_of})[:MAX_LIST])
        else:
            reasons = _problems(evidence.get(str(it["id"]), []))
            reasons += [cut(o["text"]) for o in mine if o["outcome"] in ("not_evaluated", "error") and o["text"]]
            if not reasons:
                skipped = [cut(o["text"]) for o in mine if o["outcome"] == "not_applicable" and o["text"]]
                reasons = ["No control applies to it." + (" " + skipped[0] if skipped else "")] if mine else []
            row.update(state="not_inspected", controls=covers[it["item_type"]][:MAX_LIST],
                       reason=cut(" ".join(dict.fromkeys(reasons)) or "No control could decide."))
        out.append(row)
    return out


def by_type(rows: list[dict[str, Any]], registry: ct.Registry,
            unlisted: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The coverage rows summed per item type, sorted by type.

    ``unlisted`` names the workspaces whose items couldn't be listed: their types are unknown, so
    they are reported here rather than left out silently."""
    covers = covering(registry)
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        t = str(row["item"]["item_type"])
        e = acc.setdefault(t, {"counts": Counter(), "reasons": Counter(), "decided": set(), "kinds": set()})
        e["counts"][row["state"]] += 1
        if row["state"] == "not_inspected":
            e["reasons"][row["reason"] or "No control could decide."] += 1
        if row["state"] == "inspected":
            e["decided"].update(row["controls"])
            e["kinds"].update(row["evidence_kinds"])
    types = []
    for t in sorted(acc, key=lambda x: (x.casefold(), x)):
        e = acc[t]
        n = {s: e["counts"][s] for s in STATES}
        state = ("unsupported" if t not in covers else "not_inspected" if n["not_inspected"] or not n["inspected"]
                 else "inspected")
        reasons = sorted(e["reasons"].items(), key=lambda kv: (-kv[1], kv[0]))
        types.append({"item_type": cut(t), "state": state, "items": sum(n.values()), **n,
                      "controls": covers.get(t, [])[:MAX_LIST], "decided_by": sorted(e["decided"])[:MAX_LIST],
                      "evidence_kinds": sorted(e["kinds"])[:MAX_LIST],
                      "reason": UNSUPPORTED if state == "unsupported" else None,
                      "reasons": [{"reason": r, "items": c} for r, c in reasons[:MAX_REASONS]],
                      "more_reasons": max(0, len(reasons) - MAX_REASONS)})
    gone = [{"id": cut(w["id"]), "name": cut(w.get("name") or w["id"])} for w in unlisted or []]
    return {"types": types,
            "totals": {"types": len(types), "items": len(rows),
                       **{s: sum(1 for r in rows if r["state"] == s) for s in STATES}},
            "unlisted_workspaces": gone[:MAX_LIST], "more_unlisted": max(0, len(gone) - MAX_LIST),
            "note": NOT_LISTED if gone else None}
