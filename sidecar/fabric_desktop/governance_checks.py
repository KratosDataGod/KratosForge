"""Best practice checks (Preview): one registry of checkers that governance policies point at.

A policy's **Checked by** field (``rule`` in its definition) names rule ids from this registry, comma
separated. Check now (central/checks_service.py) resolves a project's policies, turns each policy's
parameters into checker settings, and runs the rules on this PC. Nothing here writes anything.

A checker registers once::

    register(kind, rules, check, runs_in="local")

``kind``     the checker's name, like "naming" or "notebook"
``rules()``  the rules it checks, each a dict:
             id, kind, severity (Error, Warning or Info), category, name, description, and optionally
             fix (what to do, in words), url (where to read more), default_thresholds ({name: value})
             and scheduled_only (True: it checks only items marked "scheduled": true, like a notebook
             a schedule on this PC runs)
``check(item, settings)``
             ``item``: the object to check, a dict whose "kind" says what it is (entity, product, notebook...)
             ``settings``: {rule_id: {"enabled": bool, "severity": "Error"|"Warning"|"Info",
             "thresholds": {name: value}}}, only for the rules a policy turned on
             returns a list of findings, or None when no rule in ``settings`` applies to this item
``runs_in``  "local" (this PC) or "fabric": Check now never starts a Fabric checker; it shows the
             findings last stored for it (``store_fabric_findings``), with their date, or "not run"

A finding is a dict: rule_id, severity, rule (the rule's name), category, object_type, object,
description, url; optionally cell and line (notebooks), fix ({"action", "label", ...}: an action the
app offers), and outcome "unknown" when what the rule needs isn't recorded anywhere (with the reason as
its description). A checker never guesses and never reports a pass it couldn't check.

Severity maps to a policy's: Error is high (or critical), Warning medium, Info low.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Callable
from .duckdb_rows import one_row

SEVERITY_OF = {"critical": "Error", "high": "Error", "medium": "Warning", "low": "Info"}
LEVEL_OF = {"Error": "high", "Warning": "medium", "Info": "low"}
FINDING_SEVERITIES = ("Error", "Warning", "Info")
RUNS_IN = ("local", "fabric")
RULE_FIELDS = ("id", "kind", "severity", "category", "name", "description")
DOCS = "governance.md#check-a-project-against-its-policies"


@dataclass
class Checker:
    kind: str
    rules: Callable[[], list[dict[str, Any]]]
    check: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]] | None]
    runs_in: str = "local"


_REGISTRY: dict[str, Checker] = {}


def register(kind: str, rules: Callable[[], list[dict[str, Any]]],
             check: Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]] | None],
             runs_in: str = "local") -> Checker:
    """Add (or replace) a checker. Its rules are checked for the fields every rule needs. A checker that
    runs somewhere else can't take over a kind: the local model rules and the BPA that runs in Fabric
    would otherwise replace each other, so the one in Fabric needs a kind of its own."""
    if runs_in not in RUNS_IN:
        raise ValueError(f"runs_in is one of {', '.join(RUNS_IN)}")
    if kind in _REGISTRY and _REGISTRY[kind].runs_in != runs_in:
        raise ValueError(f"{kind} rules already run in {_REGISTRY[kind].runs_in}; give the checker that runs in "
                         f"{runs_in} a kind of its own")
    for r in rules():
        missing = [f for f in RULE_FIELDS if not r.get(f)]
        if missing:
            raise ValueError(f"rule {r.get('id') or '?'} of {kind} needs {', '.join(missing)}")
        if r["severity"] not in FINDING_SEVERITIES:
            raise ValueError(f"rule {r['id']}: severity is Error, Warning or Info")
    _REGISTRY[kind] = Checker(kind, rules, check, runs_in)
    return _REGISTRY[kind]


def unregister(kind: str) -> None:
    _REGISTRY.pop(kind, None)


def checkers() -> list[Checker]:
    return list(_REGISTRY.values())


def all_rules() -> list[dict[str, Any]]:
    """Every registered rule, with the checker it belongs to and where it runs."""
    out = []
    for c in _REGISTRY.values():
        for r in c.rules():
            out.append(dict(r, checker=c.kind, runs_in=c.runs_in))
    return sorted(out, key=lambda r: r["id"])


def find_rule(rule_id: str) -> tuple[Checker, dict[str, Any]] | None:
    for c in _REGISTRY.values():
        for r in c.rules():
            if r["id"] == rule_id:
                return c, r
    return None


def rule_ids(text: Any) -> list[str]:
    """The rule ids a policy's Checked by field names: separated by commas or spaces."""
    return [x for x in re.split(r"[,\s]+", str(text or "").strip()) if x]


