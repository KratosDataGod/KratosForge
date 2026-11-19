"""Structural semantic parity (L1, OSSIE-006a): do two semantic definitions have the same objects?

``parity_l1(left, right)`` compares two definitions object by object at the structural level (plan D.5, level
L1): the model, tables and datasets, columns and fields, measures and metrics, relationships, security roles,
calculation groups, hierarchies and perspectives. Each side is any of the four kinds KratosForge reads (plan D.1),
detected or named with ``left_kind`` / ``right_kind``:

- ``tmsl``: a ``model.bim``, parsed or as JSON text (at most ``MAX_TMSL_CHARS`` characters);
- ``tmdl``: a ``.SemanticModel`` folder's files (path -> text), read with ``tmdl.to_bim``;
- ``ossie``: an Ossie document (a dict, or its YAML/JSON text) in any profile ``ossie_profiles`` detects.

Both sides become one structural view: a list of objects with a structural key (their full names) and the
properties L1 compares. A TMSL or TMDL side is read as it is. An Ossie 0.2.0.dev0 side goes through the vendored
converter (``model_ossie.to_tmsl``), and each object takes the class the fidelity report (plan D.4) gives it
(``model_ossie._ossie_to_tmsl``), so what the converter couldn't carry is Unsupported and what lives only in the
POWER_BI extension says so. An Ossie 0.3.0.dev0 document or Snowflake's 0.1.1 wrapper is read as written, without
conversion: what Ossie has no place for (roles, calculation groups, hierarchies, perspectives, inactive or
many-to-many relationships, cross-filter direction) is Unsupported on that side.

The results are the six of plan D.5, and at L1 the best is **Structurally compatible but behavior unverified**
(``CEILING``): matching structure says nothing about what the expressions compute (L2), what a query returns (L3)
or how fast (L4). L1 never reports Verified equivalent or Equivalent within tolerance; ``_check_ceiling`` refuses
a report that would. An object Unsupported on either side is reported Unsupported and never counts as a match.
Input that can't be read is a Test failed result with the reason, never an exception.

Model names are only noted, never counted as a change, for every pair of kinds, deliberately: an item's name
isn't part of its structure (TMDL doesn't carry it, an export names the document after the item, a copy is named
anew), so two definitions of one model named differently are not a difference.

Reading the result (for the parity panel, OSSIE-006e): ``summary`` is the worst object result, so a Different
results summary can hide Unsupported rows (and an Unsupported summary hides nothing worse). ``counts`` carries
every result, both Different results and Unsupported; show both, never the summary alone.

Security: the report carries security metadata. A role row's ``changes`` and evidence can name role member
principals (names, ids, identity providers) and quote row-level security filter text. Any future output of it,
whether an engine method, a screen, a log line or a file, must keep those out of logs, support bundles, CI
artifacts and GitHub text (issues, pull requests, comments), and outside the local pane show counts instead:
pass the report through ``redacted`` first. Only the local results pane may show the full report.

L2 (OSSIE-006b): ``parity_l2(left, right)`` is the L1 report with each measure or metric on both sides also compared
as an expression, inside the vendored converter's translatable subset only (the L2 section below). Its ceiling is
the same: matching expression forms say nothing about what a query returns. Anything outside the subset is
Unsupported, never Different.

Pure: nothing is fetched, written or executed, and no expression is evaluated.
"""

from __future__ import annotations

import collections
import copy
import functools
import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal
from typing import Any

from . import model_ossie as mo, tmdl

VERIFIED = "Verified equivalent"
WITHIN_TOLERANCE = "Equivalent within tolerance"
DIFFERENT = "Different results"
COMPATIBLE = "Structurally compatible but behavior unverified"
UNSUPPORTED = "Unsupported"
TEST_FAILED = "Test failed"
RESULTS = (VERIFIED, WITHIN_TOLERANCE, DIFFERENT, COMPATIBLE, UNSUPPORTED, TEST_FAILED)
LEVEL = "L1"
CEILING = COMPATIBLE
# What L1 may report, worst first: the summary is the worst object result.
L1_RESULTS = (TEST_FAILED, DIFFERENT, UNSUPPORTED, COMPATIBLE)
_RANK = {r: i for i, r in enumerate(L1_RESULTS)}

NOT_CHECKED = (
    {"what": "expressions", "level": "L2", "story": "OSSIE-006b",
     "reason": "measure, calculated column and filter expressions aren't compared as code at L1"},
    {"what": "behavior", "level": "L3", "story": "OSSIE-006c",
     "reason": "no query is run on either side; DuckDB runs SQL only and is not a DAX engine"},
    {"what": "performance", "level": "L4", "story": "OSSIE-006",
     "reason": "not measured until a live baseline exists"},
)
# Tolerances are configuration with stated defaults (plan D.5). L1 compares structure only and uses none; they
# are echoed so a report has the same shape at every level.
DEFAULT_TOLERANCES: dict[str, float] = {"absolute": 0.0, "relative": 0.0}

KINDS = ("tmsl", "tmdl", "ossie")
_KIND_ALIASES = {"tmsl": "tmsl", "bim": "tmsl", "tmdl": "tmdl", "ossie": "ossie"}
_COMPARED = ("model", "table", "calculation_group", "column", "measure", "hierarchy", "relationship", "role",
             "perspective")
# What reading a hostile input can raise: refused shapes and values (ValueError, including UnknownProfile), wrong
# types, and RuntimeError (the converter missing, or RecursionError on nesting deeper than Python's limit).
_READ_ERRORS = (ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError)
_SHAPE_ERRORS = (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError)
_DEFAULT_CARDINALITY = ("many", "one")
_DEFAULT_CROSS_FILTER = "oneDirection"
COMPOSITE = "composite relationship"  # the one thing an Ossie relationship holds that TMSL can't
MAX_WARNINGS = 50
# A model.bim passed as text is JSON, read with json.loads under its own cap: a large real model.bim is tens of MB,
# well past the 20 MB Ossie and TMDL caps. 200 million characters keeps a mistaken pick (a data file) from filling
# memory. (model_ossie_service reads model.bim files from a folder under its own, smaller cap.)
MAX_TMSL_CHARS = tmdl.MAX_CHARS * 4  # the same bound Save TMDL and the service put on a model.bim


@functools.lru_cache(maxsize=1)
def _type_maps() -> tuple[frozenset[str], frozenset[str], Any]:
    """The vendored converter's type knowledge: TMSL's untyped values, Ossie's temporal types Power BI can't hold,
    and its reversibility test. Loaded on first use, as the converter is."""
    from ._vendor.ossie_microsoft._common import TMSL_UNTYPED
    from ._vendor.ossie_microsoft.ossie_to_semantic_model import _LOSSY_TEMPORAL
    from ._vendor.ossie_microsoft.semantic_model_to_ossie import _is_reversible_datatype
    return frozenset(TMSL_UNTYPED), frozenset(_LOSSY_TEMPORAL), _is_reversible_datatype


class _Side:
    """One side as the structural view: its objects in input order, each with a key of full names."""

    def __init__(self, which: str) -> None:
        self.which = which
        self.kind = ""
        self.format = ""
        self.version = ""
        self.profile = ""
        self.label = which
        self.native_ossie = False  # read as an Ossie document, not through the converter
        self.error = ""
        self.unsupported = ""
        self.entries: list[dict[str, Any]] = []
        self.objs = mo._Objects()  # the fidelity report's enumeration: ids, duplicate numbering, MAX_NAME cuts
        self.by_id: dict[str, dict[str, Any]] = {}
        self.warnings: list[dict[str, Any]] = []
        self.warnings_total = 0
        self.datasets: list[dict[str, Any]] = []  # an Ossie side's datasets, which L2 resolves SQL columns with
        self.sql_index: dict[str, tuple[str, str]] | None = None  # L2's SQL column index, built on first use
        self.column_types: dict[tuple[str, str], Any] | None = None  # L2's casefolded (table, column) -> type

    def add(self, kind: str, ident: str, key: tuple[Any, ...], attrs: dict[str, Any] | None = None,
            raw: dict[str, Any] | None = None, needs: tuple[str, ...] = (), cls: str | None = None) -> dict[str, Any]:
        obj = self.objs.add(kind, ident, "fully_preserved")
        entry = {"kind": kind, "id": obj["object"], "key": key, "attrs": attrs or {}, "raw": raw or {},
                 "needs": needs, "cls": cls, "notes": [], "evidence": [], "expr": None}
        self.entries.append(entry)
        self.by_id[entry["id"]] = entry
        return entry

    def holds(self, need: str) -> bool:
        """Whether this side can represent an object that needs ``need`` (a Power BI-only feature, or a
        composite relationship)."""
        return need == COMPOSITE if self.native_ossie else need != COMPOSITE

    def warn(self, row: dict[str, Any]) -> None:
        self.warnings_total += 1
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append({k: mo._short(v) for k, v in row.items()})

    def attach(self, rows: list[dict[str, Any]], source: str) -> None:
        """Reader rows as evidence on the object each names (the fidelity report's ``find``), else a warning."""
        for row in rows:
            hit = self.objs.find(str(row.get("object", "")))
            entry = self.by_id.get(hit["object"]) if hit is not None else None
            if entry is not None:
                entry["evidence"].append({"side": self.which, "source": source,
                                          **{k: mo._short(v) for k, v in row.items()}})
            else:
                self.warn(row)

    def info(self) -> dict[str, Any]:
        return {"kind": self.kind, "format": self.format, "version": self.version, "profile": self.profile,
                "label": self.label, "read": not self.error, "error": self.error, "unsupported": self.unsupported,
                "warnings": self.warnings, "warnings_total": self.warnings_total}


# ---- reading each kind into the structural view

