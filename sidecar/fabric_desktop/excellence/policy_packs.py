"""Policy packs (FEA-504): an organization's own policy for the assessment, versioned and auditable.

A pack is plain JSON::

    {"id": "contoso-fabric", "version": "1.2.0", "name": "Contoso Fabric policy", "owner": "Data platform team",
     "created": "2026-11-21", "description": "optional",
     "rules": {
        "naming":             {"workspace": {"pattern": "^(dev|test|prod)-[a-z0-9-]+$", "max_length": 64},
                               "item": {"pattern": "^[A-Za-z][A-Za-z0-9_]*$", "max_length": 80,
                                        "item_types": ["Lakehouse", "Notebook"]}},
        "severity_overrides": {"FEA-SEC-001": "critical",
                               "FEA-SEC-002": {"severity": "medium", "reason": "Guests are vetted partners"}},
        "disabled":           [{"control_id": "FEA-GOV-001", "reason": "Descriptions live in the wiki"}],
        "weights":            {"architecture": 12, ..., "local_development": 3},
        "applicability":      {"FEA-DE-001": {"item_types": ["Notebook"], "workspaces": ["^prod-.*$"]}}}}

Every rule is optional. What each one does when the pack is applied to a profile (:func:`apply`):

- ``naming``: the organization's naming patterns, kept with the profile's audit record and checked by
  :func:`naming_violations`. No control scores them yet.
- ``severity_overrides``: a control's severity (low, medium, high, critical). Lowering a control
  that is critical needs ``{"severity", "reason"}``; the reason is kept in the audit record.
- ``disabled``: controls turned off, each with a reason. They become *not applicable*, never passed,
  and the assessment lists them with the pack's id, version and fingerprint.
- ``weights``: the dimension weights, every dimension named, finite numbers of 0 or more, adding up to
  100. Replaces the profile's.
- ``applicability``: where a control applies: item types (a subset of the types it covers, item
  controls only) and workspace name patterns. Elsewhere it is *not applicable* and the assessment
  counts it as out of scope for the pack. A workspace whose name isn't known is assessed as usual:
  a control is never left out on a guess.

Patterns are built on the governance checks registry's matcher (governance_checks._full_match): RE2,
which matches in time linear in the name, so no pattern can backtrack. On top of that a pack is
refused when a pattern is over :data:`MAX_PATTERN` characters, nests a repeating quantifier inside a
repeated group (``(a+)+``, the classic catastrophic-backtracking shape), or isn't valid RE2 (lookarounds
and backreferences aren't). A pattern matches the whole name.

:func:`fingerprint` is the SHA-256 of the pack's canonical JSON; :func:`diff` is the audit trail between
two versions. Everything here is pure: nothing reads Fabric, files or the network, and nothing given
is changed.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any

from . import controls as ct
from . import profiles as pf
from .evidence import canonical

FIELDS = ("id", "version", "name", "owner", "created", "rules")
OPTIONAL_FIELDS = ("description",)
RULES = ("naming", "severity_overrides", "disabled", "weights", "applicability")
OVERRIDE_SEVERITIES = ("low", "medium", "high", "critical")  # informational would weigh 0: that's disabling
RANK = {s: i for i, s in enumerate(("informational", "low", "medium", "high", "critical"))}
MAX_PATTERN = 200
MAX_NAME = 256          # governance_checks.MAX_NAME: a longer name isn't checked
MAX_PATTERNS = 20       # per list
MAX_TEXT = 500          # names, owners, reasons
MAX_BYTES = 256 * 1024  # a pack's JSON text
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_BOUNDED = re.compile(r"\{(\d+)(,(\d*))?\}")


class PolicyPackError(ValueError):
    """A pack that can't be used. ``problems`` lists everything wrong with it."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("policy pack: " + "; ".join(problems))
        self.problems = list(problems)


# ---------------------------------------------------------------------------------------------- patterns


