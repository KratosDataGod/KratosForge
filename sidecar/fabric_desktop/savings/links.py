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

``link`` takes the records already known (rows of the recommendations table) and new detections
(proposals from the two builders). A detection whose fingerprint already has a record links to it:
it returns a link event, never a second record. A new fingerprint gives a new proposed record. A
detection found again after its optimization was verified is flagged as having come back. Two
different optimization ids for one fingerprint are refused, in the records or in the detections.

``report_back`` says what to tell the assessment once an optimization has a validation result
(``baselines.validate``). Only a Verified result resolves the finding or opportunity. Validating,
Regressed and Failed leave it open, each with a plain one-sentence reason.

Not here (ESI-301, which waits on decision D3): the lifecycle writer, version-aware updates,
conflicts, and the responsible user and device. This module adds no user identity and changes no
state; it only builds rows and events that ESI-301 can write later. Provenance columns other than
``record_id`` (installation, app version, schema version, created at) are left to that writer.

Pure: nothing here reads or writes a lakehouse, Fabric or this PC.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from ..excellence.findings import finding_id
from ..opportunities import model as om
from ..optimize.shared import artifact_key, recommendation_id, record_id
from .baselines import FAILED, REGRESSED, RESULTS, VALIDATING, VERIFIED

FEA, OE = "fea_finding", "oe_opportunity"  # the record's ``kind``: where it came from
SOURCES = {FEA: "assessment finding", OE: "opportunity"}
PROPOSED = "proposed"  # plan section 4.4: the Proposed state
LINKED = "linked"
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


