"""Optimization records: lifecycle, version-aware updates, rollback, reversal and retirement (ESI-301).

An optimization record starts as the proposal ``links.from_finding`` or ``links.from_opportunity``
builds, keyed by its fingerprint. Every change after that is a **new version row** of the
``fdopt_recommendations`` table: version 1 is the proposal, version 2 the first change, and so on.
Nothing is updated in place, as schema version 2 of ``fdopt_`` requires. ``history`` reads the rows
of one optimization back and checks that they form one line (versions 1, 2, 3 ... with no gaps and
one row each), so there is always exactly one **head**: the latest version.

- **Version-aware updates (plan section 4.4, test scenario 9).** Every change says the version it was
  based on (``based_on_version``). A change based on the head makes the next version. A change based
  on an older version is refused with ``VersionConflict``, which carries the newer rows, so one
  developer never silently overwrites another: nothing is merged. A retry of the very change already
  recorded returns that row and adds nothing. The id of version n>1 is ``record_id`` over the
  optimization id and n alone, so two PCs that race to write version n give one id, and the
  insert-only MERGE keeps one of them; the other PC then finds a different row at n and gets the
  conflict on its next read.
- **The lifecycle graph.** ``TRANSITIONS`` holds every allowed move, the states of section 4.4 plus
  two this story adds: ``reversed`` (the change never counted, scenario 10) and ``retired`` (the
  workload is gone, scenario 11). Two states sound alike and mean different things:

  - ``reverted`` (**rolled back**): the change was really in place and was undone with its rollback
    information. Runs before the rollback really used less and keep their accruals. Its time is
    ``rolled_back_at``.
  - ``reversed`` (**netted out**): the change never really counted (it never deployed, or went to the
    wrong item). Its accruals are netted to zero by reversal events. Its time is ``netted_out_at``.

  A move outside the graph is refused with ``TransitionRefused`` and
  a reason. Moves to dismissed, accepted risk, superseded, reverted, reversed and retired need a
  reason. Moving to applied needs the optimized reference and rollback information. That graph is
  for new changes only: reading stored rows (``history``) also accepts every move
  ``shared.TRANSITIONS`` allows, such as applied to open or applied to verified, so a row that was
  valid when it was written is never refused.
- **References, never code.** The original and optimized references are definition fingerprints
  (letters, digits, ``.``, ``_``, ``:`` and ``-``, as ``links`` checks them). Code text, or a field
  named like code (``code``, ``definition``, ``content`` ...), is refused wherever it is passed.
- **Rollback.** Recorded when the change is applied: the method (redeploy the definition, restore
  a setting, or by hand), the reference to restore (by default the original reference), the
  reference it replaces (the optimized one), the artifacts, workspaces or capacities to restore, and
  a note. ``restorable`` is False when the reference to restore isn't known: it stays None, never a
  guess. Reverting returns this plan.
- **Who: the device only.** The responsible party is the installation (``installation_id``, the
  random id of ``optimize/installation.py``). There is no user field, not even an empty one: who the
  person is waits on decision D3, so a user, email, UPN, object id or tenant id passed in any input is
  refused, and no output carries one.
- **Reversal (scenario 10).** A change that never really counted (it never deployed, it was applied
  to the wrong item) moves to ``reversed`` with its reason and time, and the ledger events that net
  its earlier accruals to zero come from ``corrections``: ``corrections.correct`` against a new
  ``attribution.attribute`` result for each run when one is given (made without the reversed
  optimization, so its layer is reversed and the layers after it, which it capped, are
  corrected), otherwise ``corrections.reverse`` of its own layer. A run where the optimization's
  share was never split from the others (an ``unattributed`` layer naming it), or where later
  layers would change, is listed as needing re-attribution; nothing is guessed for it. A **revert**
  is different (plan section 4.5): the change was really in place until it was rolled back, so runs
  before the revert keep their accruals.
- **Retirement (scenario 11).** The workload is retired: the record moves to ``retired`` with its
  time and reason, it accrues nothing for runs from then on (``accrues``, and ``for_attribution``
  ends the optimization there), and ``project`` passes the retirement to ``recurring.project``, so the
  recurring benefit stops.

Figures: a record holds no savings figure. Unknown stays None; nothing is filled in with an average.
Free text (reasons, rollback notes) goes through ``shared._clean`` (credential-shaped text removed),
then has email addresses masked, since a person's identity isn't collected; it must be one line of at
most 500 characters, so a pasted block of code is refused rather than stored.

Times are never stored twice. ``times`` derives them from the history, from the state of each version
and that version's own ``at``: ``implemented_at`` (applied), ``rolled_back_at`` (reverted),
``netted_out_at`` (reversed) and ``retired_at`` (retired), each the ``at`` of the latest version that
moved to that state, or None when none did.

What a writer needs that ``fdopt_recommendations`` doesn't have yet (additive nullable columns, to
be co-signed with Lakehouse optimize; this module changes no schema): ``previous_state`` (S),
``rule_id`` (S) and ``rule_version`` (S). These travel in each row beside the existing columns
(``NO_COLUMN``). The
responsible device is the existing provenance column ``installation_id``; the writer adds
``created_at``, ``app_version`` and ``schema_version``. The reversal's ledger events need the
``fdopt_savings`` columns ESI-404 already lists (layer, cu_hours, result figure and classification,
layer removed, reason).

Pure: nothing here reads or writes a lakehouse, Fabric or this PC. ``written`` is always False.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from .. import measurement as ms
from .. import redact
from ..optimize.shared import TRANSITIONS as STORED_TRANSITIONS
from ..optimize.shared import _clean, record_id, timestamp
from .attribution import UNATTRIBUTED
from . import corrections as co
from . import recurring as rc
from .links import FIRST_VERSION, PROPOSED, RECORD_COLUMNS, optimization_id

# States, as fdopt_recommendations stores them (plan section 4.4), plus reversed and retired.
OPEN, ACCEPTED, APPLIED, VALIDATING, VERIFIED, MONITORING = (
    "open", "accepted", "applied", "validating", "verified", "monitoring")
DISMISSED, REVERTED, SUPERSEDED, ACCEPTED_RISK = "dismissed", "reverted", "superseded", "accepted_risk"
REVERSED, RETIRED = "reversed", "retired"
STATES = (OPEN, PROPOSED, ACCEPTED, APPLIED, VALIDATING, VERIFIED, MONITORING, DISMISSED, REVERTED, SUPERSEDED,
          ACCEPTED_RISK, REVERSED, RETIRED)
# The brief's names for the stored states.
LABELS = {OPEN: "Discovered", PROPOSED: "Proposed", ACCEPTED: "Approved", APPLIED: "Implemented",
          VALIDATING: "Validating", VERIFIED: "Verified", MONITORING: "Monitoring", DISMISSED: "Rejected",
          REVERTED: "Reverted", SUPERSEDED: "Superseded", ACCEPTED_RISK: "Accepted risk", REVERSED: "Reversed",
          RETIRED: "Retired"}
# Every allowed move. An implemented change never goes back to open or accepted: it is reverted,
# reversed, superseded or retired, so its implementation time stays one time. Validation moves
# Validating to Verified (baselines.validate); a material change sends Verified or Monitoring back to
# Validating.
TRANSITIONS: dict[str, frozenset[str]] = {
    OPEN: frozenset({PROPOSED, ACCEPTED, DISMISSED, SUPERSEDED, ACCEPTED_RISK, RETIRED}),
    PROPOSED: frozenset({ACCEPTED, DISMISSED, SUPERSEDED, ACCEPTED_RISK, RETIRED}),
    ACCEPTED: frozenset({OPEN, APPLIED, DISMISSED, SUPERSEDED, ACCEPTED_RISK, RETIRED}),
    APPLIED: frozenset({VALIDATING, REVERTED, REVERSED, SUPERSEDED, RETIRED}),
    VALIDATING: frozenset({VERIFIED, REVERTED, REVERSED, SUPERSEDED, RETIRED}),
    VERIFIED: frozenset({MONITORING, VALIDATING, REVERTED, REVERSED, SUPERSEDED, RETIRED}),
    MONITORING: frozenset({VALIDATING, REVERTED, REVERSED, SUPERSEDED, RETIRED}),
    DISMISSED: frozenset({OPEN, SUPERSEDED}),
    ACCEPTED_RISK: frozenset({OPEN, SUPERSEDED, RETIRED}),
    REVERTED: frozenset({REVERSED, SUPERSEDED, RETIRED}),
    RETIRED: frozenset({REVERSED}),  # a retired change found never to have counted can still be reversed
    SUPERSEDED: frozenset(),
    REVERSED: frozenset(),
}
# What ``history`` accepts when reading: the graph above together with ``shared.TRANSITIONS``, the
# moves Optimize's own writer allows (applied to open or to verified, for example). A row that was
# valid when it was written is never refused on reading; only ``transition`` enforces the stricter
# graph, for new changes.
READ_TRANSITIONS: dict[str, frozenset[str]] = {
    s: TRANSITIONS[s] | frozenset(STORED_TRANSITIONS.get(s, set())) for s in TRANSITIONS}
NEEDS_REASON = frozenset({DISMISSED, ACCEPTED_RISK, SUPERSEDED, REVERTED, REVERSED, RETIRED})
# Columns each version row carries that fdopt_recommendations v2 has no column for yet.
NO_COLUMN = ("previous_state", "rule_id", "rule_version")
# The times ``times`` derives from the history: the state that sets each one.
TIME_OF = {"implemented_at": APPLIED, "rolled_back_at": REVERTED, "netted_out_at": REVERSED, "retired_at": RETIRED}
# The responsible device: provenance's installation_id. Never a user.
DEVICE = "installation_id"
ROW_COLUMNS = RECORD_COLUMNS + (DEVICE,) + NO_COLUMN
CHANGE_KEYS = frozenset({"based_on_version", "state", "at", DEVICE, "reason", "optimized_ref", "rollback"})
ROLLBACK_METHODS = ("redeploy_definition", "restore_setting", "manual")
ROLLBACK_KEYS = frozenset({"method", "restore_ref", "targets", "note"})
MAX_TEXT = 500
# A person's identity waits on decision D3: none of these is accepted or emitted.
IDENTITY_KEYS = frozenset({"user", "user_id", "userid", "username", "user_name", "email", "mail", "upn", "oid",
                           "object_id", "tid", "tenant_id", "display_name", "principal", "principal_name",
                           "changed_by", "owner"})
_IDENTITY_PARTS = ("user", "email", "upn", "oid", "tenant", "principal")
# Fields that would hold code text rather than a fingerprint of it.
CODE_KEYS = frozenset({"code", "code_text", "source_code", "definition", "definition_text", "content",
                       "notebook", "notebook_content", "sql", "script", "text", "body", "diff", "patch"})
_REF = re.compile(r"[A-Za-z0-9._:-]{1,128}")  # as links._REF: a fingerprint, never code
_TARGET = re.compile(r"[A-Za-z0-9._:/-]{1,256}")  # an artifact key (workspace/item), workspace or capacity id


class TransitionRefused(ValueError):
    """A change the lifecycle doesn't allow, with the reason in plain words."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class VersionConflict(ValueError):
    """A change based on a version older than the head. Nothing is merged: ``latest`` is the head and
    ``newer`` every version after the one the change was based on."""

    def __init__(self, based_on: int, latest: dict[str, Any], newer: list[dict[str, Any]]):
        self.based_on = based_on
        self.latest = latest
        self.newer = newer
        super().__init__(
            f"Optimization {latest['optimization_id']} changed since version {based_on}: it is now version "
            f"{latest['version']} ({LABELS.get(latest['state'], latest['state'])}, at {latest['at']}, from device "
            f"{latest.get(DEVICE) or 'unknown'}). Nothing was changed; read it again and make the change on the "
            "latest version.")

    def as_dict(self) -> dict[str, Any]:
        return {"conflict": True, "based_on_version": self.based_on, "latest_version": self.latest["version"],
                "latest": dict(self.latest), "newer": [dict(r) for r in self.newer], "reason": str(self)}


