"""From assessment findings and opportunities to optimization records, and back (ESI-601).

A Fabric Excellence Assessment finding (FEA) or an Optimization Engine opportunity (OE) becomes one
optimization record, keyed by the fingerprint it already has:

- an FEA finding's fingerprint is its ``id`` (``fx-``), worked out from the control id and the
  resource id (``excellence.findings.finding_id``). It doesn't change with the control's version, so
  a new rule version still finds the same record. The id is checked against the control and resource
  it names, so an edited finding is refused.
- an OE opportunity's fingerprint is its ``id`` (``opp-``), worked out from the rule and the
  artifacts it is about (``opportunities.model.opportunity_id``).

``from_finding`` and ``from_opportunity`` build the **proposed** record: one ``fdopt_recommendations``
row (schema version 2 columns) in the ``proposed`` state, and the ``fdopt_optimization_link`` rows
for the artifacts, workspaces and capacities it affects. The ``optimization_id`` and every
``record_id`` come from the fingerprint alone, so the same finding seen on two PCs, or again on a
re-run, gives the same ids and an insert-only MERGE adds nothing the second time. A record holds
fingerprints and ids only: never code, titles or evidence text. Original and optimized definition
references (``original_ref``, ``optimized_ref``) are definition fingerprints when given, never code
text. Rollback information belongs to an implemented change, so a proposed record has none.

Each proposal also carries ``rule_id`` (the control id or OE rule) and, for a finding, the
control's ``rule_version``. No table has a column for either yet, so they travel in the returned
dict and a stored row can carry them when its writer adds them.

``link`` takes the records already known (rows of the recommendations table, or proposals) and new
detections (proposals from the two builders). A detection whose fingerprint already has a record
links to it: it returns a link event, never a second record. A new fingerprint gives a new proposed
record. A detection found again after its optimization was verified is flagged as having come back.
A finding whose control has changed version since its record was made gives a ``review`` event:
it is neither linked nor recorded again, and a person checks it. Two different optimization ids for
one fingerprint are refused, in the records or in the detections.

With an optional ``matches`` table (FEA-604: control id to the OE rule ids that find the same
problem), a finding and an opportunity on the same item share one optimization record. The later
one links to the earlier one's record, so a saving is never counted twice.

``report_back`` says what to tell the source once an optimization has a validation result
(``baselines.validate``). An opportunity is verified by a Verified result alone. An assessment
finding is resolved only by a Verified result together with the assessment's own re-check outcome
``verified_fixed`` (FEA-602). Only FEA-602's status verified_fixed resolves it. A missing outcome, not_verified (for example profile_changed) or any other status
keeps it open. Validating, Regressed and Failed always leave the source open. Each answer has a
plain one-sentence reason.

Not here (ESI-301, which waits on decision D3): the lifecycle writer, version-aware updates,
conflicts, and the responsible user and device. This module adds no user identity and changes no
state; it only builds rows and events that ESI-301 can write later. Provenance columns other than
``record_id`` (installation, app version, schema version, created at) are left to that writer.

Pure: nothing here reads or writes a lakehouse, Fabric or this PC.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..excellence.findings import finding_id
from ..opportunities import model as om
from ..optimize.shared import artifact_key, recommendation_id, record_id
from .baselines import FAILED, REGRESSED, RESULTS, VALIDATING, VERIFIED

FEA, OE = "fea_finding", "oe_opportunity"  # the record's ``kind``: where it came from
SOURCES = {FEA: "assessment finding", OE: "opportunity"}
PROPOSED = "proposed"  # plan section 4.4: the Proposed state
LINKED, REVIEW = "linked", "review"
FIRST_VERSION = 1
# The ``fdopt_recommendations`` columns (schema version 2) a proposed record fills. The writer adds
# the other provenance columns.
RECORD_COLUMNS = ("record_id", "recommendation_id", "fingerprint", "kind", "state", "reason", "impact", "effort",
                  "confidence", "at", "optimization_id", "category", "original_ref", "optimized_ref", "rollback",
                  "version")
LINK_COLUMNS = ("record_id", "optimization_id", "artifact_key", "workspace_id", "capacity_id", "relation", "at")
# Opportunities in these states don't become optimizations: a rejected one stays rejected.
_CLOSED_OPPORTUNITY = ("rejected", "superseded")
# A definition fingerprint: letters, digits and . _ : - only, so code text can't pass as one.
_REF = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LEVELS = ("low", "medium", "high")


def optimization_id(fingerprint: str) -> str:
    """The optimization id for a fingerprint: the same on every PC and every run."""
    return "opt-" + record_id("optimization", fingerprint)[:16]


def _at(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"A detection needs the time it was made, as an ISO 8601 text, not {value!r}.")
    try:
        t = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"The time {value!r} isn't an ISO 8601 date and time.") from None
    if t.tzinfo is None:
        raise ValueError(f"The time {value!r} has no time zone; give it in UTC.")
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ref(value: Any, what: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _REF.match(value):
        raise ValueError(f"The {what} must be a definition fingerprint (letters, digits, '.', '_', ':' or '-', "
                         "at most 128 characters), never code text.")
    return value


def _level(value: Any) -> str | None:
    return value if value in _LEVELS else None


def _record(fp: str, kind: str, *, category: str, effort: Any, confidence: Any, at: str, original_ref: Any,
            reason: str) -> dict[str, Any]:
    oid = optimization_id(fp)
    return {"record_id": record_id("optimization-proposed", oid), "recommendation_id": recommendation_id(fp, kind),
            "fingerprint": fp, "kind": kind, "state": PROPOSED, "reason": reason, "impact": None,
            "effort": _level(effort), "confidence": confidence if isinstance(confidence, str) else None, "at": at,
            "optimization_id": oid, "category": category,
            "original_ref": _ref(original_ref, "original reference"), "optimized_ref": None, "rollback": None,
            "version": FIRST_VERSION}


def _link(oid: str, at: str, relation: str, *, artifact: str | None = None, workspace_id: str | None = None,
          capacity_id: str | None = None) -> dict[str, Any]:
    return {"record_id": record_id("optimization-link", oid, artifact, workspace_id, capacity_id, relation),
            "optimization_id": oid, "artifact_key": artifact, "workspace_id": workspace_id,
            "capacity_id": capacity_id, "relation": relation, "at": at}


def _proposal(record: dict[str, Any], links: list[dict[str, Any]], source_id: str, rule_id: str,
              rule_version: str | None) -> dict[str, Any]:
    unique = {r["record_id"]: r for r in links}
    return {"record": record, "links": [unique[k] for k in sorted(unique)], "source": record["kind"],
            "source_id": source_id, "fingerprint": record["fingerprint"],
            "optimization_id": record["optimization_id"], "rule_id": rule_id, "rule_version": rule_version}


def from_finding(finding: dict[str, Any], *, at: str, original_ref: str | None = None) -> dict[str, Any]:
    """The proposed optimization for an FEA finding (``excellence.findings.make``), or ValueError.

    Returns ``record`` (the recommendations row), ``links`` (optimization_link rows), ``source``,
    ``source_id``, ``fingerprint``, ``optimization_id``, ``rule_id`` (the control id) and
    ``rule_version`` (the control's version; no table has a column for it yet). Advisory findings (only inferred
    evidence), fixture findings and findings no longer open are refused."""
    if not isinstance(finding, dict):
        raise ValueError("An assessment finding must be a finding record (a dict), as the assessment makes it.")
    fid, rule, res = finding.get("id"), finding.get("rule"), finding.get("resource")
    if not isinstance(fid, str) or not fid.startswith("fx-"):
        raise ValueError(f"An assessment finding needs its fx- id, the fingerprint of its control and resource, "
                         f"not {fid!r}.")
    if not isinstance(rule, str) or "@" not in rule:
        raise ValueError(f"Finding {fid} doesn't name its control as id@version.")
    if not isinstance(res, dict) or not res.get("id") or res.get("type") not in ("workspace", "capacity", "item"):
        raise ValueError(f"Finding {fid} doesn't name the workspace, capacity or item it is about.")
    control_id, rule_version = rule.split("@", 1)
    if finding_id(control_id, str(res["id"])) != fid:
        raise ValueError(f"Finding {fid} doesn't match its control {control_id} and resource {res['id']}; it may "
                         "have been edited, so it isn't used.")
    if finding.get("fixture"):
        raise ValueError(f"Finding {fid} comes from the demonstration fixture, so it doesn't become an optimization.")
    if finding.get("advisory"):
        raise ValueError(f"Finding {fid} is advisory: it rests only on inferred evidence, so it doesn't become an "
                         "optimization until the evidence is confirmed.")
    if finding.get("status", "open") != "open":
        raise ValueError(f"Finding {fid} is {finding.get('status')!r}, not open, so there is nothing to optimize.")
    when = _at(at)
    rec = _record(fid, FEA, category=f"fea:{finding.get('dimension') or 'unknown'}", effort=finding.get("effort"),
                  confidence=finding.get("confidence"), at=when, original_ref=original_ref,
                  reason=f"Proposed from assessment finding {fid} of control {control_id}.")
    oid = rec["optimization_id"]
    if res["type"] == "item":
        ws = res.get("workspace_id")
        links = [_link(oid, when, "affects", artifact=artifact_key(ws or "", str(res["id"])), workspace_id=ws)]
    elif res["type"] == "workspace":
        links = [_link(oid, when, "affects", workspace_id=str(res["id"]))]
    else:
        links = [_link(oid, when, "affects", capacity_id=str(res["id"]))]
    return _proposal(rec, links, fid, control_id, rule_version)


def from_opportunity(opportunity: dict[str, Any], *, at: str, original_ref: str | None = None,
                     capacity_id: str | None = None) -> dict[str, Any]:
    """The proposed optimization for an OE opportunity (``opportunities.model.make``), or ValueError.

    Same shape as ``from_finding``. One link per artifact, its relation being the artifact's role
    (keep, remove, change, reads). ``capacity_id`` is the workspace's capacity when known. Fixture
    opportunities and rejected or superseded ones are refused."""
    if not isinstance(opportunity, dict):
        raise ValueError("An opportunity must be an opportunity record (a dict), as the engine makes it.")
    oppid = opportunity.get("id")
    if not isinstance(oppid, str) or not oppid.startswith("opp-"):
        raise ValueError(f"An opportunity needs its opp- id, the fingerprint of its rule and artifacts, not {oppid!r}.")
    if not opportunity.get("rule"):
        raise ValueError(f"Opportunity {oppid} doesn't name the rule that found it.")
    category = opportunity.get("category")
    if category not in om.CATEGORIES:
        raise ValueError(f"Opportunity {oppid} has unknown category {category!r}.")
    status = opportunity.get("status")
    if status not in om.STATUSES:
        raise ValueError(f"Opportunity {oppid} has unknown status {status!r}.")
    if status in _CLOSED_OPPORTUNITY:
        raise ValueError(f"Opportunity {oppid} was {status}, so it doesn't become an optimization.")
    if opportunity.get("fixture"):
        raise ValueError(f"Opportunity {oppid} comes from the demonstration fixture, so it doesn't become an "
                         "optimization.")
    artifacts = opportunity.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError(f"Opportunity {oppid} doesn't list the artifacts it is about.")
    when = _at(at)
    cap = _ref(capacity_id, "capacity id")
    rec = _record(oppid, OE, category=category, effort=opportunity.get("effort"),
                  confidence=opportunity.get("confidence"), at=when, original_ref=original_ref,
                  reason=f"Proposed from opportunity {oppid} of rule {opportunity['rule']}.")
    oid = rec["optimization_id"]
    links = []
    for a in artifacts:
        if not isinstance(a, dict):
            raise ValueError(f"Opportunity {oppid} has an artifact that isn't a record.")
        ws, item = a.get("workspace_id") or "", a.get("item_id") or ""
        links.append(_link(oid, when, str(a.get("role") or "affects"), artifact=artifact_key(ws, item),
                           workspace_id=ws.strip(), capacity_id=cap))
    return _proposal(rec, links, oppid, str(opportunity["rule"]), None)


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda r: (r.get("version") or 0, str(r.get("at") or ""), str(r.get("record_id") or "")))


def _matches(matches: Mapping[str, Iterable[str]] | None) -> dict[str, frozenset[str]]:
    if matches is None:
        return {}
    if not isinstance(matches, Mapping):
        raise ValueError("The matches table maps each control id to the OE rule ids it covers.")
    out: dict[str, frozenset[str]] = {}
    for control, rules in matches.items():
        if not isinstance(control, str) or not control or isinstance(rules, str) or not isinstance(rules, Iterable):
            raise ValueError(f"The matches table maps each control id to a list of OE rule ids; {control!r} doesn't.")
        rules = frozenset(rules)
        if not all(isinstance(r, str) and r for r in rules):
            raise ValueError(f"Control {control} in the matches table has an OE rule id that isn't a text.")
        out[control] = rules
    return out


def _entry(oid: str, fp: str, kind: Any, state: Any, version: Any, rule_id: Any, rule_version: Any,
           artifacts: set[str]) -> dict[str, Any]:
    return {"optimization_id": oid, "fingerprint": fp, "kind": kind, "state": state, "version": version,
            "rule_id": rule_id, "rule_version": rule_version, "artifacts": artifacts}


def _items(links: list[Any]) -> set[str]:
    return {x["artifact_key"] for x in links if isinstance(x, dict) and x.get("artifact_key")}


def _known(existing_records: list[Any], existing_links: list[Any]) -> dict[str, dict[str, Any]]:
    """The optimizations already recorded, by fingerprint, with what matching needs."""
    rows_of: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    fp_of: dict[str, str] = {}
    for item in existing_records:
        if not isinstance(item, dict):
            raise ValueError("An existing record must be a row (a dict) of the recommendations table.")
        if isinstance(item.get("record"), dict):  # a proposal from this module
            row, info = item["record"], {"rule_id": item.get("rule_id"), "rule_version": item.get("rule_version"),
                                         "links": item.get("links") or []}
        else:
            row, info = item, {"rule_id": item.get("rule_id"), "rule_version": item.get("rule_version"), "links": []}
        oid, fp = row.get("optimization_id"), row.get("fingerprint")
        if not oid:
            continue
        if not fp:
            raise ValueError(f"Optimization {oid} has a row without a fingerprint, so it can't be matched.")
        if fp_of.setdefault(oid, fp) != fp:
            raise ValueError(f"Optimization {oid} has rows for two fingerprints, {fp_of[oid]} and {fp}; "
                             "one optimization is for one finding or opportunity.")
        rows_of.setdefault(oid, []).append((row, info))
    extra_items: dict[str, set[str]] = {}
    for x in existing_links:
        if not isinstance(x, dict):
            raise ValueError("An existing link must be a row (a dict) of the optimization_link table.")
        extra_items.setdefault(str(x.get("optimization_id")), set()).update(_items([x]))
    by_fp: dict[str, dict[str, Any]] = {}
    for oid, pairs in sorted(rows_of.items()):
        fp = fp_of[oid]
        if fp in by_fp:
            raise ValueError(f"Fingerprint {fp} has two different optimization records "
                             f"({by_fp[fp]['optimization_id']}, {oid}); one fingerprint must have one record, so "
                             "this is refused until one is superseded.")
        last = _latest([r for r, _ in pairs])
        # The rule version on the latest row that names one.
        named = [(r, i) for r, i in pairs if i["rule_version"]]
        version = (max(named, key=lambda p: (p[0].get("version") or 0, str(p[0].get("at") or "")))[1]["rule_version"]
                   if named else None)
        rule_id = next((i["rule_id"] for _, i in pairs if i["rule_id"]), None)
        items = set().union(*(_items(i["links"]) for _, i in pairs)) | extra_items.get(oid, set())
        by_fp[fp] = _entry(oid, fp, last.get("kind"), last.get("state"), last.get("version"), rule_id, version, items)
    return by_fp


def _counterpart(d: dict[str, Any], entries: list[dict[str, Any]],
                 table: dict[str, frozenset[str]]) -> list[dict[str, Any]]:
    """Records of the other source that the matches table says cover the same item as ``d``."""
    items = _items(d.get("links") or [])
    if not items or not d.get("rule_id"):
        return []
    if d.get("source") == FEA:
        rules = table.get(d["rule_id"], frozenset())
        hits = [e for e in entries if e["kind"] == OE and e["rule_id"] in rules and e["artifacts"] & items]
    else:
        hits = [e for e in entries if e["kind"] == FEA and d["rule_id"] in table.get(e["rule_id"] or "", frozenset())
                and e["artifacts"] & items]
    return sorted(hits, key=lambda e: e["optimization_id"])


def link(existing_records: list[dict[str, Any]], detections: list[dict[str, Any]], *,
         matches: Mapping[str, Iterable[str]] | None = None,
         existing_links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Sort detections into new records, links to records already known, and matches a person must check.

    ``existing_records``: rows of the recommendations table, or proposals from this module. Rows
    without an ``optimization_id`` (version 1 recommendations from Optimize's own analysis) are left
    alone. A row may carry ``rule_id`` and ``rule_version`` when the caller has them; no table has
    those columns yet, so a plain row's rule version can't be compared. ``detections``: proposals
    from ``from_finding`` or ``from_opportunity``. ``existing_links``: optimization_link rows, for
    the items a stored record covers.

    ``matches`` (optional): control id to the OE rule ids that find the same problem (FEA-604). A
    finding whose control maps to an opportunity's rule, on the same item, links to that
    opportunity's record instead of making a second one, and the other way round, so a saving is
    never counted twice. Opportunities in the batch are taken before findings, so a finding links
    to an opportunity found in the same run. Without ``matches`` nothing is matched across sources.

    Returns ``new`` (proposals to write, one per new fingerprint), ``linked`` (link events; never a
    second record), ``review`` (a finding whose control changed version since its record was made:
    neither linked nor new, for a person to check) and ``repeats`` (detections that repeated one
    earlier in the batch). Refuses two different optimization ids for one fingerprint, and an id
    that names a different fingerprint."""
    if existing_links is None:
        existing_links = []
    if not all(isinstance(x, list) for x in (existing_records, detections, existing_links)):
        raise ValueError("Linking needs the existing records, the detections and the existing links, each as a list.")
    table = _matches(matches)
    known = _known(existing_records, existing_links)
    for d in detections:
        if not isinstance(d, dict) or not isinstance(d.get("record"), dict):
            raise ValueError("A detection must be a proposal from from_finding or from_opportunity.")
        fp, oid = d["record"].get("fingerprint"), d["record"].get("optimization_id")
        if not fp or oid != optimization_id(fp):
            raise ValueError(f"Detection {fp!r} doesn't carry the optimization id its fingerprint gives; build it "
                             "with from_finding or from_opportunity.")
        if fp in known and known[fp]["optimization_id"] != oid:
            raise ValueError(f"Fingerprint {fp} already has optimization {known[fp]['optimization_id']}, not {oid}; "
                             "two records for one fingerprint are refused.")
    order = sorted(range(len(detections)), key=lambda i: (detections[i].get("source") != OE, i))
    new: dict[str, dict[str, Any]] = {}
    linked: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    repeats = 0
    for i in order:
        d = detections[i]
        rec = d["record"]
        fp, oid = rec["fingerprint"], rec["optimization_id"]
        base = {"fingerprint": fp, "source": d.get("source"), "source_id": d.get("source_id"),
                "detected_at": rec.get("at")}
        stored = known.get(fp)
        if stored is None and fp in new:
            stored = _entry(oid, fp, d.get("source"), PROPOSED, FIRST_VERSION, new[fp].get("rule_id"),
                            new[fp].get("rule_version"), set())
        if stored is not None:
            was, now = stored["rule_version"], d.get("rule_version")
            if d.get("source") == FEA and was and now and was != now:
                review.append(base | {"event": REVIEW, "optimization_id": oid, "stored_rule_version": was,
                                      "detected_rule_version": now,
                                      "reason": (f"Control {d.get('rule_id')} changed from version {was} to {now} "
                                                 "since this optimization was recorded, so a person needs to check "
                                                 "that the match still holds.")})
            elif fp not in known:
                repeats += 1
            else:
                came_back = stored["state"] == VERIFIED
                linked.append(base | {
                    "event": LINKED, "optimization_id": oid, "matched": False, "existing_state": stored["state"],
                    "existing_version": stored["version"], "came_back": came_back,
                    "rule_version_checked": d.get("source") != FEA or bool(was and now),
                    "reason": (f"Found again after optimization {oid} was verified, so the fix may not have held."
                               if came_back else f"Already tracked as optimization {oid}, so no new record is made.")})
            continue
        if table:
            entries = list(known.values()) + [
                _entry(p["optimization_id"], k, p["source"], PROPOSED, FIRST_VERSION, p.get("rule_id"),
                       p.get("rule_version"), _items(p["links"])) for k, p in new.items()]
            hits = _counterpart(d, entries, table)
            if hits:
                hit = hits[0]
                what = "Assessment finding" if d.get("source") == FEA else "Opportunity"
                linked.append(base | {
                    "event": LINKED, "optimization_id": hit["optimization_id"], "matched": True,
                    "matched_fingerprint": hit["fingerprint"],
                    "also_matches": [h["optimization_id"] for h in hits[1:]], "existing_state": hit["state"],
                    "existing_version": hit["version"], "came_back": False, "rule_version_checked": False,
                    "reason": (f"{what} {fp} finds the same problem as {hit['fingerprint']} on the same item, so it "
                               f"links to optimization {hit['optimization_id']} and isn't counted twice.")})
                continue
        new[fp] = d
    return {"new": [new[k] for k in sorted(new)], "linked": linked, "review": review, "repeats": repeats}


