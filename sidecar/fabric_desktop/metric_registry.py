"""Metric registry, engine slice (OSSIE-008a): every metric of several semantic definitions, and where they conflict.

``metrics(definitions)`` reads each definition as ``semantic_parity`` reads a side (a ``model.bim`` (TMSL), a TMDL
folder's files, an Ossie document in any profile, or a Fabric Semantic View named with ``kinds``) and returns one
row per measure or metric: its source label, table, name, a normalized expression key, the expression's length and
keyed fingerprint, its format string, whether it has a description, and its dialects.

The key is L2's canonical form (``semantic_parity._side_form``), so ``SUM('orders'[amount])`` in DAX and
``SUM(orders.amount)`` in SQL share one key. Where L2 can't read an expression (outside its subset: CALCULATE,
filters, iterators...), the key is the text with case and spacing folded, and the row says so: ``basis`` is
"text only". Either way the key is a keyed fingerprint, never the text.

``conflicts(rows)`` groups the rows by name (trimmed, casefolded). Two rows agree when their keys match or their
folded texts match (every row with an expression keeps a keyed text key too, so identical text is never a conflict
just because L2 read it on one side only), and agreement is transitive. A name with more than one group of agreeing
sources is one conflict, with the groups and the evidence: L2's change lines where both sides have a form, else
"expressions differ (text only)". Rows with different names and the same key are listed apart as possible
duplicates, labelled inferred: a shared form is a hint, not a decision.

Governed versus local: a row is governed only when the caller passes its (source, table, name) in ``governed``.
Nothing is ever inferred governed.

Security: no expression text, no RLS filter text and no role member appears in any row or report; an expression is
its length and keyed fingerprint, and any other text (a format string, an L2 reason) is cut to ``MAX_TEXT``.

Engine only: read-only and local. Nothing is written, fetched or executed; the catalog record comes later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from . import keyed_fingerprint, model_ossie as mo, ossie_compare, semantic_parity as sp

GOVERNED, LOCAL = "governed", "local"
L2, TEXT_ONLY, NO_EXPRESSION = "L2", "text only", "no expression"
TEXT_DIFFERS = "expressions differ (text only)"
INFERRED = "inferred"
MAX_TEXT = 80  # the most of any free text a row or report carries
# Quoted names and strings keep their spacing; any other run of spaces is kept only between two words.
# Quoted names and strings, each to its close or, unclosed, to the end of the text in one step: the possessive
# repeat never gives back, so an unclosed "[" can't make every later "[" rescan the rest (linear, not quadratic).
_TOKEN = re.compile(r"'(?:[^']|'')*+(?:'|\Z)|\[(?:[^\]]|\]\])*+(?:\]|\Z)|\"(?:[^\"]|\"\")*+(?:\"|\Z)|`[^`]*+(?:`|\Z)"
                    r"|\s+|\w+|.", re.DOTALL)


def _cut(value: Any) -> str:
    text = str(value)
    return text if len(text) <= MAX_TEXT else f"{text[:MAX_TEXT - 3]}... ({len(text)} characters)"


def _folded(text: str) -> str:
    """Expression text with case folded and spacing dropped except between two words, for a text-only key."""
    tokens = _TOKEN.findall(text.strip().lstrip("="))
    out: list[str] = []
    for i, tok in enumerate(tokens):
        if tok.isspace():
            before, after = out[-1] if out else "", tokens[i + 1] if i + 1 < len(tokens) else ""
            if before[-1:].isalnum() and after[:1].isalnum():
                out.append(" ")
            continue
        out.append(tok.casefold())
    return "".join(out)


def _key(basis: str, dialect: str | None, normal: str) -> str:
    return keyed_fingerprint.digest(f"metric key\x00{basis}\x00{dialect or ''}\x00{normal}")


def _parsed(value: Any, kind: str | None) -> Any:
    """Text as ossie_compare parses it, unless the kind reads text itself (a TMDL folder isn't text, a view
    reads its own)."""
    if kind is not None and str(kind).lower() not in ("tmsl", "bim", "ossie"):
        return value
    return ossie_compare._parsed(value)


def _native_raw(parsed: Any) -> dict[tuple[str | None, str], dict[str, Any]]:
    """An Ossie model read as written: its metrics by (dataset or None, name)."""
    model = parsed.get("semantic_model", parsed) if isinstance(parsed, dict) else {}
    model = model[0] if isinstance(model, list) and model else model
    out: dict[tuple[str | None, str], dict[str, Any]] = {}
    if not isinstance(model, dict):
        return out
    for d in mo._named_items(model.get("datasets")):
        for m in mo._named_items(d.get("metrics")):
            out.setdefault((d["name"], m["name"]), m)
    for m in mo._named_items(model.get("metrics")):
        out.setdefault((None, m["name"]), m)
    return out


def _format(raw: dict[str, Any]) -> str | None:
    fmt = raw.get("formatString", mo._stash_data(raw).get("formatString"))
    if raw.get("formatStringDefinition") is not None:
        return "dynamic"
    return None if fmt is None else _cut(fmt)


def _read(label: str, value: Any, kind: str | None) -> tuple[sp._Side, Any]:
    side = sp._Side("left")
    try:
        parsed = _parsed(value, kind)
        sp._read(side, parsed, kind)
    except sp._READ_ERRORS as exc:  # narrow: what a hostile or wrong input raises; named by source
        raise ValueError(f"Source {mo._q(_cut(label))} couldn't be read: {type(exc).__name__}: {_cut(exc)}") from None
    if side.unsupported:
        raise ValueError(f"Source {mo._q(_cut(label))}: {side.unsupported}")
    return side, parsed


def _row(label: str, side: sp._Side, entry: dict[str, Any], raw: dict[str, Any], names: frozenset[str],
         governed: frozenset[tuple[str, str | None, str]]) -> dict[str, Any]:
    table = entry["attrs"].get("home")
    name = entry["key"][1]
    dialects = list(((entry.get("expr") or {}).get("dialects") or {}))
    p = sp._side_form(side, entry, names)
    text = p["text"]
    if p["form"] is not None:
        basis, normal, reason = L2, mo._canon(p["form"]), ""
    elif text is not None:
        basis, normal, reason = TEXT_ONLY, _folded(text), _cut(p["reason"])
    else:
        basis, normal, reason = NO_EXPRESSION, "", _cut(p["reason"])
    return {"source": label, "kind": side.kind, "table": table, "name": name,
            "key": _key(basis, p["dialect"] if basis == TEXT_ONLY else None, normal), "basis": basis,
            "l2_form": None if p["form"] is None else _cut(sp._form_text(p["form"])),
            "l2_reason": reason, "dialect": p["dialect"],
            "expression_chars": 0 if text is None else len(text),
            "expression_fingerprint": None if text is None else keyed_fingerprint.fingerprint(text),
            "format_string": _format(raw), "has_description": bool(mo._text(raw.get("description")).strip()),
            "dialects": [_cut(d) for d in dialects],
            "governance": GOVERNED if (label, table, name) in governed else LOCAL,
            "_form": p["form"],
            "_text_key": None if text is None else _key(TEXT_ONLY, p["dialect"], _folded(text))}


def metrics(definitions: Mapping[str, Any], *, kinds: Mapping[str, str] | None = None,
            governed: Iterable[tuple[str, str | None, str]] = ()) -> list[dict[str, Any]]:
    """One row per measure or metric of every definition (source label -> definition), in input order. ``kinds``
    names a source's kind where detection can't (a Semantic View); ``governed`` holds the (source, table, name) of
    governed rows, every other row is local. A definition that can't be read is a ValueError naming its source."""
    governed_set = frozenset((str(s), t, str(n)) for s, t, n in governed)
    rows: list[dict[str, Any]] = []
    for label, value in definitions.items():
        side, parsed = _read(str(label), value, (kinds or {}).get(label))
        names = sp._measure_names(side)
        native = _native_raw(parsed) if side.native_ossie else None
        tmsl = None if side.native_ossie else ossie_compare._Raw(side)
        for e in side.entries:
            if e["kind"] != "measure" or e["cls"] == "unsupported":
                continue
            raw = tmsl.of(e) if tmsl is not None else (native or {}).get((e["attrs"].get("home"), e["key"][1]), {})
            rows.append(_row(str(label), side, e, raw, names, governed_set))
    return rows


