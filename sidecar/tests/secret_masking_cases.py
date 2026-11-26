"""Seeded cases for the secret masking property tests (test_secret_masking_*.py): secret values and
Python expressions that hold them in many shapes, built with stdlib ``random`` so each case can be
reproduced from its seed.

A secret alternates letters from LETTERS with digits, so any four characters of it hold two of those
capitals, which nothing else a view shows (type names, sizes, memory, lowercase filler) is made of:
a piece of the secret found in a view is the secret's, never a coincidence.
"""

from __future__ import annotations

import random
from typing import Any, Iterable

from fabric_desktop import secret_redaction

LETTERS = "QWZXJKVY"
DIGITS = "0123456789"
FILLER = "abcdefghmnoprstu"


def make_secret(rnd: random.Random, accent: bool = False) -> str:
    n = rnd.randint(14, 60)
    chars = [rnd.choice(LETTERS) if i % 2 == 0 else rnd.choice(DIGITS) for i in range(n)]
    if accent:  # a character outside ASCII: its bytes print escaped
        chars.insert(rnd.randint(2, n - 2), "é")
    return "".join(chars)


def filler(rnd: random.Random, n: int | None = None) -> str:
    return "".join(rnd.choice(FILLER) for _ in range(n or rnd.randint(3, 9)))


def forms(secret: str) -> list[str]:
    """How the secret can read in a view: itself, its bytes as printed, and its escaped repr."""
    out = {secret, repr(secret.encode("utf-8"))[2:-1], repr(secret)[1:-1]}
    return sorted(out, key=len, reverse=True)


def pieces(secret: str) -> set[str]:
    """Every part of the secret long enough to be masked (secret_redaction.MIN_CHARS or more)."""
    k = secret_redaction.MIN_CHARS
    return {secret[i:i + k] for i in range(len(secret) - k + 1)}


