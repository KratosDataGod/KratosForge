"""One assessment run, its snapshot, rescoring and comparison (FEA-104).

``assess`` runs every registered control on the subjects in scope against an evidence bundle, with a
profile, and returns a snapshot: the evidence it used, each control's outcome per resource, the
findings, the scores, the coverage of every item in the inventory, and the exact rule and profile
versions. ``rescore`` runs the same controls on a stored snapshot's evidence and gives the same
result; it refuses when a rule's version has changed since, rather than silently mixing versions.

Subjects:

- each workspace in ``scope["workspaces"]`` gets the workspace controls;
- each capacity in ``scope["capacities"]`` gets the capacity controls;
- item controls run on every item of their types in the workspace's inventory (evidence kind
  ``workspace.inventory``, payload ``{"items": [{"id", "type", "name"}]}``). With no inventory they are
  not evaluated; with no item of their types they don't apply.

An item control reads evidence about the item itself, and kinds that start with ``workspace.`` from
its workspace. Every item in the inventory is reported as ``inspected`` (a control passed or failed
on it), ``not_inspected`` (controls cover its type but none could decide) or ``unsupported`` (no
control covers its type yet). Unsupported never reads as passing.

Nothing here reads Fabric or writes anything.
"""

from __future__ import annotations

from typing import Any

from . import controls as ct
from . import findings as fx
from . import policy_packs
from . import profiles as pf
from . import scoring
from .evidence import Bundle, digest, subject

SCHEMA = "kratosforge/excellence-assessment@1"
INVENTORY = "workspace.inventory"
FIXTURE_NOTE = ("Demonstration fixture: made-up resources and evidence that show how the assessment works. "
                "Nothing here was read from Fabric, and none of it is a finding about your environment.")


def policy_version(registry: ct.Registry, profile: pf.Profile) -> str:
    return digest([profile.as_dict(), registry.versions()])[:16]


def _inventory(bundle: Bundle, ws: dict[str, Any]) -> list[dict[str, Any]] | None:
    recs = [r for r in bundle.get(ws["id"], INVENTORY) if r["confidence"] != "insufficient"]
    if not recs:
        return None
    items: dict[str, dict[str, Any]] = {}
    for r in recs:
        listed = r["payload"].get("items") if isinstance(r["payload"], dict) else None
        for it in listed if isinstance(listed, list) else []:
            if isinstance(it, dict) and it.get("id") and isinstance(it["id"], str) and it.get("type") \
                    and isinstance(it["type"], str):
                items[str(it["id"])] = subject("item", str(it["id"]), it.get("name"), workspace_id=ws["id"],
                                               item_type=str(it["type"]))
    return [items[k] for k in sorted(items)]


def _run(c: ct.Control, subj: dict[str, Any], bundle: Bundle, profile: pf.Profile,
         ws: dict[str, Any] | None = None) -> dict[str, Any]:
    pack = profile.policy_pack or {}
    out_of_scope = policy_packs.out_of_scope(profile, c.id, subj, ws or subj)
    if c.id in profile.disabled:
        o = ct._outcome(c, subj, ct.not_applicable(f"Turned off in the {profile.title} profile: {profile.disabled[c.id]}"),
                        "insufficient")
        if any(d["control_id"] == c.id for d in pack.get("disabled") or []):
            o["policy"] = {"pack": f"{pack['id']}@{pack['version']}", "effect": "disabled"}
    elif out_of_scope:
        o = ct._outcome(c, subj, ct.not_applicable(out_of_scope), "insufficient")
        o["policy"] = {"pack": f"{pack['id']}@{pack['version']}", "effect": "out_of_scope"}
    else:
        ws_id = subj.get("workspace_id")

        def gather(kind: str) -> list[dict[str, Any]]:
            if subj["type"] == "item" and kind.startswith("workspace.") and ws_id:
                return bundle.get(ws_id, kind)
            return bundle.get(subj["id"], kind)

        o = ct.evaluate(c, subj, gather)
    o["severity"] = profile.severity_overrides.get(c.id, c.severity)
    return o


def _subjects(scope: dict[str, Any], key: str, kind: str) -> list[dict[str, Any]]:
    out = []
    for s in scope.get(key) or []:
        s = s if isinstance(s, dict) else {"id": s}
        out.append(subject(kind, str(s["id"]), s.get("name")))
    return sorted(out, key=lambda x: x["id"])


