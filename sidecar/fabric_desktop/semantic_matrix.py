"""Cross-platform compatibility matrix (OSSIE-015a, engine only): one definition against every target KratosForge
writes.

``compatibility(definition, kind=None)`` takes one semantic definition (a ``model.bim``, a TMDL folder's files or an
Ossie document in any profile, as ``semantic_parity`` reads them) and returns one row per target profile:

- ``power-bi-model``: a Power BI semantic model (TMSL), through the vendored converter's Import (``to_tmsl``);
- ``ossie-0.2``: Apache Ossie 0.2.0.dev0, through Export (``model_ossie.export``);
- ``fabric-semantic-view``: a Fabric Semantic View's files (``semantic_view.semantic_view``);
- ``snowflake-ossie``: Snowflake's Ossie 0.1.1 (``ossie_snowflake.to_snowflake`` on the Ossie 0.2 export).

Each row has a ``status`` (``STATUSES``: converted, source, refused, failed, not available) and a ``result`` in the
words of plan D.5. The best result is **Structurally compatible but behavior unverified** (``CEILING``): no row
claims Verified equivalent or Equivalent within tolerance, because nothing is run (L3) or timed (L4) here; both are
listed under ``not_checked``. A converted row carries the fidelity counts with their basis (``BASES``), the source
objects the target drops (by the source's names, each once: a view report's "dataset ... field" and a parity
report's "table ... column" are matched by structural key), the full ``semantic_parity.parity_l2`` report of the
source against the target, and what the target does to every security role and calculation group of the source
(``FATES``: kept, extension only, changed, dropped, unverified). Roles and calculation groups are never silent:
each is named in every row, and ``warnings`` puts the security warnings first.

Known limit, stated in the view row's notes rather than hidden: a Semantic View has no dataset fields (Fabric
reads the columns from the lakehouse), so L2 can't resolve a column in a view metric's SQL, and a measure the view
keeps can show Unsupported at L2.

Input that can't be read is a Test failed row for every target, with the reason; a definition the converter
can't take (Ossie 0.3.0.dev0, or Snowflake's 0.1.1) is Unsupported with the reason. ``redacted`` makes a copy safe
outside the local pane. Pure: nothing is fetched, written or executed, and nothing calls Fabric or Snowflake.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from . import model_ossie as mo, semantic_parity as sp

TARGETS = {
    "power-bi-model": "Power BI semantic model (TMSL)",
    "ossie-0.2": "Apache Ossie 0.2.0.dev0",
    "fabric-semantic-view": "Fabric Semantic View (Ossie 0.2.0.dev0)",
    "snowflake-ossie": "Snowflake's Ossie 0.1.1",
}
CONVERTED, SOURCE, REFUSED, FAILED, NOT_AVAILABLE = "converted", "source", "refused", "failed", "not available"
STATUSES = (CONVERTED, SOURCE, REFUSED, FAILED, NOT_AVAILABLE)
KEPT, EXTENSION_ONLY, CHANGED, DROPPED, UNVERIFIED = "kept", "extension only", "changed", "dropped", "unverified"
FATES = (KEPT, EXTENSION_ONLY, CHANGED, DROPPED, UNVERIFIED)
_WORST_FATE = (DROPPED, CHANGED, UNVERIFIED, EXTENSION_ONLY, KEPT)  # a role's fate is the worst any evidence gives
BASES = ("converter fidelity report", "semantic view report", "Snowflake report", "derived from parity rows")
CEILING = sp.CEILING
MAX_DROPPED = 50
VIEW_NAME = "view"  # the emitter needs a folder name; the matrix never writes one
COLUMNS = (
    {"key": "target", "label": "Target"},
    {"key": "status", "label": "Status"},
    {"key": "result", "label": "Result (D.5)"},
    {"key": "fidelity", "label": "Fidelity"},
    {"key": "dropped", "label": "Dropped"},
    {"key": "parity", "label": "Parity (L2)"},
    {"key": "roles", "label": "Security roles"},
    {"key": "calculation_groups", "label": "Calculation groups"},
)
_EXTENSION = "Preserved through extensions"
_UNSUPPORTED_CLASS = "Unsupported"
_CLASS_FATE = {"fully_preserved": KEPT, "preserved_through_extensions": EXTENSION_ONLY, "partially_converted": CHANGED,
               "unsupported": DROPPED, "unverified": UNVERIFIED}
# What a conversion can raise. A ValueError is the converter or emitter refusing the input by a rule (Unsupported);
# anything else is a failure (Test failed).
_RUN_ERRORS = (ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError, RecursionError)
_SF_PATH = re.compile(r"^\$\.semantic_model\[0\]\.(?:datasets\[(\d+)\]\.)?(fields|metrics)\[(\d+)\]")


# ---- the source

class _Source:
    """The definition as read: the parity reader's view of it (ids and structural keys), and its conversions."""

    def __init__(self) -> None:
        self.value: Any = None
        self.kind = ""
        self.side = sp._Side("left")
        self.ids: dict[str, tuple[Any, ...]] = {}  # parity id on the source side -> structural key
        self.by_key: dict[str, str] = {}  # canonical key -> the source's name for it (first)
        self.error = ""

    def read(self, definition: Any, kind: str | None) -> None:
        if kind is not None:
            if not isinstance(kind, str) or kind.lower() not in sp._KIND_ALIASES:
                raise ValueError(f"There's no kind {mo._short(kind)!r}. The kinds are tmsl, tmdl and ossie")
            kind = sp._KIND_ALIASES[kind.lower()]
            if kind == "semantic_view":
                raise ValueError("A Semantic View is a target, not a source: give the model or Ossie file it was made "
                                 "from, or compare the view with semantic_parity.parity_l2")
        value = definition
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("This file isn't UTF-8 text") from None
        if isinstance(value, str):
            if kind == "tmdl":
                raise TypeError("TMDL is a folder of files: pass them as path -> text")
            if kind == "tmsl" or (kind is None and value.lstrip("﻿ \t\r\n").startswith("{")):
                size, value = len(value), sp._json_text(value)
                if (kind or sp._detect(value)) == "ossie" and size > mo.MAX_CHARS:  # Ossie keeps Ossie's cap
                    raise ValueError(f"This file is {size // 1_000_000} MB, but an Ossie model is at most "
                                     f"{mo.MAX_CHARS // 1_000_000} MB")
            else:
                value = mo.load_text(value)
        sp._read(self.side, value, kind)
        self.value, self.kind = value, self.side.kind
        for e in self.side.entries:
            self.ids[e["id"]] = e["key"]
            self.by_key.setdefault(mo._canon(e["key"]), e["id"])

    def flagged(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.side.entries if e["kind"] == kind]

    def info(self) -> dict[str, Any]:
        s = self.side
        return {"kind": s.kind or None, "label": s.label if s.kind else "", "format": s.format, "version": s.version,
                "profile": s.profile, "read": not self.error, "error": self.error}