def settings_for(rule: dict[str, Any], policy_severity: str, params: dict[str, Any]) -> dict[str, Any]:
    """A rule's settings from the policy that names it: ``enabled`` and ``severity`` parameters switch it
    or override its severity (a policy severity or Error/Warning/Info); every other parameter is a threshold."""
    params = dict(params or {})
    sev = params.pop("severity", None)
    severity = sev if sev in FINDING_SEVERITIES else SEVERITY_OF.get(str(sev or policy_severity or ""), rule["severity"])
    enabled = params.pop("enabled", True)
    return {"enabled": bool(enabled), "severity": severity,
            "thresholds": dict(rule.get("default_thresholds") or {}) | params}


def finding(rule: dict[str, Any], settings: dict[str, Any], object_type: str, obj: str, description: str,
            **extra: Any) -> dict[str, Any]:
    """A finding in the registry's shape, for this rule as its settings say."""
    return {"rule_id": rule["id"], "severity": settings.get("severity") or rule["severity"], "rule": rule["name"],
            "category": rule["category"], "object_type": object_type, "object": obj, "description": description,
            "url": rule.get("url") or DOCS, **{k: v for k, v in extra.items() if v is not None}}


def run_rule(rule_id: str, items: list[dict[str, Any]], settings: dict[str, Any],
             stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """One rule over the items it applies to, on this PC: how many it checked, which passed, its findings
    (outcome "unknown" ones apart). A scheduled-only rule checks only the items marked scheduled. A Fabric
    checker isn't run: its last stored findings are returned instead, or none ("not run")."""
    got = find_rule(rule_id)
    if got is None:
        return {"rule_id": rule_id, "missing": True}
    checker, rule = got
    out: dict[str, Any] = {"rule_id": rule_id, "name": rule["name"], "runs_in": checker.runs_in}
    if checker.runs_in != "local":
        last = (stored or {}).get(rule_id)
        return out | {"not_run": True, "last": last}
    if not settings.get("enabled", True):
        return out | {"disabled": True, "checked": 0, "passed": 0, "findings": [], "unknown": []}
    checked = passed = 0
    findings, unknown = [], []
    for item in items:
        if item.get("kind") != rule["kind"] and rule["kind"] != "*":
            continue
        if rule.get("scheduled_only") and not item.get("scheduled"):
            continue
        try:
            result = checker.check(item, {rule_id: settings})
        except Exception as exc:  # silent-ok: one rule's bad setting or input is reported as unknown, never aborts Check now
            result = [finding(rule, settings, str(item.get("kind") or ""), str(item.get("id") or ""),
                              f"This rule couldn't run ({type(exc).__name__}: {exc}). Check the policy's settings.",
                              outcome="unknown")]
        if result is None:
            continue
        checked += 1
        mine = [f for f in result if f.get("rule_id") == rule_id]
        fails = [f for f in mine if f.get("outcome") != "unknown"]
        unknown += [f for f in mine if f.get("outcome") == "unknown"]
        findings += fails
        if not mine:
            passed += 1
    return out | {"checked": checked, "passed": passed, "findings": findings, "unknown": unknown}


def store_fabric_findings(state: dict[str, Any], project: str, rule_id: str, findings: list[dict[str, Any]],
                          at: str = "") -> dict[str, Any]:
    """Keep what a checker that runs in Fabric found, so Check now can show it with its date."""
    at = at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    state.setdefault("fabric", {}).setdefault(project, {})[rule_id] = {"at": at, "findings": list(findings)}
    return state


# --------------------------------------------------------------------------- built-in checkers
# Items (built by checks_service.py from the open project and the platform's catalog):
#   entity   {kind, id, project, layer, table, owner, classification, columns: [{name, classification, markers}]}
#   product  {kind, id, name, project, status, owner, steward (None: unknown), steward_reason, description,
#             terms (None: unknown), terms_reason, last_load (None: unknown), last_rows, load_reason, load_env,
#             endorsement ([{item_name, endorsement}] or None: unknown), endorsement_reason}


def _rule(id: str, kind: str, severity: str, category: str, name: str, description: str, fix: str,
          thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": id, "kind": kind, "severity": severity, "category": category, "name": name, "description": description,
            "fix": fix, "url": DOCS, "default_thresholds": thresholds or {}}


def _on(settings: dict[str, Any], rule_id: str) -> dict[str, Any] | None:
    s = settings.get(rule_id)
    return s if s and s.get("enabled", True) else None


def _open_entity(item: dict[str, Any]) -> dict[str, Any]:
    return {"action": "open_entity", "label": "Open the entity", "entity": item["id"]}


def _edit_product(item: dict[str, Any]) -> dict[str, Any]:
    return {"action": "edit_product", "label": "Edit the product", "product": item["name"], "project": item["project"]}


def _published(item: dict[str, Any]) -> bool:
    return item.get("status") in ("published", "deprecated")


NAMING_RULES = [
    _rule("naming.table_name", "entity", "Warning", "naming", "Table names",
          "Table names in the given layers match the pattern: by default snake_case starting dim_ or fact_.",
          "Rename the entity: its file and its table name. The save gate shows what reads it.",
          {"pattern": r"^(dim|fact)_[a-z0-9]+(_[a-z0-9]+)*$", "layers": ["gold"]}),
]


# A naming pattern runs on this PC for every table. It's matched with RE2 (through DuckDB, already in the
# engine), which runs in time linear in the name, so no pattern can backtrack for minutes as Python's re can.
MAX_PATTERN = 300
MAX_NAME = 256
_re2: Any = None


def _full_match(pattern: str, text: str) -> bool:
    """Whether text matches pattern as a whole, with RE2. Raises ValueError for a pattern RE2 doesn't take
    (lookarounds, backreferences) or that isn't valid."""
    global _re2
    import duckdb
    if _re2 is None:
        _re2 = duckdb.connect()
    try:
        return bool(one_row(_re2.cursor().execute("SELECT regexp_full_match(?, ?)", [text, pattern]))[0])
    except duckdb.Error as exc:
        raise ValueError(str(exc).replace("Invalid Input Error: ", "")) from exc


def naming_check(item: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]] | None:
    rule = NAMING_RULES[0]
    s = _on(settings, rule["id"])
    if not s or item.get("kind") != "entity":
        return None
    t = s["thresholds"]
    layers = [str(x).lower() for x in (t.get("layers") or [])]
    if layers and str(item.get("layer") or "").lower() not in layers:
        return None
    text = str(t.get("pattern") or "")
    if len(text) > MAX_PATTERN:
        return [finding(rule, s, "entity", item["id"], f"The policy's pattern is over {MAX_PATTERN} characters, so names "
                        "weren't checked. Shorten the pattern.", outcome="unknown")]
    table = str(item.get("table") or "")
    out = []
    if len(table) > MAX_NAME:
        out.append(finding(rule, s, "entity", item["id"], f"{table[:40]}... is {len(table)} characters, too long to check.",
                           fix=_open_entity(item)))
    else:
        try:
            matched = _full_match(text, table)
        except ValueError as exc:
            return [finding(rule, s, "entity", item["id"], f"The policy's pattern can't be used ({exc}), so names "
                            "can't be checked. Lookarounds and backreferences aren't supported.", outcome="unknown")]
        if not matched:
            out.append(finding(rule, s, "entity", item["id"], f"{table} doesn't match the naming pattern {text}.",
                               fix=_open_entity(item)))
    limit = t.get("max_length")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and len(table) > limit:
        out.append(finding(rule, s, "entity", item["id"], f"{table} is {len(table)} characters; at most {limit:g}.",
                           fix=_open_entity(item)))
    return out


