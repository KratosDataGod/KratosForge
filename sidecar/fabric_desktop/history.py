"""IPython's input and output history in a notebook: ``In``, ``Out``, ``_``/``__``/``___``,
``_N``, ``_i``/``_ii``/``_iii``, ``_iN`` and ``%history``.

Only the notebook's own cells are recorded, not the cells of a notebook it runs with ``%run``.
``Out`` keeps the last OUT_KEEP results, so a notebook that shows many large tables doesn't hold
every one of them in memory: no more than the result viewer keeps (frames.MAX_FRAMES)."""
from __future__ import annotations

import re
from typing import Any

from .frames import MAX_FRAMES

OUT_KEEP = MAX_FRAMES  # a result in Out stays in memory; the result viewer keeps no more tables than this
LAST = "__fd_last__"  # what _ was last set to here, so a _ the notebook assigned itself is left alone


def record_input(ns: dict[str, Any], n: int, code: str) -> None:
    """Cell ``n``'s source, before it runs."""
    inputs = ns.get("In")
    if not isinstance(inputs, list):  # the notebook replaced it; start again
        inputs = ns["In"] = [""]
    while len(inputs) < n:
        inputs.append("")
    inputs[n:] = [code]
    # As in IPython, _i is the cell before this one, _ii the one before that.
    ns["_i"], ns["_ii"], ns["_iii"] = (inputs[n - k] if n - k > 0 else "" for k in (1, 2, 3))
    ns[f"_i{n}"] = code


def record_output(ns: dict[str, Any], n: int, value: Any) -> None:
    """Cell ``n``'s result, the value its last line showed; ``_`` moves to ``__`` and ``___``
    unless the notebook assigned ``_`` itself."""
    outputs = ns.get("Out")
    if not isinstance(outputs, dict):
        outputs = ns["Out"] = {}
    outputs[n] = value
    ns[f"_{n}"] = value
    for old in sorted(outputs)[:-OUT_KEEP]:
        del outputs[old]
        ns.pop(f"_{old}", None)
    if "_" in ns and ns["_"] is not ns.get(LAST):
        return  # the notebook set _ itself; IPython leaves _, __ and ___ alone then too
    ns["___"], ns["__"], ns["_"] = ns.get("__", ""), ns.get("_", ""), value
    ns[LAST] = value


def clear_output(ns: dict[str, Any]) -> None:
    """``%reset -f``: results go, the cells' sources stay (as in IPython)."""
    for n in list(ns.get("Out") or {}):
        ns.pop(f"_{n}", None)
    ns["Out"] = {}
    for name in ("_", "__", "___", LAST):
        ns.pop(name, None)


_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")


def command(ns: dict[str, Any], line: str) -> str:
    """``%history [-n] [-o] [-l N] [range ...]``: earlier cells' sources, oldest first.

    A range is ``4`` or ``4-6``; ``-l N`` is the last N cells; ``-n`` numbers them and ``-o``
    adds each cell's result. Without a range it's every cell before this one."""
    inputs: list[str] = ns.get("In") if isinstance(ns.get("In"), list) else [""]
    outputs: dict[int, Any] = ns.get("Out") if isinstance(ns.get("Out"), dict) else {}
    numbered = output = False
    last: int | None = None
    wanted: list[int] = []
    args = line.split()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-n":
            numbered = True
        elif arg == "-o":
            output = numbered = True
        elif arg == "-l":
            if i + 1 >= len(args) or not args[i + 1].isdigit():
                raise ValueError("%history -l needs a number, like: %history -l 5")
            i += 1
            last = int(args[i])
        elif m := _RANGE.match(arg):
            start, end = int(m[1]), int(m[2] or m[1])
            if end < start:
                raise ValueError(f"%history {arg}: the range runs backwards. Use {end}-{start}.")
            wanted += range(start, end + 1)
        else:
            raise ValueError(f"%history {arg} isn't supported here. Use -n, -o, -l N and ranges like 4-6.")
        i += 1
    current = len(inputs) - 1  # this cell, which isn't history yet
    if not wanted:
        wanted = list(range(1, current))
    if last is not None:
        wanted = wanted[-last:] if last else []
    lines: list[str] = []
    for n in wanted:
        if not 0 < n < len(inputs):
            continue
        source = inputs[n].rstrip("\n")
        if numbered:
            first, *rest = source.split("\n")
            lines.append(f"{n:>3}: {first}")
            lines += [f"     {r}" for r in rest]
        else:
            lines.append(source)
        if output and n in outputs:
            lines.append(f"{n:>3}> {outputs[n]!r}")
    return "\n".join(lines)