def _relationship(r: dict[str, Any]) -> dict[str, Any]:
    """A TMSL relationship's structure, written many side first (a one-to-many is turned round, as the converter
    does), with TMSL's defaults for what it leaves out."""
    fc = str(r.get("fromCardinality", "many")).lower()
    tc = str(r.get("toCardinality", "one")).lower()
    a, b = (r.get("fromTable"), [r.get("fromColumn")]), (r.get("toTable"), [r.get("toColumn")])
    if fc == "one" and tc == "many":
        a, b, fc, tc = b, a, tc, fc
    return {"from": a, "to": b, "cardinality": (fc, tc), "active": r.get("isActive", True) is not False,
            "cross": str(r.get("crossFilteringBehavior", _DEFAULT_CROSS_FILTER)), "name": r.get("name")}


def _relationship_needs(attrs: dict[str, Any]) -> tuple[str, ...]:
    needs = []
    if attrs["active"] is False:
        needs.append("inactive relationship")
    if attrs["cardinality"] not in (None, _DEFAULT_CARDINALITY):
        needs.append(f"{attrs['cardinality'][0]}-to-{attrs['cardinality'][1]} relationship")
    if attrs["cross"] not in (None, _DEFAULT_CROSS_FILTER):
        needs.append(f"{attrs['cross']} cross-filter direction")
    return tuple(needs)