OWNERSHIP_RULES = [
    _rule("ownership.product_owner", "product", "Error", "ownership", "Every product has an owner",
          "A published data product names an owner and a steward.",
          "Edit the product (Data mesh > Data products) and fill in Owner and Steward.", {"steward_required": True}),
    _rule("ownership.entity_owner", "entity", "Warning", "ownership", "Every table has an owner",
          "An entity names its owner.", "Set owner in the entity's file."),
]


def ownership_check(item: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]] | None:
    out: list[dict[str, Any]] = []
    applied = False
    product_rule, entity_rule = OWNERSHIP_RULES
    s = _on(settings, product_rule["id"])
    if s and item.get("kind") == "product" and _published(item):
        applied = True
        missing = [] if str(item.get("owner") or "").strip() else ["owner"]
        need_steward = bool(s["thresholds"].get("steward_required", True))
        if need_steward and item.get("steward") == "":
            missing.append("steward")
        if missing:
            out.append(finding(product_rule, s, "product", item["id"], f"Names no {' or '.join(missing)}.",
                               fix=_edit_product(item)))
        elif need_steward and item.get("steward") is None:
            out.append(finding(product_rule, s, "product", item["id"],
                               item.get("steward_reason") or "Its steward isn't recorded anywhere this PC can read.",
                               outcome="unknown"))
    s = _on(settings, entity_rule["id"])
    if s and item.get("kind") == "entity":
        applied = True
        if not str(item.get("owner") or "").strip():
            out.append(finding(entity_rule, s, "entity", item["id"], "Names no owner.", fix=_open_entity(item)))
    return out if applied else None


