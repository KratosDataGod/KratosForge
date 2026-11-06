"""Publishing a Power BI report from App Studio (AS-13): readiness, conflicts and the record kept in the project.

Nothing here calls Fabric. The service reads what it needs (the semantic model's definition, the workspace's
items and name), passes it here, and makes the one write itself after the person has seen the check and
confirmed it:

1. ``check`` compiles the specification (pbir.compile_report) against the semantic model's own measures and
   columns, and lists what blocks publishing and what is only a warning. A field or measure the model doesn't
   have blocks it: a report bound to a missing column shows broken visuals.
2. ``conflict`` says whether a report with the same name is already in the workspace and whether this
   project published it (its id is in the project's history). Replacing a report needs ``replace``; replacing
   one this project didn't publish is said plainly, because that is someone else's report.
3. ``fingerprint`` binds the confirmation to what was checked: the specification, the workspace, the model,
   the compiled parts and the report it would replace. If any of them changes, publishing asks again.

The project's history keeps a ``publish`` entry for each attempt: ``started`` before the write, then
``done`` or ``failed``, with the workspace and report ids (never a token or the definition). A ``started``
with nothing after it means the app closed mid-publish; the next check says so.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from .. import redact
from . import pbir
from . import spec as specs
from .spec import SpecError

NAME_LIMIT = 256
REPORT_TYPE = "Report"


def report_name(value: Any, fallback: str) -> str:
    name = redact.one_line(value, NAME_LIMIT) if isinstance(value, str) else ""
    return name or redact.one_line(fallback, NAME_LIMIT) or "New app"


def model_source(spec: dict[str, Any]) -> dict[str, Any] | None:
    """The semantic model the app reads, or None (sample data, or no source yet)."""
    for s in spec.get("sources") or []:
        if s.get("kind") == "semantic-model" and s.get("workspace_id") and s.get("item_id"):
            return s
    return None


def check(spec: dict[str, Any], metadata: dict[str, Any] | None, model_id: str) -> dict[str, Any]:
    """``{"ready", "problems", "warnings", "parts"}``. ``metadata`` is discovery.from_bim of the bound model;
    None when there is no model to read. ``parts`` is empty unless ready."""
    problems: list[str] = []
    if spec.get("target") != "powerbi-report":
        problems.append(f"Only a Power BI report publishes from App Studio today; this app is a "
                        f"{specs.TARGETS.get(str(spec.get('target')), {}).get('label', spec.get('target'))}.")
    if metadata is None:
        problems.append("The app reads sample data. Connect it to a semantic model before publishing.")
    if problems:
        return {"ready": False, "problems": problems, "warnings": [], "parts": {}}
    assert metadata is not None
    tables = metadata.get("tables") or []
    homes = {m["name"]: t["name"] for t in tables for m in t.get("measures") or []}
    columns = {(t["name"], c["name"]) for t in tables for c in t.get("columns") or []}
    for p in spec["pages"]:
        for c in p["components"]:
            for ref in c.get("fields") or []:
                try:
                    table, col = pbir._field(ref)
                except SpecError as exc:
                    problems.extend(exc.problems)
                    continue
                if (table, col) not in columns:
                    problems.append(f"{p['title']}: {c.get('title') or c['id']} uses {ref}, which the semantic model doesn't have.")
    try:
        compiled = pbir.compile_report(spec, model_id, homes)
    except SpecError as exc:
        return {"ready": False, "problems": [*problems, *exc.problems], "warnings": [], "parts": {}}
    if metadata.get("truncated"):
        compiled["warnings"].append("The semantic model is larger than App Studio reads, so not every field could be checked.")
    if problems:
        return {"ready": False, "problems": problems, "warnings": compiled["warnings"], "parts": {}}
    return {"ready": True, "problems": [], "warnings": compiled["warnings"], "parts": compiled["parts"]}


def published_ids(history: list[dict[str, Any]], workspace_id: str) -> set[str]:
    """Report ids this project published (or started to) in the workspace."""
    return {e["report_id"] for e in history if e.get("kind") == "publish" and e.get("workspace_id") == workspace_id
            and isinstance(e.get("report_id"), str) and e["report_id"]}


def unfinished(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last publish that started and never said how it ended, or None."""
    last = next((e for e in reversed(history) if e.get("kind") == "publish"), None)
    return last if last and last.get("state") == "started" else None


def conflict(items: list[dict[str, Any]], name: str, ours: set[str]) -> dict[str, Any] | None:
    """The report already in the workspace under this name: ``{"id", "ours"}``, or None."""
    found = next((i for i in items if i.get("type") == REPORT_TYPE and i.get("displayName") == name), None)
    if found is None:
        return None
    return {"id": str(found.get("id") or ""), "ours": str(found.get("id") or "") in ours}


def fingerprint(spec: dict[str, Any], workspace_id: str, model_id: str, name: str, parts: dict[str, str],
                existing: dict[str, Any] | None) -> str:
    doc = {"spec": spec, "workspace": workspace_id, "model": model_id, "name": name, "parts": parts,
           "replaces": (existing or {}).get("id", "")}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def definition(parts: dict[str, str]) -> dict[str, Any]:
    """The Fabric item definition for the compiled parts."""
    return {"parts": [{"path": path, "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                       "payloadType": "InlineBase64"} for path, text in sorted(parts.items())]}
