"""Read TMDL into TMSL ``model.bim`` on this PC, with no Fabric call (docs/semantic-model-ossie.md).

TMDL is the text format Fabric and Power BI projects keep a semantic model in: one file per table
under ``definition/``, plus ``database.tmdl``, ``model.tmdl``, ``relationships.tmdl``,
``expressions.tmdl``, ``roles/`` and ``cultures/``. Microsoft reads it with the Tabular Object Model
libraries, which KratosForge doesn't ship, so this reader covers what Apache Ossie export needs: tables,
columns, measures, hierarchies, partitions, relationships, expressions, roles, perspectives,
calculation groups, cultures (translations and linguistic metadata), annotations and descriptions.
Anything it doesn't read is listed in the report, never dropped silently.

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
    "culture": ("cultures", None),  # the keyword Microsoft's TMDL reference uses
    "cultureInfo": ("cultures", None),  # the keyword Power BI Desktop writes, and this writer too
    "model": ("", None),
    "database": ("", None),
}
# Objects with no name that may carry a default expression and properties.
SINGLE = {"formatStringDefinition": "expression", "detailRowsDefinition": "expression",
          "linguisticMetadata": "content"}
CULTURES = ("culture", "cultureInfo")
# "ref <keyword> <name>" lines in model.tmdl whose order the reader keeps, and the collection each orders.
ORDERED_REFS = {"table": "tables", "culture": "cultures", "cultureInfo": "cultures"}
# Translations: TMDL repeats the model's tree under ``translations`` with plain property names, where TMSL's
# ``translations.model`` tree has ``translated...`` ones. These are the properties a translation has
# (Microsoft's TMDL reference) and the collections each translated object holds.
TRANSLATED = {"caption": "translatedCaption", "description": "translatedDescription",
              "displayFolder": "translatedDisplayFolder"}
TRANSLATED_CHILDREN = {"model": ("tables", "perspectives", "roles", "expressions"),
                       "table": ("columns", "measures", "hierarchies"), "hierarchy": ("levels",)}
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
        self.cultures = 0  # inside a culture
        self.raw = 0  # inside a culture's translations: objects are names and captions, not model objects
        self.order: dict[str, list[str]] = {}  # "ref table" and "ref cultureInfo" lines: the model's order of each

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
        are indented more than ``deeper_than``, or fenced in triple backticks (the opening fence after ``=``
        or alone on the next line)."""
        if not first.strip() and self.i < len(self.lines) and self.lines[self.i].strip() == "```":
            first = "```"
            self.i += 1
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
            margins = [_indent(b) for b in body if b.strip()]
            if not margins and self.i < len(self.lines):
                # Only blank lines (a value of spaces): the closing fence marks the left edge, so the layout's
                # tabs don't become part of the value.
                margins.append(_indent(self.lines[self.i]))
            self.i += 1  # the closing fence
            pad = min(margins, default=0)
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
            if word == "ref":  # the order of tables and cultures in model.tmdl, kept; other refs aren't needed here
                ref = text.split(None, 2)
                if len(ref) == 3 and ref[1] in ORDERED_REFS:
                    self.order.setdefault(ORDERED_REFS[ref[1]], []).append(unquote(ref[2]))
                continue
            m = _OBJECT.match(text)
            if m and m.group(1) in OBJECTS:
                self._object(m.group(1), unquote(m.group(2)), m.group(3), here, into, description)
                continue
            m = _EXPR.fullmatch(text)
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
            m = _PROP.fullmatch(text)
            if m:
                into[m.group(1)] = _value(m.group(1), m.group(2))
                continue
            m = _WORD.fullmatch(text)
            if m:
                nxt = self._next_level()
                if nxt is not None and nxt > here:
                    nested: dict[str, Any] = {}
                    raw = m.group(1) == "translations" and self.cultures > 0
                    self.raw += raw
                    self.block(here, nested)
                    self.raw -= raw
                    into[m.group(1)] = nested
                else:
                    into[m.group(1)] = True
                continue
            self._skip_block(here)
            self.report.append({"object": text[:80], "kind": "lost", "message": f"not understood, left out ({where})",
                                "file": self.path})

    def _object(self, kind: str, name: str, default: str | None, level: int, parent: dict[str, Any],
                description: str) -> None:
        collection, default_key = OBJECTS[kind]
        obj: dict[str, Any] = {"name": name}
        if description:
            obj["description"] = description
        if default is not None and default_key:
            obj[default_key] = self._expression(default, level + 1)
        where = f"{self.path}:{self.i}"
        culture = kind in CULTURES and not self.raw
        self.cultures += culture
        self.block(level, obj)
        self.cultures -= culture
        if kind in ("model", "database"):
            parent.setdefault(kind, {}).update(obj)
            return
        if self.raw:
            parent.setdefault(collection, []).append(obj)
            return
        if culture:
            obj = self._culture(obj, where)
        parent.setdefault(collection, []).append(_shape(kind, obj))

    def _lost(self, where: str, message: str, at: str = "") -> None:
        self.report.append({"object": where, "kind": "lost", "message": f"{message} ({at or self.path})", "file": self.path})

    def _culture(self, obj: dict[str, Any], where: str) -> dict[str, Any]:
        """A culture as TMSL has it: JSON linguistic metadata parsed, translations as ``translated...`` properties."""
        name = obj.get("name")
        meta = obj.get("linguisticMetadata")
        if (isinstance(meta, dict) and isinstance(meta.get("content"), str)
                and str(meta.get("contentType") or "").lower() == "json"):
            try:
                meta["content"] = json.loads(meta["content"])
            except ValueError:
                self._lost(f"culture {name!r} linguisticMetadata",
                           "its contentType is json but its content isn't JSON, so it was kept as text", where)
        if "translations" not in obj:
            return obj
        raw, scope = obj.pop("translations"), f"culture {name!r} translations"
        if not isinstance(raw, dict):
            self._lost(scope, "there were no translations under it", where)
            return obj
        translations = {}
        for key, value in raw.items():
            if key == "model" and isinstance(value, dict):
                translations["model"] = self._translated("model", value, f"{scope} model")
            else:
                self._lost(f"{scope} {key}", "this translation isn't read from TMDL on this PC")
        if translations:
            obj["translations"] = translations
        return obj

    def _translated(self, kind: str, raw: dict[str, Any], where: str) -> dict[str, Any]:
        """One object of a culture's translations, read as plain properties, as TMSL's ``translated...`` ones."""
        out: dict[str, Any] = {}
        for key, value in raw.items():
            if key == "name":
                out["name"] = value
            elif key in TRANSLATED and isinstance(value, (str, bool)):
                out[TRANSLATED[key]] = value if isinstance(value, str) else ("true" if value else "false")
            elif key in TRANSLATED_CHILDREN.get(kind, ()) and isinstance(value, list):
                child = _KINDS[key]
                out[key] = [self._translated(child, item, f"{where} {child} {item.get('name')!r}") for item in value]
            else:
                self._lost(f"{where} {key}", "this translation isn't read from TMDL on this PC")
        return out


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
    (TMDL calls every model "Model"; the folder or item name is the real one). A row about something in one
    file names it in ``file`` (its path as given), so a caller rewriting that file can say what it loses."""
    if sum(len(t) for t in files.values()) > MAX_CHARS:
        raise ValueError("The model's TMDL is larger than 20 MB, so it isn't read on this PC.")
    report: list[dict[str, str]] = []
    root: dict[str, Any] = {}
    model: dict[str, Any] = {}
    ordered = sorted((p for p in files if p.endswith(".tmdl")), key=_file_order)
    if not ordered:
        raise ValueError("There are no .tmdl files in this folder; it should hold definition/model.tmdl")
    order: dict[str, list[str]] = {}
    for path in ordered:
        top: dict[str, Any] = {}
        reader = _Reader(path, files[path], report)
        try:
            reader.block(-1, top)
        except RecursionError:
            raise ValueError(f"{path} is nested too deeply to be a semantic model") from None
        for key, names in reader.order.items():
            order.setdefault(key, []).extend(names)
        db = top.pop("database", None)
        if isinstance(db, dict):
            root.update(db)
        found = top.pop("model", {})
        if isinstance(found, dict):
            model.update(found)
        else:  # "model" or "model: x" with no name: not the model object
            report.append({"object": "model", "kind": "lost", "message": f"not understood, left out ({path})"})
        for key, value in top.items():
            if isinstance(value, list):
                model.setdefault(key, []).extend(value)
            else:
                model[key] = value
    model.pop("name", None)
    for key, names in order.items():  # in model.tmdl's order, as Microsoft's reader does; those it doesn't name last
        if isinstance(model.get(key), list):
            rank = {n: i for i, n in reversed(list(enumerate(names)))}
            model[key].sort(key=lambda o: rank.get(o["name"], len(rank))
                            if isinstance(o, dict) and isinstance(o.get("name"), str) else len(rank))
    bim = {"name": name or "Model", **{k: v for k, v in root.items() if k != "name"}, "model": model}
    bim.setdefault("compatibilityLevel", 1604)
    return bim, report


def _file_order(path: str) -> tuple[int, str, str]:
    """database, model, expressions and data sources first, then tables, relationships, the rest. In a folder,
    ``x.tmdl`` before ``X (2).tmdl``: the order ``file_name`` numbered them in, so writing again keeps the names."""
    name = PurePosixPath(path).name
    key = path.removesuffix(".tmdl").casefold()
    first = ("database.tmdl", "model.tmdl", "expressions.tmdl", "dataSources.tmdl")
    if name in first:
        return first.index(name), key, path
    if "/tables/" in f"/{path}":
        return 10, key, path
    if name == "relationships.tmdl":
        return 20, key, path
    return 30, key, path


# ---- writing: TMSL model.bim as TMDL files, on this PC (PB-110)
#
# The inverse of the reader above, written for it: whatever ``from_bim`` writes, ``to_bim`` reads back as the
# same model, and ``from_bim`` checks that by reading its own output. Anything that would come back different
# (or not at all) is reported by name; a name or expression that could break the layout is refused.

MAX_TABLES = 5_000
_BARE = re.compile(r"[\w-]+")
_KEY = re.compile(r"\w+")
# Every character str.splitlines breaks on: our reader splits only on \n and \r, but other TMDL readers and
# editors may break lines on the rest too, so none of them may sit where a line break would start a new object.
_BREAKS = ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def _breaks(text: str) -> bool:
    return any(c in text for c in _BREAKS)
_FENCE = "```"
# Collections a perspective and its tables hold have keywords of their own.
_PERSPECTIVE_KINDS = {("perspective", "tables"): "perspectiveTable", ("perspectiveTable", "columns"): "perspectiveColumn",
                      ("perspectiveTable", "measures"): "perspectiveMeasure",
                      ("perspectiveTable", "hierarchies"): "perspectiveHierarchy"}
_KINDS = {coll: kind for kind, (coll, _) in OBJECTS.items() if coll and not kind.startswith("perspective")}
_KINDS["perspectives"] = "perspective"
_KINDS["cultures"] = "culture"  # how a report names one; the files say cultureInfo, as Power BI Desktop writes
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
    if _breaks(name):
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


def _json_text(value: Any) -> str:
    """Linguistic metadata as JSON text, two-space indented as Power BI writes it. Characters that are line breaks
    to str.splitlines but that JSON leaves as they are can only be inside JSON strings, so they are escaped."""
    text = json.dumps(value, indent=2, ensure_ascii=False)
    return text.replace("\x85", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _expression(text: str, level: int) -> list[str]:
    """What follows ``=``: the expression inline when it is one plain line, otherwise fenced in triple
    backticks one level deeper than ``level``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not _breaks(text) and text and text == text.strip() and not text.startswith(_FENCE):
        return [" " + text]
    lines = text.split("\n")
    if any(piece.strip() == _FENCE for piece in text.splitlines()):
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
                self.lost(f"{where} property {key[:80]}".strip(), "this property name can't be written as TMDL")
            elif value is None:
                continue
            elif isinstance(value, str) and (key in _EXPRESSION_KEYS or _breaks(value)):
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
                    if isinstance(prop, str) and prop and not _breaks(prop) and prop == prop.strip():
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
            if obj.get("type") == "calculated" and default:  # the reader makes a column calculated by its expression
                obj.pop("type")
        elif kind == "partition" and isinstance(obj.get("source") or {}, dict):  # any other source: as written
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
        if isinstance(description, str):
            lines = description.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if description and all(line == line.strip() and not _breaks(line) for line in lines):
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
                        obj[f"{end}Column"] = f"{quote(table)}.{quote(column)}"
                    except _Unwritable as exc:
                        raise _Unwritable(f"{where}: {exc}") from None
                elif table is not None:
                    obj[f"{end}Table"] = table
        self.body(kind, obj, level, where, out)

    def culture(self, obj: dict[str, Any], out: list[str]) -> None:
        """A culture as ``cultureInfo <name>``: its linguistic metadata (JSON content as JSON text), any other
        settings, then its translations."""
        obj = dict(obj)
        name = obj.pop("name", None)
        where = f"culture {name!r}"
        if not isinstance(name, str) or not name:
            self.lost("culture", "a culture with no name was left out")
            return
        try:
            out.extend([f"cultureInfo {quote(name)}", ""])
        except _Unwritable as exc:
            raise _Unwritable(f"{where}: {exc}") from None
        meta = obj.get("linguisticMetadata")
        if isinstance(meta, dict):
            self.metadata(obj.pop("linguisticMetadata"), f"{where} linguisticMetadata", out)
        translations = obj.pop("translations", None)
        self.body("cultureInfo", obj, 0, where, out)
        if translations is None:
            return
        where += " translations"
        lines: list[str] = []
        if not isinstance(translations, dict) or not translations:
            self.lost(where, "an empty setting isn't written as TMDL")
        else:
            for key, node in translations.items():
                if key == "model" and isinstance(node, dict):
                    self.translated("model", node, 2, f"{where} model", lines)
                else:
                    self.lost(f"{where} {key}", "TMDL on this PC has no place for this translation")
        if lines:
            if out[-1]:
                out.append("")
            out.append("\ttranslations")
            out.extend(lines)
            out.append("")

    def metadata(self, meta: dict[str, Any], where: str, out: list[str]) -> None:
        """``linguisticMetadata =`` as Power BI Desktop writes it: the content on the lines after it, two levels
        deeper, then its contentType. JSON content needs no fence (its line breaks and separators are escaped, and
        every line is deeper than the properties); other text has one when it is more than one plain line."""
        meta = dict(meta)
        content = _joined(meta.pop("content", None))
        if content is None:
            self.lost(where, "linguistic metadata with no content isn't written as TMDL")
            return
        pad = "\t\t\t"
        if not isinstance(content, str):
            out.append("\tlinguisticMetadata =")
            out.extend(pad + line for line in _json_text(content).split("\n"))
        elif content and not _breaks(content) and content == content.strip() and not content.startswith(_FENCE):
            out.append(f"\tlinguisticMetadata = {content}")
        else:
            text = content.replace("\r\n", "\n").replace("\r", "\n")
            if any(piece.strip() == _FENCE for piece in text.splitlines()):
                raise _Unwritable(f"{where}: its expression has a line of three backticks")
            out.extend(["\tlinguisticMetadata =", pad + _FENCE, *(pad + line if line else "" for line in text.split("\n")),
                        pad + _FENCE])
        self.body("linguisticMetadata", meta, 1, where, out)
        out.append("")

    def translated(self, kind: str, node: dict[str, Any], level: int, where: str, out: list[str]) -> None:
        """One object of a culture's translations: its name, then ``caption``, ``description`` and
        ``displayFolder`` for TMSL's ``translated...`` properties, then the objects it holds."""
        node = dict(node)
        name = node.pop("name", None)
        if kind == "model" and not name:
            name = "Model"  # every model is called Model in TMDL; a translation's name isn't compared
        if not isinstance(name, str) or not name:
            self.lost(where, "a translation with no name was left out")
            return
        tabs = "\t" * level
        try:
            out.append(f"{tabs}{kind} {quote(name)}")
        except _Unwritable as exc:
            raise _Unwritable(f"{where}: {exc}") from None
        plain = {v: k for k, v in TRANSLATED.items()}
        children = []
        for key, value in node.items():
            value = _joined(value)
            if value is None:
                continue
            if key in plain and isinstance(value, str):
                if _breaks(value):
                    try:
                        lines = _expression(value, level + 1)
                    except _Unwritable as exc:
                        raise _Unwritable(f"{where} {key}: {exc}") from None
                    out.append(f"{tabs}\t{plain[key]} ={lines[0]}")
                    out.extend(lines[1:])
                else:
                    out.append(f"{tabs}\t{plain[key]}: {_scalar(value)}")
            elif key in plain:
                self.lost(f"{where} {key}", "a translation that isn't text can't be written as TMDL")
            elif key in TRANSLATED_CHILDREN.get(kind, ()) and isinstance(value, list):
                children.append((key, value))
            else:
                self.lost(f"{where} {key}", "TMDL on this PC has no place for this translation")
        for key, items in children:
            child = _KINDS[key]
            for item in items:
                if isinstance(item, dict):
                    self.translated(child, item, level + 1, f"{where} {child} {item.get('name')!r}", out)
                else:
                    self.lost(f"{where} {key}", "an entry that isn't an object was left out")


