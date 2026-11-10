"""The ``kf_learner`` package: the only KratosForge code a learner's PySpark exercise process can import
(DBA-003, Security read 2026-11-03).

The learner process gets neither ``fabric_desktop`` nor the engine's sign-in packages on its path. What
``spark`` needs from KratosForge (DuckDB's PySpark API gap fillers, the Spark SQL translation they use,
and the Academy's Delta reader and writer) is copied into a separate top-level package, built from the
engine's own source files so it never drifts: ``build()`` writes it, ``SOURCES`` lists what goes in,
and the only change made to a file is a parent-package import made relative (``REWRITES``).

``ALLOWED_IMPORTS`` is everything those files may import at the top level. ``import_problems()`` reads
each source and lists any other import, so a new dependency (say, the lakehouse module) fails CI
before it can reach a learner's process (tests/test_academy_pyspark_lab.py).
"""

from __future__ import annotations

import ast
from pathlib import Path

from .. import jsonfile

PACKAGE = "kf_learner"
_HERE = Path(__file__).resolve().parent
_ENGINE = _HERE.parent
SOURCES: dict[str, Path] = {
    "spark_compat.py": _ENGINE / "spark_compat.py",
    "sql.py": _ENGINE / "sql.py",
    "duckdb_rows.py": _ENGINE / "duckdb_rows.py",
    "spark_builtins_sql.py": _ENGINE / "spark_builtins_sql.py",
    "sql_dialects.py": _ENGINE / "sql_dialects.py",
    "spark_decimals.py": _ENGINE / "spark_decimals.py",
    "spark_hash.py": _ENGINE / "spark_hash.py",
    "spark_number_format.py": _ENGINE / "spark_number_format.py",
    "spark_struct_fields.py": _ENGINE / "spark_struct_fields.py",
    "notebook_dag.py": _ENGINE / "notebook_dag.py",
    "pyspark_shim.py": _HERE / "pyspark_shim.py",
    "pyspark_stream.py": _HERE / "pyspark_stream.py",
    "pyspark_delta.py": _HERE / "pyspark_delta.py",
    "notebookutils_lab.py": _HERE / "notebookutils_lab.py",
}
REWRITES = {"pyspark_shim.py": (("from .. import spark_compat", "from . import spark_compat"),
                                ("from ..duckdb_rows import", "from .duckdb_rows import")),
            "notebookutils_lab.py": (("from .. import notebook_dag", "from . import notebook_dag"),)}
# Top-level packages the copied files may import, besides the standard library and each other.
ALLOWED_IMPORTS = ("duckdb", "sqlglot", "pyarrow", "deltalake", "typing_extensions")
# Imported lazily, inside functions a learner's exercise never reaches; absent from kf_learner, so they fail.
LAZY_ENGINE_ONLY = ("lakehouse",)


def text(name: str) -> str:
    """One file of the package, as ``build()`` writes it."""
    out = SOURCES[name].read_text(encoding="utf-8")
    for old, new in REWRITES.get(name, ()):
        if old not in out:
            raise ValueError(f"{name}: expected {old!r} to rewrite")
        out = out.replace(old, new)
    return out


def build(dest: Path) -> Path:
    """Write ``dest/kf_learner/`` and return ``dest`` (the folder to put first on the learner's path).
    Files are written whole and renamed into place, so a half-written package is never imported."""
    pkg = Path(dest) / PACKAGE
    pkg.mkdir(parents=True, exist_ok=True)
    files = {"__init__.py": '"""KratosForge code a learner exercise may import (generated; see academy/learner_pkg.py)."""\n',
             **{name: text(name) for name in SOURCES}}
    for name, body in files.items():
        jsonfile.write_text(pkg / name, body)
    return Path(dest)


def _stdlib(name: str) -> bool:
    import sys
    return name in sys.stdlib_module_names or name == "__future__"


def import_problems() -> list[str]:
    """Every import in the package's files that isn't the standard library, an allowed package, another
    file of the package, or a lazy engine-only import inside a function."""
    local = {Path(n).stem for n in SOURCES}
    out = []
    for name in SOURCES:
        tree = ast.parse(text(name))
        top = set(tree.body)
        for node in ast.walk(tree):
            mods: list[tuple[str, int]] = []
            if isinstance(node, ast.Import):
                mods = [(a.name, 0) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    names = [node.module] if node.module else [a.name for a in node.names]
                    mods = [(n, node.level) for n in names]
                else:
                    mods = [(node.module or "", 0)]
            for mod, level in mods:
                root = mod.split(".")[0]
                if level:
                    if level > 1:
                        out.append(f"{name}: imports from a parent package ({mod})")
                    elif root not in local and not (root in LAZY_ENGINE_ONLY and node not in top):
                        out.append(f"{name}: imports {mod}, which isn't in {PACKAGE}")
                elif not (_stdlib(root) or root in ALLOWED_IMPORTS):
                    out.append(f"{name}: imports {mod}, which a learner's process doesn't get")
    return out
