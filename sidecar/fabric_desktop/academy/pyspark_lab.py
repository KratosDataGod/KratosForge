"""PySpark exercises (the ``pyspark_lab`` adapter, DBA-003): prepare the Delta tables, run a PySpark
answer, and grade the tables it leaves.

A workspace holds ``tables/<schema>/<table>/`` Delta tables built from the exercise's ``setup.sql``
(run in an in-memory DuckDB, one Delta table per table in a non-``main`` schema) and ``answer.py``.
The answer runs in its own process with ``spark`` from the generated ``kf_learner`` package
(learner_pkg.py, pyspark_shim.py): DuckDB's PySpark API on this PC, not Apache Spark.

Grading reads what the answer left, never how it was written, so any correct solution passes:

    result_matches   the output table holds the reference solution's rows (``data``: ``practice``, the
                     setup the learner sees, or ``hidden``, grading/<id>/hidden.sql, which they never see)
    table_shape      columns and types, unique and not-null keys (learn/checks.py)
    idempotent       running the answer a second time leaves the same rows

**Who runs what.** A learner's answer runs only through the Academy sandbox (sandbox.py, PR-F): until
it is wired in, ``run_learner`` refuses. The reference solution and the planted wrong answers are the
pack's own files; ``self_test`` runs them in a plain ``python -I`` process with the same package, the
way CI proves an exercise is real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import deltalake
import duckdb

from ..learn import checks
from . import exercises, learner_pkg, registry
from .pyspark_shim import table_path

ANSWER_FILE = "answer.py"
CHECKS = ("result_matches", "table_shape", "idempotent")
RUN_SECONDS = 120
Runner = Callable[[Path, str], dict[str, Any]]

# The process's first code: put kf_learner first on the path, make spark, run answer.py, exit at once
# (os._exit, so no interpreter shutdown can hang on a worker thread after the answer finished).
BOOTSTRAP = """
import os, sys, traceback
sys.path.insert(0, sys.argv[1])
from kf_learner import pyspark_shim
spark = pyspark_shim.session(sys.argv[2])
code = open(os.path.join(sys.argv[2], "answer.py"), encoding="utf-8").read()
try:
    exec(compile(code, "answer.py", "exec"), {"__name__": "__main__", "spark": spark})
    rc = 0
except BaseException:
    traceback.print_exc(limit=3)
    rc = 1
