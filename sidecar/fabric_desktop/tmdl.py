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
    """``'Sales Table'.Amount`` as (table, column). Read by hand, not by a regular expression: a long dotted
    value that doesn't match would make one backtrack for hours."""
    ref = ref.strip()
    if len(ref) > 1024:
        raise ValueError("a column reference is longer than 1,024 characters")
    if ref.startswith("'"):
        end = 1
        while True:
            end = ref.find("'", end)
            if end < 0:
                raise ValueError(f"not a column reference: {ref}")
            if not ref.startswith("''", end):
                break
            end += 2
        table, column = ref[:end + 1], ref[end + 1:]
        if not column.startswith("."):
            raise ValueError(f"not a column reference: {ref}")
        column = column[1:]
    else:
        table, dot, column = ref.partition(".")
        if not dot:
            raise ValueError(f"not a column reference: {ref}")
    if not table.strip() or not column.strip():
        raise ValueError(f"not a column reference: {ref}")
    return unquote(table), unquote(column)


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
        try:
            _Reader(path, files[path], report).block(-1, top)
        except RecursionError:
            raise ValueError(f"{path} is nested too deeply to be a semantic model") from None
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


# ---- writing: TMSL model.bim as TMDL files, on this PC (PB-110)
#
# The inverse of the reader above, written for it: whatever ``from_bim`` writes, ``to_bim`` reads back as the
# same model, and ``from_bim`` checks that by reading its own output. Anything that would come back different
# (or not at all) is reported by name; a name or expression that could break the layout is refused.

MAX_TABLES = 5_000
_BARE = re.compile(r"[\w-]+")
_KEY = re.compile(r"\w+")
_BREAKS = ("\n", "\r")
_FENCE = "```"
# Collections a perspective and its tables hold have keywords of their own.
_PERSPECTIVE_KINDS = {("perspective", "tables"): "perspectiveTable", ("perspectiveTable", "columns"): "perspectiveColumn",
                      ("perspectiveTable", "measures"): "perspectiveMeasure",
                      ("perspectiveTable", "hierarchies"): "perspectiveHierarchy"}
_KINDS = {coll: kind for kind, (coll, _) in OBJECTS.items() if coll and not kind.startswith("perspective")}
_KINDS["perspectives"] = "perspective"
_EXPRESSION_KEYS = {"expression", "filterExpression", "query", "description"}
# Model collections that get files of their own, as Fabric and Power BI projects lay them out.
_FILES = {"tables": "tables", "roles": "roles", "perspectives": "perspectives"}
_ONE_FILE = {"relationships": "relationships.tmdl", "expressions": "expressions.tmdl", "dataSources": "dataSources.tmdl"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$", *(f"COM{i}" for i in "123456789¹²³"),
             *(f"LPT{i}" for i in "123456789¹²³")}


class _Unwritable(ValueError):
    """Something in the model that can't be written as TMDL without breaking the layout."""


def quote(name: str) -> str:
    """A TMDL object name: bare when it is only letters, digits, ``_`` and ``-``, otherwise in single quotes."""
    if any(c in name for c in _BREAKS):
        raise _Unwritable("its name has a line break")
    return name if _BARE.fullmatch(name) else "'" + name.replace("'", "''") + "'"


def _scalar(value: Any) -> str:
    """A property value the reader's ``_value`` gives back as written: quoted when it would otherwise read
    as something else (a boolean, a number, quotes, edge spaces, nothing)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if (not text or text != text.strip() or text in ("true", "false") or re.fullmatch(r"-?\d+", text)
            or (text.startswith('"') and text.endswith('"'))):
        return '"' + text.replace('"', '""') + '"'
    return text


def _joined(value: Any) -> Any:
    """TMSL may write a long expression as a list of lines."""
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return "\n".join(value)
    return value


def _expression(text: str, level: int) -> list[str]:
    """What follows ``=``: the expression inline when it is one plain line, otherwise fenced in triple
    backticks one level deeper than ``level``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in text and text and text == text.strip() and not text.startswith(_FENCE):
        return [" " + text]
    lines = text.split("\n")
    if any(line.strip() == _FENCE for line in lines):
        raise _Unwritable("its expression has a line of three backticks")
    pad = "\t" * (level + 1)
    return [" " + _FENCE, *(pad + line if line else "" for line in lines), pad + _FENCE]