_REASONS = {
    VERIFIED: "Its optimization was verified on measured CU with equivalent outputs, so the {what} is resolved.",
    VALIDATING: "Its optimization is still being validated, so the {what} stays open.",
    REGRESSED: "Measured CU went up after the change, so the {what} stays open.",
    FAILED: "The outputs changed after the change, so the {what} stays open.",
}
VERIFIED_FIXED = "verified_fixed"  # FEA-602: the assessment's own re-check found the control fixed


FEA_STATUSES = ("verified_fixed", "still_failing", "regressed", "not_verified")  # FEA-602 verification.STATUSES


def _fea_outcome(value: Any) -> str | None:
    """FEA-602 verify()'s ``status`` (from its result dict, or the status itself), with its
    ``reason_code`` when it is not_verified, as ``not_verified:<code>``. Matched exactly."""
    code = None
    if isinstance(value, dict):
        value, code = value.get("status"), value.get("reason_code")
    if value is None:
        return None
    if not isinstance(value, str) or value not in FEA_STATUSES:
        raise ValueError(f"The assessment's verification status must be one of {', '.join(FEA_STATUSES)}, "
                         f"not {value!r}.")
    return f"{value}:{code}" if value == "not_verified" and code else value


def _fea_reason(outcome: str | None) -> str:
    if outcome is None:
        return ("The savings were verified, but the assessment hasn't checked the finding again, so the "
                "assessment finding stays open.")
    if outcome == "not_verified:profile_changed":
        return ("The savings were verified, but the assessment profile changed, so the assessment finding stays "
                "open until it is checked under the same profile.")
    if outcome.startswith("not_verified"):
        code = outcome.partition(":")[2] or "no reason given"
        return (f"The savings were verified, but the assessment couldn't verify the fix ({code}), so the "
                "assessment finding stays open.")
    return (f"The savings were verified, but the assessment's check gave {outcome}, not {VERIFIED_FIXED}, so the "
            "assessment finding stays open.")