def _repeats(quant: str) -> bool:
    """Whether a quantifier can repeat its atom (``*``, ``+``, ``{n,}``, ``{n,m}`` with m > 1)."""
    if quant in ("*", "+"):
        return True
    m = _BOUNDED.fullmatch(quant)
    if not m or quant == "?":
        return False
    if m.group(2) is None:
        return int(m.group(1)) > 1
    return m.group(3) == "" or int(m.group(3)) > 1


def _variable(quant: str) -> bool:
    """Whether a quantifier matches a varying number of times (everything but ``{n}`` and ``{n,n}``)."""
    m = _BOUNDED.fullmatch(quant)
    if not m:
        return True
    return m.group(2) is not None and m.group(3) != m.group(1)


def nested_quantifier(pattern: str) -> bool:
    """Whether ``pattern`` repeats a group that itself holds a variable quantifier, like ``(a+)+``,
    ``(a?)*`` or ``((ab)*c){2,}``. A static scan, linear in the pattern: escapes and character classes are skipped."""
    stack = [False]  # per open group: whether it holds a quantifier that matches a varying number of times
    prev: tuple[str, bool] | None = None  # the atom just before: ("group", held one) or ("atom", False)
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            prev = ("atom", False)
            continue
        if ch == "[":
            i += 1
            if i < n and pattern[i] == "^":
                i += 1
            if i < n and pattern[i] == "]":
                i += 1
            while i < n and pattern[i] != "]":
                i += 2 if pattern[i] == "\\" else 1
            i += 1
            prev = ("atom", False)
            continue
        if ch == "(":
            stack.append(False)
            i += 1
            if i < n and pattern[i] == "?":  # (?:  (?i)  (?P<name>  -- the flags aren't a quantifier
                i += 1
                while i < n and pattern[i] not in ":)>":
                    i += 1
                if i < n and pattern[i] in ":>":
                    i += 1
            prev = None
            continue
        if ch == ")":
            inner = stack.pop() if len(stack) > 1 else False
            stack[-1] = stack[-1] or inner
            prev = ("group", inner)
            i += 1
            continue
        quant = ""
        if ch in "*+?":
            quant = ch
        elif ch == "{":
            m = _BOUNDED.match(pattern, i)
            quant = m.group(0) if m else ""
        if quant:
            if _repeats(quant) and prev is not None and prev[0] == "group" and prev[1]:
                return True
            if _variable(quant):
                stack[-1] = True
            i += len(quant)
            if i < n and pattern[i] in "?+":  # lazy or possessive suffix
                i += 1
            prev = ("quantified", False)
            continue
        prev = ("atom", False)
        i += 1
    return False


def pattern_problems(pattern: Any, where: str) -> list[str]:
    """Why ``pattern`` can't be used; empty when it can. Fast whatever the pattern: the length is
    checked first, the scan is linear, and RE2 only compiles it against an empty name."""
    if not isinstance(pattern, str) or not pattern:
        return [f"{where}: a pattern is a non-empty string"]
    if len(pattern) > MAX_PATTERN:
        return [f"{where}: the pattern is {len(pattern)} characters, over {MAX_PATTERN}"]
    if nested_quantifier(pattern):
        return [(f"{where}: the pattern repeats a group that repeats ({pattern}); nested quantifiers can "
                "backtrack catastrophically, so they aren't accepted")]
    try:
        _full_match(pattern, "")
    except ValueError as exc:
        return [f"{where}: the pattern isn't valid ({exc}); lookarounds and backreferences aren't supported"]
    return []


def _full_match(pattern: str, text: str) -> bool:
    from .. import governance_checks  # the registry's RE2 matcher; imported late, it registers the checkers
    return governance_checks._full_match(pattern, text)


def matches(pattern: str, name: str) -> bool:
    """Whether the whole of ``name`` matches ``pattern`` (validated first), with RE2."""
    return _full_match(pattern, name)


# ---------------------------------------------------------------------------------------------- validate


