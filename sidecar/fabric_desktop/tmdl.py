"""Read TMDL into TMSL ``model.bim`` on this PC, with no Fabric call (docs/semantic-model-ossie.md).

TMDL is the text format Fabric and Power BI projects keep a semantic model in: one file per table
under ``definition/``, plus ``database.tmdl``, ``model.tmdl``, ``relationships.tmdl``,
``expressions.tmdl`` and ``roles/``. Microsoft reads it with the Tabular Object Model libraries,
which KratosForge doesn't ship, so this reader covers what Apache Ossie export needs: tables,
columns, measures, hierarchies, partitions, relationships, expressions, roles, perspectives,
calculation groups, annotations and descriptions. Anything it doesn't read (cultures and their
translations, for one) is listed in the report, never dropped silently.

The layout rules it relies on: an object is ``<type> <name>`` (``= <default>`` optional), its
properties are one level deeper, and a multi-line expression is one level deeper than the
properties it belongs with. ``///`` lines describe the object after them. Indents are tabs (four
spaces count as one).
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

MAX_CHARS = 20_000_000

# Object keyword: (collection in the parent, property the "= ..." default goes in).
OBJECTS: dict[str, tuple[str, str | None]] = {
    "table": ("tables", None),
    "column": ("columns", "expression"),
    "measure": ("measures", "expression"),
    "hierarchy": ("hierarchies", None),
    "level": ("levels", None),
    "partition": ("partitions", "_source_type"),
    "relationship": ("relationships", None),
    "expression": ("expressions", "expression"),
    "role": ("roles", None),
    "tablePermission": ("tablePermissions", "filterExpression"),
    "columnPermission": ("columnPermissions", "metadataPermission"),
    "member": ("members", None),
    "perspective": ("perspectives", None),
    "perspectiveTable": ("tables", None),
    "perspectiveColumn": ("columns", None),
    "perspectiveMeasure": ("measures", None),
    "perspectiveHierarchy": ("hierarchies", None),
    "calculationItem": ("calculationItems", "expression"),
    "annotation": ("annotations", "value"),
    "extendedProperty": ("extendedProperties", "value"),
    "dataSource": ("dataSources", None),
    "queryGroup": ("queryGroups", None),
    "variation": ("variations", None),
    "model": ("", None),
    "database": ("", None),
}
# Objects with no name that may carry a default expression and properties.
SINGLE = {"formatStringDefinition": "expression", "detailRowsDefinition": "expression"}
SKIPPED = {"culture": "translations and linguistic metadata"}
INT_KEYS = {"compatibilityLevel", "ordinal", "precedence"}

_NAME = r"(?:'(?:[^']|'')*'|[^\s=']+)"
_OBJECT = re.compile(rf"^(\w+)\s+({_NAME})\s*(?:=\s*(.*))?$")
_PROP = re.compile(r"^(\w+)\s*:\s*(.*)$")
_EXPR = re.compile(r"^(\w+)\s*=\s*(.*)$")
_WORD = re.compile(r"^(\w+)$")


def _indent(line: str) -> int:
    n = 0
    spaces = 0
    for ch in line:
        if ch == "\t":
            n += 1
        elif ch == " ":
            spaces += 1
        else:
            break
    return n + spaces // 4


def _strip_indent(line: str, levels: int) -> str:
    """``line`` without its first ``levels`` levels of indent (tabs, or four spaces each)."""
    i = 0
    while levels > 0 and i < len(line):
        if line[i] == "\t":
            i += 1
        elif line.startswith("    ", i):
            i += 4
        else:
            break
        levels -= 1
    return line[i:]


def unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and name[0] == name[-1] == "'":
        return name[1:-1].replace("''", "'")
    return name


def column_ref(ref: str) -> tuple[str, str]:
    """``'Sales Table'.Amount`` as (table, column)."""
    m = re.match(rf"^\s*({_NAME})\.({_NAME})\s*$", ref)
    if not m:
        raise ValueError(f"not a column reference: {ref}")
    return unquote(m.group(1)), unquote(m.group(2))


def _value(key: str, text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('""', '"')
    if text in ("true", "false"):
        return text == "true"
    if key in INT_KEYS and re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


class _Reader:
    def __init__(self, path: str, text: str, report: list[dict[str, str]]):
        self.path = path
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.i = 0
        self.report = report

    def _next_level(self) -> int | None:
        """The indent of the next non-blank line, or None at the end."""
        j = self.i
        while j < len(self.lines):
            if self.lines[j].strip():
                return _indent(self.lines[j])
            j += 1
        return None

    def _expression(self, first: str, deeper_than: int) -> str:
        """An expression starting with ``first`` (the text after ``=``) and going on in the lines that
        are indented more than ``deeper_than``, or fenced in triple backticks."""
        if first.strip().startswith("```"):
            body: list[str] = []
            rest = first.strip()[3:]
            if rest.rstrip().endswith("```"):
                return rest.rstrip()[:-3]
            if rest.strip():
                body.append(rest)
            while self.i < len(self.lines) and self.lines[self.i].strip() != "```":
                body.append(self.lines[self.i])
                self.i += 1
            self.i += 1  # the closing fence
            pad = min((_indent(b) for b in body if b.strip()), default=0)
            return "\n".join(_strip_indent(b, pad) for b in body).strip("\n")
        body = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() and _indent(line) <= deeper_than:
                break
            body.append(line.rstrip())
            self.i += 1
        while body and not body[-1].strip():
            body.pop()
        pad = min((_indent(b) for b in body if b.strip()), default=0)  # deeper than needed is fine too
        out = ([first.strip()] if first.strip() else []) + [_strip_indent(b, pad) for b in body]
        return "\n".join(out)

    def _skip_block(self, level: int) -> None:
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() and _indent(line) <= level:
                return
            self.i += 1

    def block(self, level: int, into: dict[str, Any]) -> None:
        """Read the properties and child objects one level deeper than ``level`` into ``into``."""
        described: list[str] = []
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            text = raw.strip()
            if not text:
                self.i += 1
                continue
            here = _indent(raw)
            if here <= level:
                return
            self.i += 1
            where = f"{self.path}:{self.i}"
            if text.startswith("///"):
                described.append(text[3:].strip())
                continue
            description, described = "\n".join(described), []
            word = text.split(None, 1)[0]
            if word == "ref":
                continue  # the order of tables in model.tmdl; the order here comes from the files
            if word in SKIPPED:
                self._skip_block(here)
                self.report.append({"object": f"{word} {text.split(None, 1)[1] if ' ' in text else ''}".strip(),
                                    "kind": "lost", "message": f"{SKIPPED[word]} aren't read from TMDL on this PC ({where})"})
                continue
            m = _OBJECT.match(text)
            if m and m.group(1) in OBJECTS:
                self._object(m.group(1), unquote(m.group(2)), m.group(3), here, into, description)
                continue
            m = _EXPR.match(text)
            if m and m.group(1) in SINGLE:
                obj: dict[str, Any] = {SINGLE[m.group(1)]: self._expression(m.group(2), here + 1)}
                self.block(here, obj)
                into[m.group(1)] = obj
                continue
            if m:
                key, value = m.group(1), self._expression(m.group(2), here)
                if key == "changedProperty":
                    into.setdefault("changedProperties", []).append({"property": value})
                else:
                    into[key] = value
                continue
            m = _PROP.match(text)
            if m:
                into[m.group(1)] = _value(m.group(1), m.group(2))
                continue
            m = _WORD.match(text)
            if m:
                nxt = self._next_level()
                if nxt is not None and nxt > here:
                    nested: dict[str, Any] = {}
                    self.block(here, nested)
                    into[m.group(1)] = nested
                else:
                    into[m.group(1)] = True
                continue
            self._skip_block(here)
            self.report.append({"object": text[:80], "kind": "lost", "message": f"not understood, left out ({where})"})

    def _object(self, kind: str, name: str, default: str | None, level: int, parent: dict[str, Any],
                description: str) -> None:
        collection, default_key = OBJECTS[kind]
        obj: dict[str, Any] = {"name": name}
        if description:
            obj["description"] = description
        if default is not None and default_key:
            obj[default_key] = self._expression(default, level + 1)
        self.block(level, obj)
        if kind in ("model", "database"):
            parent.setdefault(kind, {}).update(obj)
            return
        parent.setdefault(collection, []).append(_shape(kind, obj))


def _shape(kind: str, obj: dict[str, Any]) -> dict[str, Any]:
    """TMDL's shorthand as TMSL properties."""
    if kind == "column" and obj.get("expression"):
        obj.setdefault("type", "calculated")
    elif kind == "partition":
        src_type = str(obj.pop("_source_type", "") or "").strip()
        src = obj.pop("source", None)
        source: dict[str, Any] = {"type": src_type} if src_type else {}
        if isinstance(src, dict):
            source.update(src)
        elif src is not None:
            source["expression"] = src
        obj["source"] = source
    elif kind == "relationship":
        for end in ("from", "to"):
            ref = obj.get(f"{end}Column")
            if isinstance(ref, str) and "." in ref:
                obj[f"{end}Table"], obj[f"{end}Column"] = column_ref(ref)
    elif kind == "member":
        obj["memberName"] = obj.pop("name")
    elif kind == "expression":
        obj.setdefault("kind", "m")
    elif kind == "hierarchy":
        for n, level in enumerate(obj.get("levels") or []):
            level.setdefault("ordinal", n)
    elif kind == "extendedProperty" and isinstance(obj.get("value"), str):
        try:
            obj["value"] = json.loads(obj["value"])
            obj.setdefault("type", "json")
        except ValueError:
            pass
    for flag in [k for k, v in obj.items() if k.startswith("_")]:
        obj.pop(flag)
    return obj


