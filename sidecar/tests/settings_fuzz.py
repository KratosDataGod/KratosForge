"""Seeded, deterministic mutations of a valid JSON or TOML file, for the settings and project file fuzz
tests (tests/test_settings_files_fuzz.py, tests/test_local_data_projects_fuzz.py). Stdlib only: each
case is reproducible from its seed and index, and no package is added for it."""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from typing import Any

# Values of every JSON type, including ones a hand edit or another program might leave.
ODD_VALUES: list[Any] = [None, True, False, 0, -1, 1.5, 10 ** 30, -(10 ** 30), 1e308, "", "x" * 100_000, "\x00",
                         "é", [], [1, "a"], {}, {"a": {"b": [None]}}, "4GB", "60%", "dpapi:",
                         "dpapi:@@@", float("nan"), float("inf")]


def mutations(valid: dict[str, Any], seed: int, n: int) -> Iterator[tuple[str, bytes]]:
    """``n`` (label, file bytes) cases from ``valid``: fixed byte-level and structural cases first, then
    random field type swaps, deletions, extra keys and byte damage."""
    good = json.dumps(valid, indent=2).encode("utf-8")
    fixed: list[tuple[str, bytes]] = [
        ("empty", b""),
        ("whitespace", b"  \n\t "),
        ("bom", b"\xef\xbb\xbf" + good),
        ("utf16", json.dumps(valid).encode("utf-16")),
        ("invalid utf-8", good[:5] + b"\xff\xfe\xc3" + good[5:]),
        ("nul bytes", good.replace(b",", b",\x00", 1)),
        ("only nul", b"\x00" * 64),
        ("truncated half", good[: len(good) // 2]),
        ("truncated one", good[:-1]),
        ("trailing garbage", good + b"}{"),
        ("two documents", good + b"\n" + good),
        ("top list", b"[1, 2, 3]"),
        ("top string", b'"settings"'),
        ("top number", b"42"),
        ("top null", b"null"),
        ("top true", b"true"),
        ("nan", b'{"a": NaN, "b": Infinity, "c": -Infinity}'),
        ("huge int", b'{"a": ' + b"9" * 5000 + b"}"),
        ("deep list", b"[" * 100_000 + b"]" * 100_000),
        ("deep object", b'{"a":' * 50_000 + b"1" + b"}" * 50_000),
        ("deep unclosed", b"[" * 200_000),
        ("duplicate keys", good[:-1].rstrip() + b',\n  "' + next(iter(valid)).encode() + b'": null\n}' if valid else b"{}"),
        ("lone surrogate", b'{"a": "\\ud800"}'),
        ("long key", b'{"' + b"k" * 200_000 + b'": 1}'),
        ("comment", b"// my settings\n" + good),
        ("single quotes", good.replace(b'"', b"'")),
    ]
    yield from fixed[:n]
    rnd = random.Random(seed)
    keys = list(valid)
    for i in range(max(0, n - len(fixed))):
        kind = rnd.choice(("swap", "drop", "extra", "byte", "nest"))
        data = json.loads(good)
        if kind == "swap" and keys:
            k = rnd.choice(keys)
            data[k] = rnd.choice(ODD_VALUES)
            yield f"{i} swap {k}", _dumps(data)
        elif kind == "drop" and keys:
            for k in rnd.sample(keys, rnd.randint(1, len(keys))):
                data.pop(k, None)
            yield f"{i} drop", _dumps(data)
        elif kind == "extra":
            data[rnd.choice(("zz_new", "__proto__", "schema", "", "\u202e"))] = rnd.choice(ODD_VALUES)
            yield f"{i} extra", _dumps(data)
        elif kind == "nest" and keys:
            k = rnd.choice(keys)
            if isinstance(data[k], dict) and data[k]:
                inner = rnd.choice(list(data[k]))
                data[k][inner] = rnd.choice(ODD_VALUES)
            elif isinstance(data[k], list) and data[k]:
                data[k][0] = rnd.choice(ODD_VALUES)
            else:
                data[k] = [data[k]]
            yield f"{i} nest {k}", _dumps(data)
        else:
            raw = bytearray(good)
            for _ in range(rnd.randint(1, 4)):
                raw[rnd.randrange(len(raw))] = rnd.randrange(256)
            yield f"{i} bytes", bytes(raw)


def _dumps(data: Any) -> bytes:
    return json.dumps(data, allow_nan=True).encode("utf-8")
