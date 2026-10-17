"""What a pipeline expression refers to, found by reading it. Nothing is ever evaluated.

Fabric (like Azure Data Factory) marks a dynamic value two ways:

    {"value": "@pipeline().parameters.day", "type": "Expression"}   the whole value is an expression
    "@concat('a', 'b')"                                              a string starting with @
    "Run @{pipeline().RunId} of @{pipeline().Pipeline}"             interpolation inside a string

``@@`` at the start is an escaped literal @. ``scan`` returns each expression in a value tree with
its JSON path, and ``references`` lists what one refers to: activities (``activity('X')``),
variables (``variables('v')``), pipeline parameters, library variables, ``item()`` and the
functions it calls. The validator checks those names against the pipeline; ORCH-003 will need the
same list to know what an activity waits for.

An expression that can't be read (an unclosed quote or bracket) is reported as a problem rather
than raising, because a definition with one bad expression is still worth inspecting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Reference:
    kind: str   # activity | variable | parameter | global_parameter | library_variable | item | system
    name: str
    member: str = ""  # what follows, e.g. "output.firstRow" after activity('X'), for later stages

    def to_json(self) -> dict[str, Any]:
        out = {"kind": self.kind, "name": self.name}
        if self.member:
            out["member"] = self.member
        return out


@dataclass
class Expression:
    path: str
    text: str            # the expression without its leading @ (or one @{...} body)
    interpolated: bool   # inside a string with @{...}, rather than the whole value
    references: list[Reference] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    problem: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "text": self.text, "interpolated": self.interpolated,
                               "references": [r.to_json() for r in self.references], "functions": self.functions}
        if self.problem:
            out["problem"] = self.problem
        return out


def _bodies(value: str) -> Iterator[tuple[str, bool, str]]:
    """(expression text, interpolated, problem) for each expression in one string value."""
    if value.startswith("@@"):
        return
    if value.startswith("@") and not value.startswith("@{"):
        yield value[1:], False, ""
        return
    i = 0
    while True:
        start = value.find("@{", i)
        if start < 0:
            return
        if start > 0 and value[start - 1] == "@":  # @@{ is a literal
            i = start + 2
            continue
        depth, j, quoted = 1, start + 2, False
        while j < len(value) and depth:
            ch = value[j]
            if quoted:
                if ch == "'":
                    if j + 1 < len(value) and value[j + 1] == "'":
                        j += 1
                    else:
                        quoted = False
            elif ch == "'":
                quoted = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            j += 1
        if depth:
            yield value[start + 2:], True, "an @{ isn't closed with }"
            return
        yield value[start + 2:j - 1], True, ""
        i = j


def scan(node: Any, path: str = "$") -> Iterator[Expression]:
    """Every expression in a JSON value tree, depth first, with its JSON path."""
    if isinstance(node, dict):
        if node.get("type") == "Expression" and isinstance(node.get("value"), str):
            text = node["value"]
            body = text[1:] if text.startswith("@") and not text.startswith("@@") else text
            if text.startswith("@{") or ("@{" in text and not text.startswith("@")):
                yield from _from_string(text, f"{path}.value")
            else:
                yield _read(body, f"{path}.value", False, "")
            return
        for key, child in node.items():
            yield from scan(child, f"{path}{key_path(key)}")
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from scan(child, f"{path}[{i}]")
    elif isinstance(node, str) and "@" in node:
        yield from _from_string(node, path)


def _from_string(text: str, path: str) -> Iterator[Expression]:
    for body, interpolated, problem in _bodies(text):
        yield _read(body, path, interpolated, problem)


def key_path(key: str) -> str:
    """One JSON path step: .name, or ['odd name'] when it isn't a plain identifier."""
    return f".{key}" if _IDENT.fullmatch(key) else "[" + _quote(key) + "]"


def _quote(key: str) -> str:
    return "'" + key.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tokens(text: str) -> tuple[list[tuple[str, str]], str]:
    """(kind, value) tokens of one expression, and a problem if it can't be read to the end.
    Kinds: ident, string, number, and the punctuation itself for ( ) [ ] . , ?"""
    out: list[tuple[str, str]] = []
    i, depth = 0, 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "'":
            j, buf = i + 1, []
            while True:
                if j >= len(text):
                    return out, "a quoted string isn't closed"
                if text[j] == "'":
                    if j + 1 < len(text) and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            out.append(("string", "".join(buf)))
            i = j + 1
        elif m := _IDENT.match(text, i):
            out.append(("ident", m.group()))
            i = m.end()
        elif m := _NUMBER.match(text, i):
            out.append(("number", m.group()))
            i = m.end()
        elif ch in "([":
            depth += 1
            out.append((ch, ch))
            i += 1
        elif ch in ")]":
            depth -= 1
            if depth < 0:
                return out, f"a {ch} has no matching opening bracket"
            out.append((ch, ch))
            i += 1
        elif ch in ".,?":
            out.append((ch, ch))
            i += 1
        else:
            return out, f"{ch!r} isn't expected in an expression"
    if depth:
        return out, "a bracket isn't closed"
    return out, ""


def _member(toks: list[tuple[str, str]], i: int) -> tuple[str, int]:
    """The .a.b['c'] chain starting at toks[i] (optional ?. too), as text, and where it ends."""
    parts: list[str] = []
    while i < len(toks):
        if toks[i][0] == "?" and i + 1 < len(toks) and toks[i + 1][0] in ".[":
            i += 1
            continue
        if toks[i][0] == "." and i + 1 < len(toks) and toks[i + 1][0] == "ident":
            parts.append(toks[i + 1][1])
            i += 2
        elif toks[i][0] == "[" and i + 2 < len(toks) and toks[i + 1][0] in ("string", "number") and toks[i + 2][0] == "]":
            parts.append(toks[i + 1][1])
            i += 3
        else:
            break
    return ".".join(parts), i


def _read(text: str, path: str, interpolated: bool, problem: str) -> Expression:
    toks, tok_problem = tokens(text)
    expr = Expression(path=path, text=text, interpolated=interpolated, problem=problem or tok_problem)
    seen: set[tuple[str, str, str]] = set()

    def add(ref: Reference) -> None:
        if (ref.kind, ref.name, ref.member) not in seen:
            seen.add((ref.kind, ref.name, ref.member))
            expr.references.append(ref)

    i = 0
    while i < len(toks):
        kind, value = toks[i]
        if kind == "ident" and i + 1 < len(toks) and toks[i + 1][0] == "(":
            fn = value.lower()
            if fn not in expr.functions:
                expr.functions.append(fn)
            arg = toks[i + 2] if i + 2 < len(toks) else ("", "")
            closes = i + 3 < len(toks) and toks[i + 3][0] == ")"
            if fn in ("activity", "variables") and arg[0] == "string" and closes:
                member, end = _member(toks, i + 4)
                add(Reference("activity" if fn == "activity" else "variable", arg[1], member))
                i = end
                continue
            if fn in ("pipeline", "item") and arg[0] == ")":
                member, end = _member(toks, i + 3)
                if fn == "item":
                    add(Reference("item", "item", member))
                else:
                    head, _, rest = member.partition(".")
                    kinds = {"parameters": "parameter", "globalparameters": "global_parameter",
                             "libraryvariables": "library_variable"}
                    if head.lower() in kinds and rest:
                        name, _, more = rest.partition(".")
                        add(Reference(kinds[head.lower()], name, more))
                    elif head:
                        add(Reference("system", head, rest))
                i = end
                continue
        i += 1
    return expr


def references(text: str) -> list[Reference]:
    """What one expression (without its leading @) refers to."""
    return _read(text, "$", False, "").references