def _proposal(record: dict[str, Any], links: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    unique = {r["record_id"]: r for r in links}
    return {"record": record, "links": [unique[k] for k in sorted(unique)], "source": record["kind"],
            "source_id": source_id, "fingerprint": record["fingerprint"],
            "optimization_id": record["optimization_id"]}


def from_finding(finding: dict[str, Any], *, at: str, original_ref: str | None = None) -> dict[str, Any]:
    """The proposed optimization for an FEA finding (``excellence.findings.make``), or ValueError.

    Returns ``record`` (the recommendations row), ``links`` (optimization_link rows), ``source``,
    ``source_id``, ``fingerprint`` and ``optimization_id``. Advisory findings (only inferred
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
    control_id = rule.split("@", 1)[0]
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
    return _proposal(rec, links, fid)


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
    return _proposal(rec, links, oppid)


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda r: (r.get("version") or 0, str(r.get("at") or ""), str(r.get("record_id") or "")))


def link(existing_records: list[dict[str, Any]], detections: list[dict[str, Any]]) -> dict[str, Any]:
    """Sort detections into new records and links to records already known.

    ``existing_records``: rows of the recommendations table. Rows without an ``optimization_id``
    (version 1 recommendations from Optimize's own analysis) are not optimization records and are
    left alone. ``detections``: proposals from ``from_finding`` or ``from_opportunity``.

    Returns ``new`` (proposals to write, one per new fingerprint), ``linked`` (one link event per
    detection of a known fingerprint, each naming the existing record and its latest state) and
    ``repeats`` (how many detections in this batch repeated one before it). Refuses two different
    optimization ids for one fingerprint, and an id that names a different fingerprint."""
    if not isinstance(existing_records, list) or not isinstance(detections, list):
        raise ValueError("Linking needs the existing records and the detections, each as a list.")
    by_fp: dict[str, dict[str, list[dict[str, Any]]]] = {}
    fp_of: dict[str, str] = {}
    for r in existing_records:
        if not isinstance(r, dict):
            raise ValueError("An existing record must be a row (a dict) of the recommendations table.")
        oid, fp = r.get("optimization_id"), r.get("fingerprint")
        if not oid:
            continue
        if not fp:
            raise ValueError(f"Optimization {oid} has a row without a fingerprint, so it can't be matched.")
        if fp_of.setdefault(oid, fp) != fp:
            raise ValueError(f"Optimization {oid} has rows for two fingerprints, {fp_of[oid]} and {fp}; "
                             "one optimization is for one finding or opportunity.")
        by_fp.setdefault(fp, {}).setdefault(oid, []).append(r)
    for fp, records in by_fp.items():
        if len(records) > 1:
            raise ValueError(f"Fingerprint {fp} has two different optimization records ({', '.join(sorted(records))}); "
                             "one fingerprint must have one record, so this is refused until one is superseded.")
    new: dict[str, dict[str, Any]] = {}
    linked: list[dict[str, Any]] = []
    repeats = 0
    for d in detections:
        if not isinstance(d, dict) or not isinstance(d.get("record"), dict):
            raise ValueError("A detection must be a proposal from from_finding or from_opportunity.")
        rec = d["record"]
        fp, oid = rec.get("fingerprint"), rec.get("optimization_id")
        if not fp or oid != optimization_id(fp):
            raise ValueError(f"Detection {fp!r} doesn't carry the optimization id its fingerprint gives; build it "
                             "with from_finding or from_opportunity.")
        if fp in by_fp:
            existing_id, rows = next(iter(by_fp[fp].items()))
            if existing_id != oid:
                raise ValueError(f"Fingerprint {fp} already has optimization {existing_id}, not {oid}; two records "
                                 "for one fingerprint are refused.")
            latest = _latest(rows)
            came_back = latest.get("state") == VERIFIED
            linked.append({"event": LINKED, "optimization_id": oid, "fingerprint": fp, "source": d.get("source"),
                           "source_id": d.get("source_id"), "detected_at": rec.get("at"),
                           "existing_state": latest.get("state"), "existing_version": latest.get("version"),
                           "came_back": came_back,
                           "reason": (f"Found again after optimization {oid} was verified, so the fix may not have "
                                      "held." if came_back else
                                      f"Already tracked as optimization {oid}, so no new record is made.")})
        elif fp in new:
            repeats += 1
        else:
            new[fp] = d
    return {"new": [new[k] for k in sorted(new)], "linked": linked, "repeats": repeats}


_REASONS = {
    VERIFIED: "Its optimization was verified on measured CU with equivalent outputs, so the {what} is resolved.",
    VALIDATING: "Its optimization is still being validated, so the {what} stays open.",
    REGRESSED: "Measured CU went up after the change, so the {what} stays open.",
    FAILED: "The outputs changed after the change, so the {what} stays open.",
}


def report_back(record: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    """What to tell the assessment about this optimization's validation, or ValueError.

    ``record``: the optimization's record (a recommendations row, or a proposal from this module).
    ``validation``: the result of ``baselines.validate``, or its ``fdopt_validation`` row.

    Returns ``resolved`` (True only for Verified), the ``status`` to show on the source (an FEA
    finding: resolved or open; an opportunity: verified or validating, as its lifecycle has it),
    the source and its id, the validation id and result, and a one-sentence ``reason``."""
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
    if not isinstance(validation, dict):
        raise ValueError("Reporting back needs the validation result from baselines.validate.")
    row = validation.get("row") if isinstance(validation.get("row"), dict) else validation
    result = validation.get("result", row.get("result"))
    if result not in RESULTS:
        raise ValueError(f"The validation result must be one of {', '.join(RESULTS)}, not {result!r}.")
    if row.get("optimization_id") != oid:
        raise ValueError(f"That validation is for optimization {row.get('optimization_id')!r}, not {oid}.")
    resolved = result == VERIFIED
    if kind == FEA:
        status = "resolved" if resolved else "open"
    else:
        status = VERIFIED if resolved else VALIDATING
    return {"optimization_id": oid, "source": kind, "source_id": fp, "fingerprint": fp, "resolved": resolved,
            "status": status, "validation_id": row.get("validation_id"), "validation_result": result,
            "reason": _REASONS[result].format(what=SOURCES[kind])}
