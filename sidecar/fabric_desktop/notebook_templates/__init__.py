"""Enterprise notebook templates for Silver tables and Gold dimensions and facts (docs/notebook-templates.md).

Each template is an .ipynb per platform (``<platform>/<kind>.ipynb``) whose cells are marked up for handover:

- ``# kratosforge:parameters ...``: the one cell a domain fills in. Values only, no code.
- ``# kratosforge:standard ... cell=<id>``: cells the enterprise maintains. ``standards.json`` holds the SHA-256
  of every released version of each, so a notebook made from an older version still conforms.

The marker is the first line of the cell, so it survives an import to Fabric, Databricks or Snowflake that drops
notebook metadata. ``check`` says whether a notebook still conforms (the enterprise can take it over) or has
changed (domain-owned ad hoc work), and lists every change. ``handover`` and ``accept`` record the handover in
the notebook's metadata. Nothing here runs a notebook or reaches a platform.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
HERE = Path(__file__).parent

PLATFORMS = {"fabric": "Fabric", "databricks": "Databricks", "snowflake": "Snowflake"}
# The Native skin runs Fabric notebooks, so it takes the Fabric templates.
SKIN_PLATFORM = {"kratosforge": "fabric", "fabric": "fabric", "databricks": "databricks", "snowflake": "snowflake"}

KINDS = {
    "silver": ("silver-upsert", "Silver table",
               "Clean a Bronze table to a contract, add surrogate keys from the key table, upsert into Silver"),
    "gold_dim_type1": ("gold-dim-type1", "Gold dimension, Type 1",
                       "Current values only, keyed by the Silver key, with an Unknown member"),
    "gold_dim_type2": ("gold-dim-type2", "Gold dimension, Type 2",
                       "History with valid from and to dates, version and durable keys, an Unknown member"),
    "gold_fact": ("gold-fact", "Gold fact",
                  "Declared grain, dimension keys and numeric measures only, Unknowns to -1"),
}
_KIND_BY_ID = {tid: kind for kind, (tid, _, _) in KINDS.items()}

HANDOVER_SCHEMA = "kratosforge.template-handover/1"
_MARKER = re.compile(r"#\s*kratosforge:(standard|parameters)\s+template=(\S+)\s+platform=(\S+)\s+version=(\S+)"
                     r"(?:\s+cell=(\S+))?")


def _standards() -> dict[str, dict[str, dict[str, str]]]:
    return json.loads((HERE / "standards.json").read_text(encoding="utf-8"))


def catalog() -> list[dict[str, Any]]:
    """Every template: platform, kind, id, title, description, version and file name."""
    return [{"platform": p, "platform_title": PLATFORMS[p], "kind": k, "id": tid, "title": title,
             "description": text, "version": VERSION, "file": f"{p}/{k}.ipynb"}
            for p in PLATFORMS for k, (tid, title, text) in KINDS.items()]


def path(platform: str, kind: str) -> Path:
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of {', '.join(PLATFORMS)}")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    return HERE / platform / f"{kind}.ipynb"


def load(platform: str, kind: str) -> dict[str, Any]:
    return json.loads(path(platform, kind).read_text(encoding="utf-8"))


def source_of(cell: dict[str, Any]) -> str:
    s = cell.get("source", "")
    return "".join(s) if isinstance(s, list) else str(s)


def cell_hash(source: str) -> str:
    """SHA-256 of a cell, ignoring line endings and trailing spaces, which editors change freely."""
    lines = [line.rstrip() for line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256("\n".join(lines).strip("\n").encode("utf-8")).hexdigest()


def marker(source: str) -> dict[str, str] | None:
    first = source.lstrip().split("\n", 1)[0]
    m = _MARKER.match(first)
    if not m:
        return None
    role, tid, platform, version, cell = m.groups()
    return {"role": role, "template": tid, "platform": platform, "version": version, "cell": cell or ""}


def _parameters(source: str) -> tuple[dict[str, Any], list[str]]:
    """The values in a parameters cell, and why it isn't values only."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {}, [f"The Parameters cell doesn't parse (line {e.lineno})"]
    values: dict[str, Any] = {}
    problems: list[str] = []
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
                continue
            except (ValueError, TypeError, MemoryError, RecursionError):  # silent-ok: reported just below
                pass  # not a plain value ({[]: 1} is a TypeError): listed as code, not a crash
        problems.append(f"The Parameters cell has code on line {node.lineno}; it may only set values")
    return values, problems


def _template_parameters(platform: str, kind: str) -> dict[str, Any]:
    for cell in load(platform, kind)["cells"]:
        m = marker(source_of(cell))
        if m and m["role"] == "parameters":
            return _parameters(source_of(cell))[0]
    return {}