def assess(records: list[dict[str, Any]], registry: ct.Registry, *, scope: dict[str, Any], assessed_at: str,
           profile: pf.Profile | None = None, fixture: bool = False) -> dict[str, Any]:
    """The assessment of ``scope`` (``{"workspaces": [id | {id, name}], "capacities": [...]}``)."""
    profile = pf.checked(profile or pf.get())
    bundle = Bundle(records, registry.payloads)
    workspaces = _subjects(scope, "workspaces", "workspace")
    capacities = _subjects(scope, "capacities", "capacity")
    outcomes: list[dict[str, Any]] = []
    item_cov: list[dict[str, Any]] = []
    for c in registry.controls():
        if c.scope == "workspace":
            outcomes += [_run(c, ws, bundle, profile) for ws in workspaces]
        elif c.scope == "capacity":
            outcomes += [_run(c, cap, bundle, profile) for cap in capacities]
    for ws in workspaces:
        inv = _inventory(bundle, ws)
        for c in (c for c in registry.controls() if c.scope == "item"):
            if inv is None:
                o = ct._outcome(c, ws, ct.not_evaluated("The workspace's items couldn't be listed.",
                                                        needs=INVENTORY), "insufficient")
                o["severity"] = profile.severity_overrides.get(c.id, c.severity)
                outcomes.append(o)
                continue
            mine = [it for it in inv if it["item_type"] in c.item_types]
            if not mine:
                o = ct._outcome(c, ws, ct.not_applicable("No " + " or ".join(c.item_types) + " items in the workspace."),
                                "insufficient")
                o["severity"] = profile.severity_overrides.get(c.id, c.severity)
                outcomes.append(o)
            outcomes += [_run(c, it, bundle, profile, ws) for it in mine]
        item_cov += _item_coverage(inv or [], outcomes, registry)
    findings = fx.rank([fx.make(registry.get(o["control_id"]), o, o["severity"]) for o in outcomes
                        if o["outcome"] == "fail" or o.get("advisory", {}) and o["advisory"].get("outcome") == "fail"])
    if fixture:
        for f in findings:
            f["fixture"] = True
    scores = scoring.score(outcomes, profile)
    by_ws = {ws["id"]: scoring.score([o for o in outcomes if o["subject"]["id"] == ws["id"]
                                      or o["subject"].get("workspace_id") == ws["id"]], profile)["overall"]
             for ws in workspaces}
    by_cap = {cap["id"]: scoring.score([o for o in outcomes if o["subject"]["id"] == cap["id"]], profile)["overall"]
              for cap in capacities}
    pv = policy_version(registry, profile)
    used = sorted({i for o in outcomes for i in o["evidence_ids"]}
                  | {i for o in outcomes for i in (o.get("advisory") or {}).get("evidence_ids", [])})
    scored = [f for f in findings if not f["advisory"]]
    return {"schema": SCHEMA, "fixture": fixture, "fixture_note": FIXTURE_NOTE if fixture else None,
            "assessed_at": assessed_at, "scope": {"workspaces": workspaces, "capacities": capacities},
            "profile": profile.as_dict(), "policy_version": pv, "rule_versions": registry.versions(),
            "policy_pack": policy_packs.effects(profile, outcomes),
            "fingerprint": digest([pv, [r["id"] for r in bundle.records]])[:16],
            "scores": scores, "workspace_scores": by_ws, "capacity_scores": by_cap,
            "findings": findings, "next_actions": fx.next_actions(findings),
            "counts": {"findings": len(scored), "advisory": len(findings) - len(scored),
                       "critical": sum(1 for f in scored if f["severity"] == "critical"),
                       "high": sum(1 for f in scored if f["severity"] == "high")},
            "outcomes": outcomes, "item_coverage": item_cov,
            "evidence": bundle.records, "evidence_used": used, "evidence_rejected": bundle.rejected}


def _item_coverage(inv: list[dict[str, Any]], outcomes: list[dict[str, Any]], registry: ct.Registry) -> list[dict[str, Any]]:
    covered = registry.item_types()
    out = []
    for it in inv:
        mine = [o for o in outcomes if o["subject"]["id"] == it["id"]]
        if it["item_type"] not in covered:
            state, why = "unsupported", "No control covers this kind of item yet."
        elif any(o["outcome"] in ("pass", "fail") for o in mine):
            state, why = "inspected", None
        else:
            reasons = sorted({o["text"] for o in mine if o["outcome"] in ("not_evaluated", "error")})
            state, why = "not_inspected", " ".join(reasons) or "No control could decide."
        out.append({"item": it, "state": state, "reason": why})
    return out


def rescore(snapshot: dict[str, Any], registry: ct.Registry, profile: pf.Profile | None = None) -> dict[str, Any]:
    """The snapshot's evidence assessed again: with its own profile, the same result; with another
    profile, the same evidence scored that way. Refused when a rule's version differs from the one
    the snapshot used, or a rule it used is gone."""
    then, now = snapshot["rule_versions"], registry.versions()
    changed = sorted(k for k in set(then) | set(now) if then.get(k) != now.get(k))
    if changed:
        raise ValueError("rules changed since this snapshot: " + ", ".join(changed))
    return assess(snapshot["evidence"], registry, scope=snapshot["scope"], assessed_at=snapshot["assessed_at"],
                  profile=profile or pf.from_dict(snapshot["profile"]), fixture=bool(snapshot.get("fixture")))


def compare(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """The trend since ``previous``: score changes, and new, resolved and recurring findings.
    ``comparable`` is false when the two used a different profile or rules: the change may come
    from the policy, not the environment, and views say so."""
    def delta(a: Any, b: Any) -> float | None:
        return None if a is None or b is None else round(b - a, 1)

    po, co = previous["scores"]["overall"], current["scores"]["overall"]
    before = {f["id"] for f in previous["findings"] if not f["advisory"]}
    after = {f["id"] for f in current["findings"] if not f["advisory"]}
    return {"comparable": previous["policy_version"] == current["policy_version"],
            "from": previous["assessed_at"], "to": current["assessed_at"],
            "score": delta(po["score"], co["score"]), "assured": delta(po["assured"], co["assured"]),
            "coverage": delta(po["coverage"], co["coverage"]),
            "dimensions": {d: {"score": delta(previous["scores"]["dimensions"][d]["score"], x["score"]),
                               "assured": delta(previous["scores"]["dimensions"][d]["assured"], x["assured"])}
                           for d, x in current["scores"]["dimensions"].items()},
            "new": sorted(after - before), "resolved": sorted(before - after), "recurring": sorted(after & before)}