def _number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _text(x: Any, limit: int = MAX_TEXT) -> bool:
    return isinstance(x, str) and bool(x.strip()) and len(x) <= limit


def _override(value: Any) -> tuple[Any, str]:
    """A severity override's (severity, reason): a bare severity, or {"severity", "reason"}."""
    if isinstance(value, dict):
        return value.get("severity"), str(value.get("reason") or "").strip()
    return value, ""


def _default_registry() -> ct.Registry:
    from . import catalog  # imported late: the catalog is big and imports most of the engine
    return catalog.registry()


def _known(registry: ct.Registry) -> dict[str, ct.Control]:
    return {c.id: c for c in registry.controls()}


def _string_list(value: Any, where: str, out: list[str]) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > MAX_PATTERNS:
        out.append(f"{where}: a list of 1 to {MAX_PATTERNS} strings")
        return []
    if any(not isinstance(v, str) or not v for v in value):
        out.append(f"{where}: a list of 1 to {MAX_PATTERNS} strings")
        return []
    if len(set(value)) != len(value):
        out.append(f"{where}: lists something twice")
    return value


def _naming(naming: Any, out: list[str]) -> None:
    if not isinstance(naming, dict) or not naming or set(naming) - {"workspace", "item"}:
        out.append("rules.naming: an object with workspace and/or item")
        return
    for what, rule in naming.items():
        where = f"rules.naming.{what}"
        allowed = {"pattern", "max_length"} | ({"item_types"} if what == "item" else set())
        if not isinstance(rule, dict) or not rule or set(rule) - allowed or not {"pattern", "max_length"} & set(rule):
            out.append(f"{where}: an object with pattern and/or max_length" + (" (and item_types)" if what == "item" else ""))
            continue
        if "pattern" in rule:
            out += pattern_problems(rule["pattern"], f"{where}.pattern")
        if "max_length" in rule:
            m = rule["max_length"]
            if not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= MAX_NAME:
                out.append(f"{where}.max_length: a whole number from 1 to {MAX_NAME}")
        if "item_types" in rule:
            _string_list(rule["item_types"], f"{where}.item_types", out)