def strings(value: Any) -> Iterable[str]:
    """Every text inside a view's answer (dict keys too), as it is, without JSON escaping."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, (bytes, bytearray)):
        yield bytes(value).decode("utf-8", errors="replace")
        yield repr(bytes(value))
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            yield from strings(v)
    elif value is not None:
        yield str(value)


def shown(value: Any, secrets: Iterable[str]) -> str | None:
    """The first piece of any secret found in ``value``, or None. Used in assertions as a yes/no: an
    assertion message never prints the secret."""
    texts = list(strings(value))
    for secret in secrets:
        for form in forms(secret):
            if any(form in t for t in texts):
                return f"whole ({len(form)} characters)"
        for piece in pieces(secret):
            if any(piece in t for t in texts):
                return f"a part of {len(piece)} characters"
    return None


# ---- Python source for values that hold a secret (run in a kernel)

# Defined in the kernel before the cases: classes and functions are left out of the Variables pane.
PRELUDE = '''
class Shown:
    def __init__(self, v): self.v = v
    def __repr__(self): return 'Shown(' + str(self.v) + ')'
    def __str__(self): return 'shown ' + str(self.v)
class Quoted:
    def __init__(self, v): self.v = v
    def __repr__(self): return f'Quoted(v={self.v!r}, n=3)'
class Folded(dict):
    def __repr__(self): return 'Folded<' + '|'.join(f'{k}={v}' for k, v in self.items()) + '>'
def _join(a, b):
    return a + b
def _joined(parts):
    return ''.join(parts)
def _chained(v):
    try:
        try:
            raise KeyError(v)
        except KeyError as inner:
            raise RuntimeError('lookup failed: ' + str(inner)) from inner
    except RuntimeError as outer:
        return outer
'''

KINDS = ("plain", "bytes", "bytearray", "longer", "fstring", "split", "split_join", "shown", "quoted",
         "folded", "error", "error_args", "chained", "cut", "cut_tail")
HASHABLE = {"plain", "bytes", "longer", "fstring", "split", "split_join", "shown", "quoted", "error",
            "error_args", "chained", "cut"}


def leaf(rnd: random.Random, name: str, kind: str) -> str:
    """An expression for a value that is, or holds, the secret in variable ``name``."""
    f = repr(filler(rnd))
    if kind == "plain":
        return name
    if kind == "bytes":
        return f"{name}.encode()"
    if kind == "bytearray":
        return f"bytearray({name}.encode())"
    if kind == "longer":
        return f"{f} + ' ' + {name} + ' ' + {f}"
    if kind == "fstring":
        return f"f'<{{{name}}}>'"
    if kind == "split":  # two values joined into the one shown
        k = rnd.randint(1, 10)
        return f"_join({name}[:{k}], {name}[{k}:])"
    if kind == "split_join":
        k = rnd.randint(1, 10)
        return f"_joined([{f}, {name}[:{k}], {name}[{k}:]])"
    if kind == "shown":
        return f"Shown({name})"
    if kind == "quoted":
        return f"Quoted({name})"
    if kind == "folded":
        return f"Folded(a=1, b={name})"
    if kind == "error":
        return f"ValueError({name})"
    if kind == "error_args":
        return f"KeyError({f}, {name}, 7)"
    if kind == "chained":
        return f"_chained({name})"
    if kind == "cut":  # the preview's length limit falls somewhere in or near the secret
        return f"'x' * {rnd.randint(40, 105)} + {name} + 'y' * {rnd.randint(0, 60)}"
    if kind == "cut_tail":
        return f"[{', '.join(str(i) for i in range(rnd.randint(8, 30)))}, {name}]"
    raise ValueError(kind)


WRAPS = ("list", "tuple", "dict_value", "dict_key", "set", "frozenset", "long_list", "long_dict", "long_dict_key",
         "nested_key")


def wrap(rnd: random.Random, expr: str, hashable: bool, kind: str) -> tuple[str, bool]:
    """``expr`` inside one more container; and whether the result is hashable."""
    f = repr(filler(rnd))
    if kind == "list":
        items = [f, str(rnd.randint(0, 99))]
        items.insert(rnd.randint(0, 2), expr)
        return f"[{', '.join(items)}]", False
    if kind == "tuple":
        return (f"({expr},)" if rnd.random() < 0.5 else f"({f}, {expr}, None)"), hashable
    if kind == "dict_value":
        return f"{{{f}: {expr}, 'n': 1}}", False
    if kind == "dict_key" and hashable:
        return f"{{{expr}: {f}}}", False
    if kind == "nested_key" and hashable:
        return f"{{({f}, {expr}): 1}}", False
    if kind == "set" and hashable:
        return f"{{{expr}, {f}}}", False
    if kind == "frozenset" and hashable:
        return f"frozenset([{expr}])", True
    # Over 100 items, so a preview reads only the first 20: the one that holds the secret is past them,
    # and within the first 100, which an opened variable shows.
    if kind == "long_list":
        n = rnd.randint(20, 98)
        return f"[{f}] * {n} + [{expr}] + [{f}] * {101 - n + rnd.randint(0, 30)}", False
    if kind in ("long_dict", "long_dict_key"):
        n = rnd.randint(20, 98)
        item = f"{expr}: {f}" if kind == "long_dict_key" and hashable else f"{f}: {expr}"
        return (f"{{**{{'k%d' % i: i for i in range({n})}}, {item}, "
                f"**{{'m%d' % i: i for i in range({101 - n + rnd.randint(0, 30)})}}}}"), False
    return f"[{expr}]", False  # a key or set member that can't be hashed goes in a list instead


def case(rnd: random.Random, names: list[str], min_depth: int = 0, outer: str | None = None) -> tuple[str, str]:
    """One generated value: (its kind, its expression). ``outer`` is a last container it goes in."""
    name = rnd.choice(names)
    kind = rnd.choice(KINDS)
    expr, hashable = leaf(rnd, name, kind), kind in HASHABLE
    depth = rnd.randint(min_depth, max(min_depth, 5))
    shapes = [rnd.choice(WRAPS) for _ in range(depth)] + ([outer] if outer else [])
    for shape in shapes:
        expr, hashable = wrap(rnd, expr, hashable, shape)
    return f"{kind}/{'/'.join(shapes) or '-'}", expr


def cases(seed: int, names: list[str], n: int, outer: str | None = None) -> list[tuple[str, str]]:
    """``n`` cases; every leaf kind and container appears, and a third are nested three or more deep.
    With ``outer`` (a WRAPS shape such as "long_list"), each case is put in one of those last."""
    rnd = random.Random(seed)
    out = [case(rnd, names, 3 if i % 3 == 0 else 0, outer) for i in range(n)]
    # Each kind on its own, and five deep with a dict key and a set on the way.
    for kind in KINDS:
        expr = leaf(rnd, rnd.choice(names), kind)
        e, h = expr, kind in HASHABLE
        for shape in ("tuple", "frozenset", "dict_key", "list", "dict_value"):
            e, h = wrap(rnd, e, h, shape)
        if outer:
            expr, e = wrap(rnd, expr, kind in HASHABLE, outer)[0], wrap(rnd, e, h, outer)[0]
        out += [(f"{kind}/-", expr), (f"{kind}/tuple/frozenset/dict_key/list/dict_value", e)]
    return out


def fake_vault(secrets: dict[str, str], dbutils: bool = False) -> str:
    """Kernel code making getSecret answer from ``secrets`` (by secret name) in the kernel's own process,
    then reading each into s0, s1, ... (and with ``dbutils`` each again with dbutils.secrets.get into d0,
    d1, ..., which masks it in printed output too). It leaves no helper behind in the namespace."""
    return ("import fabric_desktop.http_session as _h\n"
            f"_vals = {secrets!r}\n"
            "class _Resp:\n"
            "    status_code = 200\n"
            "    def __init__(self, v): self.v = v\n"
            "    def json(self): return {'value': self.v}\n"
            "class _Session:\n"
            "    def get(self, url, *a, **k): return _Resp(_vals[url.rsplit('/', 1)[-1]])\n"
            "_h.session = lambda: _Session()\n"
            + "".join(f"s{i} = notebookutils.credentials.getSecret('kv', {name!r})\n" for i, name in enumerate(secrets))
            + "".join(f"d{i} = dbutils.secrets.get('kv', {name!r})\n" for i, name in enumerate(secrets) if dbutils)
            + "del _h, _Resp, _Session, _vals\n")
