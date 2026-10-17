"""What changed between two versions of an entity or a notebook, by meaning rather than by line.

An entity is compared as parsed TOML, so comments, ordering and spacing never show up. Contract
columns, keys, load mode and the list sections (cleaning, derived columns, checks, keys, lookups)
are reported in their own terms: "column email removed", "check returns_are_negative changed
on_fail from quarantine to stop". Anything else is reported by its setting path. Each change says
whether it changes the logic (what the table holds) or only describes it (owner, catalog text).

A notebook is compared cell by cell, by the stable cell id, on source only: outputs, execution
counts and metadata are left out.
"""

from __future__ import annotations

import difflib
import tomllib
from typing import Any

from .project import LOGIC_KEYS, OPERATIONAL_KEYS

# How items of each list section are told apart, so an edit reads as "changed", not "removed and added".
_IDENTITY = {
    "derive": lambda x: x.get("column"),
    "check": lambda x: x.get("name") or "/".join(str(x.get(k)) for k in ("column", "python", "expr") if x.get(k)),
    "keys": lambda x: f"{x.get('table')}:{x.get('business_key')}",
    "lookup": lambda x: x.get("as") or x.get("dim"),
    "clean": lambda x: x.get("column") or ("dedupe" if "dedupe_keep" in x else None),
}
_NOUN = {"derive": "derived column", "check": "check", "keys": "surrogate key", "lookup": "lookup", "clean": "cleaning step"}


def parse(text: str | None) -> dict[str, Any] | None:
    """An entity file's settings, or None for a file that doesn't exist (or doesn't parse)."""
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _change(kind: str, text: str, section: str, **extra: Any) -> dict[str, Any]:
    operational = any(section == s and extra.get("setting") == k for s, k in OPERATIONAL_KEYS)
    return {"kind": kind, "text": text, "logic": section in LOGIC_KEYS and not operational, **extra}


def _fmt(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v).lower() if isinstance(v, bool) else str(v)