def to_bim(files: dict[str, str], name: str = "") -> tuple[dict[str, Any], list[dict[str, str]]]:
    """A model's TMDL files (path -> text, paths as in a ``.SemanticModel`` folder) as a TMSL
    ``model.bim`` document, with the report of anything left out. ``name`` is the model's name
    (TMDL calls every model "Model"; the folder or item name is the real one)."""
    if sum(len(t) for t in files.values()) > MAX_CHARS:
        raise ValueError("The model's TMDL is larger than 20 MB, so it isn't read on this PC.")
    report: list[dict[str, str]] = []
    root: dict[str, Any] = {}
    model: dict[str, Any] = {}
    ordered = sorted((p for p in files if p.endswith(".tmdl")), key=_file_order)
    if not ordered:
        raise ValueError("There are no .tmdl files in this folder; it should hold definition/model.tmdl")
    for path in ordered:
        top: dict[str, Any] = {}
        _Reader(path, files[path], report).block(-1, top)
        db = top.pop("database", None)
        if isinstance(db, dict):
            root.update(db)
        model.update(top.pop("model", {}))
        for key, value in top.items():
            if isinstance(value, list):
                model.setdefault(key, []).extend(value)
            else:
                model[key] = value
    model.pop("name", None)
    bim = {"name": name or "Model", **{k: v for k, v in root.items() if k != "name"}, "model": model}
    bim.setdefault("compatibilityLevel", 1604)
    return bim, report


def _file_order(path: str) -> tuple[int, str]:
    """database, model, expressions and data sources first, then tables, relationships, the rest."""
    name = PurePosixPath(path).name
    first = ("database.tmdl", "model.tmdl", "expressions.tmdl", "dataSources.tmdl")
    if name in first:
        return first.index(name), path
    if "/tables/" in f"/{path}":
        return 10, path
    if name == "relationships.tmdl":
        return 20, path
    return 30, path