class _Writer:
    def __init__(self, report: list[dict[str, str]]):
        self.report = report

    def lost(self, where: str, message: str) -> None:
        self.report.append({"object": where, "kind": "lost", "message": message})

    def body(self, parent_kind: str, obj: dict[str, Any], level: int, where: str, out: list[str]) -> None:
        """``obj``'s properties and children, one level deeper than ``level``."""
        tabs = "\t" * (level + 1)
        scalars, exprs, nested, children = [], [], [], []
        for key, value in obj.items():
            value = _joined(value)
            if not _KEY.fullmatch(key):
                self.lost(f"{where} property {key[:80]}", "this property name can't be written as TMDL")
            elif value is None:
                continue
            elif isinstance(value, str) and (key in _EXPRESSION_KEYS or any(c in value for c in _BREAKS)):
                exprs.append((key, value))
            elif isinstance(value, (str, bool, int, float)):
                scalars.append((key, value))
            elif isinstance(value, dict):
                nested.append((key, value))
            elif isinstance(value, list):
                children.append((key, value))
            else:
                self.lost(f"{where} property {key}", "this value can't be written as TMDL")
        for key, value in scalars:
            if value is True and key not in OBJECTS and key not in SINGLE and key != "ref":
                out.append(f"{tabs}{key}")
            else:
                out.append(f"{tabs}{key}: {_scalar(value)}")
        for key, value in exprs:
            try:
                lines = _expression(value, level + 1)
            except _Unwritable as exc:
                raise _Unwritable(f"{where} {key}: {exc}") from None
            out.append(f"{tabs}{key} ={lines[0]}")
            out.extend(lines[1:])
        for key, value in nested:
            if key in SINGLE:
                rest = dict(value)
                expr = _joined(rest.pop(SINGLE[key], ""))
                try:
                    lines = _expression(str(expr), level + 1)
                except _Unwritable as exc:
                    raise _Unwritable(f"{where} {key}: {exc}") from None
                out.append(f"{tabs}{key} ={lines[0]}")
                out.extend(lines[1:])
                self.body(key, rest, level + 1, f"{where} {key}", out)
            elif not value:
                self.lost(f"{where} {key}", "an empty setting isn't written as TMDL")
            else:
                out.append(f"{tabs}{key}")
                self.body(key, value, level + 1, f"{where} {key}", out)
        for key, items in children:
            if key == "changedProperties":
                for item in items:
                    prop = item.get("property") if isinstance(item, dict) else None
                    if isinstance(prop, str) and prop and not any(c in prop for c in _BREAKS) and prop == prop.strip():
                        out.append(f"{tabs}changedProperty = {prop}")
                    else:
                        self.lost(f"{where} changedProperty", "this changed property can't be written as TMDL")
                continue
            kind = _PERSPECTIVE_KINDS.get((parent_kind, key)) or _KINDS.get(key)
            if kind is None:
                self.lost(f"{where} {key}", "TMDL on this PC has no place for this list")
                continue
            for item in items:
                if not isinstance(item, dict):
                    self.lost(f"{where} {key}", "an entry that isn't an object was left out")
                    continue
                self.object(kind, item, level + 1, where, out)

    def object(self, kind: str, obj: dict[str, Any], level: int, parent_where: str, out: list[str]) -> None:
        obj = dict(obj)
        name = obj.pop("memberName" if kind == "member" else "name", None)
        where = f"{parent_where} {kind} {name!r}".strip()
        if not isinstance(name, str) or not name:
            self.lost(f"{parent_where} {kind}".strip(), "an object with no name was left out")
            return
        tabs = "\t" * level
        description = _joined(obj.pop("description", None))
        default = None
        if kind == "column" and obj.get("expression") is not None:
            default = _joined(obj.pop("expression"))
            if obj.get("type") == "calculated":
                obj.pop("type")
        elif kind == "partition":
            source = dict(obj.pop("source", None) or {})
            default = source.pop("type", None)
            expr = _joined(source.get("expression"))
            if set(source) == {"expression"} and isinstance(expr, str):
                obj["source"] = expr
            elif source:
                obj["source"] = source
        elif kind == "expression":
            if obj.get("kind") == "m":
                obj.pop("kind")
            default = _joined(obj.pop("expression", None))
        elif kind == "extendedProperty" and obj.get("type") == "json" and not isinstance(obj.get("value"), str):
            obj.pop("type")
            default = json.dumps(obj.pop("value", None), separators=(",", ":"))
        elif (default_key := OBJECTS[kind][1]) and default_key in obj:
            default = _joined(obj.pop(default_key))
        try:
            head = f"{tabs}{kind} {quote(name)}"
        except _Unwritable as exc:
            raise _Unwritable(f"{where}: {exc}") from None
        if isinstance(description, str) and description:
            lines = description.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if all(line == line.strip() for line in lines):
                out.extend(f"{tabs}/// {line}" if line else f"{tabs}///" for line in lines)
            else:
                obj["description"] = description  # written as a fenced property, keeping its spaces
        if default is not None:
            try:
                lines = _expression(default if isinstance(default, str) else str(default), level)
            except _Unwritable as exc:
                raise _Unwritable(f"{where}: {exc}") from None
            out.append(f"{head} ={lines[0]}")
            out.extend(lines[1:])
        else:
            out.append(head)
        if kind == "relationship":
            for end in ("from", "to"):
                table, column = obj.pop(f"{end}Table", None), obj.get(f"{end}Column")
                if isinstance(table, str) and isinstance(column, str):
                    try:
                        obj[f"{end}Column"] = f"{quote(table)}.{quote(column) if not _BARE.fullmatch(column) else column}"
                    except _Unwritable as exc:
                        raise _Unwritable(f"{where}: {exc}") from None
                elif table is not None:
                    obj[f"{end}Table"] = table
        self.body(kind, obj, level, where, out)