# ---------- checks

def _keys(value: Any, where: str) -> None:
    """Refuse identity and code fields anywhere in ``value`` (nested dicts and lists included)."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            name = str(k).lower()
            if name in IDENTITY_KEYS or any(p in name for p in _IDENTITY_PARTS):
                raise ValueError(f"The {where} has a field {k!r} about a person. Optimization records name the "
                                 "responsible device only; who the person is waits on decision D3 and isn't "
                                 "collected.")
            if name in CODE_KEYS:
                raise ValueError(f"The {where} has a field {k!r} that would hold code. Records hold definition "
                                 "fingerprints, never code text.")
            _keys(v, where)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _keys(v, where)


def _ref(value: Any, what: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise ValueError(f"The {what} must be a definition fingerprint (letters, digits, '.', '_', ':' or '-', at "
                         "most 128 characters), never code text.")
    return value


def _text(value: Any, what: str, *, required: bool = False) -> str:
    """Free text as everyone who reads the lakehouse sees it: credential-shaped text removed
    (``shared._clean``), emails masked, one line of at most ``MAX_TEXT`` characters."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"The {what} must be text, not {type(value).__name__}.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"The {what} is needed, in plain words.")
    if "\n" in value or "\r" in value or any(ord(c) < 32 and c != "\t" for c in value):
        raise ValueError(f"The {what} must be one line of plain words; a block of text or code isn't stored.")
    if len(value) > MAX_TEXT:
        raise ValueError(f"The {what} must be at most {MAX_TEXT} characters.")
    cleaned = _clean(value)
    return str(redact._EMAIL.sub(lambda m: "[email]" if m.group(1) else m.group(0), cleaned))