def _levels(h: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    levels = list(enumerate(mo._named_items(h.get("levels"))))
    levels.sort(key=lambda p: (p[1]["ordinal"] if isinstance(p[1].get("ordinal"), int) else p[0], p[0]))
    return tuple((lv["name"], str(lv.get("column"))) for _, lv in levels)


def _members(p: dict[str, Any]) -> frozenset[tuple[str, str, str]]:
    out = set()
    for t in mo._named_items(p.get("tables")):
        out.add(("table", t["name"], ""))
        for key, kind in (("columns", "column"), ("measures", "measure"), ("hierarchies", "hierarchy")):
            out.update((kind, t["name"], i["name"]) for i in mo._named_items(t.get(key)))
    return frozenset(out)


def _walk_tmsl(side: _Side, bim: dict[str, Any], column_type: Any = None, unhomed: frozenset[Any] = frozenset()) -> None:
    """Every object of a parsed ``model.bim`` L1 compares, in input order."""
    model = bim.get("model")
    if not isinstance(model, dict):
        raise ValueError("This model.bim has no model object")
    side.add("model", "model", ("model",), {"name": bim.get("name")})
    for t in mo._named_items(model.get("tables")):
        name = t["name"]
        group = bool(t.get("calculationGroup"))
        tid = f"table {mo._q(name)}"
        needs = ("calculation group",) if group else ()
        side.add("calculation_group" if group else "table", tid, ("table", name), {}, raw=t, needs=needs)
        for c in mo._named_items(t.get("columns")):
            if c.get("type") == "rowNumber":  # the engine's own row number column, in no definition's structure
                continue
            side.add("column", f"{tid} column {mo._q(c['name'])}", ("column", name, c["name"]),
                     {"type": column_type(name, c) if column_type else ("tmsl", c.get("dataType")),
                      "key": c.get("isKey") is True}, needs=needs)
        for m in mo._named_items(t.get("measures")):
            entry = side.add("measure", f"{tid} measure {mo._q(m['name'])}", ("measure", m["name"]),
                             {"home": None if m["name"] in unhomed else name}, needs=needs)
            entry["expr"] = {"dialects": {"DAX": mo._text(m.get("expression"))}, "scope": name}
        for h in mo._named_items(t.get("hierarchies")):
            side.add("hierarchy", f"{tid} hierarchy {mo._q(h['name'])}", ("hierarchy", name, h["name"]),
                     {"levels": _levels(h)}, needs=("hierarchy", *needs))
    for r in mo._dicts(model.get("relationships")):
        label = r["name"] if isinstance(r.get("name"), str) and r["name"] else mo._rel_key(r)
        attrs = _relationship(r)
        side.add("relationship", f"relationship {mo._q(label)}", ("relationship", mo.tmsl_ends_key(r)), attrs,
                 needs=_relationship_needs(attrs))
    for r in mo._named_items(model.get("roles")):
        side.add("role", f"role {mo._q(r['name'])}", ("role", r["name"]), {}, raw=r, needs=("role",))
    for p in mo._named_items(model.get("perspectives")):
        side.add("perspective", f"perspective {mo._q(p['name'])}", ("perspective", p["name"]),
                 {"members": _members(p)}, needs=("perspective",))


def _columns(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else [value]


def _walk_ossie(side: _Side, model: dict[str, Any]) -> None:
    """An Ossie model (a flat document, or the model inside Snowflake's wrapper) as written, without conversion."""
    side.add("model", "model", ("model",), {"name": model.get("name")})
    side.datasets = mo._named_items(model.get("datasets"))
    for d in side.datasets:
        name = d["name"]
        tid = f"table {mo._q(name)}"
        pk = d.get("primary_key")
        single = pk[0] if isinstance(pk, list) and len(pk) == 1 else None
        table = side.add("table", tid, ("table", name), {})
        if isinstance(pk, list) and len(pk) > 1:
            table["notes"].append(f"on the {side.which} side the primary key has {len(pk)} columns; TMSL has no "
                                  "composite key, so no column is read as the key")
        for f in mo._named_items(d.get("fields")):
            dt = f.get("datatype")
            side.add("column", f"{tid} column {mo._q(f['name'])}", ("column", name, f["name"]),
                     {"type": ("ossie", dt if isinstance(dt, str) and dt else None), "key": f["name"] == single})
        for m in mo._named_items(d.get("metrics")):
            entry = side.add("measure", f"{tid} measure {mo._q(m['name'])}", ("measure", m["name"]), {"home": name})
            entry["expr"] = {"dialects": _dialects(m.get("expression")), "scope": name}
    for m in mo._named_items(model.get("metrics")):
        entry = side.add("measure", f"metric {mo._q(m['name'])}", ("measure", m["name"]), {"home": None})
        entry["expr"] = {"dialects": _dialects(m.get("expression")), "scope": None}
    for r in mo._dicts(model.get("relationships")):
        a, b = (r.get("from"), _columns(r.get("from_columns"))), (r.get("to"), _columns(r.get("to_columns")))
        label = r["name"] if isinstance(r.get("name"), str) and r["name"] else \
            f"{a[0]}.{','.join(map(str, a[1]))} -> {b[0]}.{','.join(map(str, b[1]))}"
        attrs = {"from": a, "to": b, "cardinality": None, "active": None, "cross": None, "name": r.get("name")}
        side.add("relationship", f"relationship {mo._q(label)}",
                 ("relationship", mo.ends_key(a[0], a[1], b[0], b[1])), attrs,
                 needs=(COMPOSITE,) if len(a[1]) != 1 or len(b[1]) != 1 else ())


def _ident(kind: str, key: tuple[Any, ...], fallback: str) -> str:
    """The structural view's id for an object only the fidelity report knows (the converter didn't write it)."""
    if kind in ("table", "calculation_group"):
        return f"table {mo._q(key[1])}"
    if kind in ("column", "hierarchy"):
        return f"table {mo._q(key[1])} {kind} {mo._q(key[2])}"
    if kind == "measure":
        return f"metric {mo._q(key[1])}"
    if kind in ("role", "perspective"):
        return f"{kind} {mo._q(key[1])}"
    return fallback


def _read_ossie_converted(side: _Side, doc: dict[str, Any]) -> None:
    """Ossie 0.2.0.dev0 through the converter, each object classed by the fidelity report of that conversion."""
    bim, rows = mo.to_tmsl(doc)
    fid = mo._Objects()
    mo._ossie_to_tmsl(doc, bim, fid)
    for row in rows:  # the converter's rows on the fidelity object each names, as fidelity() attaches them
        hit = fid.find(str(row.get("object", "")))
        if hit is not None:
            hit["evidence"].append(dict(row))
        else:
            side.warn(row)
    fields: dict[tuple[str, str], dict[str, Any]] = {}
    for d in mo._named_items(doc.get("datasets")):
        for f in mo._named_items(d.get("fields")):
            fields.setdefault((d["name"], f["name"]), f)
    unhomed = set()
    for m in mo._named_items(doc.get("metrics")):
        data = mo._stash_data(m)
        placed = data.get("name", m["name"])
        if "table" not in data and isinstance(placed, str):
            unhomed.add(placed)

    def column_type(table: str, c: dict[str, Any]) -> tuple[str, Any]:
        """A field's own Ossie type, unless its POWER_BI section kept the exact TMSL type (then the converter
        wrote that). Never the converter's lossy mapping (Time written as dateTime)."""
        f = fields.get((table, c["name"]))
        dt = f.get("datatype") if f is not None else None
        if f is None or "dataType" in mo._stash_data(f) or not isinstance(dt, str) or not dt:
            return ("tmsl", c.get("dataType"))
        return ("ossie", dt)

    _walk_tmsl(side, bim, column_type, frozenset(unhomed))
    # L2 compares what the Ossie document says (its SQL and DAX), not the DAX the converter wrote from it.
    side.datasets = mo._named_items(doc.get("datasets"))
    written: dict[str, dict[str, Any]] = {}
    for d in side.datasets:
        for m in mo._named_items(d.get("metrics")):
            placed = mo._stash_data(m).get("name", m["name"])
            if isinstance(placed, str):
                written.setdefault(placed, {"dialects": _dialects(m.get("expression")), "scope": d["name"]})
    for m in mo._named_items(doc.get("metrics")):
        placed = mo._stash_data(m).get("name", m["name"])
        if isinstance(placed, str):
            written[placed] = {"dialects": _dialects(m.get("expression")), "scope": None}
    for entry in side.entries:
        if entry["kind"] == "measure" and entry["key"][1] in written:
            entry["expr"] = written[entry["key"][1]]
    present: dict[str, collections.deque[dict[str, Any]]] = {}
    lost: list[dict[str, Any]] = []
    for item in fid.items:
        key = fid.keys.get(item["object"])
        if key is None or item["kind"] not in _COMPARED:
            continue
        if item["class"] == "unsupported":
            lost.append(item)
        else:
            present.setdefault(mo._canon(key), collections.deque()).append(item)
    for entry in side.entries:
        queue = present.get(mo._canon(entry["key"]))
        fobj = queue.popleft() if queue else None
        if fobj is None:
            entry["cls"] = "unverified"
            entry["notes"].append(f"on the {side.which} side the converter wrote this, but the fidelity report "
                                  "doesn't name it")
            continue
        entry["cls"] = "preserved_through_extensions" if mo.RESTORED in fobj["notes"] else fobj["class"]
        _fidelity_evidence(side, entry, fobj)
    for fobj in lost:
        key = fid.keys[fobj["object"]]
        entry = side.add(fobj["kind"], _ident(fobj["kind"], key, fobj["object"]), key, {}, cls="unsupported")
        _fidelity_evidence(side, entry, fobj)


def _fidelity_evidence(side: _Side, entry: dict[str, Any], fobj: dict[str, Any]) -> None:
    if fobj["changes"] or fobj["notes"] or fobj["class"] != "fully_preserved":
        entry["evidence"].append({"side": side.which, "source": "fidelity", "object": fobj["object"],
                                  "class": mo.CLASSES[fobj["class"]], "changes": list(fobj["changes"]),
                                  "notes": list(fobj["notes"])})
    entry["evidence"] += [{"side": side.which, "source": "converter", **{k: mo._short(v) for k, v in row.items()}}
                          for row in fobj["evidence"]]


def _detect(value: Any) -> str:
    """The kind of a parsed definition. Ossie's markers come first: a text ``version`` or a ``semantic_model`` key
    is Ossie even beside a stray ``model`` key; a dict ``model`` with no Ossie version is TMSL."""
    if isinstance(value, dict):
        if isinstance(value.get("version"), str) or "semantic_model" in value:
            return "ossie"
        if isinstance(value.get("model"), dict):
            return "tmsl"
        if "version" in value:  # a version that isn't text: Ossie's reader refuses it by name
            return "ossie"
        if value and any(isinstance(k, str) and k.endswith(".tmdl") for k in value):
            return "tmdl"
    raise ValueError("This isn't a semantic model KratosForge can compare: a model.bim (TMSL) has a model object, "
                     "a TMDL folder is its files (path -> text, with .tmdl files), and an Ossie document has a version")


def _json_text(text: str) -> Any:
    """JSON text (a model.bim, or an Ossie document written as JSON) under ``MAX_TMSL_CHARS``."""
    if len(text) > MAX_TMSL_CHARS:
        raise ValueError(f"This text is {len(text) // 1_000_000} million characters; a model.bim is compared at "
                         f"most {MAX_TMSL_CHARS // 1_000_000} million characters")
    try:
        return json.loads(text.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"This isn't valid JSON: {exc}") from None
    except RecursionError:
        raise ValueError("This JSON is nested too deeply to be a semantic model") from None


def _read(side: _Side, value: Any, kind: str | None) -> None:
    if kind is not None:
        if not isinstance(kind, str) or kind.lower() not in _KIND_ALIASES:
            raise ValueError(f"There's no kind {mo._short(kind)!r} to compare; the kinds are {', '.join(KINDS)}")
        kind = _KIND_ALIASES[kind.lower()]
    if isinstance(value, str):
        if kind == "tmdl":
            raise TypeError("TMDL is a folder of files: pass them as path -> text")
        size = len(value)
        if kind == "tmsl" or (kind is None and value.lstrip("\ufeff \t\r\n").startswith("{")):
            value = _json_text(value)  # a model.bim: JSON under its own, larger cap
            if (kind or _detect(value)) == "ossie" and size > mo.MAX_CHARS:  # Ossie keeps Ossie's cap
                raise ValueError(f"This file is {size // 1_000_000} MB; an Ossie model is at most "
                                 f"{mo.MAX_CHARS // 1_000_000} MB")
        else:
            value = mo.load_text(value)  # Ossie YAML or JSON: size-capped, no aliases
    kind = kind or _detect(value)
    side.kind = kind
    if not isinstance(value, dict):
        raise TypeError(f"A {kind.upper()} definition is one object, not {type(value).__name__}")
    if kind == "tmsl":
        side.format, side.version = "TMSL", str(value.get("compatibilityLevel", ""))
        side.label = f"TMSL (compatibility level {mo._short(side.version)})" if side.version else "TMSL"
        _walk_tmsl(side, value)
        return
    if kind == "tmdl":
        from . import tmdl

        if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise TypeError("TMDL files are path -> text")
        bim, rows = tmdl.to_bim(value)
        side.format, side.version = "TMDL", str(bim.get("compatibilityLevel", ""))
        side.label = f"TMDL (compatibility level {mo._short(side.version)})"
        _walk_tmsl(side, bim)
        side.attach(rows, "tmdl reader")
        return
    from . import ossie_profiles

    mo.require()
    profile = ossie_profiles.detect_profile(value)
    meta = ossie_profiles.PROFILES[profile]
    side.format, side.version, side.profile, side.label = "Apache Ossie", meta["version"], profile, meta["label"]
    if meta["convertible"]:
        _read_ossie_converted(side, value)
        return
    side.native_ossie = True
    model: Any = value
    if meta["root"] == "wrapper":
        model = value.get("semantic_model")
        if isinstance(model, list) and len(model) != 1:
            side.unsupported = (f"This {meta['label']} file holds {len(model)} models in semantic_model; L1 compares "
                                "one model with one model")
            return
        model = model[0] if isinstance(model, list) else model
        if not isinstance(model, dict):
            raise ValueError("semantic_model should hold one model object")
    _walk_ossie(side, model)


# ---- comparing one object of each side

def _end(e: tuple[Any, list[Any]]) -> str:
    return f"{mo._q(e[0])}[{', '.join(mo._short(c) for c in e[1])}]"


def _cardinality(c: tuple[str, str]) -> str:
    return f"{mo._short(c[0])}-to-{mo._short(c[1])}"


def _type_shown(t: tuple[str, Any]) -> str:
    vocab, value = t
    return "no data type" if value is None else f"{mo._shown(str(value))} ({'TMSL' if vocab == 'tmsl' else 'Ossie'})"


def _same_type(a: tuple[str, Any], b: tuple[str, Any]) -> bool:
    """Whether two data types are the same, through the converter's own maps only: an Ossie type and a TMSL type
    are the same when the converter reverses one into the other exactly. A type Power BI can't hold (Time,
    DateTimeTz) is never the same as dateTime."""
    if a[0] == b[0]:
        return bool(a[1] == b[1])
    tmsl, ossie = (a[1], b[1]) if a[0] == "tmsl" else (b[1], a[1])
    untyped, lossy, reversible = _type_maps()
    if ossie is None:
        return tmsl is None or tmsl in untyped
    if tmsl is None or ossie in lossy:
        return False
    return bool(reversible(tmsl, ossie))


def _who(side: _Side) -> str:
    return f"the {side.which} side ({side.label})"


def _changes(kind: str, a: dict[str, Any], b: dict[str, Any], left: _Side, right: _Side,
             notes: list[str]) -> list[str]:
    """How ``b`` (right) differs from ``a`` (left) in structure, one line each."""
    out: list[str] = []
    x, y = a["attrs"], b["attrs"]
    if kind == "model":
        if x.get("name") != y.get("name"):
            notes.append(f"the model is named {mo._q(x.get('name'))} on the left and {mo._q(y.get('name'))} on the "
                         "right (an item's name isn't part of its structure; TMDL doesn't carry it)")
    elif kind in ("table", "calculation_group"):
        if a["kind"] != b["kind"]:
            out.append(f"a {a['kind'].replace('_', ' ')} on the left side, a {b['kind'].replace('_', ' ')} on the right")
        elif kind == "calculation_group":
            out += mo._calc_changes(a["raw"], b["raw"])
    elif kind == "column":
        if not _same_type(x["type"], y["type"]):
            out.append(f"data type changed from {_type_shown(x['type'])} to {_type_shown(y['type'])}")
        if x["key"] != y["key"]:
            out.append("the table's key on the left side, not on the right" if x["key"]
                       else "the table's key on the right side, not on the left")
    elif kind == "measure":
        notes.append("expressions weren't compared: that is L2 (OSSIE-006b)")
        if x["home"] is not None and y["home"] is not None and x["home"] != y["home"]:
            out.append(f"home table changed from {mo._q(x['home'])} to {mo._q(y['home'])}")
        elif (x["home"] is None) != (y["home"] is None):
            notes.append("one side records no home table for it (an Ossie model-level metric), so the home table "
                         "wasn't compared")
    elif kind == "hierarchy":
        if x["levels"] != y["levels"]:
            def shown(levels: tuple[tuple[str, str], ...]) -> str:
                return mo._shown(", ".join(f"{n} ({c})" for n, c in levels))
            out.append(f"levels changed from {shown(x['levels'])} to {shown(y['levels'])}")
    elif kind == "relationship":
        out += _relationship_changes(x, y, left, right, notes)
    elif kind == "role":
        out += mo._role_changes(a["raw"], b["raw"])
    elif kind == "perspective":
        for what, items in (("gone", sorted(x["members"] - y["members"])), ("added", sorted(y["members"] - x["members"]))):
            if items:
                listed = ", ".join(f"{k} {mo._q(t)}" + (f"[{mo._short(n)}]" if n else "") for k, t, n in items[:5])
                out.append(f"{len(items)} perspective member(s) {what}: {listed}")
    return out


def _relationship_changes(x: dict[str, Any], y: dict[str, Any], left: _Side, right: _Side,
                          notes: list[str]) -> list[str]:
    out: list[str] = []
    symmetric = all(c is not None and c[0] == c[1] for c in (x["cardinality"], y["cardinality"]))
    if (x["from"], x["to"]) != (y["from"], y["to"]) and not (symmetric and (x["from"], x["to"]) == (y["to"], y["from"])):
        out.append(f"endpoints changed from {_end(x['from'])} -> {_end(x['to'])} to {_end(y['from'])} -> {_end(y['to'])}")
    checks: tuple[tuple[str, str, str, Any, Any], ...] = (
        ("cardinality", "a", "cardinality", _DEFAULT_CARDINALITY, _cardinality),
        ("active", "an", "active flag", True, lambda v: str(v).lower()),
        ("cross", "a", "cross-filter direction", _DEFAULT_CROSS_FILTER, lambda v: mo._shown(str(v))))
    for attr, article, what, default, shown in checks:
        if x[attr] is not None and y[attr] is not None:
            if x[attr] != y[attr]:
                out.append(f"{what} changed from {shown(x[attr])} to {shown(y[attr])}")
        elif (x[attr] is None) != (y[attr] is None):
            silent = left if x[attr] is None else right
            notes.append(f"{_who(silent)} doesn't record {article} {what}; read as {shown(default)}, the converter's "
                         "default")
    if x["name"] != y["name"] and x["name"] and y["name"]:
        notes.append(f"named {mo._q(x['name'])} on the left and {mo._q(y['name'])} on the right; matched by its endpoints")
    return out


def _class_notes(side: _Side, entry: dict[str, Any], notes: list[str]) -> None:
    cls = entry["cls"]
    if cls == "preserved_through_extensions":
        if entry["kind"] == "role":
            notes.append(f"on {_who(side)} this role lives only in the POWER_BI extension: Power BI applies it, a "
                         "tool that reads the Ossie core doesn't apply this row-level security")
        else:
            notes.append(f"on {_who(side)} it lives only in the POWER_BI extension: only Power BI sees it")
    elif cls in ("partially_converted", "unverified"):
        notes.append(f"on {_who(side)} the conversion classed it {mo.CLASSES[cls]}; see evidence")
    notes += entry["notes"]


def _structure(entry: dict[str, Any], side: _Side) -> dict[str, Any]:
    """What L1 read of one object on one side, short, for evidence."""
    x, kind = entry["attrs"], entry["kind"]
    values: dict[str, Any] = {}
    if entry["cls"] == "unsupported":  # only the fidelity report knows it: nothing of its structure was read
        pass
    elif kind == "column":
        values = {"data type": _type_shown(x["type"]), "key": x["key"]}
    elif kind == "measure":
        values = {"home table": None if x["home"] is None else mo._short(x["home"])}
    elif kind == "relationship" and x:
        values = {"from": _end(x["from"]), "to": _end(x["to"]),
                  "cardinality": None if x["cardinality"] is None else _cardinality(x["cardinality"]),
                  "active": x["active"], "cross-filter direction": x["cross"]}
    elif kind == "role":
        shape = mo._role_shape(entry["raw"])
        values = {"model permission": mo._short(shape["modelPermission"]), "filters": len(shape["filters"]),
                  "members": len(shape["members"])}
    elif kind == "calculation_group":
        values = {"calculation items": len(mo._calc_items(entry["raw"]))}
    elif kind == "hierarchy":
        values = {"levels": len(x["levels"])}
    elif kind == "perspective":
        values = {"members": len(x["members"])}
    return {"side": side.which, "source": "structure", "object": entry["id"], "values": values}


def _pair(a: dict[str, Any] | None, b: dict[str, Any] | None, left: _Side, right: _Side) -> dict[str, Any]:
    """One object's L1 result: ``a`` on the left, ``b`` on the right, either missing."""
    kind = (a or b or {})["kind"]
    changes: list[str] = []
    notes: list[str] = []
    evidence: list[dict[str, Any]] = []
    for side, e in ((left, a), (right, b)):
        if e is not None:
            _class_notes(side, e, notes)
            evidence.append(_structure(e, side))
            evidence += e["evidence"]
    if a is None or b is None:
        here, there, e = (left, right, a) if a is not None else (right, left, b)
        assert e is not None
        need = next((n for n in e["needs"] if not there.holds(n)), None)
        if e["cls"] == "unsupported":
            result = UNSUPPORTED
            notes.append(f"Unsupported on {_who(here)}: the conversion couldn't carry it")
        elif need is not None:
            result = UNSUPPORTED
            notes.append(f"{_who(there)} has no place for a {need}, so it can't be compared")
        else:
            result = DIFFERENT
            changes.append(f"missing on {_who(there)}")
    else:
        unsupported = [s for s, e in ((left, a), (right, b)) if e["cls"] == "unsupported"]
        needs = [(n, s) for e, s in ((a, right), (b, left)) for n in e["needs"] if not s.holds(n)]
        if unsupported:
            result = UNSUPPORTED
            notes += [f"Unsupported on {_who(s)}: the conversion couldn't carry it" for s in unsupported]
        elif needs:
            result = UNSUPPORTED
            notes += [f"{_who(s)} has no place for a {n}, so it can't be compared" for n, s in dict.fromkeys(needs)]
        else:
            changes = _changes(kind, a, b, left, right, notes)
            result = DIFFERENT if changes else COMPATIBLE
    row = {"id": (a or b or {})["id"], "kind": kind, "result": result, "changes": changes,
           "notes": list(dict.fromkeys(notes)), "evidence": evidence,
           "ids": {"left": a["id"] if a else None, "right": b["id"] if b else None},
           "fidelity": {"left": mo.CLASSES.get(a["cls"]) if a and a["cls"] else None,
                        "right": mo.CLASSES.get(b["cls"]) if b and b["cls"] else None}}
    if kind == "role":  # the same changes without members or filter text, for redacted()
        if a is not None and b is not None and result == DIFFERENT:
            row["changes_redacted"] = _role_changes_redacted(a["raw"], b["raw"])
        else:
            row["changes_redacted"] = list(changes)
    return row


def _fingerprint(text: str) -> str:
    """Text as its length and a short hash: enough to see that two filters differ, without the filter. A short
    filter's hash can be guessed by trying values, so this keeps text out of logs, not out of reach."""
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return f"{len(text)} characters, sha256 {digest}"


def _role_changes_redacted(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """``model_ossie._role_changes`` line for line, with each filter as a length and hash and members, table and
    column permissions as counts."""
    a, b = mo._role_shape(before), mo._role_shape(after)
    out = []
    if a["modelPermission"] != b["modelPermission"]:
        out.append(f"model permission {mo._shown(str(a['modelPermission']))} became "
                   f"{mo._shown(str(b['modelPermission']))}")
    old, new = dict(sorted(a["filters"] - b["filters"])), dict(sorted(b["filters"] - a["filters"]))
    for table in sorted(old.keys() | new.keys()):
        if table not in new:
            out.append(f"the filter on table {mo._q(table)} is gone (it was {_fingerprint(old[table])})")
        elif table not in old:
            out.append(f"a filter on table {mo._q(table)} was added ({_fingerprint(new[table])})")
        else:
            out.append(f"the filter on table {mo._q(table)} changed from {_fingerprint(old[table])} to "
                       f"{_fingerprint(new[table])}")
    for key, what in (("tables", "table permission"), ("columns", "column permission"), ("members", "member")):
        gone, added = len(a[key] - b[key]), len(b[key] - a[key])
        if gone:
            out.append(f"{gone} {what}(s) gone")
        if added:
            out.append(f"{added} {what}(s) added")
    return out


def _withheld(item: dict[str, Any]) -> dict[str, Any]:
    kept = {k: item[k] for k in ("side", "source", "class") if k in item}
    return kept | {"withheld": _fingerprint(json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))}


def redacted(report: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``report`` safe to log or attach outside the local pane: each role row's changes name no member
    (counts instead) and quote no filter (a length and hash instead), and its evidence other than the structure
    counts is withheld (a length and hash of it). Reader warnings about a role are withheld the same way. At L2 each
    measure's expression text and canonical forms (which can hold literals) are a length and hash too; its changes
    and notes name functions, tables and columns, never a literal. The report itself is unchanged."""
    out = copy.deepcopy(report)
    for o in out.get("objects") or []:
        if isinstance(o.get("expressions"), dict):  # L2: expression text and forms can hold literals
            o["expressions"] = {k: _fingerprint(v) if isinstance(v, str) and not k.endswith("_dialect") else v
                                for k, v in o["expressions"].items()}
        if o.get("kind") != "role":
            continue
        o["changes"] = list(o.pop("changes_redacted", None) or
                            ([f"{len(o['changes'])} change(s) withheld"] if o.get("changes") else []))
        o["evidence"] = [e if e.get("source") == "structure" else _withheld(e) for e in o.get("evidence") or []]
    for side in (out.get("sides") or {}).values():
        side["warnings"] = [_withheld(w) if str(w.get("object", "")).startswith("role") else w
                            for w in side.get("warnings") or []]
    out["redacted"] = True
    return out


def _claim(queue: collections.deque[dict[str, Any]] | None, used: set[int]) -> dict[str, Any] | None:
    while queue:
        candidate = queue.popleft()
        if id(candidate) not in used:
            used.add(id(candidate))
            return candidate
    return None


def _compare(left: _Side, right: _Side, level: str = LEVEL) -> list[dict[str, Any]]:
    """Every object of both sides once: matched by key (relationships by name, then by endpoints), keyed lookups
    only, so the work grows with the number of objects. At L2 each measure row also carries the expression
    comparison (``_expression_pair``)."""
    by_key: dict[str, collections.deque[dict[str, Any]]] = {}
    by_name: dict[str, collections.deque[dict[str, Any]]] = {}
    for e in right.entries:
        by_key.setdefault(mo._canon(e["key"]), collections.deque()).append(e)
        name = e["attrs"].get("name") if e["kind"] == "relationship" else None
        if isinstance(name, str) and name:
            by_name.setdefault(name, collections.deque()).append(e)
    used: set[int] = set()
    match: dict[int, dict[str, Any] | None] = {}
    for e in left.entries:  # relationships by name first, so a renamed one doesn't take another's endpoints
        name = e["attrs"].get("name") if e["kind"] == "relationship" else None
        if isinstance(name, str) and name:
            match[id(e)] = _claim(by_name.get(name), used)
    for e in left.entries:
        if match.get(id(e)) is None:
            match[id(e)] = _claim(by_key.get(mo._canon(e["key"])), used)
    pairs = [(e, match[id(e)]) for e in left.entries] + [(None, e) for e in right.entries if id(e) not in used]
    report_ids = mo._Objects()  # one id per row, numbered when two rows would share one
    out = []
    names = (_measure_names(left), _measure_names(right)) if level == LEVEL_L2 else (frozenset(), frozenset())
    for a, b in pairs:
        row = _pair(a, b, left, right)
        if level == LEVEL_L2 and row["kind"] == "measure":
            row = _expression_pair(row, a, b, left, right, names)
        row["id"] = report_ids.add(row["kind"], row["id"], "fully_preserved")["object"]
        out.append(row)
    return out


# ---- L2: expression parity (OSSIE-006b)
#
# Each measure or metric on both sides is read into a small canonical form, but only inside the subset the vendored
# converter itself translates (``_vendor/ossie_microsoft/_dax_to_sql.py`` and ``_sql_to_dax.py``): SUM, AVERAGE
# (SQL AVG), MIN, MAX, COUNT, a distinct count over one column, COUNTROWS / COUNT(*), one division of two of those
# (DIVIDE, with or without its alternate result, or SQL '/'), and a reference to another measure by name. DAX is read
# by the small tokenizer below, which knows this subset only; SQL is parsed by the converter's own reader (sqlglot,
# ``_sql_to_dax._parse``) after a linear size check, and its columns are resolved the converter's way
# (``ossie_to_semantic_model._dataset_column_index``). Nothing is evaluated, and nothing recurses on the input.
#
# A form is a tuple: ("agg", function, table, column), ("rows", table), ("measure", name), or
# ("div", numerator, divisor, zero), where ``zero`` is what a zero divisor gives. Identifiers are casefolded, as DAX
# compares them without case. Two forms that are equal are "Structurally compatible but behavior unverified" (no
# query ran); two that differ are "Different results", each difference listed; an expression outside the subset on
# either side is "Unsupported" with the reason, never Different.
#
# Semantics the forms keep apart (with the converter's evidence):
# - DIVIDE gives BLANK (or its alternate result) on a zero divisor; SQL '/' raises an error or gives NULL by engine.
#   ``_sql_to_dax`` writes DIVIDE(a, b) for ``a / NULLIF(b, 0)``, which gives NULL there, so that form (and a safe
#   division, which also gives NULL) matches DIVIDE(a, b). It also writes DIVIDE for a bare ``a / b``, but that is
#   not the same on a zero divisor, so L2 reports it Different. DAX '/' gives infinity or NaN, and the SQL subset has
#   no alternate result, so DIVIDE(a, b, x) against any SQL division is Different.
# - COUNT: SQL COUNT(c) is the same form as DAX COUNTA (``_sql_to_dax`` writes COUNTA for it) and as DAX COUNT, with
#   a note that a blank and an empty string can differ (L3). ``_dax_to_sql`` refuses DAX COUNT because it doesn't
#   count TRUE/FALSE values, so DAX COUNT against SQL COUNT matches only when the DAX side's column is known and isn't
#   a boolean; otherwise it is Unsupported with that reason.
# - DISTINCTCOUNT counts BLANK as a value, SQL COUNT(DISTINCT c) skips NULL: Different. The converter maps COUNT
#   (DISTINCT c) to DISTINCTCOUNTNOBLANK and back, so that pair matches, with a note that NULL handling is for L3.

LEVEL_L2 = "L2"
NOT_CHECKED_L2 = NOT_CHECKED[1:]  # behavior (L3) and performance (L4)
MAX_EXPRESSION_CHARS = 4096  # a subset expression is a few dozen characters plus names
MAX_EXPRESSION_TOKENS = 64  # DIVIDE(SUM('t'[a]), COUNTROWS('t'), 0) is 17
MAX_SQL_PAREN_DEPTH = 8  # checked before sqlglot (a recursive parser) sees the text
MATCHED = "expression forms match (L2)"
_L1_EXPRESSION_NOTE = "expressions weren't compared: that is L2 (OSSIE-006b)"
_NO_QUERY = "L2 compares expression forms, never results: no query ran (L3 checks behavior)"

SUBSET: tuple[dict[str, str], ...] = (
    {"dax": "SUM('T'[c])", "sql": "SUM(c)", "outcome": COMPATIBLE},
    {"dax": "AVERAGE('T'[c])", "sql": "AVG(c)", "outcome": COMPATIBLE},
    {"dax": "MIN('T'[c]), MAX('T'[c])", "sql": "MIN(c), MAX(c)", "outcome": COMPATIBLE},
    {"dax": "COUNTA('T'[c])", "sql": "COUNT(c)", "outcome": COMPATIBLE,
     "note": "a blank and an empty string can differ (L3)"},
    {"dax": "COUNT('T'[c])", "sql": "COUNT(c)", "outcome": COMPATIBLE,
     "note": "only when the column is known and isn't a boolean (DAX COUNT doesn't count TRUE/FALSE); else Unsupported"},
    {"dax": "DISTINCTCOUNTNOBLANK('T'[c])", "sql": "COUNT(DISTINCT c)", "outcome": COMPATIBLE,
     "note": "the converter's own mapping; NULL and BLANK handling is checked at L3"},
    {"dax": "DISTINCTCOUNT('T'[c])", "sql": "COUNT(DISTINCT c)", "outcome": DIFFERENT,
     "note": "DISTINCTCOUNT counts BLANK as a value; COUNT(DISTINCT) skips NULL"},
    {"dax": "COUNTROWS('T')", "sql": "COUNT(*)", "outcome": COMPATIBLE},
    {"dax": "DIVIDE(a, b)", "sql": "a / NULLIF(b, 0), or a safe division", "outcome": COMPATIBLE},
    {"dax": "DIVIDE(a, b)", "sql": "a / b", "outcome": DIFFERENT, "note": "BLANK against an error or NULL on zero"},
    {"dax": "DIVIDE(a, b, x)", "sql": "any division", "outcome": DIFFERENT, "note": "SQL has no alternate result"},
    {"dax": "a / b", "sql": "any division", "outcome": DIFFERENT, "note": "DAX '/' gives infinity or NaN on zero"},
    {"dax": "[Measure]", "sql": "measure", "outcome": COMPATIBLE, "note": "by name only"},
    {"dax": "anything else (CALCULATE, filters, time intelligence, iterators, VAR, IF, window functions)",
     "sql": "anything else (filtered aggregates, OVER, CASE, COALESCE, arithmetic)", "outcome": UNSUPPORTED},
)

_DAX_AGGREGATES = {"SUM": "SUM", "AVERAGE": "AVERAGE", "MIN": "MIN", "MAX": "MAX", "COUNT": "COUNT",
                   "COUNTA": "COUNT", "DISTINCTCOUNTNOBLANK": "DISTINCTCOUNTNOBLANK", "DISTINCTCOUNT": "DISTINCTCOUNT"}
_DAX_FUNCTIONS = frozenset({*_DAX_AGGREGATES, "COUNTROWS", "DIVIDE", "BLANK"})
_FN_SHOWN = {"SUM": "SUM", "AVERAGE": "AVERAGE (SQL AVG)", "MIN": "MIN", "MAX": "MAX",
             "COUNT": "COUNT of non-blank values (DAX COUNT or COUNTA, SQL COUNT(column))",
             "DISTINCTCOUNTNOBLANK": "a distinct count without BLANK (DAX DISTINCTCOUNTNOBLANK, SQL COUNT(DISTINCT))",
             "DISTINCTCOUNT": "DAX DISTINCTCOUNT, which counts BLANK as a value"}
_DISTINCT_WHY = ("; SQL COUNT(DISTINCT) skips NULL where DAX DISTINCTCOUNT counts BLANK as a value, so they differ by "
                 "one on a nullable column (the converter maps COUNT(DISTINCT) to DISTINCTCOUNTNOBLANK and refuses "
                 "DISTINCTCOUNT)")
_ZERO_SHOWN = {"BLANK": "BLANK (DIVIDE without an alternate result, or SQL '/' over NULLIF(divisor, 0) or a safe "
                        "division, which gives NULL)",
               "DAX /": "infinity or NaN (DAX '/')",
               "SQL /": "an error or NULL, depending on the engine (SQL '/' without NULLIF(divisor, 0))"}
_FILTER_CONTEXT = frozenset({"CALCULATE", "CALCULATETABLE", "FILTER", "ALL", "ALLEXCEPT", "ALLSELECTED", "ALLNOBLANKROW",
                             "KEEPFILTERS", "REMOVEFILTERS", "USERELATIONSHIP", "CROSSFILTER", "VALUES", "DISTINCT",
                             "SELECTEDVALUE", "HASONEVALUE", "ISFILTERED", "TREATAS", "RELATED", "RELATEDTABLE"})
_TIME_PREFIXES = ("TOTALYTD", "TOTALQTD", "TOTALMTD", "DATES", "SAMEPERIOD", "DATEADD", "PARALLELPERIOD", "PREVIOUS",
                  "NEXT", "OPENINGBALANCE", "CLOSINGBALANCE", "STARTOF", "ENDOF", "FIRSTDATE", "LASTDATE")
_CONDITIONAL = frozenset({"IF", "IF.EAGER", "SWITCH", "IFERROR", "COALESCE", "ISBLANK", "AND", "OR", "NOT"})
_WINDOW = frozenset({"WINDOW", "OFFSET", "INDEX", "RANK", "ROWNUMBER", "RUNNINGSUM", "MOVINGAVERAGE", "PARTITIONBY",
                     "ORDERBY"})
_DAX_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r\n]+)
  | (?P<comment>//|--|/\*)
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<table>'(?:[^']|'')*')
  | (?P<column>\[(?:[^\]]|\]\])*\])
  | (?P<number>[0-9]+(?:\.[0-9]+)?)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<punct>[(),/])
  | (?P<op>[-+*&=<>!^|{};:.%])
""", re.VERBOSE)
# SQL tokens for the size check only (quoted names and strings, words, single characters): linear, never parsed.
_SQL_TOKEN = re.compile(r"\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'|`[^`]*`|\[[^\]]*\]|\w+|\S")


class _Outside(Exception):
    """An expression outside the L2 subset; the message is the reason (names only, never a literal)."""


def _dialects(expression: Any) -> dict[str, str]:
    """An Ossie expression's {dialect: text}, first entry per dialect, skipping what isn't text."""
    entries = expression.get("dialects") if isinstance(expression, dict) else None
    out: dict[str, str] = {}
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and isinstance(e.get("dialect"), str) and isinstance(e.get("expression"), str) \
                and e["expression"].strip():
            out.setdefault(e["dialect"], e["expression"])
    return out


def _measure_names(side: _Side) -> frozenset[str]:
    return frozenset(str(e["key"][1]).casefold() for e in side.entries if e["kind"] == "measure")


def _dax_reason(name: str) -> str:
    """Why a DAX function is outside the subset, by family."""
    shown = mo._short(name)
    if name.startswith(_TIME_PREFIXES):
        return f"{shown} is time intelligence, which depends on a date table and the filter context"
    if name in _FILTER_CONTEXT:
        return f"{shown} changes or reads the filter context"
    if name in _WINDOW:
        return f"{shown} is a window function"
    if name in _CONDITIONAL:
        return f"{shown} is conditional logic"
    if name.endswith("X"):
        return f"{shown} is an iterator, evaluated row by row"
    return f"{shown} isn't one of the subset's functions (SUM, AVERAGE, MIN, MAX, COUNT, COUNTA, " \
           "DISTINCTCOUNT, DISTINCTCOUNTNOBLANK, COUNTROWS, DIVIDE)"


def _dax_tokens(text: str) -> list[tuple[str, str]]:
    """The subset's DAX tokens, one pass, at most ``MAX_EXPRESSION_TOKENS``; anything else is outside."""
    _hidden_characters(text)
    tokens: list[tuple[str, str]] = []
    pos, end = 0, len(text)
    while pos < end:
        m = _DAX_TOKEN.match(text, pos)
        if m is None:
            raise _Outside(f"a character the subset doesn't use (U+{ord(text[pos]):04X}) at position {pos}")
        pos = m.end()
        kind = str(m.lastgroup)
        if kind == "ws":
            continue
        if kind == "comment":
            raise _Outside("a comment: L2 reads expressions without comments only")
        tokens.append((kind, m.group()))
        if len(tokens) > MAX_EXPRESSION_TOKENS:
            raise _Outside(f"more than {MAX_EXPRESSION_TOKENS} tokens, past L2's cap")
    return tokens


class _DaxReader:
    """The subset's grammar over a short token list, by position, without recursion:
    expression := DIVIDE '(' term ',' term [',' alternate] ')' | term ['/' term]
    term := aggregate '(' table column ')' | COUNTROWS '(' table ')' | [measure] | table[measure]"""

    def __init__(self, tokens: list[tuple[str, str]], names: frozenset[str]) -> None:
        self.tokens, self.names, self.i = tokens, names, 0
        self.flags: set[str] = set()

    def peek(self, k: int = 0) -> tuple[str, str]:
        return self.tokens[self.i + k] if self.i + k < len(self.tokens) else ("end", "")

    def take(self, kind: str, value: str | None, what: str) -> str:
        tok = self.peek()
        if tok[0] != kind or (value is not None and tok[1] != value):
            raise _Outside(f"expected {what} at token {self.i + 1}")
        self.i += 1
        return tok[1]

    def call(self, name: str) -> bool:
        tok = self.peek()
        return tok[0] == "name" and tok[1].upper() == name and self.peek(1) == ("punct", "(")

    @staticmethod
    def unquote(kind: str, value: str) -> str:
        if kind == "table":
            return value[1:-1].replace("''", "'")
        if kind == "column":
            return value[1:-1].replace("]]", "]")
        return value

    def table(self) -> str:
        kind, value = self.peek()
        if kind not in ("table", "name"):
            raise _Outside(f"expected a table at token {self.i + 1}")
        self.i += 1
        return self.unquote(kind, value).casefold()

    def term(self) -> tuple[Any, ...]:
        kind, value = self.peek()
        if kind == "column":
            self.i += 1
            return ("measure", self.unquote(kind, value).casefold())
        if kind in ("table", "name") and self.peek(1)[0] == "column":
            self.i += 2
            name = self.unquote("column", self.tokens[self.i - 1][1]).casefold()
            if name in self.names:
                return ("measure", name)
            raise _Outside("a column outside an aggregate")
        if kind == "name" and self.peek(1) == ("punct", "("):
            fn = value.upper()
            self.i += 2
            if fn == "COUNTROWS":
                table = self.table()
                self.take("punct", ")", "')' after COUNTROWS's table")
                return ("rows", table)
            if fn in _DAX_AGGREGATES:
                table = self.table()
                column = self.unquote("column", self.take("column", None, f"one fully qualified column (Table[Column]) in {fn}"))
                self.take("punct", ")", f"')' after {fn}'s column: the subset aggregates one column")
                if fn == "COUNT":
                    self.flags.add("dax_count")
                return ("agg", _DAX_AGGREGATES[fn], table, column.casefold())
            raise _Outside(f"{mo._short(fn)} inside another expression" if fn == "DIVIDE" else _dax_reason(fn))
        raise _Outside(f"expected an aggregate, COUNTROWS or a measure at token {self.i + 1}")

    def alternate(self) -> str:
        if self.call("BLANK"):
            self.i += 2
            self.take("punct", ")", "')' after BLANK(")
            return "BLANK"
        sign = "-" if self.peek() == ("op", "-") else ""
        self.i += bool(sign)
        number = Decimal(sign + self.take("number", None, "a number or BLANK() as DIVIDE's alternate result"))
        return f"ALT:{format(number.normalize() if number else Decimal(0), 'f')}"

    def done(self) -> None:
        kind, value = self.peek()
        if kind == "op":
            raise _Outside(f"arithmetic other than one division (the operator {value})")
        if kind != "end":
            raise _Outside(f"more than one division or term (token {self.i + 1})")

    def expression(self) -> tuple[Any, ...]:
        if self.call("DIVIDE"):
            self.i += 2
            numerator = self.term()
            self.take("punct", ",", "',' between DIVIDE's numerator and divisor")
            divisor = self.term()
            zero = "BLANK"
            if self.peek() == ("punct", ","):
                self.i += 1
                zero = self.alternate()
            self.take("punct", ")", "')' closing DIVIDE")
            self.done()
            return ("div", numerator, divisor, zero)
        first = self.term()
        if self.peek() == ("punct", "/"):
            self.i += 1
            divisor = self.term()
            self.done()
            return ("div", first, divisor, "DAX /")
        self.done()
        return first


def _dax_form(text: str, names: frozenset[str]) -> tuple[tuple[Any, ...], frozenset[str]]:
    tokens = _dax_tokens(text)
    if tokens[:1] == [("op", "=")]:  # a measure body may start with '=', as the converter strips
        tokens = tokens[1:]
    if not tokens:
        raise _Outside("no expression")
    for i, (kind, value) in enumerate(tokens):  # the family of the first function outside the subset, by name
        if kind == "name" and value.upper() in ("VAR", "RETURN"):
            raise _Outside("variables (VAR ... RETURN)")
        if kind == "name" and tokens[i + 1:i + 2] == [("punct", "(")] and value.upper() not in _DAX_FUNCTIONS:
            raise _Outside(_dax_reason(value.upper()))
        if kind == "string":
            raise _Outside("a text value (a filter or a condition)")
    reader = _DaxReader(tokens, names)
    return reader.expression(), frozenset(reader.flags)


@functools.lru_cache(maxsize=1)
def _vendor_sql() -> tuple[Any, Any, Any]:
    """The converter's SQL reader, its column index and its error. Loaded on first use, as the converter is."""
    from ._vendor.ossie_microsoft import _sql_to_dax
    from ._vendor.ossie_microsoft._common import ConversionError
    from ._vendor.ossie_microsoft.ossie_to_semantic_model import _dataset_column_index
    return _sql_to_dax, _dataset_column_index, ConversionError


def _column_index(side: _Side) -> dict[str, tuple[str, str]]:
    """SQL column names (bare and dataset-qualified, casefolded) to (dataset, field), the converter's way: a field's
    name or plain source column, dropped where it names more than one field."""
    if side.sql_index is not None:
        return side.sql_index
    _, dataset_index, conversion_error = _vendor_sql()
    seen: dict[str, set[tuple[str, str]]] = {}
    for d in side.datasets:
        try:
            index = dataset_index(d)
        except (conversion_error, AttributeError, TypeError) as exc:  # narrow: a field the converter can't read
            side.warn({"object": f"table {mo._q(d['name'])}", "kind": "lost",
                       "message": f"L2 couldn't read this dataset's fields to resolve SQL columns: {mo._short(exc)}"})
            continue
        for key, target in index.items():
            seen.setdefault(key, set()).add(target)
    side.sql_index = {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}
    return side.sql_index


def _hidden_characters(text: str) -> None:
    """Control and format characters (a bidirectional override, a zero-width space) change what an expression
    looks like, not what a reader sees: outside the subset wherever they are."""
    for pos, ch in enumerate(text):
        if ch not in "\t\r\n" and unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            raise _Outside(f"a control or invisible character (U+{ord(ch):04X}) at position {pos}")


def _sql_prescreen(text: str) -> None:
    """Size, nesting and characters before the recursive parser sees the text: linear passes. Outside quotes the
    subset's SQL is ASCII, so a lookalike letter (a fullwidth SUM) is never read as a function or name."""
    _hidden_characters(text)
    count = depth = 0
    for m in _SQL_TOKEN.finditer(text):
        count += 1
        if count > MAX_EXPRESSION_TOKENS:
            raise _Outside(f"more than {MAX_EXPRESSION_TOKENS} tokens, past L2's cap")
        if m.group()[0] not in "\"'`[" and not m.group().isascii():
            raise _Outside(f"a character the subset doesn't use outside quotes (U+{ord(m.group()[0]):04X})")
        depth += {"(": 1, ")": -1}.get(m.group(), 0)
        if depth > MAX_SQL_PAREN_DEPTH:
            raise _Outside(f"parentheses nested more than {MAX_SQL_PAREN_DEPTH} deep, past L2's cap")


def _sql_reason(node: Any, exp: Any) -> str:
    """Why a SQL node is outside the subset, for the families the plan names, or "" when it may be inside."""
    if isinstance(node, exp.Filter):
        return "a filtered aggregate (FILTER (WHERE ...))"
    if isinstance(node, exp.Window):
        return "a window function (OVER ...)"
    if isinstance(node, (exp.Case, exp.If)):
        return "conditional logic (CASE or IF)"
    if isinstance(node, exp.Coalesce):
        return "COALESCE"
    if isinstance(node, (exp.Select, exp.Subquery)):
        return "a subquery"
    if isinstance(node, exp.Anonymous):
        return f"{mo._short(node.name)} isn't one of the subset's functions (SUM, AVG, MIN, MAX, COUNT, NULLIF)"
    return ""


def _sql_form(text: str, dialect: str, side: _Side, scope: str | None,
              names: frozenset[str]) -> tuple[tuple[Any, ...], frozenset[str]]:
    from sqlglot import exp

    _sql_prescreen(text)
    sql_to_dax, _, _ = _vendor_sql()
    tree, reason = sql_to_dax._parse(text, dialect)
    if tree is None:
        raise _Outside(f"the converter's SQL reader refused it: {reason}")
    for node in tree.walk():  # breadth first, iterative
        why = _sql_reason(node, exp)
        if why:
            raise _Outside(why)
    aggregates = {exp.Sum: "SUM", exp.Avg: "AVERAGE", exp.Min: "MIN", exp.Max: "MAX", exp.Count: "COUNT"}

    def unparen(n: Any) -> Any:
        while isinstance(n, exp.Paren):
            n = n.this
        return n

    def resolve(column: Any) -> tuple[str, str]:
        if column.args.get("db") or column.args.get("catalog"):
            raise _Outside("a column named with its database or catalog")
        index = _column_index(side)
        keys = [f"{column.table}.{column.name}"] if column.table else \
            ([f"{scope}.{column.name}"] if scope else []) + [column.name]
        hit = next((index[k.casefold()] for k in keys if k.casefold() in index), None)
        if hit is None:
            raise _Outside(f"column {mo._q(column.sql())} doesn't resolve to exactly one dataset field")
        return hit

    def term(n: Any) -> tuple[Any, ...]:
        n = unparen(n)
        fn = aggregates.get(type(n))
        if fn is not None:
            if n.args.get("expressions"):
                raise _Outside(f"{fn} over more than one argument")
            arg = n.this
            if isinstance(n, exp.Count) and isinstance(arg, exp.Star):
                table = scope or (side.datasets[0]["name"] if len(side.datasets) == 1 else None)
                if table is None:
                    raise _Outside("COUNT(*) needs exactly one dataset to count rows of (the converter's rule)")
                return ("rows", table.casefold())
            if isinstance(arg, exp.Distinct):
                operands = arg.args.get("expressions") or []
                if not isinstance(n, exp.Count) or len(operands) != 1:
                    raise _Outside(f"DISTINCT inside {fn} or over more than one column")
                arg, fn = operands[0], "DISTINCTCOUNTNOBLANK"
            if not isinstance(arg, exp.Column):
                raise _Outside(f"{fn} over an expression, not one column")
            dataset, field = resolve(arg)
            return ("agg", fn, dataset.casefold(), field.casefold())
        if isinstance(n, exp.Column) and not n.table and n.name.casefold() in names:
            return ("measure", n.name.casefold())
        if isinstance(n, exp.Column):
            raise _Outside("a column outside an aggregate")
        if isinstance(n, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Neg)):
            raise _Outside("arithmetic other than one division")
        raise _Outside(f"a {type(n).__name__} expression isn't in the subset")

    node = unparen(tree)
    if not isinstance(node, exp.Div):
        return term(node), frozenset()
    if node.args.get("typed"):
        raise _Outside(f"'/' in {dialect} divides integers as integers, which DAX never does")
    divisor, zero = unparen(node.expression), "BLANK" if node.args.get("safe") else "SQL /"
    if isinstance(divisor, exp.Nullif):
        literal = divisor.expression
        if not isinstance(literal, exp.Literal) or literal.is_string or literal.this != "0":
            raise _Outside("a divisor guarded by NULLIF with anything but 0 (the converter's rule)")
        divisor, zero = divisor.this, "BLANK"
    return ("div", term(node.this), term(divisor), zero), frozenset({"sql_division"})


def _pick(dialects: dict[str, str]) -> tuple[str, str] | None:
    """The expression L2 compares on one side: a SQL dialect the converter reads (the cross-dialect check is the
    point of L2), else DAX."""
    for name in ("ANSI_SQL", "OSSIE_SQL_2026", *sorted(d for d in dialects if d in mo._SQL_DIALECTS)):
        if name in dialects:
            return name, dialects[name]
    return ("DAX", dialects["DAX"]) if "DAX" in dialects else None


def _side_form(side: _Side, entry: dict[str, Any], names: frozenset[str]) -> dict[str, Any]:
    expr = entry.get("expr") or {}
    dialects: dict[str, str] = expr.get("dialects") or {}
    picked = _pick(dialects)
    out: dict[str, Any] = {"which": side.which, "dialect": None, "text": None, "form": None, "flags": frozenset(),
                           "reason": "", "others": []}
    if picked is None:
        out["reason"] = "no DAX or SQL expression the converter reads" + \
            (f" (dialects: {', '.join(mo._short(d) for d in list(dialects)[:5])})" if dialects else "")
        return out
    dialect, text = picked
    out.update(dialect=dialect, text=text, others=[d for d in dialects if d != dialect])
    try:
        if len(text) > MAX_EXPRESSION_CHARS:
            raise _Outside(f"{len(text)} characters, past L2's cap of {MAX_EXPRESSION_CHARS}")
        if dialect == "DAX":
            out["form"], out["flags"] = _dax_form(text, names)
        else:
            out["form"], out["flags"] = _sql_form(text, dialect, side, expr.get("scope"), names)
    except _Outside as exc:  # narrow: outside the subset; the reason is the result
        out["reason"] = str(exc)
    return out


def _term_shown(t: tuple[Any, ...]) -> str:
    if t[0] == "agg":
        return f"{t[1]}({mo._short(t[2])}[{mo._short(t[3])}])"
    if t[0] == "rows":
        return f"COUNTROWS({mo._short(t[1])})"
    return f"measure [{mo._short(t[1])}]"


def _form_text(f: tuple[Any, ...]) -> str:
    if f[0] != "div":
        return _term_shown(f)
    zero = "alternate " + f[3][4:] if f[3].startswith("ALT:") else f[3]
    return mo._short(f"DIVIDE({_term_shown(f[1])}, {_term_shown(f[2])}; zero divisor: {zero})")


def _term_changes(prefix: str, s: tuple[Any, ...], t: tuple[Any, ...]) -> list[str]:
    if s == t:
        return []
    if s[0] != t[0] or s[0] != "agg":
        return [f"{prefix}{_term_shown(s)} on the left side, {_term_shown(t)} on the right"]
    out = []
    if s[1] != t[1]:
        why = _DISTINCT_WHY if {s[1], t[1]} == {"DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK"} else ""
        out.append(f"{prefix}aggregate {_FN_SHOWN[s[1]]} on the left side, {_FN_SHOWN[t[1]]} on the right{why}")
    if s[2:] != t[2:]:
        out.append(f"{prefix}column {mo._short(s[2])}[{mo._short(s[3])}] on the left side, "
                   f"{mo._short(t[2])}[{mo._short(t[3])}] on the right")
    return out


def _form_changes(f: tuple[Any, ...], g: tuple[Any, ...]) -> list[str]:
    """How form ``g`` (right) differs from ``f`` (left), one line each; alternate values aren't quoted."""
    if f == g:
        return []
    if (f[0] == "div") != (g[0] == "div"):
        div, other = ("left", "right") if f[0] == "div" else ("right", "left")
        single = g if f[0] == "div" else f
        return [f"a division on the {div} side, {_term_shown(single)} on the {other}"]
    if f[0] != "div":
        return _term_changes("", f, g)
    out = _term_changes("numerator: ", f[1], g[1]) + _term_changes("divisor: ", f[2], g[2])
    if f[3] != g[3]:
        if f[3].startswith("ALT:") and g[3].startswith("ALT:"):
            out.append("on a zero divisor: DIVIDE's alternate result differs between the sides")
        else:
            def shown(z: str) -> str:
                return "DIVIDE's alternate result" if z.startswith("ALT:") else _ZERO_SHOWN[z]
            out.append(f"on a zero divisor the left side gives {shown(f[3])} and the right side {shown(g[3])}")
    return out


def _is_boolean(side: _Side, table: str, column: str) -> bool | None:
    """Whether one side's column is a boolean: None when that side has no such column with a type."""
    if side.column_types is None:
        side.column_types = {}
        for e in side.entries:
            if e["kind"] == "column":
                side.column_types.setdefault((str(e["key"][1]).casefold(), str(e["key"][2]).casefold()),
                                             e["attrs"].get("type", (None, None))[1])
    value = side.column_types.get((table, column))
    return None if value is None else str(value).casefold() == "boolean"


def _caveats(pl: dict[str, Any], pr: dict[str, Any], left: _Side, right: _Side) -> tuple[str, list[str]]:
    """For two equal forms: Unsupported with a reason when a known semantic difference can't be ruled out, else the
    notes that go with the match."""
    form = pl["form"]
    terms = [form[1], form[2]] if form[0] == "div" else [form]
    cross = (pl["dialect"] == "DAX") != (pr["dialect"] == "DAX")
    notes: list[str] = []
    fns = {t[1] for t in terms if t[0] == "agg"}
    if cross and "COUNT" in fns:
        notes.append("COUNT counts non-blank values in DAX and non-NULL values in SQL; a blank and an empty string "
                     "can differ, which L3 checks")
        dax, side = (pl, left) if pl["dialect"] == "DAX" else (pr, right)
        if "dax_count" in dax["flags"]:
            for t in terms:
                if t[0] == "agg" and t[1] == "COUNT" and _is_boolean(side, t[2], t[3]) is not False:
                    return UNSUPPORTED, [(f"DAX COUNT doesn't count TRUE/FALSE values where SQL COUNT does, and "
                                          f"the {side.which} side's column {mo._short(t[2])}[{mo._short(t[3])}] is a "
                                          "boolean or has no known type (the converter refuses this pair and writes "
                                          "COUNTA for SQL COUNT)")]
            notes.append("DAX COUNT doesn't count TRUE/FALSE values; this column isn't a boolean")
    if not cross and "COUNT" in fns and ("dax_count" in pl["flags"]) != ("dax_count" in pr["flags"]):
        notes.append("COUNT on one side and COUNTA on the other: they differ only on a TRUE/FALSE column")
    if cross and "DISTINCTCOUNTNOBLANK" in fns:
        notes.append("the converter maps SQL COUNT(DISTINCT) to DAX DISTINCTCOUNTNOBLANK and back, so the forms "
                     "match; how each side skips NULL and BLANK is checked at L3")
    if cross and (fns & {"COUNT", "DISTINCTCOUNTNOBLANK"} or any(t[0] == "rows" for t in terms)):
        notes.append("over no rows a DAX count gives BLANK and SQL gives 0 (the converter accepts this both ways)")
    if cross and "AVERAGE" in fns:
        notes.append("SQL AVG over an integer column gives an integer in some engines; DAX AVERAGE gives a decimal")
    if cross and form[0] == "div":
        notes.append("SQL '/' between integers truncates in some engines; DAX divides as a decimal")
    if any(t[0] == "measure" for t in terms):
        notes.append("a measure reference matches by name only; what that measure computes is its own row")
    return COMPATIBLE, notes


def _compare_forms(pl: dict[str, Any], pr: dict[str, Any], left: _Side, right: _Side) -> tuple[str, list[str], list[str]]:
    """One measure's L2 result from the two sides' forms: (result, changes, notes)."""
    notes = [f"on the {p['which']} side its {', '.join(mo._short(d) for d in p['others'][:5])} form wasn't compared; "
             f"L2 compared its {p['dialect']} form" for p in (pl, pr) if p["others"]]
    outside = [p for p in (pl, pr) if p["form"] is None]
    if outside:
        notes += [f"outside the L2 subset on the {p['which']} side: {p['reason']}" for p in outside]
        if pl["text"] is not None and (pl["dialect"], pl["text"]) == (pr["dialect"], pr["text"]):
            notes.append("the expression text is the same on both sides; outside the subset that is compared as "
                         "text only, which says nothing about what it computes")
        return UNSUPPORTED, [], notes
    changes = _form_changes(pl["form"], pr["form"])
    if changes:
        return DIFFERENT, changes, notes
    result, more = _caveats(pl, pr, left, right)
    if result == UNSUPPORTED:
        return UNSUPPORTED, [], notes + more
    return COMPATIBLE, [], [MATCHED, *notes, *more, _NO_QUERY]


def _expression_pair(row: dict[str, Any], a: dict[str, Any] | None, b: dict[str, Any] | None, left: _Side,
                     right: _Side, names: tuple[frozenset[str], frozenset[str]]) -> dict[str, Any]:
    """An L1 measure row with its expressions and, when both sides have it and L1 could compare it, the L2 result:
    the worse of the two, the changes of both."""
    exprs: dict[str, Any] = {"left": None, "right": None, "left_form": None, "right_form": None,
                             "left_dialect": None, "right_dialect": None}
    parsed: list[dict[str, Any] | None] = []
    for side, e, nm in ((left, a, names[0]), (right, b, names[1])):
        p = _side_form(side, e, nm) if e is not None else None
        if p is not None:
            exprs[side.which] = None if p["text"] is None else mo._short(p["text"])
            exprs[f"{side.which}_form"] = None if p["form"] is None else _form_text(p["form"])
            exprs[f"{side.which}_dialect"] = p["dialect"]
        parsed.append(p)
    row["expressions"] = exprs
    notes = [n for n in row["notes"] if n != _L1_EXPRESSION_NOTE]
    pl, pr = parsed
    if pl is None or pr is None or row["result"] in (UNSUPPORTED, TEST_FAILED):
        why = "it is on one side only" if pl is None or pr is None else f"the L1 result is {row['result']}"
        row["notes"] = notes + [f"expressions weren't compared at L2: {why}"]
        return row
    result, changes, more = _compare_forms(pl, pr, left, right)
    if result not in _RANK or row["result"] not in _RANK:  # never reported: L2 can't claim equivalence (the ceiling guard, before it's ranked)
        raise AssertionError(f"L2 parity can't report better than {CEILING!r}")
    row["expression_result"] = result
    row["changes"] = row["changes"] + changes
    row["notes"] = list(dict.fromkeys(notes + more))
    row["result"] = min(row["result"], result, key=_RANK.__getitem__)
    return row


# ---- the report

def _tolerances(value: Any, level: str = LEVEL) -> dict[str, Any]:
    values = dict(DEFAULT_TOLERANCES)
    ignored: list[str] = []
    if value is not None and not isinstance(value, dict):
        ignored.append(f"(not a mapping: {type(value).__name__})")
    elif value:
        for k, v in value.items():
            ok = k in DEFAULT_TOLERANCES and isinstance(v, (int, float)) and not isinstance(v, bool) \
                and math.isfinite(v) and v >= 0
            if ok:
                values[k] = float(v)
            elif len(ignored) < 20:
                ignored.append(mo._short(k))
    return {"values": values, "defaults": dict(DEFAULT_TOLERANCES), "used": False, "ignored": ignored,
            "note": (f"{level} compares {'structure' if level == LEVEL else 'expression forms'} only, so no "
                     "tolerance applies; they apply from L3 (OSSIE-006c)")}


def _check_ceiling(objects: list[dict[str, Any]], summary: str, level: str = LEVEL) -> None:
    """L1 and L2 never claim equivalence: a result above the ceiling is a programming error, never reported."""
    if summary not in L1_RESULTS or any(o["result"] not in L1_RESULTS for o in objects) or \
            any(o.get("expression_result", CEILING) not in L1_RESULTS for o in objects):
        raise AssertionError(f"{level} parity can't report better than {CEILING!r}")


def _report(left: _Side, right: _Side, objects: list[dict[str, Any]], tolerances: dict[str, Any],
            error: str = "", level: str = LEVEL) -> dict[str, Any]:
    _check_ceiling(objects, TEST_FAILED, level)
    counts = dict.fromkeys(RESULTS, 0)
    for o in objects:
        counts[o["result"]] += 1
    summary = min((o["result"] for o in objects), key=_RANK.__getitem__, default=TEST_FAILED)
    _check_ceiling(objects, summary, level)
    out = {"level": level, "ceiling": CEILING, "summary": summary, "error": error,
           "sides": {"left": left.info(), "right": right.info()},
           "objects": objects, "counts": counts,
           "not_checked": [dict(n) for n in (NOT_CHECKED if level == LEVEL else NOT_CHECKED_L2)],
           "tolerances": tolerances}
    if level == LEVEL_L2:
        out["subset"] = [dict(row) for row in SUBSET]
    return out


def _whole(result: str, reason: str, sides: list[_Side]) -> list[dict[str, Any]]:
    """One model row standing for the whole comparison (Test failed, or a side L1 can't compare)."""
    return [{"id": "model", "kind": "model", "result": result, "changes": [], "notes": [reason],
             "evidence": [{"side": s.which, "source": "read", "detail": s.error or s.unsupported} for s in sides
                          if s.error or s.unsupported],
             "ids": {"left": None, "right": None}, "fidelity": {"left": None, "right": None}}]


def parity_l1(left: Any, right: Any, *, tolerances: dict[str, Any] | None = None, left_kind: str | None = None,
              right_kind: str | None = None) -> dict[str, Any]:
    """The L1 structural parity report of ``left`` and ``right`` (module docstring). Never raises for bad input:
    a side that can't be read makes the summary Test failed, with the reason in ``error`` and in that side's
    ``sides[...]["error"]``."""
    return _parity(left, right, tolerances, left_kind, right_kind, LEVEL)


def parity_l2(left: Any, right: Any, *, tolerances: dict[str, Any] | None = None, left_kind: str | None = None,
              right_kind: str | None = None) -> dict[str, Any]:
    """The L2 expression parity report of ``left`` and ``right``: the L1 report, every row of it, with each measure
    or metric on both sides also compared as an expression (the L2 section above). Same inputs as ``parity_l1``;
    never raises for bad input. The best result is still ``CEILING``: no query runs at L2."""
    return _parity(left, right, tolerances, left_kind, right_kind, LEVEL_L2)


def _parity(left: Any, right: Any, tolerances: dict[str, Any] | None, left_kind: str | None,
            right_kind: str | None, level: str) -> dict[str, Any]:
    tol = _tolerances(tolerances, level)
    sides = [_Side("left"), _Side("right")]
    for side, value, kind in ((sides[0], left, left_kind), (sides[1], right, right_kind)):
        try:
            _read(side, value, kind)
        except _READ_ERRORS as exc:  # narrow: what a hostile or wrong input raises; the reason is the result
            side.error = f"{type(exc).__name__}: {mo._short(exc)}"
    failed = [s for s in sides if s.error]
    if failed:
        reason = "; ".join(f"the {s.which} side couldn't be read: {s.error}" for s in failed)
        return _report(sides[0], sides[1], _whole(TEST_FAILED, reason, failed), tol, reason, level)
    unsupported = [s for s in sides if s.unsupported]
    if unsupported:
        reason = "; ".join(f"the {s.which} side: {s.unsupported}" for s in unsupported)
        return _report(sides[0], sides[1], _whole(UNSUPPORTED, reason, unsupported), tol, reason, level)
    try:
        objects = _compare(sides[0], sides[1], level)
    except _SHAPE_ERRORS as exc:  # narrow: a shape the comparison trips on is a failed test, never a crash
        reason = f"the comparison couldn't be completed: {type(exc).__name__}: {mo._short(exc)}"
        return _report(sides[0], sides[1], _whole(TEST_FAILED, reason, []), tol, reason, level)
    return _report(sides[0], sides[1], objects, tol, level=level)