def file_name(name: str, taken: set[str]) -> str:
    """A Windows-safe file name (no extension) for an object called ``name``: never the name used raw, so a
    table called ``..\\x`` or ``CON`` can't leave the folder or name a device. Unique in ``taken`` (case-folded)."""
    safe = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 or c in "\x7f  " else c for c in name)
    safe = safe.strip().rstrip(".").lstrip(".")[:100].rstrip(". ") or "_"
    if safe.split(".")[0].upper().rstrip() in _RESERVED:
        safe = "_" + safe
    candidate, n = safe, 1
    while candidate.casefold() in taken:
        n += 1
        candidate = f"{safe} ({n})"
    taken.add(candidate.casefold())
    return candidate


def from_bim(bim: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, str]]]:
    """A TMSL ``model.bim`` document as TMDL files (path under the ``.SemanticModel`` folder -> text), and the report.

    The files are read back with ``to_bim`` before they are returned: anything that wouldn't come back as it
    went in is in the report by name (``kind`` "lost"), so nothing is dropped silently. Names with line breaks
    and expressions with a line of three backticks would break the layout, so they are refused (ValueError,
    naming the object)."""
    report: list[dict[str, str]] = []
    w = _Writer(report)
    model = dict(bim.get("model") or {})
    tables = model.get("tables") or []
    if len(tables) > MAX_TABLES:
        raise ValueError(f"This model has {len(tables)} tables; TMDL is written on this PC for at most {MAX_TABLES}.")
    files: dict[str, str] = {}

    def emit(path: str, lines: list[str]) -> None:
        files[f"definition/{path}"] = "\n".join(lines) + "\n"

    try:
        root = {k: v for k, v in bim.items() if k not in ("name", "model")}
        lines = ["database"] if root else []
        w.body("database", root, 0, "database", lines)
        if lines:
            emit("database.tmdl", lines)
        for culture in model.pop("cultures", None) or []:
            w.lost(f"culture {culture.get('name') if isinstance(culture, dict) else culture!r}",
                   "translations and linguistic metadata aren't written as TMDL on this PC")
        split = {k: model.pop(k) for k in list(model) if k in _FILES or k in _ONE_FILE}
        lines = ["model Model"]
        w.body("model", model, 0, "model", lines)
        for t in split.get("tables") or []:
            if isinstance(t, dict) and isinstance(t.get("name"), str):
                lines.append(f"ref table {quote(t['name'])}")
        emit("model.tmdl", lines)
        for key, file in _ONE_FILE.items():
            lines = []
            for item in split.get(key) or []:
                if isinstance(item, dict):
                    w.object(_KINDS[key], item, 0, "", lines)
                    lines.append("")
            if lines:
                emit(file, lines[:-1])
        for key, folder in _FILES.items():
            taken: set[str] = set()
            for item in split.get(key) or []:
                if not isinstance(item, dict):
                    w.lost(key, "an entry that isn't an object was left out")
                    continue
                lines = []
                w.object(_KINDS[key], item, 0, "", lines)
                if lines:
                    emit(f"{folder}/{file_name(str(item.get('name') or ''), taken)}.tmdl", lines)
    except _Unwritable as exc:
        raise ValueError(f"This model can't be written as TMDL: {str(exc).strip()}") from None
    if sum(len(t) for t in files.values()) > MAX_CHARS:
        raise ValueError("This model's TMDL would be larger than 20 MB, so it isn't written on this PC.")
    back, read_report = to_bim(files, str(bim.get("name") or ""))
    report += [r for r in read_report if r not in report]
    report += [{"object": where, "kind": "lost", "message": "wouldn't read back from TMDL as it was"}
               for where in differences(bim, back) if not any(where.startswith(r["object"]) for r in report)]
    return files, report