def file_name(name: str, taken: set[str]) -> str:
    """A Windows-safe file name (no extension) for an object called ``name``: never the name used raw, so a
    table called ``..\\x`` or ``CON`` can't leave the folder or name a device. Unique in ``taken`` (case-folded)."""
    safe = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 or c in "\x7f\x85\u2028\u2029" else c for c in name)
    safe = safe.strip().rstrip(".").lstrip(".")[:100].rstrip(". ") or "_"
    if safe.split(".")[0].upper().rstrip() in _RESERVED:
        safe = "_" + safe
    candidate, n = safe, 1
    while candidate.casefold() in taken:
        n += 1
        suffix = f" ({n})"
        candidate = (safe[:100 - len(suffix)].rstrip(". ") or "_") + suffix  # still at most 100 characters
    taken.add(candidate.casefold())
    return candidate


def from_bim(bim: dict[str, Any], culture_refs: list[str] | None = None) -> tuple[dict[str, str], list[dict[str, str]]]:
    """See ``_from_bim``; a model nested deeper than Python's stack is refused with a plain reason."""
    try:
        return _from_bim(bim, culture_refs)
    except RecursionError:
        raise ValueError("This model is nested too deeply to be a semantic model") from None


def _from_bim(bim: dict[str, Any], culture_refs: list[str] | None = None) -> tuple[dict[str, str], list[dict[str, str]]]:
    """A TMSL ``model.bim`` document as TMDL files (path under the ``.SemanticModel`` folder -> text), and the report.
    ``culture_refs``, when given, are the cultures model.tmdl lists the order of, in place of the model's own: a
    caller that keeps some culture files as they are names their cultures there too, so the order stays.

    The files are read back with ``to_bim`` before they are returned: anything that wouldn't come back as it
    went in is in the report by name (``kind`` "lost"), so nothing is dropped silently. Names with line breaks
    and expressions with a line of three backticks would break the layout, so they are refused (ValueError,
    naming the object)."""
    report: list[dict[str, str]] = []
    w = _Writer(report)
    if not isinstance(bim, dict) or not isinstance(bim.get("model") or {}, dict):
        raise ValueError("This isn't a semantic model: it should be one object, with its model as an object in it")
    model = dict(bim.get("model") or {})
    tables = model.get("tables") or []
    if isinstance(tables, list) and len(tables) > MAX_TABLES:
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
        cultures = model.pop("cultures", None)
        if cultures is not None and not isinstance(cultures, list):
            w.lost("model cultures", "TMDL on this PC has no place for this setting")
            cultures = None
        split = {k: model.pop(k) for k in list(model) if k in _FILES or k in _ONE_FILE}
        for key in [k for k, v in split.items() if v and not isinstance(v, list)]:
            w.lost(key, "this should be a list, so it was left out")
            del split[key]
        lines = ["model Model"]
        w.body("model", model, 0, "model", lines)
        for t in split.get("tables") or []:
            if isinstance(t, dict) and isinstance(t.get("name"), str):
                lines.append(f"ref table {quote(t['name'])}")
        if culture_refs is None:
            culture_refs = [c["name"] for c in cultures or [] if isinstance(c, dict) and isinstance(c.get("name"), str)]
        for c in culture_refs:  # the cultures' order, which TMSL keeps and the reader above reads
            if c:
                try:
                    lines.append(f"ref cultureInfo {quote(c)}")
                except _Unwritable as exc:
                    raise _Unwritable(f"culture {c!r}: {exc}") from None
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
        taken = set()
        for culture in cultures or []:
            if not isinstance(culture, dict):
                w.lost("cultures", "an entry that isn't an object was left out")
                continue
            lines = []
            w.culture(culture, lines)
            if lines:
                emit(f"cultures/{file_name(str(culture.get('name') or ''), taken)}.tmdl", lines)
    except _Unwritable as exc:
        raise ValueError(f"This model can't be written as TMDL: {str(exc).strip()}") from None
    if sum(len(t) for t in files.values()) > MAX_CHARS:
        raise ValueError("This model's TMDL would be larger than 20 MB, so it isn't written on this PC.")
    try:
        back, read_report = to_bim(files, str(bim.get("name") or ""))
    except ValueError as exc:  # a value the reader takes for something else, such as a column reference
        raise ValueError(f"This model can't be written as TMDL: it wouldn't read back ({exc})") from None
    report += [r for r in read_report if r not in report]
    report += [{"object": where, "kind": "lost", "message": "wouldn't read back from TMDL as it was"}
               for where in differences(bim, back)
               if not any(where.startswith(r["object"].replace(" property ", " ", 1)) for r in report)]
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