def entity_changes(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every change from ``old`` to ``new`` (parsed entity settings; None means absent)."""
    if old is None and new is None:
        return []
    if old is None:
        return [_change("added", "new table", "source")]
    if new is None:
        return [_change("removed", "table removed", "source")]
    out: list[dict[str, Any]] = []
    old, new = dict(old), dict(new)
    oc, nc = dict(old.get("contract") or {}), dict(new.get("contract") or {})
    ocols, ncols = dict(oc.pop("columns", {}) or {}), dict(nc.pop("columns", {}) or {})
    for c in ncols.keys() - ocols.keys():
        out.append(_change("column_added", f"column {c} added ({_fmt(ncols[c])})", "contract", column=c, new=ncols[c]))
    for c in ocols.keys() - ncols.keys():
        out.append(_change("column_removed", f"column {c} removed (was {_fmt(ocols[c])})", "contract",
                           column=c, old=ocols[c]))
    for c in ocols.keys() & ncols.keys():
        if ocols[c] != ncols[c]:
            out.append(_change("column_retyped", f"column {c} changed type from {_fmt(ocols[c])} to {_fmt(ncols[c])}",
                               "contract", column=c, old=ocols[c], new=ncols[c]))
    if oc.get("keys") != nc.get("keys"):
        out.append(_change("keys", f"contract keys changed from {_fmt(oc.get('keys') or [])} to {_fmt(nc.get('keys') or [])}",
                           "contract", old=oc.get("keys"), new=nc.get("keys")))
    oc.pop("keys", None), nc.pop("keys", None)
    out += _settings(oc, nc, "contract")
    for section in _IDENTITY:
        out += _items(section, old.pop(section, None) or [], new.pop(section, None) or [])
    old.pop("contract", None), new.pop("contract", None)
    for section in sorted(old.keys() | new.keys()):
        a, b = old.get(section), new.get(section)
        if isinstance(a, dict) or isinstance(b, dict):
            out += _settings(a if isinstance(a, dict) else {}, b if isinstance(b, dict) else {}, section)
        elif a != b:
            out.append(_setting_change(section, section, a, b))
    order = {c: i for i, c in enumerate({**ocols, **ncols})}
    return sorted(out, key=lambda c: (not c["logic"], c.get("column") is None, order.get(c.get("column"), 0)))


def _setting_change(section: str, path: str, a: Any, b: Any) -> dict[str, Any]:
    setting = path.split(".")[-1]
    if a is None:
        text = f"{path} set to {_fmt(b)}"
    elif b is None:
        text = f"{path} removed (was {_fmt(a)})"
    else:
        text = f"{path} changed from {_fmt(a)} to {_fmt(b)}"
    return _change("setting", text, section, setting=setting, path=path, old=a, new=b)


def _settings(a: dict[str, Any], b: dict[str, Any], section: str, prefix: str = "") -> list[dict[str, Any]]:
    out = []
    base = prefix or section
    for k in sorted(a.keys() | b.keys()):
        x, y = a.get(k), b.get(k)
        if x == y:
            continue
        if isinstance(x, dict) and isinstance(y, dict):
            out += _settings(x, y, section, f"{base}.{k}")
        else:
            out.append(_setting_change(section, f"{base}.{k}", x, y))
    return out


def _items(section: str, a: list[Any], b: list[Any]) -> list[dict[str, Any]]:
    ident, noun = _IDENTITY[section], _NOUN[section]
    a = [x for x in a if isinstance(x, dict)]
    b = [x for x in b if isinstance(x, dict)]
    olds = {ident(x) or f"#{i + 1}": x for i, x in enumerate(a)}
    news = {ident(x) or f"#{i + 1}": x for i, x in enumerate(b)}
    out = []
    for key in news:
        if key not in olds:
            out.append(_change(f"{section}_added", f"{noun} {key} added", section, item=key))
        elif olds[key] != news[key]:
            x, y = olds[key], news[key]
            fields = [f"{k} from {_fmt(x[k])} to {_fmt(y[k])}" if k in x and k in y else
                      f"{k} set to {_fmt(y[k])}" if k in y else f"{k} removed"
                      for k in sorted(x.keys() | y.keys()) if x.get(k) != y.get(k)]
            out.append(_change(f"{section}_changed", f"{noun} {key} changed {', '.join(fields)}", section, item=key))
    for key in olds:
        if key not in news:
            out.append(_change(f"{section}_removed", f"{noun} {key} removed", section, item=key))
    if not out and list(olds) != list(news):
        out.append(_change(f"{section}_reordered", f"{noun}s reordered", section))
    return out


def project_changes(old: dict[str, str | None], new: dict[str, str | None]) -> list[dict[str, Any]]:
    """Per entity, the changes from ``old`` to ``new`` ({name: file text, or None if absent}).
    Entities with no meaningful change are left out; a file that doesn't parse says so."""
    out = []
    for name in sorted(old.keys() | new.keys()):
        a, b = old.get(name), new.get(name)
        pa, pb = parse(a), parse(b)
        if (a is not None and pa is None) or (b is not None and pb is None):
            out.append({"entity": name, "state": "unreadable", "changes": [],
                        "problem": "the file isn't valid TOML, so it can't be compared by meaning"})
            continue
        changes = entity_changes(pa, pb)
        if changes:
            state = "new" if pa is None else "removed" if pb is None else "changed"
            out.append({"entity": name, "state": state, "logic": any(c["logic"] for c in changes), "changes": changes})
    return out


# ---- notebooks


def _cells(nb: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    rows = []
    for i, c in enumerate((nb or {}).get("cells") or []):
        src = c.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        rows.append((str(c.get("id") or f"#{i + 1}"), c.get("cell_type", "code"), src))
    return rows


def notebook_changes(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Cell changes from ``old`` to ``new`` (.ipynb JSON), by cell id, source only."""
    a, b = _cells(old), _cells(new)
    olds = {cid: (kind, src) for cid, kind, src in a}
    news = {cid: (kind, src) for cid, kind, src in b}
    out = []
    pos_old = {cid: i for i, (cid, _, _) in enumerate(a)}
    for i, (cid, kind, src) in enumerate(b):
        if cid not in olds:
            out.append({"cell": cid, "kind": "added", "type": kind, "position": i + 1,
                        "diff": [f"+{line}" for line in src.splitlines()]})
            continue
        okind, osrc = olds[cid]
        if osrc != src or okind != kind:
            diff = list(difflib.unified_diff(osrc.splitlines(), src.splitlines(), lineterm="", n=1))[2:]
            out.append({"cell": cid, "kind": "changed", "type": kind, "position": i + 1, "diff": diff,
                        **({"was": okind} if okind != kind else {})})
    kept = [cid for cid, _, _ in b if cid in olds]
    if kept != sorted(kept, key=lambda c: pos_old[c]):
        out.append({"cell": None, "kind": "reordered", "type": None, "position": None, "diff": []})
    for i, (cid, kind, src) in enumerate(a):
        if cid not in news:
            out.append({"cell": cid, "kind": "removed", "type": kind, "position": i + 1,
                        "diff": [f"-{line}" for line in src.splitlines()]})
    return out