# ---- comparing two models: what a conversion kept

_ORDERLESS = {"tables", "roles", "perspectives"}  # their order comes from file names, not from the model


def canonical(value: Any, key: str = "") -> Any:
    """A model in the form a TMDL round trip keeps: expressions as one string with \\n line ends, numbers and
    text as text (TMDL has no types for them), booleans as booleans."""
    value = _joined(value)
    if isinstance(value, dict):
        return {k: canonical(v, k) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [canonical(v, key) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    return str(value)


def _named(item: Any) -> str | None:
    if isinstance(item, dict):
        name = item.get("name", item.get("memberName"))
        return name if isinstance(name, str) else None
    return None


def differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Where ``after`` differs from ``before`` (two parsed ``model.bim``), each as a readable path such as
    ``table 'Sales' measure 'Net'`` or ``table 'Sales' column 'amount' formatString``. Empty when they match."""
    a, b = canonical(before.get("model") or {}), canonical(after.get("model") or {})
    a.pop("cultures", None)
    out: list[str] = []
    _diff(a, b, "", out, "model")
    level_a, level_b = before.get("compatibilityLevel"), after.get("compatibilityLevel")
    if level_a is not None and str(level_a) != str(level_b):
        out.append("database compatibilityLevel")
    return out


def _diff(a: Any, b: Any, where: str, out: list[str], parent: str) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() | b.keys():
            if k in ("name", "memberName"):
                continue
            if k not in b:
                out.append(f"{where} {k}".strip() + " (left out)")
            elif k not in a:
                out.append(f"{where} {k}".strip() + " (added)")
            else:
                _diff(a[k], b[k], f"{where} {k}".strip() if not isinstance(a[k], list) else where, out, k)
        return
    if isinstance(a, list) and isinstance(b, list) and all(_named(i) for i in a + b):
        kind = _PERSPECTIVE_KINDS.get(("", parent)) or _KINDS.get(parent, parent)
        by_b = {_named(i): i for i in b}
        names_a = [_named(i) for i in a]
        for item in a:
            n = _named(item)
            path = f"{where} {kind} {n!r}".strip()
            if n not in by_b:
                out.append(f"{path} (left out)")
            else:
                _diff(item, by_b[n], path, out, kind)
        for n in by_b:
            if n not in names_a:
                out.append(f"{where} {kind} {n!r} (added)".strip())
        if parent not in _ORDERLESS and [n for n in names_a if n in by_b] != [n for n in by_b if n in names_a]:
            out.append(f"{where} {parent} order".strip())
        return
    if a != b:
        out.append(where or parent)