def validate(pack: Any, registry: ct.Registry | None = None) -> list[str]:
    """Everything wrong with ``pack``; empty when it can be applied. Control ids are checked against
    ``registry`` (by default the assessment's catalog)."""
    out: list[str] = []
    if not isinstance(pack, dict):
        return ["a policy pack is a JSON object"]
    missing = [f for f in FIELDS if f not in pack]
    if missing:
        out.append("missing " + ", ".join(missing))
    unknown = sorted(set(pack) - set(FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        out.append("unknown fields " + ", ".join(map(str, unknown)))
    if "id" in pack and not (isinstance(pack["id"], str) and _ID.fullmatch(pack["id"])):
        out.append("id: lower-case letters, digits, '.', '_' or '-', at most 64, like contoso-fabric")
    if "version" in pack and not (isinstance(pack["version"], str) and _VERSION.fullmatch(pack["version"])):
        out.append("version: like 1.0.0")
    for f in ("name", "owner"):
        if f in pack and not _text(pack[f]):
            out.append(f"{f}: a non-empty string of at most {MAX_TEXT} characters")
    if "description" in pack and not (isinstance(pack["description"], str) and len(pack["description"]) <= 4000):
        out.append("description: a string of at most 4000 characters")
    if "created" in pack and not _iso(pack["created"]):
        out.append("created: an ISO 8601 date or date and time, like 2026-11-21")
    rules = pack.get("rules")
    if "rules" not in pack:
        return out
    if not isinstance(rules, dict):
        return out + ["rules: an object"]
    bad_rules = sorted(set(rules) - set(RULES))
    if bad_rules:
        out.append("unknown rules " + ", ".join(map(str, bad_rules)))
    known = _known(registry or _default_registry())

    def control(cid: Any, where: str) -> ct.Control | None:
        if not isinstance(cid, str) or cid not in known:
            out.append(f"{where}: unknown control {cid!r}")
            return None
        return known[cid]

    if "naming" in rules:
        _naming(rules["naming"], out)

    overrides = rules.get("severity_overrides", {})
    if not isinstance(overrides, dict):
        out.append("rules.severity_overrides: an object of control id to severity")
        overrides = {}
    for cid, value in overrides.items():
        where = f"rules.severity_overrides.{cid}"
        c = control(cid, where)
        if isinstance(value, dict) and set(value) - {"severity", "reason"}:
            out.append(f"{where}: an object has only severity and reason")
        sev, reason = _override(value)
        if sev not in OVERRIDE_SEVERITIES:
            out.append(f"{where}: severity is one of {', '.join(OVERRIDE_SEVERITIES)}")
            continue
        if isinstance(value, dict) and "reason" in value and not _text(value["reason"]):
            out.append(f"{where}: a reason is a non-empty string of at most {MAX_TEXT} characters")
        elif c is not None and c.severity == "critical" and RANK[sev] < RANK["critical"] and not reason:
            out.append(f"{where}: lowering a critical control needs a reason ({{\"severity\": \"{sev}\", \"reason\": ...}})")

    disabled = rules.get("disabled", [])
    off: set[str] = set()
    if not isinstance(disabled, list):
        out.append("rules.disabled: a list of {control_id, reason}")
        disabled = []
    for i, d in enumerate(disabled):
        where = f"rules.disabled[{i}]"
        if not isinstance(d, dict) or set(d) != {"control_id", "reason"}:
            out.append(f"{where}: {{control_id, reason}}, a reason is required")
            continue
        control(d["control_id"], where)
        if not _text(d["reason"]):
            out.append(f"{where}: a reason is required (a non-empty string of at most {MAX_TEXT} characters)")
        if d["control_id"] in off:
            out.append(f"{where}: {d['control_id']} is disabled twice")
        off.add(str(d["control_id"]))

    if "weights" in rules:
        w = rules["weights"]
        if not isinstance(w, dict):
            out.append("rules.weights: an object of dimension to weight")
        else:
            if set(w) != set(ct.DIMENSIONS):
                out.append("rules.weights: name every dimension, and only those (" + ", ".join(ct.DIMENSIONS) + ")")
            if any(not _number(x) or x < 0 for x in w.values()):
                out.append("rules.weights: each weight is a finite number of 0 or more (no NaN, infinity or negative)")
            elif w and abs(sum(w.values()) - 100) > 1e-9:
                out.append(f"rules.weights: add up to {sum(w.values()):g}, not 100")

    app = rules.get("applicability", {})
    if not isinstance(app, dict):
        out.append("rules.applicability: an object of control id to {item_types, workspaces}")
        app = {}
    for cid, scope in app.items():
        where = f"rules.applicability.{cid}"
        c = control(cid, where)
        if not isinstance(scope, dict) or not scope or set(scope) - {"item_types", "workspaces"}:
            out.append(f"{where}: an object with item_types and/or workspaces")
            continue
        if c is not None and c.scope == "capacity":
            out.append(f"{where}: a capacity control can't be scoped by item type or workspace name")
        if "item_types" in scope:
            types = _string_list(scope["item_types"], f"{where}.item_types", out)
            if c is not None and c.scope != "item":
                out.append(f"{where}.item_types: only item controls cover item types")
            elif c is not None and set(types) - set(c.item_types):
                out.append(f"{where}.item_types: {c.id} covers only {', '.join(c.item_types)}; a pack can narrow "
                           "that, not widen it")
        if "workspaces" in scope:
            for j, p in enumerate(_string_list(scope["workspaces"], f"{where}.workspaces", out)):
                out += pattern_problems(p, f"{where}.workspaces[{j}]")
        if cid in off:
            out.append(f"{where}: {cid} is disabled; scoping it too is ambiguous")
    for cid in overrides:
        if cid in off:
            out.append(f"rules.severity_overrides.{cid}: {cid} is disabled; overriding its severity too is ambiguous")
    return out


def _iso(x: Any) -> bool:
    if not isinstance(x, str) or not 10 <= len(x) <= 40:
        return False
    for parse in (dt.date.fromisoformat, dt.datetime.fromisoformat):
        try:
            parse(x.replace("Z", "+00:00"))
            return True
        except ValueError:
            continue
    return False


def checked(pack: Any, registry: ct.Registry | None = None) -> dict[str, Any]:
    """``pack``, or PolicyPackError listing every problem."""
    bad = validate(pack, registry)
    if bad:
        raise PolicyPackError(bad)
    return pack


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} isn't a number a pack can hold")


def loads(text: str | bytes, registry: ct.Registry | None = None) -> dict[str, Any]:
    """A pack from its JSON text, checked. NaN and Infinity are refused while parsing (Python's json
    would otherwise accept them), as is text over :data:`MAX_BYTES`."""
    raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if len(raw) > MAX_BYTES:
        raise PolicyPackError([f"the pack is {len(raw)} bytes, over {MAX_BYTES}"])
    try:
        pack = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PolicyPackError([f"not valid JSON ({exc})"]) from exc
    return checked(pack, registry)


# ---------------------------------------------------------------------------------------------- audit


def fingerprint(pack: dict[str, Any]) -> str:
    """The SHA-256 (hex) of the pack's canonical JSON: sorted keys, no spacing. The same pack always
    gives the same fingerprint, whatever order its keys were written in."""
    return hashlib.sha256(canonical(pack).encode("utf-8")).hexdigest()


def ref(pack: dict[str, Any]) -> dict[str, Any]:
    return {"id": pack.get("id"), "version": pack.get("version"), "fingerprint": fingerprint(pack)}


def _flat(pack: dict[str, Any]) -> dict[str, Any]:
    """path -> value, one level per rule key; disabled controls keyed by id so a reorder isn't a change."""
    out: dict[str, Any] = {}
    for k, v in pack.items():
        if k != "rules":
            out[k] = v
    rules = pack.get("rules") or {}
    for rule, body in rules.items():
        if rule == "disabled" and isinstance(body, list):
            for d in body:
                if isinstance(d, dict):
                    out[f"rules.disabled.{d.get('control_id')}"] = d.get("reason")
        elif isinstance(body, dict):
            for k, v in body.items():
                out[f"rules.{rule}.{k}"] = v
        else:
            out[f"rules.{rule}"] = body
    return out


def _sev(v: Any) -> Any:
    return _override(v)[0]


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What changed from ``old`` to ``new``: one entry per changed path (``added``, ``removed`` or
    ``changed``, with both values), the changes that can raise a score (``relaxes``: a control newly
    disabled, its severity lowered or its override removed, its applicability added or changed, weights
    changed), and ``problems`` an auditor should see (another pack, or content changed without a higher
    version)."""
    a, b = _flat(old), _flat(new)
    changes = []
    for path in sorted(set(a) | set(b)):
        if path not in a:
            changes.append({"path": path, "change": "added", "old": None, "new": copy.deepcopy(b[path])})
        elif path not in b:
            changes.append({"path": path, "change": "removed", "old": copy.deepcopy(a[path]), "new": None})
        elif canonical(a[path]) != canonical(b[path]):
            changes.append({"path": path, "change": "changed", "old": copy.deepcopy(a[path]), "new": copy.deepcopy(b[path])})
    relaxes = []
    for c in changes:
        p = c["path"]
        if p.startswith("rules.disabled.") and c["change"] == "added":
            relaxes.append(p)
        elif p.startswith("rules.severity_overrides."):
            before, after = _sev(c["old"]), _sev(c["new"])
            if c["change"] == "removed" or (before in RANK and after in RANK and RANK[after] < RANK[before]):
                relaxes.append(p)
        elif p.startswith("rules.applicability.") and c["change"] in ("added", "changed"):
            relaxes.append(p)
        elif p.startswith("rules.weights"):
            relaxes.append(p)
    problems = []
    if old.get("id") != new.get("id"):
        problems.append(f"these are different packs ({old.get('id')} and {new.get('id')})")
    content = [c for c in changes if c["path"] not in ("version", "created")]
    vo, vn = _version(old.get("version")), _version(new.get("version"))
    if content and vo is not None and vn is not None and vn <= vo:
        problems.append(f"the content changed but the version didn't go up ({old.get('version')} to {new.get('version')})")
    return {"from": ref(old), "to": ref(new), "same": fingerprint(old) == fingerprint(new),
            "changes": changes, "relaxes": relaxes, "problems": problems}


def _version(v: Any) -> tuple[int, ...] | None:
    m = _VERSION.fullmatch(v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


# ---------------------------------------------------------------------------------------------- apply


def apply(profile: pf.Profile, pack: dict[str, Any], registry: ct.Registry | None = None) -> pf.Profile:
    """A new profile: ``profile`` with ``pack`` applied. Neither is changed. Refused (PolicyPackError)
    when the pack has problems, when the profile already carries a pack, or when the pack lowers a
    control the profile makes critical without a reason."""
    reg = registry or _default_registry()
    checked(pack, reg)
    if profile.policy_pack is not None:
        raise PolicyPackError([(f"the profile already has policy pack {profile.policy_pack.get('id')}; "
                               "apply a pack to a profile without one")])
    pack = copy.deepcopy(pack)
    rules = pack["rules"]
    known = _known(reg)
    tag = f"policy pack {pack['id']}@{pack['version']}"
    overrides = dict(profile.severity_overrides)
    audit_overrides = []
    for cid, value in sorted((rules.get("severity_overrides") or {}).items()):
        sev, reason = _override(value)
        before = profile.severity_overrides.get(cid, known[cid].severity)
        if before == "critical" and RANK[sev] < RANK["critical"] and not reason:
            raise PolicyPackError([(f"rules.severity_overrides.{cid}: the {profile.title} profile makes it critical; "
                                   "lowering it needs a reason")])
        overrides[cid] = sev
        audit_overrides.append({"control_id": cid, "from": before, "to": sev, "reason": reason or None})
    disabled = dict(profile.disabled)
    audit_disabled = []
    for d in sorted(rules.get("disabled") or [], key=lambda d: d["control_id"]):
        disabled[d["control_id"]] = f"{d['reason'].strip()} ({tag})"
        audit_disabled.append({"control_id": d["control_id"], "reason": d["reason"].strip()})
    weights = {d: float(rules["weights"][d]) for d in ct.DIMENSIONS} if "weights" in rules else dict(profile.weights)
    applicability = {cid: {k: list(v) for k, v in sorted(scope.items())}
                     for cid, scope in sorted((rules.get("applicability") or {}).items())}
    record = {"id": pack["id"], "version": pack["version"], "name": pack["name"], "owner": pack["owner"],
              "created": pack["created"], "fingerprint": fingerprint(pack),
              "base_profile": {"id": profile.id, "version": profile.version, "fingerprint": profile.fingerprint()},
              "disabled": audit_disabled, "severity_overrides": audit_overrides,
              "weights": dict(weights) if "weights" in rules else None,
              "applicability": copy.deepcopy(applicability), "naming": copy.deepcopy(rules.get("naming"))}
    return pf.checked(dataclasses.replace(
        profile, id=f"{profile.id}+{pack['id']}", version=f"{profile.version}+{pack['version']}",
        title=f"{profile.title} with {pack['name']}", weights=weights,
        severity_weights=dict(profile.severity_weights), severity_overrides=overrides, disabled=disabled,
        applicability=applicability, policy_pack=record))


def _known_name(subj: dict[str, Any]) -> str | None:
    """A subject's name when it was read (evidence.subject falls back to the id), else None."""
    name = subj.get("name")
    if not isinstance(name, str) or not name or name == subj.get("id") or len(name) > MAX_NAME:
        return None
    return name


def out_of_scope(profile: pf.Profile, control_id: str, subj: dict[str, Any], ws: dict[str, Any]) -> str | None:
    """Why the profile's pack puts ``control_id`` out of scope on ``subj`` (in workspace ``ws``), or
    None when it applies. A workspace whose name isn't known is never put out of scope."""
    scope = profile.applicability.get(control_id)
    if not scope:
        return None
    pack = profile.policy_pack or {}
    tag = f"policy pack {pack.get('id')}@{pack.get('version')}"
    types = scope.get("item_types")
    if types and subj.get("type") == "item" and subj.get("item_type") not in types:
        return f"Out of scope in {tag}: it applies only to {', '.join(types)} items."
    patterns = scope.get("workspaces")
    if patterns and ws.get("type") == "workspace":
        name = _known_name(ws)
        if name is not None and not any(matches(p, name) for p in patterns):
            return f"Out of scope in {tag}: the workspace name {name} matches none of its workspace patterns."
    return None


def effects(profile: pf.Profile, outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What the profile's pack did to an assessment, for the snapshot: the pack's id, version and
    fingerprint, the controls it turned off, the outcomes it put out of scope, and its overrides. None
    without a pack."""
    pack = profile.policy_pack
    if pack is None:
        return None
    scoped: dict[str, int] = {}
    for o in outcomes:
        if (o.get("policy") or {}).get("effect") == "out_of_scope":
            scoped[o["control_id"]] = scoped.get(o["control_id"], 0) + 1
    return {"id": pack["id"], "version": pack["version"], "name": pack["name"], "owner": pack["owner"],
            "fingerprint": pack["fingerprint"], "base_profile": copy.deepcopy(pack["base_profile"]),
            "disabled_controls": copy.deepcopy(pack["disabled"]),
            "out_of_scope": [{"control_id": k, "outcomes": v} for k, v in sorted(scoped.items())],
            "severity_overrides": copy.deepcopy(pack["severity_overrides"]),
            "weights_changed": pack["weights"] is not None,
            "note": "Controls this pack turns off or puts out of scope are not applicable: they are left out of "
                    "the score, never passed. Compare with an assessment without the pack to see the difference."}


# ---------------------------------------------------------------------------------------------- naming


def naming_violations(pack: dict[str, Any], workspace_name: str | None,
                      items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The names that break the pack's naming rules: the workspace's (when known) and its items'
    (``{"id", "type", "name"}``). Each: object_type, id, name, rule (pattern or max_length), text. A
    name over :data:`MAX_NAME` characters is reported as too long to check, never matched."""
    naming = (pack.get("rules") or {}).get("naming") or {}
    out: list[dict[str, Any]] = []

    def check(rule: dict[str, Any], object_type: str, oid: str, name: str) -> None:
        if len(name) > MAX_NAME:
            out.append({"object_type": object_type, "id": oid, "name": name[:40] + "...", "rule": "max_length",
                        "text": f"{len(name)} characters, too long to check."})
            return
        limit = rule.get("max_length")
        if limit is not None and len(name) > limit:
            out.append({"object_type": object_type, "id": oid, "name": name, "rule": "max_length",
                        "text": f"{name} is {len(name)} characters; at most {limit}."})
        if rule.get("pattern") and not matches(rule["pattern"], name):
            out.append({"object_type": object_type, "id": oid, "name": name, "rule": "pattern",
                        "text": f"{name} doesn't match the naming pattern {rule['pattern']}."})

    if naming.get("workspace") and workspace_name:
        check(naming["workspace"], "workspace", workspace_name, workspace_name)
    rule = naming.get("item")
    if rule:
        types = rule.get("item_types")
        for it in sorted(items, key=lambda x: str(x.get("id"))):
            if types and it.get("type") not in types:
                continue
            check(rule, "item", str(it.get("id")), str(it.get("name") or ""))
    return out
