"""Large-model timing (OSSIE-012b, scenario 18): how long each semantic stage takes on generated models, as JSON.

A developer tool, run by hand or as an informational CI step; the engine never imports it, and it never goes
online. Times are wall clock on the machine it runs on, so compare them only with a baseline from the same kind
of machine. The tests (``tests/test_ossie_large.py``) count work instead and never time anything.

    cd sidecar
    python tools/ossie_large_timing.py                          # the default sizes
    python tools/ossie_large_timing.py --size 40x50x20 --no-l3  # tables x columns x measures per table
    python tools/ossie_large_timing.py --repeat 3               # the fastest of three runs per stage

Each size is a model from ``tests/ossie_large.py`` (relationships between the tables, one role, one calculation
group), as a ``model.bim`` and as Ossie 0.2.0.dev0. The stages: ``to_tmsl`` (Ossie to TMSL), ``fidelity`` (the
fidelity report of that conversion), ``export`` (TMSL to Ossie with its fidelity report), ``export_validated``
(the same with the round-trip check, as Export runs it), and L1, L2 and L3 parity of the ``model.bim`` against the
Ossie file. L3 runs at most ``MAX_MEASURES_RUN`` measures a side on DuckDB.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SIDECAR = Path(__file__).resolve().parents[1]
for _p in (SIDECAR, SIDECAR / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ossie_large as ol  # noqa: E402  # the generator beside the tests
from fabric_desktop import model_ossie as mo  # noqa: E402
from fabric_desktop import semantic_parity as sp  # noqa: E402
from fabric_desktop import semantic_parity_l3 as l3  # noqa: E402

DEFAULT_SIZES = ("10x50x20", "20x50x20", "40x50x20")
STAGES = ("generate", "to_tmsl", "fidelity", "export", "export_validated", "l1", "l2", "l3")


def size(text: str) -> tuple[int, int, int]:
    """``NxMxK`` as (tables, columns per table, measures per table)."""
    parts = text.lower().split("x")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise argparse.ArgumentTypeError(f"a size is tables x columns x measures, such as 10x50x20, not {text!r}")
    tables, columns, measures = (int(p) for p in parts)
    if tables < 1 or columns < 2:
        raise argparse.ArgumentTypeError("a size has at least one table and two columns per table")
    return tables, columns, measures


def _versions() -> dict[str, str]:
    import duckdb
    import sqlglot
    import yaml
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__, "pyyaml": yaml.__version__,
            "converter": mo.CONVERTER["commit"][:7]}


def _timed(fn: Callable[[], Any], repeat: int) -> tuple[float, Any]:
    best, out = float("inf"), None
    for _ in range(repeat):
        start = time.perf_counter()
        out = fn()
        best = min(best, time.perf_counter() - start)
    return round(best, 3), out


def measure(tables: int, columns: int, measures: int, repeat: int = 1, with_l3: bool = True) -> dict[str, Any]:
    """One size: seconds per stage (the fastest of ``repeat`` runs), object counts and each stage's result."""
    seconds: dict[str, float] = {}
    seconds["generate"], (bim, text) = _timed(
        lambda: (ol.bim(tables, columns, measures), ol.ossie(tables, columns, measures)), repeat)
    doc = mo.parse(text)
    seconds["to_tmsl"], (back, report) = _timed(lambda: mo.to_tmsl(doc), repeat)
    seconds["fidelity"], fid = _timed(lambda: mo.safe_fidelity(doc, back, report), repeat)
    seconds["export"], _ = _timed(lambda: mo.export(bim, validate=False), repeat)
    seconds["export_validated"], exported = _timed(lambda: mo.export(bim, validate=True), repeat)
    results: dict[str, Any] = {"fidelity_status": fid["status"],
                               "round_trip": exported["fidelity"]["validation"]["passed"]
                               if exported["fidelity"]["validation"]["ran"]
                               else exported["fidelity"]["validation"]["reason"]}
    levels: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("l1", lambda: sp.parity_l1(bim, text)), ("l2", lambda: sp.parity_l2(bim, text))]
    if with_l3:
        levels.append(("l3", lambda: l3.parity_l3(bim, text)))
    for name, fn in levels:
        seconds[name], parity = _timed(fn, repeat)
        results[name] = {"summary": parity["summary"], "rows": len(parity["objects"]),
                         "counts": {k: v for k, v in parity["counts"].items() if v}}
    return {"size": {"tables": tables, "columns_per_table": columns, "measures_per_table": measures},
            "objects": ol.counts(tables, columns, measures), "yaml_chars": len(text),
            "seconds": seconds, "results": results}


def baseline(sizes: list[tuple[int, int, int]], repeat: int = 1, with_l3: bool = True) -> dict[str, Any]:
    """The whole baseline: the machine, the versions and one entry per size."""
    return {"tool": "sidecar/tools/ossie_large_timing.py", "seed": ol.SEED, "repeat": repeat,
            "machine": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
            "versions": _versions(), "max_measures_run_l3": l3.MAX_MEASURES_RUN,
            "runs": [measure(*s, repeat=repeat, with_l3=with_l3) for s in sizes]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time conversion, fidelity and parity on generated large models "
                                                 "and print the baseline as JSON.")
    parser.add_argument("--size", action="append", type=size,
                        help=f"tables x columns x measures per table (repeatable; default {', '.join(DEFAULT_SIZES)})")
    parser.add_argument("--repeat", type=int, default=1, help="runs per stage; the fastest counts (default 1)")
    parser.add_argument("--no-l3", action="store_true", help="leave out L3 (DuckDB)")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat is at least 1")
    logging.getLogger("ossie_microsoft").addHandler(logging.NullHandler())  # its warnings are in the report already
    sizes = args.size or [size(s) for s in DEFAULT_SIZES]
    print(json.dumps(baseline(sizes, args.repeat, not args.no_l3), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
