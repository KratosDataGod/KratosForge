"""Environment profiles: which Python and packages a notebook needs, and where it will find them.

For each code cell, the modules it imports are found by parsing it (not by running it). Each
module is then classified for both places a notebook can run:

* **This PC**: the engine's own Python. A module is ``available`` (with the installed version),
  ``standard`` (Python's standard library), ``provided`` (Fabric Desktop supplies it:
  ``fabric_desktop``, ``notebookutils``) or ``missing``.
* **Fabric (Python notebook)**: Microsoft doesn't publish the package versions of the Python
  notebook runtime. A module is ``standard``, ``provided``, ``documented`` (Microsoft's page names
  it as preinstalled, version unknown), ``observed`` (a previous run in Fabric reported it, with
  the version and when), or ``unknown``. Never "available" on a guess.

An import inside ``try: ... except ImportError`` (the cell has a fallback) or inside a function
(needed only if it's called) is optional and doesn't make the notebook fail the check. A cell that doesn't parse, or imports through ``importlib``/``__import__``/``%pip``, is reported
as having imports that can't be known before it runs.

``runtime()`` describes the Python running right now (version, platform, every installed
distribution); the proof runtime records the same in Fabric, so a comparison can show exactly
where the two environments differ.
"""

from __future__ import annotations

import ast
import importlib.metadata as md
import importlib.util
import platform
import re
import sys
from typing import Any

from . import magics

# Named as preinstalled in Microsoft's "Use Python experience on Notebook" page (checked
# 2026-10-16): DuckDB, Polars, Scikit-learn and delta-rs (the ``deltalake`` package). No versions
# are published. Kernels offered: Python 3.10, 3.11, 3.12 (3.12 is Fabric's default).
FABRIC_DOCUMENTED = {"duckdb": "duckdb", "polars": "polars", "sklearn": "scikit-learn", "deltalake": "deltalake"}
FABRIC_DOCS = "https://learn.microsoft.com/en-us/fabric/data-engineering/using-python-experience-on-notebook"
# Fabric Desktop publishes Python notebooks with this kernel (fabric_run.PYTHON_NOTEBOOK_METADATA).
FABRIC_PYTHON_KERNEL = "3.11"
# Modules a notebook gets from Fabric Desktop on this PC and from Fabric itself in Fabric.
PROVIDED = {"fabric_desktop": "KratosForge's lakehouse module (added to the published notebook in Fabric)",
            "notebookutils": "Fabric's notebook utilities (KratosForge's version on this PC)",
            "mssparkutils": "Fabric's notebook utilities (KratosForge's version on this PC)"}
# Spark exists on both sides, as different engines: DuckDB's PySpark API here, Fabric's Spark there.
SPARK = {"pyspark"}
_DYNAMIC = re.compile(r"\b(importlib\.import_module|__import__)\s*\(|^\s*%(pip|conda)\b|^\s*!\s*pip\b|"
                      r"[\"']pip[\"']\s*,\s*[\"']install[\"']", re.M)


def _cell_python(source: str) -> str | None:
    """The Python in a cell, or None for a cell that isn't Python (SQL, HTML, Scala...)."""
    head = source.lstrip()
    if head.startswith(("%%sql", "%%html", "%%spark", "%%sparkr", "%%csharp", "%%configure")):
        return None
    if re.match(r"%%(pyspark|python)\b", head):
        head = head.split("\n", 1)[1] if "\n" in head else ""
    # Line magics aren't Python; blank them out, keeping line numbers.
    return magics.blank("\n".join("" if re.match(r"^\s*(!|%%)", ln) else ln for ln in head.split("\n")))


def imports(source: str) -> dict[str, Any]:
    """Top-level modules a cell imports, with the line of each. ``parsed`` is False when the
    cell isn't valid Python; ``dynamic`` lists lines that import in ways parsing can't follow."""
    code = _cell_python(source)
    if code is None:
        return {"parsed": True, "modules": [], "dynamic": []}
    dynamic = [source.count("\n", 0, m.start()) + 1 for m in _DYNAMIC.finditer(source)]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"parsed": False, "modules": [], "dynamic": dynamic, "error": f"line {exc.lineno}: {exc.msg}"}
    found: dict[str, dict[str, Any]] = {}

    def visit(node: ast.AST, optional: bool) -> None:
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for n in names:
            top = n.split(".")[0]
            seen = found.setdefault(top, {"module": top, "line": node.lineno, "optional": optional})
            seen["optional"] = seen["optional"] and optional  # required anywhere means required
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for child in ast.iter_child_nodes(node):
                visit(child, True)  # needed only if the function is called
            return
        if isinstance(node, ast.Try) and _catches_import_error(node):
            for child in node.body:
                visit(child, True)
            for child in node.handlers + node.orelse + node.finalbody:
                visit(child, optional)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, optional)
    visit(tree, False)
    return {"parsed": True, "modules": sorted(found.values(), key=lambda m: m["line"]), "dynamic": dynamic}


def _catches_import_error(node: ast.Try) -> bool:
    """An import inside ``try: ... except ImportError:`` is optional: the cell has a fallback."""
    for h in node.handlers:
        types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        for t in types:
            if t is None or (isinstance(t, ast.Name) and t.id in ("ImportError", "ModuleNotFoundError", "Exception")):
                return True
    return False