CLASSIFICATION_RULES = [
    _rule("classification.sensitive_columns", "entity", "Error", "classification", "Sensitive columns are labelled",
          "Columns marked PII or PHI carry a sensitivity label (the catalog's classification).",
          "Label the column Confidential or Restricted (entity page > About).",
          {"markers": ["phi", "pii"], "labels": ["confidential", "restricted"]}),
    _rule("classification.required", "entity", "Warning", "classification", "Tables are classified",
          "Tables in the given layers have a classification.", "Set the classification on the entity's About card.",
          {"layers": ["gold"]}),
]


def classification_check(item: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]] | None:
    if item.get("kind") != "entity":
        return None
    out: list[dict[str, Any]] = []
    applied = False
    cols_rule, table_rule = CLASSIFICATION_RULES
    s = _on(settings, cols_rule["id"])
    if s:
        markers = {str(m).lower() for m in s["thresholds"].get("markers") or []}
        labels = [str(x).lower() for x in s["thresholds"].get("labels") or []]
        marked = [c for c in item.get("columns") or [] if markers & {str(m).lower() for m in c.get("markers") or []}]
        if marked:
            applied = True
        for c in marked:
            if str(c.get("classification") or "").lower() not in labels:
                what = "/".join(sorted(m.upper() for m in markers & {str(m).lower() for m in c["markers"]}))
                has = c.get("classification") or "no label"
                out.append(finding(cols_rule, s, "column", f"{item['id']}#{c['name']}",
                                   f"{c['name']} is marked {what} but has {has}.",
                                   fix={"action": "label", "label": "Label it", "entity": item["id"], "column": c["name"],
                                        "labels": labels}))
    s = _on(settings, table_rule["id"])
    if s:
        layers = [str(x).lower() for x in s["thresholds"].get("layers") or []]
        if not layers or str(item.get("layer") or "").lower() in layers:
            applied = True
            if not item.get("classification"):
                out.append(finding(table_rule, s, "entity", item["id"], "Has no classification.", fix=_open_entity(item)))
    return out if applied else None


CONTRACT_RULES = [
    _rule("contract.freshness", "product", "Warning", "contract", "Fresh within N hours",
          "The product's tables loaded successfully within the hours given.",
          "Run the product's entities, or schedule them.", {"within_hours": 24}),
    _rule("contract.row_count", "product", "Warning", "contract", "Row count checked",
          "Each load of the product's tables recorded its row count, at least min_rows.",
          "Look at the last run of the product's entities.", {"min_rows": 1}),
    _rule("contract.terms_linked", "product", "Warning", "contract", "Business terms linked",
          "The product links at least one business term in Purview.",
          "Add a term on the product's page (Data mesh > Products).", {"min_terms": 1}),
    _rule("contract.description", "product", "Warning", "contract", "Description",
          "The product says what it is.", "Edit the product and write a description."),
    _rule("contract.endorsed", "product", "Info", "contract", "Endorsement",
          "The product's items are endorsed in Fabric (as the last tenant scan read it).",
          "Endorse the lakehouse in Fabric (its settings). KratosForge can't set endorsement.",
          {"accepted": ["Certified", "Master data", "Promoted"]}),
]
_CONTRACT = {r["id"]: r for r in CONTRACT_RULES}