sys.stdout.flush(); sys.stderr.flush()
os._exit(rc)
"""


def prepare(work: Path, setup_sql: str) -> list[str]:
    """Build the exercise's Delta tables under ``work/tables`` from ``setup_sql``; returns their names."""
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET enable_external_access = false")
        con.execute(setup_sql)
        names = [f"{s}.{t}" for s, t in con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() WHERE schema_name <> 'main' ORDER BY ALL").fetchall()]
        for name in names:
            path = table_path(work, name)
            path.mkdir(parents=True, exist_ok=True)
            data = con.sql(f"SELECT * FROM {name}")
            deltalake.write_deltalake(str(path), (getattr(data, "to_arrow_table", None) or data.arrow)(), mode="overwrite")
    finally:
        con.close()
    return names


def _package_dir() -> Path:
    """kf_learner, built once per engine process into a temporary folder."""
    global _PKG
    if _PKG is None or not (_PKG / learner_pkg.PACKAGE).is_dir():
        _PKG = learner_pkg.build(Path(tempfile.mkdtemp(prefix="kf-learner-")))
    return _PKG


_PKG: Path | None = None


def run_reference(work: Path, code: str) -> dict[str, Any]:
    """Run first-party code (a pack's reference solution or planted wrong answer) in a ``python -I`` process
    with an empty environment. Never for a learner's answer: that is ``run_learner``."""
    (Path(work) / ANSWER_FILE).write_text(code, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "TEMP", "TMP", "PATH")}
    try:
        p = subprocess.run([sys.executable, "-I", "-c", BOOTSTRAP, str(_package_dir()), str(work)], cwd=work,
                           env=env, capture_output=True, text=True, timeout=RUN_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "time_limit", "stdout": "", "stderr": ""}
    return {"status": "ok" if p.returncode == 0 else "error", "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:]}


def run_learner(work: Path, code: str) -> dict[str, Any]:
    """A learner's answer runs only in the Academy sandbox (PR-F), which isn't wired in yet."""
    raise NotImplementedError("PySpark exercises run in the Academy sandbox, which isn't in this version yet")


def _rows(work: Path, name: str) -> tuple[list[str], list[tuple[Any, ...]]] | None:
    path = table_path(work, name)
    if not (path / "_delta_log").is_dir():
        return None
    data = deltalake.DeltaTable(str(path)).to_pyarrow_table()
    con = duckdb.connect(":memory:")
    try:
        rel = con.from_arrow(data)
        return list(rel.columns), sorted(rel.fetchall(), key=repr)
    finally:
        con.close()


def _load(con: duckdb.DuckDBPyConnection, work: Path, name: str) -> bool:
    path = table_path(work, name)
    if not (path / "_delta_log").is_dir():
        return False
    con.register("kf_out", deltalake.DeltaTable(str(path)).to_pyarrow_table())
    con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM kf_out')
    con.unregister("kf_out")
    return True


def _error(run: dict[str, Any]) -> str:
    if run["status"] == "time_limit":
        return f"Your code was stopped after {RUN_SECONDS} seconds."
    last = [ln for ln in (run.get("stderr") or "").strip().splitlines() if ln.strip()]
    return f"Your code stopped with an error: {last[-1] if last else 'no message'}"


def _n(count: int, one: str, many: str) -> str:
    return f"{count:,} {one if count == 1 else many}"


def _row_difference(table: str, where: str, got: list[tuple[Any, ...]], want: list[tuple[Any, ...]]) -> str:
    """What is wrong with the rows, in counts only: never the expected values."""
    wrong = sum(1 for r in got if r not in want)
    missing = sum(1 for r in want if r not in got)
    if len(got) != len(want) and not wrong and not missing:
        return (f"{table} {where} has {_n(len(got), 'row', 'rows')} where {len(want):,} are expected: "
                "some rows appear more than once.")
    if len(got) != len(want):
        return (f"{table} {where} has {_n(len(got), 'row', 'rows')} where {len(want):,} are expected; "
                f"{_n(wrong, 'row isn', 'rows aren')}'t right and {_n(missing, 'expected row is', 'expected rows are')} missing.")
    if wrong == 0:
        return f"{table} {where} has the right rows but some appear a different number of times."
    return f"{table} {where} has the right number of rows, but {_n(wrong, 'of them isn', 'of them aren')}'t right."


def _check_result(kind: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": passed, "detail": detail}


def grade(ex: dict[str, Any], setup: str, hidden: str | None, answer: str, solution: str,
          runner: Runner) -> dict[str, Any]:
    """Run ``answer`` (and ``solution`` for the expected rows) on fresh copies of the exercise's tables and
    apply ``ex["validation"]``. Each check says what it found; ``score`` is the share that passed."""
    with tempfile.TemporaryDirectory(prefix="kf-academy-") as tmp:
        base = Path(tmp)

        def fresh(tag: str, sql: str) -> Path:
            w = base / tag
            w.mkdir()
            prepare(w, sql)
            return w

        practice = fresh("answer", setup)
        first = runner(practice, answer)
        results: list[dict[str, Any]] = []
        outputs: dict[str, Any] = {}
        refs: dict[tuple[str, str], Any] = {}
        hidden_run: dict[str, Any] | None = None
        for c in ex["validation"]:
            kind, table = c["kind"], c.get("table")
            if first["status"] != "ok":
                results.append(_check_result(kind, False, _error(first)))
                continue
            if kind == "table_shape":
                con = duckdb.connect(":memory:")
                try:
                    if not _load(con, practice, table):
                        results.append(_check_result(kind, False, f"Your code didn't write {table}."))
                    else:
                        results.append(checks.table_shape(con, table, c.get("columns"), c.get("unique"), c.get("not_null")))
                finally:
                    con.close()
            elif kind == "result_matches":
                data = c.get("data", "practice")
                if data == "hidden":
                    if hidden_run is None:
                        hw = fresh("hidden", hidden or setup)
                        hidden_run = {"run": runner(hw, answer), "work": hw}
                    if hidden_run["run"]["status"] != "ok":
                        results.append(_check_result(kind, False, "On data you haven't seen, " + _error(hidden_run["run"]).lower()))
                        continue
                    got = _rows(hidden_run["work"], table)
                else:
                    got = outputs.setdefault(table, _rows(practice, table))
                key = (data, table)
                if key not in refs:
                    rw = fresh(f"ref-{data}-{table.replace('.', '-')}", hidden if data == "hidden" and hidden else setup)
                    rrun = runner(rw, solution)
                    if rrun["status"] != "ok":
                        raise RuntimeError(f"{ex['id']}: the reference solution failed: {_error(rrun)}")
                    refs[key] = _rows(rw, table)
                want = refs[key]
                where = "on data you haven't seen" if data == "hidden" else "on the exercise data"
                if got is None:
                    results.append(_check_result(kind, False, f"Your code didn't write {table}."))
                elif [x.lower() for x in got[0]] != [x.lower() for x in want[0]]:
                    results.append(_check_result(kind, False, f"{table} has columns {', '.join(got[0])}; it should have "
                                                              f"{', '.join(want[0])}, in that order."))
                elif got[1] != want[1]:
                    results.append(_check_result(kind, False, _row_difference(table, where, got[1], want[1])))
                else:
                    results.append(_check_result(kind, True, f"{table} holds the right rows {where}."))
            elif kind == "idempotent":
                before = outputs.setdefault(table, _rows(practice, table))
                again = runner(practice, answer)
                after = _rows(practice, table)
                outputs[table] = after
                if again["status"] != "ok":
                    results.append(_check_result(kind, False, "Run a second time, " + _error(again).lower()))
                elif before != after:
                    n0, n1 = len(before[1]) if before else 0, len(after[1]) if after else 0
                    results.append(_check_result(kind, False, f"Running your code again changed {table} ({n0:,} rows, then {n1:,}). "
                                                              "A rerun of a load should leave the same table."))
                else:
                    results.append(_check_result(kind, True, f"Running your code again leaves {table} as it was."))
    passed = sum(r["passed"] for r in results)
    return {"passed": passed == len(results), "score": round(passed / len(results), 2) if results else 0.0,
            "results": results}


def reference(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    ex = exercises.get(pack_id, exercise_id, root)
    grading = root / pack_id / "grading" / exercise_id
    hidden = grading / "hidden.sql"
    return {"setup": exercises.file_text(pack_id, exercise_id, ex["setup"], root),
            "starter": exercises.file_text(pack_id, exercise_id, ex["starter"], root) if ex.get("starter") else "",
            "hidden": hidden.read_text(encoding="utf-8") if hidden.is_file() else None,
            "solution": (grading / "solution.py").read_text(encoding="utf-8"),
            "wrong": {p.name: p.read_text(encoding="utf-8") for p in sorted(grading.glob("wrong-*.py"))}}


def self_test(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    """The reference solution must pass and every planted wrong answer must fail."""
    ex = exercises.get(pack_id, exercise_id, root)
    ref = reference(pack_id, exercise_id, root)
    run = lambda answer: grade(ex, ref["setup"], ref["hidden"], answer, ref["solution"], run_reference)  # noqa: E731
    return {"solution": run(ref["solution"]), "wrong": {n: run(code) for n, code in ref["wrong"].items()}}


def cleanup_package() -> None:
    global _PKG
    if _PKG is not None:
        shutil.rmtree(_PKG, ignore_errors=True)
        _PKG = None