def _rank(cell_id: str) -> int:
    """Where a standard cell runs: setup first, then step-1, step-2 and so on."""
    m = re.fullmatch(r"step-(\d+)", cell_id)
    return int(m.group(1)) if m else 0


def identify(nb: dict[str, Any]) -> dict[str, str] | None:
    """Which template a notebook came from: its metadata, else the first marked cell."""
    meta = ((nb.get("metadata") or {}).get("kratosforge") or {}).get("template") or {}
    if meta.get("id") in _KIND_BY_ID and meta.get("platform") in PLATFORMS and meta.get("version"):
        return {"template": meta["id"], "platform": meta["platform"], "version": str(meta["version"]),
                "kind": _KIND_BY_ID[meta["id"]]}
    for cell in nb.get("cells", []):
        m = marker(source_of(cell))
        if m and m["template"] in _KIND_BY_ID and m["platform"] in PLATFORMS:
            return {"template": m["template"], "platform": m["platform"], "version": m["version"],
                    "kind": _KIND_BY_ID[m["template"]]}
    return None


def check(nb: dict[str, Any]) -> dict[str, Any]:
    """Whether the notebook still conforms to its template, so the enterprise can support it.

    ``status`` is ``conforms``, ``domain-owned`` (with ``changes`` listing why) or ``not-a-template``.
    """
    who = identify(nb)
    if who is None:
        return {"status": "not-a-template", "changes": ["No kratosforge template markup in this notebook"]}
    expected = _standards().get(f"{who['platform']}/{who['kind']}", {}).get(who["version"])
    out: dict[str, Any] = {**who, "latest_version": VERSION, "upgrade_available": who["version"] != VERSION,
                           "parameters": {}, "changes": []}
    changes: list[str] = out["changes"]
    if expected is None:
        changes.append(f"Version {who['version']} isn't a released version of {who['template']}")
        expected = {}
    seen: set[str] = set()
    params_found = 0
    last_rank = -1
    for n, cell in enumerate(nb.get("cells", []), 1):
        if cell.get("cell_type") != "code":
            continue
        source = source_of(cell)
        m = marker(source)
        if m is None:
            if source.strip():
                changes.append(f"Cell {n} is code the template doesn't have")
            continue
        if (m["template"], m["platform"]) != (who["template"], who["platform"]):
            changes.append(f"Cell {n} comes from another template ({m['template']} for {m['platform']})")
            continue
        if m["role"] == "parameters":
            params_found += 1
            if last_rank >= 0:
                changes.append(f"Cell {n}, the Parameters cell, is out of order: it must come before the standard cells")
            values, problems = _parameters(source)
            changes.extend(problems)
            out["parameters"] = values
            continue
        cid = m["cell"]
        if cid in seen:
            changes.append(f"Cell {n} repeats the standard cell {cid}")
        elif cid not in expected:
            changes.append(f"Cell {n} names a standard cell, {cid}, the template doesn't have")
        elif cell_hash(source) != expected[cid]:
            changes.append(f"Cell {n}, the standard cell {cid}, was edited")
        if cid in expected and cid not in seen:
            if _rank(cid) < last_rank:
                changes.append(f"Cell {n}, the standard cell {cid}, is out of order")
            last_rank = max(last_rank, _rank(cid))
        seen.add(cid)
    for cid in expected:
        if cid not in seen:
            changes.append(f"The standard cell {cid} was removed")
    if params_found != 1:
        changes.append("The notebook needs exactly one Parameters cell" if params_found == 0
                       else "The notebook has more than one Parameters cell")
    else:
        missing = sorted(set(_template_parameters(who["platform"], who["kind"])) - set(out["parameters"]))
        if missing:
            changes.append(f"The Parameters cell is missing {', '.join(missing)}")
    out["status"] = "domain-owned" if changes else "conforms"
    state = ((nb.get("metadata") or {}).get("kratosforge") or {}).get("handover") or {}
    out["handover"] = {k: state[k] for k in ("state", "domain", "owner", "accepted_by") if state.get(k)}
    if changes and state.get("state") in ("requested", "enterprise"):
        out["warning"] = ("This notebook was handed to the enterprise and has changed since. Undo the changes, "
                          "or take it back as domain-owned work.")
    return out


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _handover_meta(nb: dict[str, Any]) -> dict[str, Any]:
    meta = nb.setdefault("metadata", {}).setdefault("kratosforge", {})
    return meta.setdefault("handover", {})


