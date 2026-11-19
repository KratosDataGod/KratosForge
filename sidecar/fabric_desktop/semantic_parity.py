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

Pure: nothing is fetched, written or executed, and no expression is evaluated.
"""

from __future__ import annotations

import collections
import copy
import functools
import hashlib
import json
import math
from typing import Any

from . import model_ossie as mo

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
MAX_TMSL_CHARS = 200_000_000


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

    def add(self, kind: str, ident: str, key: tuple[Any, ...], attrs: dict[str, Any] | None = None,
            raw: dict[str, Any] | None = None, needs: tuple[str, ...] = (), cls: str | None = None) -> dict[str, Any]:
        obj = self.objs.add(kind, ident, "fully_preserved")
        entry = {"kind": kind, "id": obj["object"], "key": key, "attrs": attrs or {}, "raw": raw or {},
                 "needs": needs, "cls": cls, "notes": [], "evidence": []}
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
            side.add("measure", f"{tid} measure {mo._q(m['name'])}", ("measure", m["name"]),
                     {"home": None if m["name"] in unhomed else name}, needs=needs)
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
    for d in mo._named_items(model.get("datasets")):
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
            side.add("measure", f"{tid} measure {mo._q(m['name'])}", ("measure", m["name"]), {"home": name})
    for m in mo._named_items(model.get("metrics")):
        side.add("measure", f"metric {mo._q(m['name'])}", ("measure", m["name"]), {"home": None})
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
    counts is withheld (a length and hash of it). Reader warnings about a role are withheld the same way. The
    report itself is unchanged."""
    out = copy.deepcopy(report)
    for o in out.get("objects") or []:
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


def _compare(left: _Side, right: _Side) -> list[dict[str, Any]]:
    """Every object of both sides once: matched by key (relationships by name, then by endpoints), keyed lookups
    only, so the work grows with the number of objects."""
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
    for a, b in pairs:
        row = _pair(a, b, left, right)
        row["id"] = report_ids.add(row["kind"], row["id"], "fully_preserved")["object"]
        out.append(row)
    return out


# ---- the report

def _tolerances(value: Any) -> dict[str, Any]:
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
            "note": "L1 compares structure only, so no tolerance applies; they apply from L3 (OSSIE-006c)"}


def _check_ceiling(objects: list[dict[str, Any]], summary: str) -> None:
    """L1 never claims equivalence: a result above the ceiling is a programming error, never reported."""
    if summary not in L1_RESULTS or any(o["result"] not in L1_RESULTS for o in objects):
        raise AssertionError(f"L1 structural parity can't report better than {CEILING!r}")


def _report(left: _Side, right: _Side, objects: list[dict[str, Any]], tolerances: dict[str, Any],
            error: str = "") -> dict[str, Any]:
    _check_ceiling(objects, TEST_FAILED)
    counts = dict.fromkeys(RESULTS, 0)
    for o in objects:
        counts[o["result"]] += 1
    summary = min((o["result"] for o in objects), key=_RANK.__getitem__, default=TEST_FAILED)
    _check_ceiling(objects, summary)
    return {"level": LEVEL, "ceiling": CEILING, "summary": summary, "error": error,
            "sides": {"left": left.info(), "right": right.info()},
            "objects": objects, "counts": counts,
            "not_checked": [dict(n) for n in NOT_CHECKED], "tolerances": tolerances}


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
    tol = _tolerances(tolerances)
    sides = [_Side("left"), _Side("right")]
    for side, value, kind in ((sides[0], left, left_kind), (sides[1], right, right_kind)):
        try:
            _read(side, value, kind)
        except _READ_ERRORS as exc:  # narrow: what a hostile or wrong input raises; the reason is the result
            side.error = f"{type(exc).__name__}: {mo._short(exc)}"
    failed = [s for s in sides if s.error]
    if failed:
        reason = "; ".join(f"the {s.which} side couldn't be read: {s.error}" for s in failed)
        return _report(sides[0], sides[1], _whole(TEST_FAILED, reason, failed), tol, reason)
    unsupported = [s for s in sides if s.unsupported]
    if unsupported:
        reason = "; ".join(f"the {s.which} side: {s.unsupported}" for s in unsupported)
        return _report(sides[0], sides[1], _whole(UNSUPPORTED, reason, unsupported), tol, reason)
    try:
        objects = _compare(sides[0], sides[1])
    except _SHAPE_ERRORS as exc:  # narrow: a shape the comparison trips on is a failed test, never a crash
        reason = f"the comparison couldn't be completed: {type(exc).__name__}: {mo._short(exc)}"
        return _report(sides[0], sides[1], _whole(TEST_FAILED, reason, []), tol, reason)
    return _report(sides[0], sides[1], objects, tol)