def _device(value: Any) -> str:
    try:
        ok = isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"The responsible device is this installation's id (a UUID), not {value!r}.")
    return str(value)


def _time(value: Any, what: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        t: dt.datetime | None = value if value.tzinfo is not None else None
    elif isinstance(value, str):
        try:
            t = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            t = None
        if t is not None and t.tzinfo is None:
            t = None
    else:
        t = None
    if t is None:
        raise ValueError(f"The {what} must be an ISO 8601 date and time with its time zone, not {value!r}.")
    return t.astimezone(dt.timezone.utc)


def _version(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < FIRST_VERSION:
        raise ValueError(f"The {what} must be a whole number from {FIRST_VERSION}, not {value!r}.")
    return value


def _rollback(value: Any, original_ref: str | None, optimized_ref: str | None) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("Rollback information says how to undo the change: method, restore_ref, targets and note.")
    extra = sorted(set(value) - ROLLBACK_KEYS)
    if extra:
        raise ValueError(f"Rollback information has {', '.join(map(str, extra))}; it takes "
                         f"{', '.join(sorted(ROLLBACK_KEYS))}.")
    method = value.get("method")
    if method not in ROLLBACK_METHODS:
        raise ValueError(f"The rollback method is one of {', '.join(ROLLBACK_METHODS)}, not {method!r}.")
    restore = _ref(value["restore_ref"], "reference to restore") if "restore_ref" in value else original_ref
    targets = value.get("targets") or []
    if not isinstance(targets, (list, tuple)) or not all(isinstance(t, str) and _TARGET.fullmatch(t)
                                                          for t in targets):
        raise ValueError("Rollback targets are artifact keys (workspace/item), workspace or capacity ids, never "
                         "names or code.")
    plan = {"method": method, "restore_ref": restore, "from_ref": optimized_ref,
            "targets": sorted(set(targets)), "note": _text(value.get("note"), "rollback note"),
            "restorable": restore is not None}
    return json.dumps(plan, sort_keys=True)


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return {k: a.get(k) for k in ROW_COLUMNS} == {k: b.get(k) for k in ROW_COLUMNS}


# ---------- building and reading

def version_record_id(oid: str, version: int) -> str:
    """The id of an optimization's version row: links' proposal id for version 1, and the
    optimization id and version alone after that, so two writers of one version share an id."""
    if version == FIRST_VERSION:
        return record_id("optimization-proposed", oid)
    return record_id("optimization-version", oid, version)


def create(proposal: dict[str, Any], *, installation_id: str) -> dict[str, Any]:
    """Version 1 of a record from a ``links`` proposal, with the responsible device.

    Returns ``record`` (the version row), ``links`` (the optimization_link rows, unchanged) and
    ``written`` (False)."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("record"), dict):
        raise ValueError("A record starts from a proposal of links.from_finding or links.from_opportunity.")
    _keys(proposal, "proposal")
    rec = proposal["record"]
    fp, oid = rec.get("fingerprint"), rec.get("optimization_id")
    if not fp or oid != optimization_id(fp):
        raise ValueError(f"Proposal {fp!r} doesn't carry the optimization id its fingerprint gives.")
    if rec.get("state") != PROPOSED or rec.get("version") != FIRST_VERSION:
        raise ValueError(f"Optimization {oid} must start as version {FIRST_VERSION}, proposed.")
    if rec.get("record_id") != version_record_id(oid, FIRST_VERSION):
        raise ValueError(f"Proposal {oid} doesn't carry the id links gives it; build it with links.")
    if rec.get("rollback") is not None or rec.get("optimized_ref") is not None:
        raise ValueError(f"Proposal {oid} has rollback or an optimized reference; those come when it is applied.")
    extra = sorted(set(rec) - set(RECORD_COLUMNS))
    if extra:
        raise ValueError(f"Proposal {oid} has {', '.join(extra)}, which a record doesn't hold.")
    row = {k: rec.get(k) for k in RECORD_COLUMNS}
    row.update({"original_ref": _ref(rec.get("original_ref"), "original reference"),
                "reason": _text(rec.get("reason"), "reason"), DEVICE: _device(installation_id),
                "previous_state": None, "rule_id": proposal.get("rule_id"), "rule_version": proposal.get("rule_version")})
    _time(row["at"], "proposal time")
    return {"record": row, "links": [dict(x) for x in proposal.get("links") or []], "written": False}


def history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One optimization's version rows, oldest first, or ValueError when they aren't one line.

    The same row twice (a retry, a second PC) counts once. Two different rows at one version, a
    missing version, a row whose move neither the graph nor ``shared.TRANSITIONS`` allows (so rows
    Optimize's own writer made stay readable), or rows of two optimizations are refused: the history
    must have one head."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("A record's history is a non-empty list of its version rows.")
    by_version: dict[int, dict[str, Any]] = {}
    oids: set[Any] = set()
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("A version row must be a row (a dict).")
        _keys(r, "stored record")
        oids.add(r.get("optimization_id"))
        v = _version(r.get("version"), "record version")
        seen = by_version.get(v)
        if seen is not None and not _same(seen, r):
            raise ValueError(f"Optimization {r.get('optimization_id')} has two different rows at version {v} "
                             f"({seen.get('state')} and {r.get('state')}); a change was written without its version "
                             "check, and a person must choose one.")
        by_version.setdefault(v, r)
    if len(oids) != 1:
        raise ValueError("A history holds the rows of one optimization.")
    versions = sorted(by_version)
    if versions != list(range(FIRST_VERSION, FIRST_VERSION + len(versions))):
        raise ValueError(f"Optimization {next(iter(oids))} has versions {versions}; versions run 1, 2, 3 ... without gaps.")
    out = [by_version[v] for v in versions]
    if out[0].get("state") not in (PROPOSED, OPEN):
        raise ValueError(f"Optimization {next(iter(oids))} starts as {out[0].get('state')!r}, not proposed or open.")
    for before, after in zip(out, out[1:], strict=False):
        if after.get("state") not in READ_TRANSITIONS.get(str(before.get("state")), frozenset()):
            raise ValueError(f"Optimization {after.get('optimization_id')} moved from {before.get('state')} to "
                             f"{after.get('state')} at version {after['version']}, which the lifecycle doesn't allow.")
        if after.get("record_id") != version_record_id(str(after.get("optimization_id")), after["version"]):
            raise ValueError(f"Version {after['version']} of optimization {after.get('optimization_id')} doesn't carry "
                             "the id its version gives.")
    return out


def head(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The latest version of one optimization's record."""
    return history(rows)[-1]


def transition(rows: list[dict[str, Any]], change: dict[str, Any]) -> dict[str, Any]:
    """The next version of a record, from one change, or a refusal.

    ``rows``: the record's version rows as read. ``change``: ``based_on_version`` (the version the
    change was made against), ``state`` (where it moves), ``at`` (when, ISO 8601 with time zone),
    ``installation_id`` (the responsible device), ``reason`` (plain words; needed for dismissed,
    accepted risk, superseded, reverted, reversed and retired), and for ``applied`` only
    ``optimized_ref`` (a definition fingerprint) and ``rollback`` (method, restore_ref, targets, note).

    Raises ``VersionConflict`` when ``based_on_version`` isn't the head's version (nothing is merged)
    and ``TransitionRefused`` for a move the graph doesn't allow. Returns ``record`` (the new version
    row), ``previous`` (the head it was based on), ``new`` (False when this very change was already
    recorded: a retry adds nothing), ``times`` (``times`` of the history up to that row),
    ``rollback`` (the plan to carry out, when reverting) and ``written`` (False)."""
    if not isinstance(change, dict):
        raise ValueError("A change is a dict with based_on_version, state, at, installation_id and reason.")
    _keys(change, "change")
    extra = sorted(set(change) - CHANGE_KEYS)
    if extra:
        raise ValueError(f"A change has {', '.join(map(str, extra))}, which a record doesn't hold. It takes "
                         f"{', '.join(sorted(CHANGE_KEYS))}.")
    based_on = _version(change.get("based_on_version"), "version the change was based on")
    chain = history(rows)
    current = chain[-1]
    if based_on > current["version"]:
        raise ValueError(f"Optimization {current['optimization_id']} has no version {based_on} yet; its latest is "
                         f"{current['version']}.")
    state = change.get("state")
    device = _device(change.get(DEVICE))
    if based_on < current["version"]:
        retry = _build(chain[based_on - 1], change, state, device)
        if isinstance(retry, dict) and _same(retry, chain[based_on]):
            return {"record": chain[based_on], "previous": chain[based_on - 1], "new": False,
                    "times": _times(chain[:based_on + 1]),
                    "rollback": _plan(chain[based_on]) if state == REVERTED else None, "written": False}
        raise VersionConflict(based_on, current, chain[based_on:])
    row = _build(current, change, state, device)
    if isinstance(row, str):
        raise TransitionRefused(row)
    return {"record": row, "previous": current, "new": True, "times": _times(chain + [row]),
            "rollback": _plan(row) if state == REVERTED else None, "written": False}


def _build(current: dict[str, Any], change: dict[str, Any], state: Any, device: str) -> dict[str, Any] | str:
    """The row ``change`` makes on top of ``current``, or the reason it is refused."""
    was = current["state"]
    oid = current["optimization_id"]
    if state not in STATES:
        return f"{state!r} isn't a state of an optimization; it is one of {', '.join(STATES)}."
    if state not in TRANSITIONS[was]:
        allowed = ", ".join(sorted(TRANSITIONS[was])) or "nothing: it is final"
        return (f"Optimization {oid} is {LABELS[was]} ({was}), so it can't become {LABELS[state]} ({state}). From "
                f"{was} it can move to {allowed}.")
    reason = _text(change.get("reason"), "reason", required=state in NEEDS_REASON)
    when = _time(change.get("at"), "time of the change")
    prev = _time(current["at"], "time of the latest version")
    if when <= prev:  # a PC whose clock is behind still sorts after the version it changed
        when = prev + dt.timedelta(microseconds=1)
    stamp = timestamp(when)
    optimized, rollback = current.get("optimized_ref"), current.get("rollback")
    if state == APPLIED:
        optimized = _ref(change.get("optimized_ref"), "optimized reference")
        if optimized is None:
            return f"Optimization {oid} can't be applied without the optimized definition's fingerprint."
        if change.get("rollback") is None:
            return f"Optimization {oid} can't be applied without rollback information: what to restore, and from which reference."
        rollback = _rollback(change["rollback"], current.get("original_ref"), optimized)
    elif change.get("optimized_ref") is not None or change.get("rollback") is not None:
        return "The optimized reference and rollback information are given when the change is applied, and only then."
    if state == REVERTED and not rollback:
        return f"Optimization {oid} has no rollback information, so there is nothing recorded to restore."
    version = current["version"] + 1
    row = {k: current.get(k) for k in ROW_COLUMNS}
    row.update({"record_id": version_record_id(oid, version), "state": state, "reason": reason, "at": stamp,
                "version": version, "optimized_ref": optimized, "rollback": rollback, DEVICE: device,
                "previous_state": was})
    return row


def _times(chain: list[dict[str, Any]]) -> dict[str, str | None]:
    out: dict[str, str | None] = dict.fromkeys(TIME_OF)
    for row in chain:  # oldest first, so the latest version that set a time wins
        for name, state in TIME_OF.items():
            if row.get("state") == state:
                out[name] = row.get("at")
    return out


def times(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    """When the change was implemented, rolled back, netted out and retired, from the history: each
    the ``at`` of the latest version that moved to that state, or None when none did."""
    return _times(history(rows))


def _plan(row: dict[str, Any]) -> dict[str, Any] | None:
    if not row.get("rollback"):
        return None
    plan: dict[str, Any] = json.loads(row["rollback"])
    return plan | {"optimization_id": row["optimization_id"], "reason": row.get("reason")}


def rollback_plan(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What to restore and from which reference, as recorded when the change was applied; None
    before it was applied."""
    return _plan(head(rows))


# ---------- reversal, retirement and what accrues

def _layer_order(e: dict[str, Any]) -> int:
    v = e.get("layer_order")
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def reverse(rows: list[dict[str, Any]], *, based_on_version: int, at: str, reason: str, installation_id: str,
            ledger_events: list[dict[str, Any]], reattributed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Reverse an optimization that never really counted (scenario 10): the record moves to
    ``reversed`` with ``reason`` and ``at``, and the ledger events that net its accruals to zero.

    ``ledger_events``: the ledger events of every run credited to it (accruals, regressions and
    their corrections, as ``accruals.accrue`` or ``corrections`` make them). ``reattributed``
    (optional): a new ``attribution.attribute`` result for some of those runs, made without this
    optimization (leave it out, or pass the reversed record's ``for_attribution``); one that still
    credits it is refused. For those runs the events come from ``corrections.correct``; for the
    others from ``corrections.reverse`` of the optimization's own layer.

    Returns ``record`` (the new version), ``events`` (new ledger events, none written),
    ``needs_reattribution`` (runs whose other layers depend on this one, or whose share of it was
    never split, with the reason) and ``written`` (False). Raises like ``transition``."""
    moved = transition(rows, {"based_on_version": based_on_version, "state": REVERSED, "at": at,
                              "reason": reason, DEVICE: installation_id})
    row = moved["record"]
    oid = str(row["optimization_id"])
    why = f"Optimization {oid} was reversed: {row['reason']}"
    if not isinstance(ledger_events, list):
        raise ValueError("Reversing needs the ledger events of the runs credited to the optimization, as a list.")
    _keys(ledger_events, "ledger events")
    by_run: dict[str, list[dict[str, Any]]] = {}
    for e in ledger_events:
        if not isinstance(e, dict) or not e.get("execution_id"):
            raise ValueError("A ledger event needs its execution_id.")
        by_run.setdefault(str(e["execution_id"]), []).append(e)
    latest: dict[str, dict[str, Any]] = {}
    for r in reattributed or []:
        if not isinstance(r, dict) or not r.get("execution_id"):
            raise ValueError("A re-attribution is an attribution.attribute result.")
        if oid in [str(x) for x in r.get("optimizations") or []] or any(
                str(x.get("optimization_id")) == oid or oid in [str(i) for i in x.get("optimization_ids") or []]
                for x in r.get("layers") or []):
            raise ValueError(f"The re-attribution of run {r['execution_id']} still credits optimization {oid}; "
                             "attribute it with for_attribution of the reversed record.")
        latest[str(r["execution_id"])] = r
    events: list[dict[str, Any]] = []
    needs: list[dict[str, Any]] = []
    for eid in sorted(by_run):
        run_events = by_run[eid]
        own = [e for e in run_events if str(e.get("layer")) == oid]
        mixed = [e for e in run_events if e.get("layer") == UNATTRIBUTED
                 and oid in [str(i) for i in e.get("optimization_ids") or []]]
        if not own and not mixed:
            continue
        if eid in latest:
            events += co.correct(run_events, latest[eid], reason=why)
            continue
        if mixed:
            needs.append({"execution_id": eid, "reason": (
                f"Run {eid}'s share for optimization {oid} was never split from the others, so nothing is reversed "
                "until it is attributed again without it.")})
            continue
        events += co.reverse(run_events, reason=why, layers=[oid])
        first = min(_layer_order(e) for e in own)
        if any(_layer_order(e) > first for e in run_events if str(e.get("layer")) != oid):
            needs.append({"execution_id": eid, "reason": (
                f"Run {eid}'s later layers started from what optimization {oid} left, so they may change; attribute "
                "it again without it and write the difference as corrections.")})
    return {"record": row, "previous": moved["previous"], "new": moved["new"], "times": moved["times"],
            "events": events,
            "needs_reattribution": needs, "written": False}


def retire(rows: list[dict[str, Any]], *, based_on_version: int, at: str, reason: str,
           installation_id: str) -> dict[str, Any]:
    """The workload is retired (scenario 11): the record moves to ``retired`` with ``reason`` and
    ``at``. From then on it accrues nothing and its workload's recurring benefit isn't projected."""
    return transition(rows, {"based_on_version": based_on_version, "state": RETIRED, "at": at, "reason": reason,
                             DEVICE: installation_id})


def accrues(rows: list[dict[str, Any]], run_at: Any) -> bool:
    """Whether a run at ``run_at`` may accrue to this optimization: it was implemented at or before
    the run, wasn't rolled back or retired at or before it, and was never netted out (reversed)."""
    chain = history(rows)
    when = _times(chain)
    t = _time(run_at, "run time")
    implemented = ms.parse_time(when["implemented_at"])
    if implemented is None or implemented > t or chain[-1]["state"] == REVERSED:
        return False
    for key in ("rolled_back_at", "retired_at"):
        end = ms.parse_time(when[key])
        if end is not None and end <= t:
            return False
    return True


def for_attribution(rows: list[dict[str, Any]], *, after: dict[str, Any] | None,
                    action: str | None = None) -> dict[str, Any]:
    """The record as ``attribution.attribute`` takes an optimization. ``after``: the measured or
    estimated CU per run with it in place (None when not known yet). The end of its effect is the
    rollback or the retirement, whichever came first; attribution's input names that ``reverted_at``.
    A netted-out (reversed) one is given as reverted with no time, which attribution leaves out of
    every run: it never counted."""
    chain = history(rows)
    h, when = chain[-1], _times(chain)
    ends = [t for t in (ms.parse_time(when["rolled_back_at"]), ms.parse_time(when["retired_at"])) if t is not None]
    state, end = h["state"], (min(ends).isoformat() if ends else None)
    if state == REVERSED:
        state, end = REVERTED, None
    return {"optimization_id": h["optimization_id"], "implemented_at": when["implemented_at"], "action": action,
            "state": state, "reverted_at": end, "after": after}


def project(rows: list[dict[str, Any]], results: list[dict[str, Any]], *, workload_key: str, baseline_version: str,
            now: Any, **kwargs: Any) -> dict[str, Any]:
    """``recurring.project`` for the workload this record is about, stopped once it is retired."""
    if "retired_at" in kwargs:
        raise ValueError("The retirement comes from the record, not from the caller.")
    return rc.project(results, workload_key=workload_key, baseline_version=baseline_version, now=now,
                      retired_at=times(rows)["retired_at"], **kwargs)
