"""xml() and xpath() for local pipeline runs, written here on the standard library's expat parser.

An XML value is carried as its text, so it fits in a pipeline variable or an activity's output like
any other string: ``xml()`` checks the text (or builds it from a JSON object) and ``xpath()`` parses it
again. XPath is XPath 1.0, as in Data Factory, with its whole function library except ``id()`` and
``lang()``, and without variables.

The parser refuses a document type declaration (``<!DOCTYPE``), so no entity can expand and nothing
outside the text is read. Documents nest at most ``MAX_DEPTH`` elements, an XPath is at most
``MAX_XPATH`` characters, one evaluation does at most ``MAX_VISITS`` units of work (nodes visited,
comparisons, text read) in at most ``MAX_SECONDS``, and no text it builds passes ``MAX_STRING`` characters,
so an expression in a loop can't use all the engine's memory or time.

Choices made here that haven't been compared with Fabric's results (docs/orchestration/local-runs.md):
text that is only white space between elements is dropped, as .NET's XmlDocument does by default; a
node set comes back as an array, with an element as its XML text and a text or attribute node as its
value; a prefixed name test (``/p:item``) is refused because Data Factory gives xpath() no namespace
prefixes; match on ``local-name()`` and ``namespace-uri()`` instead.
"""

from __future__ import annotations

import math
import re
import time
from typing import Any, Callable
from xml.parsers import expat


class XmlError(Exception):
    """XML or an XPath that can't be used here; the message says why."""


MAX_DEPTH = 512
MAX_XPATH = 10_000
MAX_VISITS = 500_000          # nodes visited, comparisons made and text read (per 256 characters), per xpath()
MAX_SECONDS = 2.0             # and wall-clock time per xpath(), whichever comes first
MAX_STRING = 1_048_576        # characters in any text an XPath builds
MAX_NESTING = 32   # brackets and predicates inside one another; each level is a dozen Python frames
_NAME = re.compile(r"[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?\Z")


# ---- the document

class Node:
    __slots__ = ("kind", "name", "uri", "value", "parent", "children", "attrs", "decls", "order")

    def __init__(self, kind: str, name: str = "", uri: str = "", value: str = "", parent: "Node | None" = None):
        self.kind, self.name, self.uri, self.value, self.parent = kind, name, uri, value, parent
        self.children: list[Node] = []
        self.attrs: list[Node] = []
        self.decls: dict[str, str] = {}   # xmlns declarations made on this element, prefix -> uri
        self.order = 0

    @property
    def local(self) -> str:
        return self.name.split(":", 1)[-1]

    def scope(self) -> dict[str, str]:
        """The namespace declarations in scope here, prefix -> uri ("" is the default namespace)."""
        chain: list[dict[str, str]] = []
        node: Node | None = self
        while node is not None:
            chain.append(node.decls)
            node = node.parent
        out: dict[str, str] = {}
        for decls in reversed(chain):
            out.update(decls)
        return out