def code_digest(nb: dict[str, Any]) -> str:
    """SHA-256 of every code cell's source, in order. Outputs, Markdown and metadata don't count, so running
    the notebook or adding notes doesn't change it; any change to code or parameter values does."""
    sources = [cell_hash(source_of(c)) for c in nb.get("cells", []) if c.get("cell_type") == "code"]
    return hashlib.sha256(json.dumps(sources).encode("utf-8")).hexdigest()


def handover(nb: dict[str, Any], domain: str, owner: str, name: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """The notebook with its handover requested, and the record the enterprise keeps.

    Refused unless the notebook conforms: domain-owned work stays with the domain."""
    if not domain.strip() or not owner.strip():
        raise ValueError("A handover needs the domain and the person who owns the notebook in it")
    result = check(nb)
    if result["status"] != "conforms":
        raise ValueError("Only a notebook that conforms to its template can be handed over:\n- "
                         + "\n- ".join(result["changes"]))
    out = copy.deepcopy(nb)
    at = _now()
    _handover_meta(out).update({"state": "requested", "domain": domain.strip(), "owner": owner.strip(),
                                "requested_at": at})
    record = {
        "schema": HANDOVER_SCHEMA, "name": name, "template": result["template"], "platform": result["platform"],
        "kind": result["kind"], "version": result["version"], "upgrade_available": result["upgrade_available"],
        "parameters": result["parameters"], "domain": domain.strip(), "owner": owner.strip(),
        "requested_at": at, "code_sha256": code_digest(nb),
        "support": {"enterprise": "the standard cells, upgrades and run failures they cause",
                    "domain": "parameter values: table names, keys, columns, measures and what the data means"},
    }
    return out, record


def accept(nb: dict[str, Any], by: str, record: dict[str, Any]) -> dict[str, Any]:
    """The notebook marked as enterprise-managed, once the enterprise accepts the handover ``record`` it reviewed.

    Refused unless the notebook's code, parameter values included, is exactly what the record describes."""
    if not by.strip():
        raise ValueError("Say who in the enterprise accepts it")
    if ((nb.get("metadata") or {}).get("kratosforge") or {}).get("handover", {}).get("state") != "requested":
        raise ValueError("Request the handover first (handover), then accept it")
    if not isinstance(record, dict) or record.get("schema") != HANDOVER_SCHEMA:
        raise ValueError("That isn't a handover record")
    result = check(nb)
    if result["status"] != "conforms":
        raise ValueError("The notebook changed since the handover was requested:\n- " + "\n- ".join(result["changes"]))
    problems = [f"its {k} is {result.get(k)!r}, the record says {record.get(k)!r}"
                for k in ("template", "platform", "version") if result.get(k) != record.get(k)]
    if result["parameters"] != record.get("parameters"):
        changed = sorted(k for k in set(result["parameters"]) | set(record.get("parameters") or {})
                         if result["parameters"].get(k) != (record.get("parameters") or {}).get(k))
        problems.append(f"parameter values changed: {', '.join(changed)}")
    if code_digest(nb) != record.get("code_sha256"):
        problems.append("its code isn't the code the record describes")
    if problems:
        raise ValueError("The notebook isn't the one in the handover record:\n- " + "\n- ".join(problems))
    out = copy.deepcopy(nb)
    _handover_meta(out).update({"state": "enterprise", "accepted_by": by.strip(), "accepted_at": _now(),
                                "code_sha256": record["code_sha256"]})
    return out


def record_version(allow_change: bool = False) -> dict[str, dict[str, str]]:
    """For template maintainers only, from a source checkout (it rewrites standards.json in the package
    folder, so never run it from an installed engine): add the current VERSION's standard-cell hashes.

    A released version's hashes never change, or notebooks built from it would stop conforming: after editing a
    standard cell, raise VERSION (and the version in every marker) first. ``allow_change`` is only for a version
    that hasn't been released yet."""
    std = _standards()
    added: dict[str, dict[str, str]] = {}
    for p in PLATFORMS:
        for k in KINDS:
            cells = {}
            for cell in load(p, k)["cells"]:
                m = marker(source_of(cell))
                if m and m["role"] == "standard":
                    if m["version"] != VERSION:
                        raise ValueError(f"{p}/{k} cell {m['cell']} is marked {m['version']}, not {VERSION}")
                    cells[m["cell"]] = cell_hash(source_of(cell))
            have = std.setdefault(f"{p}/{k}", {}).get(VERSION)
            if have is not None and have != cells and not allow_change:
                raise ValueError(f"{p}/{k} {VERSION} is released and its standard cells changed; raise VERSION")
            std[f"{p}/{k}"][VERSION] = added[f"{p}/{k}"] = cells
    (HERE / "standards.json").write_text(json.dumps(std, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return added