def _distribution(module: str) -> tuple[str | None, str | None]:
    """The installed distribution that provides ``module``, and its version."""
    try:
        dists = md.packages_distributions().get(module) or []
    except Exception:  # noqa: BLE001 - broken metadata in one package shouldn't stop the check
        dists = []
    for d in dists:
        try:
            return d, md.version(d)
        except md.PackageNotFoundError:
            continue
    try:
        return module, md.version(module)
    except md.PackageNotFoundError:
        return None, None


def _standard(module: str) -> bool:
    return module in sys.stdlib_module_names or module in ("__future__",)


def on_this_pc(module: str) -> dict[str, Any]:
    if module in PROVIDED:
        return {"status": "provided", "text": PROVIDED[module]}
    if _standard(module):
        return {"status": "standard"}
    if module in SPARK:
        return {"status": "provided", "text": "On this PC, spark is DuckDB's PySpark API"}
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return {"status": "missing", "text": f"{module} isn't installed in the engine on this PC"}
    dist, version = _distribution(module)
    return {"status": "available", "distribution": dist, "version": version}


def in_fabric(module: str, observed: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Where ``module`` stands in a Fabric Python notebook. ``observed`` is what earlier runs in
    Fabric reported: {module: {"version", "seen_at", "python"}}."""
    if module in PROVIDED:
        return {"status": "provided", "text": PROVIDED[module]}
    if _standard(module):
        return {"status": "standard"}
    if module in SPARK:
        return {"status": "provided", "text": "Fabric's Spark (the notebook is published as a Spark notebook)"}
    seen = (observed or {}).get(module)
    if seen:
        return {"status": "observed", "version": seen.get("version"), "seen_at": seen.get("seen_at"),
                "text": f"Seen in a run in Fabric on {str(seen.get('seen_at', ''))[:10]}"}
    if module in FABRIC_DOCUMENTED:
        return {"status": "documented", "text": "Microsoft lists it as preinstalled; the version isn't published",
                "source": FABRIC_DOCS}
    return {"status": "unknown", "text": "Not known until it runs in Fabric. Install it in the notebook with %pip if it's missing."}


def check(cells: list[dict[str, Any]], observed: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The notebook's dependencies, cell by cell, and a summary for each place it can run."""
    out_cells, modules = [], {}
    unparsed, dynamic = [], []
    for n, c in enumerate(cells, 1):
        if c.get("type") == "markdown" or not (c.get("source") or "").strip():
            continue
        found = imports(c["source"])
        row = {"cell": n, "cell_id": c.get("cell_id"), "imports": []}
        if not found["parsed"]:
            unparsed.append({"cell": n, "cell_id": c.get("cell_id"), "text": found.get("error", "")})
        for line in found["dynamic"]:
            dynamic.append({"cell": n, "cell_id": c.get("cell_id"), "line": line})
        for m in found["modules"]:
            entry = modules.setdefault(m["module"], {"module": m["module"], "pc": on_this_pc(m["module"]),
                                                      "fabric": in_fabric(m["module"], observed), "cells": [],
                                                      "optional": True})
            entry["cells"].append(n)
            entry["optional"] = entry["optional"] and m["optional"]
            row["imports"].append({"module": m["module"], "line": m["line"]})
        out_cells.append(row)
    mods = sorted(modules.values(), key=lambda e: e["module"].lower())
    missing = [e["module"] for e in mods if e["pc"]["status"] == "missing" and not e["optional"]]
    unknown = [e["module"] for e in mods if e["fabric"]["status"] == "unknown" and not e["optional"]]
    return {
        "modules": mods, "cells": out_cells, "unparsed": unparsed, "dynamic": dynamic,
        "pc": {"ok": not missing and not unparsed, "missing": missing,
               "python": platform.python_version()},
        "fabric": {"ok": not unparsed, "unknown": unknown, "python": FABRIC_PYTHON_KERNEL,
                   "note": "Fabric's Python notebook package versions are known only from runs there."},
    }


def runtime(modules: list[str] | None = None) -> dict[str, Any]:
    """The Python running now: version, implementation, platform and installed distributions
    (all of them, or only those providing ``modules``)."""
    packages: dict[str, str] = {}
    if modules is None:
        for d in md.distributions():
            name = d.metadata.get("Name")
            if name:
                packages[name.lower()] = d.version
    else:
        for m in modules:
            dist, version = _distribution(m)
            if dist and version:
                packages[dist.lower()] = version
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.system(), "machine": platform.machine(), "packages": dict(sorted(packages.items()))}


def differences(a: dict[str, Any], b: dict[str, Any], names: tuple[str, str] = ("This PC", "Fabric")) -> list[dict[str, Any]]:
    """How two runtimes (from ``runtime``) differ: Python, then each package present in either."""
    rows = []
    if a.get("python") != b.get("python"):
        rows.append({"what": "Python", names[0]: a.get("python"), names[1]: b.get("python")})
    pa_, pb = a.get("packages") or {}, b.get("packages") or {}
    for k in sorted(set(pa_) & set(pb)):
        if pa_.get(k) != pb.get(k):
            rows.append({"what": k, names[0]: pa_.get(k), names[1]: pb.get(k)})
    return rows