def parse(text: str) -> Node:
    """The document in ``text``; refuses a DOCTYPE, nesting past MAX_DEPTH and anything not well formed."""
    if not isinstance(text, str):
        raise XmlError("XML must be text")
    root = Node("root")
    stack = [root]
    pending: list[str] = []
    counter = [0]

    def number(node: Node) -> Node:
        counter[0] += 1
        node.order = counter[0]
        return node

    def flush() -> None:
        if pending:
            data = "".join(pending)
            pending.clear()
            if data.strip():
                stack[-1].children.append(number(Node("text", value=data, parent=stack[-1])))

    def doctype(*_: Any) -> None:
        raise XmlError("XML with a <!DOCTYPE> isn't read in local runs, so no entity can expand")

    def start(name: str, attrs: list[str]) -> None:
        flush()
        if len(stack) > MAX_DEPTH:
            raise XmlError(f"XML nests more than {MAX_DEPTH} elements deep")
        parent = stack[-1]
        node = number(Node("element", name=name, parent=parent))
        plain = []
        for i in range(0, len(attrs), 2):
            key, value = attrs[i], attrs[i + 1]
            if key == "xmlns":
                node.decls[""] = value
            elif key.startswith("xmlns:"):
                node.decls[key[6:]] = value
            else:
                plain.append((key, value))
        names = node.scope()
        prefix = name.split(":", 1)[0] if ":" in name else ""
        if prefix and prefix not in names:
            raise XmlError(f"the prefix {prefix!r} on <{name}> isn't declared")
        node.uri = names.get(prefix, "")
        for key, value in plain:
            p = key.split(":", 1)[0] if ":" in key else ""
            if p and p not in names:
                raise XmlError(f"the prefix {p!r} on the attribute {key!r} isn't declared")
            node.attrs.append(number(Node("attribute", name=key, uri=names.get(p, "") if p else "",
                                          value=value, parent=node)))
        parent.children.append(node)
        stack.append(node)

    def end(_: str) -> None:
        flush()
        stack.pop()

    def chars(data: str) -> None:
        pending.append(data)

    def comment(data: str) -> None:
        flush()
        stack[-1].children.append(number(Node("comment", value=data, parent=stack[-1])))

    def pi(target: str, data: str) -> None:
        flush()
        stack[-1].children.append(number(Node("pi", name=target, value=data, parent=stack[-1])))

    parser = expat.ParserCreate()
    parser.ordered_attributes = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = doctype
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.CommentHandler = comment
    parser.ProcessingInstructionHandler = pi
    try:
        parser.Parse(text, True)
    except expat.ExpatError as e:
        raise XmlError(f"the XML isn't well formed: {expat.errors.messages[e.code]} "
                       f"(line {e.lineno}, column {e.offset + 1})") from None
    if not any(c.kind == "element" for c in root.children):
        raise XmlError("the XML has no root element")
    return root


def _escape(s: str, attr: bool = False) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;") if attr else s


def _used_prefixes(node: Node, out: set[str]) -> None:
    out.add(node.name.split(":", 1)[0] if ":" in node.name else "")
    for a in node.attrs:
        if ":" in a.name:
            out.add(a.name.split(":", 1)[0])
    for c in node.children:
        if c.kind == "element":
            _used_prefixes(c, out)


def outer_xml(node: Node) -> str:
    """An element as XML text, with the namespace declarations it inherits and uses added, so the text
    stands on its own."""
    extra: dict[str, str] = {}
    if node.parent is not None and node.parent.kind == "element":
        used: set[str] = set()
        _used_prefixes(node, used)
        inherited = node.parent.scope()
        extra = {p: u for p, u in inherited.items() if p in used and p not in node.decls and u}
    out: list[str] = []
    _write(node, out, extra)
    return "".join(out)


def _write(node: Node, out: list[str], extra: dict[str, str] | None = None) -> None:
    if node.kind == "text":
        out.append(_escape(node.value))
    elif node.kind == "comment":
        out.append(f"<!--{node.value}-->")
    elif node.kind == "pi":
        out.append(f"<?{node.name} {node.value}?>" if node.value else f"<?{node.name}?>")
    elif node.kind == "element":
        out.append("<" + node.name)
        for prefix, uri in {**(extra or {}), **node.decls}.items():
            out.append(f' xmlns="{_escape(uri, True)}"' if not prefix else f' xmlns:{prefix}="{_escape(uri, True)}"')
        for a in node.attrs:
            out.append(f' {a.name}="{_escape(a.value, True)}"')
        if not node.children:
            out.append(" />")
            return
        out.append(">")
        for c in node.children:
            _write(c, out)
        out.append(f"</{node.name}>")
    else:
        for c in node.children:
            _write(c, out)


# ---- xml()