def _name(row: dict[str, Any]) -> str:
    return str(row["name"]).strip().casefold()


def _where(row: dict[str, Any]) -> str:
    return _cut(row["source"] if row["table"] is None else f"{row['source']} ({row['table']})")


def _evidence(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """How b's expression differs from a's: L2's change lines with sources for sides, else text only."""
    if a["_form"] is None or b["_form"] is None:
        return [TEXT_DIFFERS]
    lines = sp._form_changes(a["_form"], b["_form"])
    wa, wb = _where(a), _where(b)
    out = []
    for line in lines:
        for old, new in (("on the left side", f"in {wa}"), ("on the right side", f"in {wb}"),
                         ("on the right", f"in {wb}"), ("on the left", f"in {wa}"),
                         ("the left side", wa), ("the right side", wb)):
            line = line.replace(old, new)
        out.append(line)
    return out or [TEXT_DIFFERS]


def _agreeing(group: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The rows of one name in groups that agree (same key, or same text key), transitively: a union-find over the
    rows, groups in first-seen order."""
    parent = list(range(len(group)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[tuple[str, str], int] = {}
    for i, r in enumerate(group):
        for tag in ("key", "_text_key"):
            value = r.get(tag)
            if value is None:
                continue
            j = seen.setdefault((tag, value), i)
            parent[find(i)] = find(j)
    out: dict[int, list[dict[str, Any]]] = {}
    for i, r in enumerate(group):
        out.setdefault(find(i), []).append(r)
    return list(out.values())


def _shown(variant: list[dict[str, Any]]) -> dict[str, Any]:
    """The row that speaks for a group in evidence: one L2 read, if any did."""
    return next((r for r in variant if r["_form"] is not None), variant[0])


def conflicts(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Conflicts (one per name whose keys differ) and possible duplicates (one per key under several names)."""
    rows = list(rows)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_name.setdefault(_name(r), []).append(r)
    found: list[dict[str, Any]] = []
    for group in by_name.values():
        variants = _agreeing(group)
        if len(variants) < 2:
            continue
        first = _shown(variants[0])
        found.append({
            "name": _cut(first["name"]),
            "sources": [_where(r) for r in group],
            "agree": [[_where(r) for r in v] for v in variants],
            "governed": [_where(r) for r in group if r["governance"] == GOVERNED],
            "evidence": [{"between": [_where(first), _where(_shown(v))], "changes": _evidence(first, _shown(v))}
                         for v in variants[1:]]})
    by_key: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["basis"] != NO_EXPRESSION:
            by_key.setdefault(r["key"], []).append(r)
    duplicates = []
    for group in by_key.values():
        names = list(dict.fromkeys(_name(r) for r in group))
        if len(names) > 1:
            duplicates.append({"label": f"possible duplicates ({INFERRED})", "inferred": True,
                               "basis": group[0]["basis"], "names": [_cut(n) for n in names],
                               "sources": [f"{_where(r)}: {_cut(r['name'])}" for r in group]})
    return {"conflicts": found, "possible_duplicates": duplicates,
            "counts": {"metrics": len(rows), "names": len(by_name), "conflicts": len(found),
                       "possible_duplicates": len(duplicates),
                       "governed": sum(r["governance"] == GOVERNED for r in rows)}}


def public(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows without the internal L2 form and text key ``conflicts`` compares, for showing or saving."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