def _age_hours(iso: str, now: dt.datetime) -> float:
    t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return (now - t).total_seconds() / 3600


def contract_check(item: dict[str, Any], settings: dict[str, Any],
                   now: dt.datetime | None = None) -> list[dict[str, Any]] | None:
    if item.get("kind") != "product" or not _published(item):
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    out: list[dict[str, Any]] = []
    applied = False
    pid = item["id"]
    for rid, rule in _CONTRACT.items():
        s = _on(settings, rid)
        if not s:
            continue
        applied = True
        t = s["thresholds"]
        if rid == "contract.freshness":
            if not item.get("last_load"):
                out.append(finding(rule, s, "product", pid, item.get("load_reason") or "No successful load is recorded.",
                                   outcome="unknown"))
                continue
            age, limit = _age_hours(item["last_load"], now), float(t.get("within_hours") or 24)
            if age > limit:
                out.append(finding(rule, s, "product", pid, f"Last loaded {age:.0f} h ago{_env(item)}; the contract says within {limit:g} h.",
                                   fix={"action": "open_entity", "label": "Open the entity", "entity": (item.get("entities") or [""])[0]}
                                   if item.get("entities") else None))
        elif rid == "contract.row_count":
            if item.get("last_rows") is None:
                out.append(finding(rule, s, "product", pid, item.get("load_reason") or "No load recorded its row count.",
                                   outcome="unknown"))
            elif int(item["last_rows"]) < int(t.get("min_rows") or 0):
                out.append(finding(rule, s, "product", pid, f"The last load wrote {item['last_rows']} rows; the contract says at least {t.get('min_rows')}."))
        elif rid == "contract.terms_linked":
            if item.get("terms") is None:
                out.append(finding(rule, s, "product", pid, item.get("terms_reason") or "Its business terms can't be read.",
                                   outcome="unknown"))
            elif len(item["terms"]) < int(t.get("min_terms") or 1):
                out.append(finding(rule, s, "product", pid, "Links no business term." if not item["terms"]
                                   else f"Links {len(item['terms'])} business terms; the contract says at least {t.get('min_terms')}.",
                                   fix={"action": "open_product_page", "label": "Add a term", "product_id": pid}))
        elif rid == "contract.description":
            if not str(item.get("description") or "").strip():
                out.append(finding(rule, s, "product", pid, "Has no description.", fix=_edit_product(item)))
        elif rid == "contract.endorsed":
            seen = item.get("endorsement")
            if seen is None:
                out.append(finding(rule, s, "product", pid, item.get("endorsement_reason") or "Unknown: needs a tenant scan.",
                                   outcome="unknown"))
            elif not seen:
                out.append(finding(rule, s, "product", pid, "Has no item in Fabric yet, so there's no endorsement to read.",
                                   outcome="unknown"))
            elif any(x.get("endorsement") is None for x in seen):
                out.append(finding(rule, s, "product", pid, "Unknown: needs a tenant scan.", outcome="unknown"))
            else:
                bare = [x for x in seen if x.get("endorsement") not in (t.get("accepted") or [])]
                if bare:
                    names = ", ".join(str(x.get("item_name") or x.get("item_id") or "?") for x in bare)
                    out.append(finding(rule, s, "product", pid,
                                       f"Not endorsed in Fabric: {names}." if len(bare) == len(seen)
                                       else f"{len(bare)} of {len(seen)} items aren't endorsed in Fabric: {names}."))
    return out if applied else None


def _env(item: dict[str, Any]) -> str:
    return f" in {str(item['load_env']).title()}" if item.get("load_env") else ""


register("naming", lambda: [dict(r) for r in NAMING_RULES], naming_check)
register("ownership", lambda: [dict(r) for r in OWNERSHIP_RULES], ownership_check)
register("classification", lambda: [dict(r) for r in CLASSIFICATION_RULES], classification_check)
register("contract", lambda: [dict(r) for r in CONTRACT_RULES], contract_check)

# The notebook and SQL checkers (best_practice_checkers.py) register themselves on import.
from . import best_practice_checkers  # noqa: F401