def to_xml(value: Any) -> str:
    """xml(): XML text checked as well formed, or a JSON object with one root property written as XML
    (the way Data Factory converts one: "@name" is an attribute, "#text" is text, an array repeats its
    element)."""
    if isinstance(value, dict) and "$content" in value:
        from .expr_binary import binary_text   # a binary value holding XML text
        value = binary_text(value)
    if isinstance(value, str):
        parse(value)
        return value
    if isinstance(value, dict):
        roots = [(k, v) for k, v in value.items() if k not in ("?xml", "$schema")]
        if len(roots) != 1:
            raise XmlError(f"xml() needs a JSON object with exactly one root property, not {len(roots)}")
        out: list[str] = []
        _json_element(roots[0][0], roots[0][1], out, 0)
        text = "".join(out)
        parse(text)
        return text
    raise XmlError(f"xml() needs XML text or a JSON object, not {type(value).__name__}")


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _json_element(name: str, value: Any, out: list[str], depth: int) -> None:
    if depth > MAX_DEPTH:
        raise XmlError(f"the JSON nests more than {MAX_DEPTH} levels deep")
    if not _NAME.match(str(name)):
        raise XmlError(f"{name!r} can't be an XML element name")
    if isinstance(value, list):
        for item in value:
            _json_element(name, item, out, depth + 1)
        return
    if value is None:
        out.append(f"<{name} />")
        return
    if not isinstance(value, dict):
        out.append(f"<{name}>{_escape(_scalar(value))}</{name}>")
        return
    out.append("<" + name)
    for k, v in value.items():
        if k.startswith("@"):
            if not _NAME.match(k[1:]):
                raise XmlError(f"{k[1:]!r} can't be an XML attribute name")
            if isinstance(v, (dict, list)):
                raise XmlError(f"the attribute {k[1:]!r} must be a single value")
            out.append(f' {k[1:]}="{_escape("" if v is None else _scalar(v), True)}"')
    body = [(k, v) for k, v in value.items() if not k.startswith("@")]
    if not body:
        out.append(" />")
        return
    out.append(">")
    for k, v in body:
        if k in ("#text", "#cdata-section"):
            for part in v if isinstance(v, list) else [v]:
                out.append(_escape("" if part is None else _scalar(part)))
        else:
            _json_element(k, v, out, depth + 1)
    out.append(f"</{name}>")


# ---- xpath()

_TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:\.\d*)?|\.\d+)
    | (?P<str>"[^"]*"|'[^']*')
    | (?P<op>//|::|\.\.|!=|<=|>=|[/()\[\]@,|+\-=<>*.$])
    | (?P<name>[A-Za-z_][\w.\-]*(?::(?:[A-Za-z_][\w.\-]*|\*))?)
    )""", re.X)
_AXES = {"child", "descendant", "descendant-or-self", "self", "parent", "ancestor", "ancestor-or-self",
         "attribute", "following-sibling", "preceding-sibling", "following", "preceding"}
_REVERSE = {"parent", "ancestor", "ancestor-or-self", "preceding-sibling", "preceding"}
_NODE_TYPES = {"node", "text", "comment", "processing-instruction"}
_OPERATOR_NAMES = {"and", "or", "div", "mod"}


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    text = text.rstrip()
    i = 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m or m.end() == i:
            raise XmlError(f"the XPath can't be read at {text[i:i + 12].strip()!r}")
        kind = m.lastgroup or ""
        value = m.group(kind)
        # XPath 1.0's rule: after something that ends an operand, * and and/or/div/mod are operators.
        prev = out[-1] if out else None
        operand_before = prev is not None and not (prev[0] == "op" and prev[1] not in (")", "]", ".", "..")) \
            and prev[1] not in ("@", "::")
        if prev is not None and prev[0] == "op" and prev[1] in (")", "]", ".", ".."):
            operand_before = True
        if kind == "op" and value == "*" and operand_before:
            kind = "mul"
        elif kind == "name" and operand_before and value in _OPERATOR_NAMES:
            kind = "opname"
        out.append((kind, value))
        i = m.end()
    return out


Value = Any   # list[Node] (a node set), str, float or bool


class _XPath:
    def __init__(self, text: str):
        if len(text) > MAX_XPATH:
            raise XmlError(f"the XPath is longer than {MAX_XPATH:,} characters")
        self.toks = _tokens(text)
        self.i = 0
        self.depth = 0
        self.visits = 0
        self.deadline = math.inf
        self.run = self.expr()
        if self.i != len(self.toks):
            raise XmlError(f"unexpected {self.toks[self.i][1]!r} in the XPath")

    # parsing: each method returns a function of (node, position, size)

    def peek(self, offset: int = 0) -> tuple[str, str]:
        j = self.i + offset
        return self.toks[j] if j < len(self.toks) else ("", "")

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if not tok[0]:
            raise XmlError("the XPath ends too soon")
        self.i += 1
        return tok

    def expect(self, value: str) -> None:
        tok = self.take()
        if tok[1] != value:
            raise XmlError(f"expected {value!r} in the XPath but found {tok[1]!r}")

    def expr(self) -> Callable:
        self.depth += 1
        if self.depth > MAX_NESTING:
            raise XmlError("the XPath nests too deeply")
        try:
            return self.binary(0)
        finally:
            self.depth -= 1

    _LEVELS = [("or",), ("and",), ("=", "!="), ("<", "<=", ">", ">="), ("+", "-"), ("*", "div", "mod")]

    def binary(self, level: int) -> Callable:
        if level == len(self._LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        while True:
            kind, value = self.peek()
            if value not in self._LEVELS[level] or kind not in ("op", "mul", "opname"):
                return left
            if value == "*" and kind != "mul":
                return left
            self.take()
            right = self.binary(level + 1)
            left = self._operator(value, left, right)

    def _operator(self, op: str, left: Callable, right: Callable) -> Callable:
        if op == "or":
            return lambda n, p, s: _boolean(left(n, p, s)) or _boolean(right(n, p, s))
        if op == "and":
            return lambda n, p, s: _boolean(left(n, p, s)) and _boolean(right(n, p, s))
        if op in ("=", "!=", "<", "<=", ">", ">="):
            def compared(n: Node, p: int, s: int) -> bool:
                a, b = left(n, p, s), right(n, p, s)
                self._visit(max(len(a) if isinstance(a, list) else 1, 1) * max(len(b) if isinstance(b, list) else 1, 1))
                return _compare(op, a, b)
            return compared
        maths = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
                 "div": _divide, "mod": _modulo}[op]
        return lambda n, p, s: maths(_number(left(n, p, s)), _number(right(n, p, s)))

    def unary(self) -> Callable:
        negate = False
        while self.peek() == ("op", "-"):
            self.take()
            negate = not negate
        inner = self.union()
        if negate:
            return lambda n, p, s: -_number(inner(n, p, s))
        return inner

    def union(self) -> Callable:
        left = self.path()
        while self.peek() == ("op", "|"):
            self.take()
            right, before = self.path(), left

            def joined(n: Node, p: int, s: int, a: Callable = before, b: Callable = right) -> list[Node]:
                x, y = a(n, p, s), b(n, p, s)
                if not isinstance(x, list) or not isinstance(y, list):
                    raise XmlError("| joins two node sets")
                return _sorted(x + y)
            left = joined
        return left

    def path(self) -> Callable:
        kind, value = self.peek()
        if kind == "op" and value in ("/", "//"):
            self.take()
            absolute = True
            if value == "//":
                steps = [self._descendant_or_self()] + self.relative()
            elif self._step_starts():
                steps = self.relative()
            else:
                steps = []
            return self._run_path(None, steps, absolute)
        if self._step_starts() and not self._is_call():
            return self._run_path(None, self.relative(), False)
        start = self.filter()
        if self.peek() in (("op", "/"), ("op", "//")):
            steps = []
            if self.take()[1] == "//":
                steps.append(self._descendant_or_self())
            steps += self.relative()
            return self._run_path(start, steps, False)
        return start

    def _step_starts(self) -> bool:
        kind, value = self.peek()
        return kind == "name" or (kind == "op" and value in ("@", ".", "..", "*"))

    def _is_call(self) -> bool:
        kind, value = self.peek()
        return kind == "name" and self.peek(1) == ("op", "(") and value not in _NODE_TYPES

    def filter(self) -> Callable:
        kind, value = self.peek()
        if kind == "op" and value == "(":
            self.take()
            inner = self.expr()
            self.expect(")")
            out = inner
        elif kind == "str":
            self.take()
            text = value[1:-1]
            out = lambda n, p, s: text
        elif kind == "num":
            self.take()
            number = float(value)
            out = lambda n, p, s: number
        elif kind == "op" and value == "$":
            raise XmlError("XPath variables ($name) aren't available in xpath()")
        elif kind == "name" and self.peek(1) == ("op", "("):
            out = self.function()
        else:
            raise XmlError(f"unexpected {value!r} in the XPath" if value else "the XPath ends too soon")
        predicates = self.predicates()
        if not predicates:
            return out
        base = out

        def filtered(n: Node, p: int, s: int) -> list[Node]:
            nodes = base(n, p, s)
            if not isinstance(nodes, list):
                raise XmlError("a [predicate] can only follow a node set")
            for pred in predicates:
                nodes = self._apply(pred, nodes)
            return nodes
        return filtered

    def relative(self) -> list[tuple]:
        steps = [self.step()]
        while self.peek() in (("op", "/"), ("op", "//")):
            if self.take()[1] == "//":
                steps.append(self._descendant_or_self())
            steps.append(self.step())
        return steps

    @staticmethod
    def _descendant_or_self() -> tuple:
        return ("descendant-or-self", ("type", "node"), [])

    def step(self) -> tuple:
        kind, value = self.peek()
        if kind == "op" and value == ".":
            self.take()
            return ("self", ("type", "node"), [])
        if kind == "op" and value == "..":
            self.take()
            return ("parent", ("type", "node"), [])
        axis = "child"
        if kind == "op" and value == "@":
            self.take()
            axis = "attribute"
        elif kind == "name" and self.peek(1) == ("op", "::"):
            axis = self.take()[1]
            self.take()
            if axis not in _AXES:
                raise XmlError(f"the XPath axis {axis!r} isn't supported (namespace:: isn't)")
        kind, value = self.take()
        if kind in ("op", "mul") and value == "*":
            test: tuple = ("any",)
        elif kind == "name" and value in _NODE_TYPES and self.peek() == ("op", "("):
            self.take()
            if value == "processing-instruction" and self.peek()[0] == "str":
                self.take()
            self.expect(")")
            test = ("type", value)
        elif kind in ("name", "opname"):
            if ":" in value:
                raise XmlError(f"the name test {value!r} uses a namespace prefix, which xpath() has no way to "
                               "declare; use *[local-name()='...' and namespace-uri()='...'] instead")
            test = ("name", value)
        else:
            raise XmlError(f"expected a step in the XPath but found {value!r}")
        return (axis, test, self.predicates())

    def predicates(self) -> list[Callable]:
        out = []
        while self.peek() == ("op", "["):
            self.take()
            out.append(self.expr())
            self.expect("]")
        return out

    def function(self) -> Callable:
        name = self.take()[1]
        self.expect("(")
        args: list[Callable] = []
        while self.peek() != ("op", ")"):
            args.append(self.expr())
            if self.peek() == ("op", ","):
                self.take()
            elif self.peek() != ("op", ")"):
                raise XmlError(f"expected , or ) in the XPath function {name}()")
        self.expect(")")
        fn = _FUNCTIONS.get(name)
        if fn is None:
            raise XmlError(f"the XPath function {name}() isn't supported in local runs")
        lo, hi, impl = fn
        if not lo <= len(args) <= hi:
            raise XmlError(f"the XPath function {name}() was given {len(args)} argument(s)")
        def called(n: Node, p: int, s: int) -> Value:
            out = impl(self, n, p, s, args)
            if isinstance(out, str):
                if len(out) > MAX_STRING:
                    raise XmlError(f"the XPath function {name}() made text of more than {MAX_STRING:,} characters, "
                                   "the limit in xpath()")
                self._visit(1 + len(out) // 256)
            return out
        return called

    # evaluating

    def _visit(self, count: int = 1) -> None:
        self.visits += count
        if self.visits > MAX_VISITS:
            raise XmlError(f"the XPath does more than {MAX_VISITS:,} steps of work, the limit for one xpath()")
        if time.monotonic() > self.deadline:
            raise XmlError(f"the XPath ran longer than {MAX_SECONDS:g} seconds, the limit for one xpath()")

    def _run_path(self, start: Callable | None, steps: list[tuple], absolute: bool) -> Callable:
        def run(n: Node, p: int, s: int) -> list[Node]:
            if start is not None:
                nodes = start(n, p, s)
                if not isinstance(nodes, list):
                    raise XmlError("a / path can only follow a node set")
            elif absolute:
                root = n
                while root.parent is not None:
                    root = root.parent
                nodes = [root]
            else:
                nodes = [n]
            for axis, test, preds in steps:
                found: list[Node] = []
                seen: set[int] = set()
                for node in nodes:
                    picked = [c for c in _axis(axis, node, self) if _matches(test, c, axis)]
                    for pred in preds:
                        picked = self._apply(pred, picked)
                    for c in picked:
                        if id(c) not in seen:
                            seen.add(id(c))
                            found.append(c)
                nodes = _sorted(found)
            return nodes
        return run

    def _apply(self, pred: Callable, nodes: list[Node]) -> list[Node]:
        size, kept = len(nodes), []
        for pos, node in enumerate(nodes, 1):
            self._visit()
            v = pred(node, pos, size)
            if (v == pos) if isinstance(v, float) else _boolean(v):
                kept.append(node)
        return kept


def _axis(axis: str, node: Node, xp: _XPath) -> list[Node]:
    """The nodes on ``axis`` from ``node``, nearest first (so a reverse axis is in reverse order)."""
    if axis == "child":
        out = list(node.children)
    elif axis == "attribute":
        out = list(node.attrs)
    elif axis == "self":
        out = [node]
    elif axis == "parent":
        out = [node.parent] if node.parent is not None else []
    elif axis in ("ancestor", "ancestor-or-self"):
        out, cur = ([node] if axis == "ancestor-or-self" else []), node.parent
        while cur is not None:
            out.append(cur)
            cur = cur.parent
    elif axis in ("descendant", "descendant-or-self"):
        out = [node] if axis == "descendant-or-self" else []
        stack = list(reversed(node.children))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(reversed(c.children))
            if len(out) % 4096 == 0:
                xp._visit(4096)
    elif axis in ("following-sibling", "preceding-sibling"):
        if node.parent is None or node.kind == "attribute":
            out = []
        else:
            sibs = node.parent.children
            i = next(k for k, c in enumerate(sibs) if c is node)
            out = sibs[i + 1:] if axis == "following-sibling" else sibs[:i][::-1]
    else:   # following, preceding: every node after (before) this one in document order, but not its own
        root = node
        while root.parent is not None:
            root = root.parent
        every = _axis("descendant", root, xp)
        anchor = node.parent if node.kind == "attribute" and node.parent is not None else node
        if axis == "following":
            last = _last_descendant(anchor)
            out = [c for c in every if c.order > last.order]
        else:
            ancestors = {id(a) for a in _axis("ancestor", anchor, xp)}
            out = [c for c in every if c.order < anchor.order and id(c) not in ancestors][::-1]
    xp._visit(len(out) or 1)
    return out


def _last_descendant(node: Node) -> Node:
    while node.children:
        node = node.children[-1]
    return node


def _matches(test: tuple, node: Node, axis: str) -> bool:
    principal = "attribute" if axis == "attribute" else "element"
    if test[0] == "any":
        return node.kind == principal
    if test[0] == "name":
        return node.kind == principal and node.name == test[1] and node.uri == ""
    kind = test[1]
    if kind == "node":
        return True
    return node.kind == {"text": "text", "comment": "comment", "processing-instruction": "pi"}[kind]


def _sorted(nodes: list[Node]) -> list[Node]:
    seen: set[int] = set()
    out = []
    for n in sorted(nodes, key=lambda n: n.order):
        if id(n) not in seen:
            seen.add(id(n))
            out.append(n)
    return out


def string_value(node: Node) -> str:
    if node.kind in ("text", "attribute", "comment", "pi"):
        return node.value
    parts, stack = [], [node]
    while stack:
        n = stack.pop()
        if n.kind == "text":
            parts.append(n.value)
        elif n.kind in ("element", "root"):
            stack.extend(reversed(n.children))
    return "".join(parts)


def _string(v: Value) -> str:
    if isinstance(v, list):
        return string_value(v[0]) if v else ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v.is_integer():
            return str(int(v))
        return format(v, "f").rstrip("0") if "e" in repr(v) else repr(v)
    return v


def _number(v: Value) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, float):
        return v
    text = _string(v).strip()
    if re.fullmatch(r"-?(\d+(\.\d*)?|\.\d+)", text):
        return float(text)
    return math.nan


def _boolean(v: Value) -> bool:
    if isinstance(v, list):
        return bool(v)
    if isinstance(v, float):
        return not (v == 0 or math.isnan(v))
    if isinstance(v, str):
        return bool(v)
    return bool(v)


def _divide(a: float, b: float) -> float:
    if b == 0:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1, b)
    return a / b


def _modulo(a: float, b: float) -> float:
    if b == 0 or math.isnan(a) or math.isnan(b) or math.isinf(a):
        return math.nan
    return math.fmod(a, b)


_RELATIONAL = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
               ">=": lambda a, b: a >= b}


def _compare(op: str, a: Value, b: Value) -> bool:
    """XPath 1.0's comparison: a node set compares true when any of its nodes does."""
    if isinstance(a, list) and isinstance(b, list):
        right = [string_value(y) for y in b]
        return any(_compare(op, string_value(x), y) for x in a for y in right)
    if isinstance(a, list) or isinstance(b, list):
        nodes, other, flip = (a, b, False) if isinstance(a, list) else (b, a, True)
        if isinstance(other, bool):
            pair = (_boolean(nodes), other)
            return _compare(op, *(pair[::-1] if flip else pair))
        for n in nodes:
            x: Value = _number(string_value(n)) if isinstance(other, float) else string_value(n)
            if _compare(op, *((other, x) if flip else (x, other))):
                return True
        return False
    if op in ("=", "!="):
        if isinstance(a, bool) or isinstance(b, bool):
            same = _boolean(a) == _boolean(b)
        elif isinstance(a, float) or isinstance(b, float):
            same = _number(a) == _number(b)
        else:
            same = a == b
        return same if op == "=" else not same
    return _RELATIONAL[op](_number(a), _number(b))