def report_back(record: dict[str, Any], validation: dict[str, Any], *, fea_outcome: Any = None,
                to: str | None = None) -> dict[str, Any]:
    """What to tell the assessment about this optimization's validation, or ValueError.

    ``record``: the optimization's record (a recommendations row, or a proposal from this module).
    ``validation``: the result of ``baselines.validate``, or its ``fdopt_validation`` row.
    ``to``: the finding or opportunity to report to (its fx- or opp- id) when it was linked to this
    record through ``matches``; default: the record's own fingerprint.
    ``fea_outcome``: for a finding, the assessment's own re-check (FEA-602), as verify()'s result dict
    (``status`` and ``reason_code``) or its status.

    A finding is resolved only when the validation is Verified and ``fea_outcome`` is
    ``verified_fixed``. Only status verified_fixed resolves it. A missing outcome, not_verified or any other status keeps it
    open. An opportunity is resolved (verified) on a Verified validation alone.

    Returns ``resolved``, the ``status`` to show on the source (a finding: resolved or open; an
    opportunity: verified or validating, as its lifecycle has it), the source and its id, the
    validation id and result, the FEA outcome and a one-sentence ``reason``."""
    if isinstance(record, dict) and isinstance(record.get("record"), dict):
        record = record["record"]
    if not isinstance(record, dict):
        raise ValueError("Reporting back needs the optimization's record.")
    kind, fp, oid = record.get("kind"), record.get("fingerprint"), record.get("optimization_id")
    if kind not in SOURCES:
        raise ValueError(f"Optimization {oid!r} didn't come from an assessment finding or an opportunity, so "
                         "there is nothing to report back to.")
    if not fp or oid != optimization_id(fp):
        raise ValueError(f"Optimization {oid!r} doesn't match its fingerprint {fp!r}.")
    target = fp if to is None else to
    if not isinstance(target, str) or not target.startswith(("fx-", "opp-")):
        raise ValueError(f"Reporting back goes to an fx- finding or an opp- opportunity id, not {target!r}.")
    source = FEA if target.startswith("fx-") else OE
    if not isinstance(validation, dict):
        raise ValueError("Reporting back needs the validation result from baselines.validate.")
    row = validation["row"] if isinstance(validation.get("row"), dict) else validation
    result = validation.get("result", row.get("result"))
    if result not in RESULTS:
        raise ValueError(f"The validation result must be one of {', '.join(RESULTS)}, not {result!r}.")
    if row.get("optimization_id") != oid:
        raise ValueError(f"That validation is for optimization {row.get('optimization_id')!r}, not {oid}.")
    outcome = _fea_outcome(fea_outcome) if source == FEA else None
    if source == OE:
        resolved = result == VERIFIED
        status = VERIFIED if resolved else VALIDATING
        reason = _REASONS[result].format(what=SOURCES[OE])
    else:
        resolved = result == VERIFIED and outcome == VERIFIED_FIXED
        status = "resolved" if resolved else "open"
        if result != VERIFIED:
            reason = _REASONS[result].format(what=SOURCES[FEA])
        elif resolved:
            reason = ("Its optimization was verified and the assessment confirmed the fix, so the assessment "
                      "finding is resolved.")
        else:
            reason = _fea_reason(outcome)
    return {"optimization_id": oid, "source": source, "source_id": target, "fingerprint": target,
            "resolved": resolved, "status": status, "validation_id": row.get("validation_id"),
            "validation_result": result, "fea_outcome": outcome, "reason": reason}