def _source_target(src: _Source) -> str | None:
    if src.kind in ("tmsl", "tmdl"):
        return "power-bi-model"
    return src.side.profile if src.side.profile in ("ossie-0.2", "snowflake-ossie") else None


# ---- one row

def _row(target: str, status: str, result: str, reason: str = "") -> dict[str, Any]:
    return {"target": target, "label": TARGETS[target], "status": status, "result": result, "reason": reason,
            "notes": [], "fidelity": None, "dropped": {"objects": [], "total": 0},
            "extension_only": {"objects": [], "total": 0}, "parity": None, "roles": [], "calculation_groups": [],
            "roles_dropped": [], "calculation_groups_dropped": [], "warnings": []}


def _not_run(target: str, status: str, result: str, reason: str, src: _Source) -> dict[str, Any]:
    """A row with no conversion to judge: every role and calculation group of the source is named, unverified."""
    row = _row(target, status, result, reason)
    for kind, out in (("role", "roles"), ("calculation_group", "calculation_groups")):
        row[out] = [{"object": e["id"], "name": mo._short(e["key"][1]), "status": UNVERIFIED,
                     "why": f"not checked for this target: {status}"} for e in src.flagged(kind)]
    _warnings(row, [])
    return row


def _fidelity(basis: str, counts: dict[str, int], status: str = "complete") -> dict[str, Any]:
    return {"basis": basis, "scale": "fidelity classes", "status": status,
            "counts": {mo.CLASSES.get(c, c): n for c, n in counts.items()}}