def _nodeset(v: Value, fn: str) -> list[Node]:
    if not isinstance(v, list):
        raise XmlError(f"the XPath function {fn}() needs a node set")
    return v


def _first_or_context(xp: _XPath, n: Node, p: int, s: int, args: list[Callable], fn: str) -> Node | None:
    if not args:
        return n
    nodes = _nodeset(args[0](n, p, s), fn)
    return nodes[0] if nodes else None


def _qname(node: Node | None) -> str:
    return node.name if node is not None and node.kind in ("element", "attribute", "pi") else ""


def _substring(text: str, start: float, length: float | None = None) -> str:
    # XPath counts from 1 and rounds; characters at positions p with round(start) <= p < round(start)+round(length)
    begin = _round(start)
    end = math.inf if length is None else begin + _round(length)
    if math.isnan(begin) or math.isnan(end):
        return ""
    return "".join(ch for i, ch in enumerate(text, 1) if begin <= i < end)


def _round(v: float) -> float:
    if math.isnan(v) or math.isinf(v):
        return v
    return float(math.floor(v + 0.5))


def _sum(nodes: list[Node]) -> float:
    return math.fsum(_number(string_value(n)) for n in nodes) if nodes else 0.0


_A = Callable[..., Any]
_FUNCTIONS: dict[str, tuple[int, float, Callable[[_XPath, Node, int, int, list[_A]], Value]]] = {
    "last": (0, 0, lambda xp, n, p, s, a: float(s)),
    "position": (0, 0, lambda xp, n, p, s, a: float(p)),
    "count": (1, 1, lambda xp, n, p, s, a: float(len(_nodeset(a[0](n, p, s), "count")))),
    "local-name": (0, 1, lambda xp, n, p, s, a: (lambda x: x.local if x is not None and _qname(x) else "")(
        _first_or_context(xp, n, p, s, a, "local-name"))),
    "name": (0, 1, lambda xp, n, p, s, a: _qname(_first_or_context(xp, n, p, s, a, "name"))),
    "namespace-uri": (0, 1, lambda xp, n, p, s, a: (lambda x: x.uri if x is not None else "")(
        _first_or_context(xp, n, p, s, a, "namespace-uri"))),
    "string": (0, 1, lambda xp, n, p, s, a: _string(a[0](n, p, s)) if a else string_value(n)),
    "concat": (2, math.inf, lambda xp, n, p, s, a: "".join(_string(f(n, p, s)) for f in a)),
    "starts-with": (2, 2, lambda xp, n, p, s, a: _string(a[0](n, p, s)).startswith(_string(a[1](n, p, s)))),
    "contains": (2, 2, lambda xp, n, p, s, a: _string(a[1](n, p, s)) in _string(a[0](n, p, s))),
    "substring-before": (2, 2, lambda xp, n, p, s, a: (lambda t, x: t[:t.find(x)] if x in t else "")(
        _string(a[0](n, p, s)), _string(a[1](n, p, s)))),
    "substring-after": (2, 2, lambda xp, n, p, s, a: (lambda t, x: t[t.find(x) + len(x):] if x in t else "")(
        _string(a[0](n, p, s)), _string(a[1](n, p, s)))),
    "substring": (2, 3, lambda xp, n, p, s, a: _substring(_string(a[0](n, p, s)), _number(a[1](n, p, s)),
                                                          _number(a[2](n, p, s)) if len(a) > 2 else None)),
    "string-length": (0, 1, lambda xp, n, p, s, a: float(len(_string(a[0](n, p, s)) if a else string_value(n)))),
    "normalize-space": (0, 1, lambda xp, n, p, s, a: " ".join(
        (_string(a[0](n, p, s)) if a else string_value(n)).split())),
    "translate": (3, 3, lambda xp, n, p, s, a: (lambda t, f, r: "".join(
        (r[f.index(ch)] if f.index(ch) < len(r) else "") if ch in f else ch for ch in t))(
        _string(a[0](n, p, s)), _string(a[1](n, p, s)), _string(a[2](n, p, s)))),
    "boolean": (1, 1, lambda xp, n, p, s, a: _boolean(a[0](n, p, s))),
    "not": (1, 1, lambda xp, n, p, s, a: not _boolean(a[0](n, p, s))),
    "true": (0, 0, lambda xp, n, p, s, a: True),
    "false": (0, 0, lambda xp, n, p, s, a: False),
    "number": (0, 1, lambda xp, n, p, s, a: _number(a[0](n, p, s)) if a else _number(string_value(n))),
    "sum": (1, 1, lambda xp, n, p, s, a: _sum(_nodeset(a[0](n, p, s), "sum"))),
    "floor": (1, 1, lambda xp, n, p, s, a: (lambda v: v if math.isnan(v) or math.isinf(v) else float(math.floor(v)))(
        _number(a[0](n, p, s)))),
    "ceiling": (1, 1, lambda xp, n, p, s, a: (lambda v: v if math.isnan(v) or math.isinf(v) else float(math.ceil(v)))(
        _number(a[0](n, p, s)))),
    "round": (1, 1, lambda xp, n, p, s, a: _round(_number(a[0](n, p, s)))),
}


def xpath(document: Any, path: Any) -> Any:
    """xpath(): the XPath's result on the XML. A node set is an array (an element as its XML text, any
    other node as its value); a number, string or boolean comes back as itself."""
    if not isinstance(path, str):
        raise XmlError("xpath() needs the XPath as text")
    root = parse(to_xml(document))
    xp = _XPath(path)
    xp.deadline = time.monotonic() + MAX_SECONDS
    result = xp.run(root, 1, 1)
    if isinstance(result, list):
        # an element, or the document itself (as .NET's OuterXml gives for "/"), as its XML text
        return [outer_xml(n) if n.kind in ("element", "root") else string_value(n) for n in result]
    if isinstance(result, float):
        if math.isnan(result) or math.isinf(result):
            raise XmlError(f"the XPath's result is {_string(result)}, which a pipeline value can't hold")
        return int(result) if result.is_integer() and abs(result) < 2 ** 53 else result
    return result