def _derived(report: dict[str, Any], why: str) -> dict[str, Any]:
    return {"basis": BASES[3], "scale": "parity results", "status": "derived", "note": mo._short(why),
            "counts": dict(report["counts"])}


class _Listing:
    """Source objects in input order, each once by structural key, at most ``MAX_DROPPED`` shown."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def add(self, key: str, obj: str, kind: str, why: str) -> None:
        if key not in self.items:
            self.items[key] = {"object": obj, "kind": kind, "why": mo._short(why)}

    def shown(self) -> dict[str, Any]:
        return {"objects": list(self.items.values())[:MAX_DROPPED], "total": len(self.items)}


def _fate_of(parity_row: dict[str, Any] | None, key: str, ext_keys: set[str]) -> str:
    """What the target does to one source object, from its parity row (``ext_keys``: kept in a POWER_BI extension
    the target carries without reading)."""
    if parity_row is None or parity_row["result"] == sp.TEST_FAILED:
        return UNVERIFIED
    if parity_row["fidelity"]["left"] == _UNSUPPORTED_CLASS:  # the source side couldn't be read through the converter
        return UNVERIFIED
    if parity_row["ids"]["right"] is None or (parity_row["result"] == sp.UNSUPPORTED and
                                              "expression_result" not in parity_row):
        return EXTENSION_ONLY if key in ext_keys else DROPPED
    if parity_row["fidelity"]["right"] == _EXTENSION:
        return EXTENSION_ONLY
    if parity_row["result"] == sp.DIFFERENT:
        return CHANGED
    return KEPT


_WHY = {
    KEPT: "kept in {label}",
    EXTENSION_ONLY: "kept only in the POWER_BI extension: {label} carries it but doesn't apply it",
    CHANGED: "reaches {label} changed; see the parity report",
    DROPPED: "{label} has no place for it, so it is left out",
    UNVERIFIED: "couldn't be checked for {label}",
}


def _judge(row: dict[str, Any], src: _Source, report: dict[str, Any], ext_keys: set[str],
           flagged: dict[str, str]) -> None:
    """Dropped and extension-only objects, and the fate of every role and calculation group, from the parity rows
    (and ``flagged``, a fidelity report's class for a role or calculation group by name, when there is one)."""
    by_left = {o["ids"]["left"]: o for o in report["objects"] if o["ids"]["left"] is not None}
    dropped, extension = _Listing(), _Listing()
    unread = 0
    for lid, o in by_left.items():
        key = src.ids.get(lid)
        if key is None or o["kind"] == "model":
            continue
        canon = mo._canon(key)
        fate = _fate_of(o, canon, ext_keys)
        if fate == DROPPED:
            dropped.add(canon, lid, o["kind"], _WHY[DROPPED].format(label=row["label"]))
        elif fate == EXTENSION_ONLY:
            extension.add(canon, lid, o["kind"], _WHY[EXTENSION_ONLY].format(label=row["label"]))
        elif fate == UNVERIFIED and o["fidelity"]["left"] == _UNSUPPORTED_CLASS:
            unread += 1
    if unread:
        row["notes"].append(f"{unread} object(s) of the source couldn't be read through the converter, so this "
                            "target isn't judged on them (see the parity report)")
    row["_dropped"], row["_extension"] = dropped, extension
    for kind, out in (("role", "roles"), ("calculation_group", "calculation_groups")):
        items = []
        for e in src.flagged(kind):
            fate = _fate_of(by_left.get(e["id"]), mo._canon(e["key"]), ext_keys)
            if e["id"] in flagged:
                fate = min(fate, flagged[e["id"]], key=_WORST_FATE.index)
            items.append({"object": e["id"], "name": mo._short(e["key"][1]), "status": fate,
                          "why": _WHY[fate].format(label=row["label"])})
        row[out] = items


def _warnings(row: dict[str, Any], extra: list[dict[str, Any]]) -> None:
    out: list[dict[str, Any]] = []
    label = row["label"]
    for r in row["roles"]:
        if r["status"] == DROPPED:
            msg = (f"This security role is dropped in {label}: the result has no row-level security for it. Anyone "
                   "who can read the result sees the data this role restricts. Set up the same restriction there "
                   "before sharing it.")
        elif r["status"] == EXTENSION_ONLY:
            msg = (f"This security role is kept only in the POWER_BI extension: {label} doesn't apply it, and neither "
                   "does any tool other than Power BI that reads the file.")
        elif r["status"] == CHANGED:
            msg = f"This security role reaches {label} changed. Check its filters, permission and members by hand."
        else:
            continue
        out.append({"object": r["object"], "severity": "security", "prominent": True, "message": msg})
    for g in row["calculation_groups"]:
        if g["status"] in (DROPPED, EXTENSION_ONLY, CHANGED):
            out.append({"object": g["object"], "severity": "warning", "prominent": True,
                        "message": f"This calculation group is {g['status']} in {label}."})
    out += extra
    out.sort(key=lambda w: w["severity"] != "security")  # security first, in order
    row["warnings"] = out
    row["roles_dropped"] = [r["name"] for r in row["roles"] if r["status"] == DROPPED]
    row["calculation_groups_dropped"] = [g["name"] for g in row["calculation_groups"] if g["status"] == DROPPED]


def _finish(row: dict[str, Any]) -> None:
    dropped, extension = row.pop("_dropped", None), row.pop("_extension", None)
    if dropped is not None:
        row["dropped"] = dropped.shown()
    if extension is not None:
        row["extension_only"] = extension.shown()
    _warnings(row, [])


def _compared(row: dict[str, Any], src: _Source, target_value: Any, target_kind: str | None, ext_keys: set[str],
              flagged: dict[str, str]) -> dict[str, Any]:
    """The source against what a conversion gave: the full L2 report, its summary, and what it says was lost."""
    report = sp.parity_l2(src.value, target_value, left_kind=src.kind, right_kind=target_kind)
    row["parity"] = {"level": report["level"], "summary": report["summary"], "counts": dict(report["counts"]),
                     "report": report}
    row["result"] = report["summary"]
    if report["summary"] == sp.TEST_FAILED:
        row["reason"] = mo._short(report["error"])
    _judge(row, src, report, ext_keys, flagged)
    return report


def _from_fidelity(row: dict[str, Any], src: _Source, fid: dict[str, Any], basis: str) -> None:
    """Objects a fidelity-classed report (converter or view) lists as lost, named as the source names them."""
    dropped: _Listing = row["_dropped"]
    for o in fid.get("objects") or []:
        if o.get("class") != "unsupported" or o.get("kind") in ("model", "custom_extensions"):
            continue
        key = o.get("key")
        if isinstance(key, list):
            canon = mo._canon(key)
            name = src.by_key.get(canon, str(o["object"]))
        else:
            if basis == BASES[1]:  # a construct the view leaves out (a key, an extension): not a source object
                continue
            name = str(o["object"])
            canon = mo._canon(src.ids[name]) if name in src.ids else mo._canon(("object", name))
        if canon in row["_extension"].items:
            continue
        why = str(o.get("reason") or o.get("remediation") or _WHY[DROPPED].format(label=row["label"]))
        dropped.add(canon, mo._short(name), str(o.get("kind", "")), why)


def _flagged(fid: dict[str, Any]) -> dict[str, str]:
    out = {}
    for kind in ("roles", "calculation_groups"):
        for f in (fid.get("flagged") or {}).get(kind) or []:
            if isinstance(f, dict) and f.get("class") in _CLASS_FATE:
                out[str(f["object"])] = _CLASS_FATE[f["class"]]
    return out


def _fidelity_basis(row: dict[str, Any], fid: dict[str, Any], basis: str) -> None:
    if fid.get("status") == "complete":
        row["fidelity"] = _fidelity(basis, fid["counts"])
    elif row["parity"] is not None:
        row["fidelity"] = _derived(row["parity"]["report"], str(fid.get("error") or "the report couldn't be built"))


# ---- each target

def _view_row(src: _Source) -> dict[str, Any]:
    from . import semantic_view

    row = _row("fabric-semantic-view", CONVERTED, CEILING)
    view = semantic_view.semantic_view(src.value, view_name=VIEW_NAME)
    report = _compared(row, src, view["files"], "semantic_view", set(), {})
    vr = view["report"]
    row["fidelity"] = _fidelity(BASES[1], vr["counts"])
    _from_fidelity(row, src, vr, BASES[1])
    kept_unsupported = sum(1 for o in report["objects"] if o["kind"] == "measure" and o["ids"]["right"] is not None
                           and o.get("expression_result") == sp.UNSUPPORTED)
    if kept_unsupported:
        row["notes"].append(f"{kept_unsupported} measure(s) the view keeps show Unsupported at L2. That is a limit of "
                            "the check, not a loss: a view has no dataset fields (Fabric reads the columns from the "
                            "lakehouse), so L2 can't resolve the columns its SQL names.")
    row["notes"].append(f"The view is checked as files only (status {vr['validation'].get('status')}); KratosForge "
                        "doesn't publish it, and Fabric hasn't read it.")
    return row


def _snowflake_row(src: _Source, doc: dict[str, Any], ext_keys: set[str]) -> dict[str, Any]:
    from . import ossie_snowflake

    row = _row("snowflake-ossie", CONVERTED, CEILING)
    out = ossie_snowflake.to_snowflake(doc)
    sf = mo.yaml.safe_load(out["yaml"])
    model = sf["semantic_model"][0]
    carried = ext_keys if mo._has_stash(model) else set()
    _compared(row, src, sf, "ossie", carried, {})
    rep = out["report"]
    row["fidelity"] = _fidelity(BASES[2], rep["counts"], str(rep.get("status", "")))
    dropped: _Listing = row["_dropped"]
    for s in rep["skipped_by_snowflake"]:
        key = _snowflake_key(model, str(s["path"]))
        canon = mo._canon(key) if key is not None else mo._canon(("object", s["object"]))
        name = src.by_key.get(canon, mo._short(s["object"]))
        dropped.add(canon, name, "measure" if s["kind"] == "metric" else "column",
                    "Snowflake skips it silently: it has no SNOWFLAKE or ANSI_SQL expression (it stays in the file)")
    row["notes"].append("Made from the Ossie 0.2.0.dev0 export. The file is Unverified: no 0.1.1 schema is pinned and "
                        "Snowflake hasn't read it (OSSIE-010b).")
    return row


def _snowflake_key(model: dict[str, Any], path: str) -> tuple[Any, ...] | None:
    """The structural key of the field or metric a Snowflake report row's path points at."""
    m = _SF_PATH.match(path)
    if m is None:
        return None
    ds, what, i = m.groups()
    owner: Any = model
    name = None
    if ds is not None:
        datasets = model.get("datasets")
        owner = datasets[int(ds)] if isinstance(datasets, list) and int(ds) < len(datasets) else None
        name = owner.get("name") if isinstance(owner, dict) else None
    items = owner.get(what) if isinstance(owner, dict) else None
    obj = items[int(i)] if isinstance(items, list) and int(i) < len(items) else None
    if not isinstance(obj, dict):
        return None
    if what == "fields":
        return ("column", name, obj.get("name"))
    return ("measure", mo._stash_data(obj).get("name", obj.get("name")))


def _source_row(target: str, src: _Source) -> dict[str, Any]:
    row = _row(target, SOURCE, CEILING, "this is the definition itself")
    row["notes"].append("Nothing was converted, and no expression was run, so the best result still applies.")
    for kind, out in (("role", "roles"), ("calculation_group", "calculation_groups")):
        items = []
        for e in src.flagged(kind):
            fate = _CLASS_FATE.get(e["cls"] or "fully_preserved", KEPT)
            fate = UNVERIFIED if fate in (DROPPED, CHANGED) else fate
            items.append({"object": e["id"], "name": mo._short(e["key"][1]), "status": fate,
                          "why": "in the source " + ("only in its POWER_BI extension" if fate == EXTENSION_ONLY
                                                     else "as the converter read it")})
        row[out] = items
    _warnings(row, [])
    return row


def _attempt(target: str, build: Any, src: _Source) -> dict[str, Any]:
    """``build()``'s row, or a refused (ValueError: Unsupported) or failed (Test failed) row with the reason."""
    try:
        row: dict[str, Any] = build()
    except ValueError as exc:  # narrow: the converter, emitter or exporter refusing the input by a rule
        return _not_run(target, REFUSED, sp.UNSUPPORTED, mo._short(exc), src)
    except _RUN_ERRORS as exc:  # narrow: what a conversion trips on; the reason is the row's result
        return _not_run(target, FAILED, sp.TEST_FAILED, f"{type(exc).__name__}: {mo._short(exc)}", src)
    _finish(row)
    return row


# ---- the matrix

def compatibility(definition: Any, kind: str | None = None) -> dict[str, Any]:
    """The compatibility matrix of ``definition`` (module docstring). ``kind`` is ``tmsl``, ``tmdl`` or ``ossie``
    (detected when None). Never raises for bad input: it is a Test failed row for every target."""
    src = _Source()
    try:
        src.read(definition, kind)
    except sp._READ_ERRORS as exc:  # narrow: what a hostile or wrong input raises; the reason is every row's result
        src.error = f"{type(exc).__name__}: {mo._short(exc)}"
        reason = f"the definition couldn't be read: {src.error}"
        return _matrix(src, [_not_run(t, FAILED, sp.TEST_FAILED, reason, src) for t in TARGETS])
    own = _source_target(src)
    if src.side.unsupported or not _convertible(src):
        why = src.side.unsupported or _not_convertible(src)
        return _matrix(src, [_source_row(t, src) if t == own else _not_run(t, REFUSED, sp.UNSUPPORTED, why, src)
                             for t in TARGETS])
    rows: dict[str, dict[str, Any]] = {}
    doc: dict[str, Any] | None = None
    ossie_failure: dict[str, Any] | None = None
    ext_keys: set[str] = set()
    if own == "power-bi-model":
        rows[own] = _source_row(own, src)

        def ossie() -> dict[str, Any]:
            nonlocal doc
            from . import tmdl

            bim, read_report = (tmdl.to_bim(src.value) if src.kind == "tmdl" else (src.value, None))
            out = mo.export(bim, translate_dax=True, read_report=read_report)
            doc = mo.yaml.safe_load(out["yaml"])
            row = _row("ossie-0.2", CONVERTED, CEILING)
            _compared(row, src, doc, "ossie", set(), _flagged(out["fidelity"]))
            _fidelity_basis(row, out["fidelity"], BASES[0])
            _from_fidelity(row, src, out["fidelity"], BASES[0])
            return row

        rows["ossie-0.2"] = _attempt("ossie-0.2", ossie, src)
        ossie_report = rows["ossie-0.2"]["parity"]
        if doc is None:
            ossie_failure = rows["ossie-0.2"]
        elif ossie_report is not None:
            ext_keys = {mo._canon(src.ids[o["ids"]["left"]]) for o in ossie_report["report"]["objects"]
                        if o["fidelity"]["right"] == _EXTENSION and o["ids"]["left"] in src.ids}
    else:  # an Ossie 0.2.0.dev0 source
        doc = src.value
        rows["ossie-0.2"] = _source_row("ossie-0.2", src)
        ext_keys = {mo._canon(e["key"]) for e in src.side.entries if e["cls"] == "preserved_through_extensions"}

        def power_bi() -> dict[str, Any]:
            bim, conv = mo.to_tmsl(src.value)
            fid = mo.safe_fidelity(src.value, bim, conv)
            row = _row("power-bi-model", CONVERTED, CEILING)
            _compared(row, src, bim, "tmsl", set(), _flagged(fid))
            _fidelity_basis(row, fid, BASES[0])
            _from_fidelity(row, src, fid, BASES[0])
            return row

        rows["power-bi-model"] = _attempt("power-bi-model", power_bi, src)
    rows["fabric-semantic-view"] = _attempt("fabric-semantic-view", lambda: _view_row(src), src)
    if doc is None:
        # an explicit check, not an assert (python -O drops those): the export's row always explains a missing doc
        failure = ossie_failure or rows.get("ossie-0.2") or {"result": sp.TEST_FAILED, "status": FAILED,
                                                             "reason": "it gave no model"}
        rows["snowflake-ossie"] = _not_run("snowflake-ossie", NOT_AVAILABLE, failure["result"],
                                           f"made from the Ossie 0.2.0.dev0 export, which is {failure['status']}: "
                                           f"{failure['reason']}", src)
    else:
        sf_doc = doc
        rows["snowflake-ossie"] = _attempt("snowflake-ossie", lambda: _snowflake_row(src, sf_doc, ext_keys), src)
    return _matrix(src, [rows[t] for t in TARGETS])


def _convertible(src: _Source) -> bool:
    from . import ossie_profiles

    return src.kind in ("tmsl", "tmdl") or bool(ossie_profiles.PROFILES[src.side.profile]["convertible"])


def _not_convertible(src: _Source) -> str:
    try:
        return mo.not_convertible(src.value) or "the converter can't take this definition"
    except ValueError as exc:  # narrow: UnknownProfile; read() already detected the profile, so not expected
        return mo._short(exc)


def _matrix(src: _Source, rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if row["result"] not in sp.L1_RESULTS:  # never reported: nothing here can claim equivalence
            raise AssertionError(f"The compatibility matrix can't report better than {CEILING!r}")
    results = dict.fromkeys(sp.RESULTS, 0)
    statuses = dict.fromkeys(STATUSES, 0)
    for row in rows:
        results[row["result"]] += 1
        statuses[row["status"]] += 1
    judged = [r["result"] for r in rows if r["status"] != SOURCE] or [r["result"] for r in rows]
    return {
        "definition": src.info(),
        "targets": rows,
        "columns": [dict(c) for c in COLUMNS],
        "summary": {"result": min(judged, key=sp._RANK.__getitem__), "results": results, "statuses": statuses,
                    "roles_dropped": sorted({n for r in rows for n in r["roles_dropped"]}),
                    "calculation_groups_dropped": sorted({n for r in rows for n in r["calculation_groups_dropped"]}),
                    "security_warnings": sum(1 for r in rows for w in r["warnings"] if w["severity"] == "security")},
        "security": {"roles": [mo._short(e["key"][1]) for e in src.flagged("role")],
                     "calculation_groups": [mo._short(e["key"][1]) for e in src.flagged("calculation_group")]},
        "not_checked": [dict(n) for n in sp.NOT_CHECKED_L2],
        "ceiling": CEILING,
    }


def redacted(matrix: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``matrix`` safe outside the local pane: each parity report through ``semantic_parity.redacted``
    (no role member or filter text, expressions as a length and hash), and the free text on a dropped or changed
    role withheld (a length and hash). The matrix itself is unchanged."""
    out = copy.deepcopy(matrix)
    for row in out.get("targets") or []:
        parity = row.get("parity")
        if isinstance(parity, dict) and isinstance(parity.get("report"), dict):
            parity["report"] = sp.redacted(parity["report"])
        for r in row.get("roles") or []:
            if r.get("status") in (DROPPED, CHANGED) and isinstance(r.get("why"), str):
                r["why"] = sp._fingerprint(r["why"])
                r["withheld"] = True
        for d in (row.get("dropped") or {}).get("objects") or []:
            if d.get("kind") == "role" and isinstance(d.get("why"), str):
                d["why"] = sp._fingerprint(d["why"])
                d["withheld"] = True
    out["redacted"] = True
    return out


__all__ = ["BASES", "CEILING", "COLUMNS", "FATES", "MAX_DROPPED", "STATUSES", "TARGETS", "compatibility", "redacted"]
