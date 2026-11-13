"""PySpark exercises (the ``pyspark_lab`` adapter, DBA-003): prepare the Delta tables, run a PySpark
answer, and grade the tables it leaves.

A workspace holds ``tables/<schema>/<table>/`` Delta tables built from the exercise's ``setup.sql``
(run in an in-memory DuckDB, one Delta table per table in a non-``main`` schema) and ``answer.py``.
An exercise can also give the answer other notebooks to run with notebookutils (``notebooks``: ``.py``
files copied to ``notebooks/``) and data files (``files``: copied to ``Files/``, or to
``Files/<files_folder>/``), for orchestration and streaming exercises; every run gets the same ones.
The answer runs in its own process with ``spark`` from the generated ``kf_learner`` package
(learner_pkg.py, pyspark_shim.py): DuckDB's PySpark API on this PC, not Apache Spark.

Grading reads what the answer left, never how it was written, so any correct solution passes:

    result_matches   the output table holds the reference solution's rows (``data``: ``practice``, the
                     setup the learner sees, or ``hidden``, grading/<id>/hidden.sql, which they never see)
    table_shape      columns and types, unique and not-null keys (learn/checks.py)
    idempotent       running the answer a second time leaves the same rows

The answer's process wrote those tables, so the engine reads one only after checking it: its Delta log
through validators.check_log and each live data file through workspace.inside (``_table``). A table that
points outside itself, goes through a link or has a checkpoint fails its checks, in words.

**Who runs what.** A learner's answer runs only through the Academy learner process (``run_learner``,
sandbox.py): no sign-in, no network from Python, time and memory limits, writes only in its workspace.
The reference solution and the planted wrong answers are the pack's own files; ``self_test`` runs them
in a plain ``python -I`` process with the same package, the way CI proves an exercise is real.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import deltalake
import duckdb

from .. import jsonfile
from ..learn import checks
from . import exercises, learner_pkg, registry, validators
from . import workspace as ws
from .pyspark_shim import TABLES, table_path

ANSWER_FILE = "answer.py"
CHECKS = ("result_matches", "table_shape", "idempotent")
RUN_SECONDS = 120
# What the engine reads back from a table an answer left: the checked live files' size on disk (not
# what the log says) and the rows. An exercise table is far smaller.
MAX_TABLE_BYTES = 256 * 1024 * 1024
MAX_TABLE_ROWS = checks.MAX_ROWS
# Any failure on hidden data says only this: the learner's error text could print the hidden rows.
HIDDEN_FAILED = "On data you haven't seen, your code didn't finish. Check it handles rows unlike the exercise's."
Runner = Callable[[Path, str], dict[str, Any]]

# The process's first code: put kf_learner first on the path, make spark, run answer.py, exit at once
# (so no interpreter shutdown can hang on a worker thread after the answer finished). On Windows it ends
# with TerminateProcess, not os._exit: os._exit is ExitProcess, which stops delta-rs's and DuckDB's worker
# threads wherever they are and then runs each library's shutdown code, and that code can crash on what
# a stopped thread left behind. C1's reference crashed that way on Windows CI three times (access
# violation 0xC0000005 with faulthandler's frame on the os._exit line, after the answer had finished);
# sql_worker._end_now ends the same way for the same reason. Nothing is lost: output is flushed first.
BOOTSTRAP = """
import faulthandler, os, sys, traceback
faulthandler.enable()   # a crash in native code (DuckDB, delta-rs) still leaves a trace on stderr
sys.path.insert(0, sys.argv[1])
from kf_learner import pyspark_shim
spark = pyspark_shim.session(sys.argv[2])
code = open(os.path.join(sys.argv[2], "answer.py"), encoding="utf-8").read()
try:
    exec(compile(code, "answer.py", "exec"), pyspark_shim.namespace(spark))
    rc = 0
except pyspark_shim.NotebookExit:   # notebookutils.notebook.exit ends the notebook, not as an error
    rc = 0
except BaseException:
    traceback.print_exc(limit=3)
    rc = 1
sys.stdout.flush(); sys.stderr.flush()
if sys.platform == "win32":
    import ctypes
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    k.TerminateProcess(k.GetCurrentProcess(), rc)
os._exit(rc)   # elsewhere, or if TerminateProcess returned
"""


def prepare(work: Path, setup_sql: str, assets: dict[str, bytes] | None = None) -> list[str]:
    """Build the exercise's Delta tables under ``work/tables`` from ``setup_sql`` and write its
    ``assets`` (``{"notebooks/clean.py": b"..."}``); returns the tables' names."""
    for rel, blob in (assets or {}).items():
        (Path(work) / rel).parent.mkdir(parents=True, exist_ok=True)
        target = ws.inside(work, rel, must_exist=False)   # its folders exist now, so none of them is a link
        if os.path.lexists(target) and not target.is_file():
            raise ws.Refused(f"{rel} isn't a plain file; reset the exercise.")
        jsonfile.write_bytes(target, blob)
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


_FOLDER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$")


def asset_problems(ex: dict[str, Any]) -> list[str]:
    """What's wrong with an exercise's ``notebooks``, ``files`` and ``files_folder``, in words."""
    out = []
    notebooks, files = ex.get("notebooks") or [], ex.get("files") or []
    if not isinstance(notebooks, list) or not isinstance(files, list):
        return ["notebooks and files are lists of file names in the exercise's folder"]
    out += [f"notebook {n!r} is a .py file named like a notebook (clean_orders.py)" for n in notebooks
            if not (isinstance(n, str) and n.endswith(".py") and _FOLDER.match(n[:-3]))]
    folder = ex.get("files_folder")
    if folder is not None and not (isinstance(folder, str) and all(_FOLDER.match(p) for p in folder.split("/"))):
        out.append("files_folder is a folder under Files/, like landing or landing/orders")
    return out


def assets(ex: dict[str, Any], folder: Path) -> dict[str, bytes]:
    """The exercise's notebooks and data files, by where they go in the workspace."""
    out = {f"notebooks/{n}": (folder / n).read_bytes() for n in ex.get("notebooks") or []}
    sub = [p for p in str(ex.get("files_folder") or "").split("/") if p]
    out.update({"/".join(["Files", *sub, n]): (folder / n).read_bytes() for n in ex.get("files") or []})
    return out


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
    jsonfile.write_text(Path(work) / ANSWER_FILE, code)
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "TEMP", "TMP", "PATH")} | {"PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run([sys.executable, "-I", "-c", BOOTSTRAP, str(_package_dir()), str(work)], cwd=work,
                           env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=RUN_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "time_limit", "stdout": "", "stderr": ""}
    return {"status": "ok" if p.returncode == 0 else "error", "exit_code": p.returncode, "stdout": p.stdout[-4000:],
            "stderr": p.stderr[-4000:]}


# The learner's own first code, run by the Academy learner process (academy/sandbox.py), which puts
# kf_learner first on the path as a read-only library folder and starts in the workspace.
LEARNER_SCRIPT = """
import os, sys, traceback
from kf_learner import pyspark_shim
spark = pyspark_shim.session(os.getcwd())
code = open("answer.py", encoding="utf-8").read()
try:
    exec(compile(code, "answer.py", "exec"), pyspark_shim.namespace(spark))
    rc = 0
except pyspark_shim.NotebookExit:   # notebookutils.notebook.exit ends the notebook, not as an error
    rc = 0
except BaseException:
    traceback.print_exc(limit=3)
    rc = 1
sys.stdout.flush(); sys.stderr.flush()
os._exit(rc)
"""
LEARNER_FILE = "kf_run.py"


def _put(work: Path, name: str, text: str) -> None:
    """Write ``name`` in the workspace; refused if it (or the way to it) is a link the answer left."""
    path = ws.inside(work, name, must_exist=False)
    if os.path.lexists(path) and not path.is_file():
        raise ws.Refused(f"{name} isn't a plain file; reset the exercise.")
    jsonfile.write_text(path, text)


def run_learner(work: Path, code: str, data_root: Path | None = None) -> dict[str, Any]:
    """Run a learner's answer in the Academy learner process (academy/sandbox.py): its own process with
    time, memory and CPU limits, no network from Python, the app's packages unimportable and writes only
    in ``work``. ``data_root``: the app's data folder, denied to the learner outside ``work`` (default:
    work's parent). Returns once the process (and on Windows its job) has ended."""
    from . import sandbox   # it starts processes; only learner runs need it
    ws.inside(work, ".")   # refused if the exercise folder itself is a link, before resolving follows it
    # sandbox.run compares paths with resolved ones, so pass resolved paths (on Windows a temp folder can
    # come as an 8.3 short name and resolve to its long one).
    work = Path(work).resolve()
    _put(work, ANSWER_FILE, code)
    _put(work, LEARNER_FILE, LEARNER_SCRIPT)
    root = Path(data_root).resolve() if data_root else work.parent
    # The shim reads and writes Delta through delta-rs, which an AppContainer can't use (Chris,
    # 2026-11-04: Delta exercises run the v1 way), so PySpark exercises never ask for one.
    r = sandbox.run(work, LEARNER_FILE, root, time_limit=RUN_SECONDS, lib_roots=[_package_dir().resolve()],
                    appcontainer_ok=False)
    return {"status": r["status"], "exit_code": r.get("exit_code"), "stdout": r["stdout"], "stderr": r["stderr"]}


def _table(work: Path, name: str) -> Any:
    """Table ``name`` as the learner's code left it (a pyarrow Table), or None if it wasn't written.

    The answer's process wrote these files, so they are read only once checked: the Delta log through
    validators.check_log (every commit read as data, no entry pointing outside the table, no checkpoint)
    and every live data file through workspace.inside (inside the exercise folder, no link on the way).
    A table that fails either is refused (workspace.Refused, the reason in words for the learner)."""
    path = table_path(work, name)   # refuses a name that isn't schema.table or table
    rel = "/".join([TABLES, *name.lower().split(".")])
    try:
        log = ws.inside(work, f"{rel}/_delta_log")
    except ws.Refused:
        if not os.path.lexists(path / "_delta_log"):
            return None
        raise
    if not log.is_dir():
        return None
    size = 0
    for file in validators._table_facts(validators.check_log(work, rel))["files"]:
        live = ws.inside(work, f"{rel}/{file}")
        if not live.is_file():
            raise ws.Refused(f"{name}'s Delta log lists a data file that isn't there.")
        size += os.stat(live).st_size
        if size > MAX_TABLE_BYTES:
            raise ws.Refused(f"{name} is bigger than {MAX_TABLE_BYTES // (1024 * 1024)} MB; an exercise table is much smaller.")
    table = deltalake.DeltaTable(str(path))
    data = table.to_pyarrow_dataset().head(MAX_TABLE_ROWS + 1)
    if data.num_rows > MAX_TABLE_ROWS:
        raise ws.Refused(f"{name} has more than {MAX_TABLE_ROWS:,} rows; an exercise table has far fewer.")
    return data


def _rows(work: Path, name: str) -> tuple[list[str], list[tuple[Any, ...]]] | None:
    data = _table(work, name)
    if data is None:
        return None
    con = duckdb.connect(":memory:")
    try:
        rel = con.from_arrow(data)
        return list(rel.columns), sorted(rel.fetchall(), key=repr)
    finally:
        con.close()


def _load(con: duckdb.DuckDBPyConnection, work: Path, name: str) -> bool:
    data = _table(work, name)
    if data is None:
        return False
    con.register("kf_out", data)
    schema, _, bare = name.rpartition(".")   # table_path checked both parts are plain names
    if schema:
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    con.execute(f'CREATE TABLE {".".join(chr(34) + p + chr(34) for p in ([schema] if schema else []) + [bare])} '
                'AS SELECT * FROM kf_out')
    con.unregister("kf_out")
    return True


def _error(run: dict[str, Any]) -> str:
    if run["status"] == "time_limit":
        return f"Your code was stopped after {RUN_SECONDS} seconds."
    if run["status"] == "memory_limit":
        return "Your code used more memory than the exercise allows and was stopped."
    if run["status"] == "refused":
        return "Your code wasn't run: the app couldn't start it inside its limits."
    lines = [ln.strip() for ln in (run.get("stderr") or "").splitlines() if ln.strip()]
    code = run.get("exit_code")
    shown = _exit_code(code)
    # faulthandler writes a native crash's stack to stderr; its last line is a frame or module list,
    # so the useful line is the header ("Fatal Python error: ..." / "Windows fatal exception: ...").
    fatal = next((ln for ln in lines if ln.startswith(("Fatal Python error:", "Windows fatal exception:"))), None)
    if fatal:
        return f"Your code crashed: {fatal}" + (f" ({shown})." if shown else ".")
    if lines:
        return f"Your code stopped with an error: {lines[-1]}"
    if shown:
        # A process killed by a native crash may leave no message at all; its exit code is the only clue.
        return f"Your code stopped without an error message ({shown})."
    return "Your code stopped with an error: no message"


_FRAME = re.compile(r'^File "(?P<path>[^"]+)", line (?P<line>\d+) in (?P<func>\S+)$')
_SEP = re.compile(r"[\\/]")   # the dump names Windows or POSIX paths; only the file name is kept
CRASH_FRAMES = 4


def _crash_frames(run: dict[str, Any]) -> str:
    """Where a native crash happened, from faulthandler's dump of the crashing thread: its innermost
    Python frames as ``file:line in function``, the call that went into DuckDB or delta-rs first. Only
    for the pack's own reference solution (never a learner's answer): the frames name files and
    functions, never data. Empty when the run didn't crash in native code."""
    lines = [ln.strip() for ln in (run.get("stderr") or "").splitlines()]
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(("Fatal Python error:", "Windows fatal exception:")))
    except StopIteration:
        return ""
    # With other Python threads alive the dump lists each; the crashing one is "Current thread".
    start = next((i for i, ln in enumerate(lines) if i > start and ln.startswith("Current thread")), start)
    frames: list[str] = []
    for ln in lines[start + 1:]:
        if ln.startswith(("Thread 0x", "Extension modules:")):
            break   # the next thread's stack, or the module list: the crashing thread's ends here
        if (m := _FRAME.match(ln)) and len(frames) < CRASH_FRAMES:
            frames.append(f"{_SEP.split(m['path'])[-1]}:{m['line']} in {m['func']}")
    return f" Innermost frames: {'; '.join(frames)}." if frames else ""


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _exit_code(code: Any) -> str:
    """How a non-zero exit code reads: POSIX reports a signal as a negative code, Windows an NTSTATUS
    such as 0xC0000005 (an access violation) as a large unsigned one."""
    if not isinstance(code, int) or code == 0:
        return ""
    if code < 0:
        return f"signal {-code}"
    return f"exit code 0x{code:08X}" if code > 255 else f"exit code {code}"


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
          runner: Runner, files: dict[str, bytes] | None = None) -> dict[str, Any]:
    """Run ``answer`` (and ``solution`` for the expected rows) on fresh copies of the exercise's tables
    (and its notebooks and data ``files``, ``assets()``) and apply ``ex["validation"]``. Each check says
    what it found; ``score`` is the share that passed."""
    with tempfile.TemporaryDirectory(prefix="kf-academy-") as tmp:
        base = Path(tmp)

        def fresh(tag: str, sql: str) -> Path:
            w = base / tag
            w.mkdir()
            prepare(w, sql, files)
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
            try:
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
                            results.append(_check_result(kind, False, HIDDEN_FAILED))
                            continue
                        got = _rows(hidden_run["work"], table)
                    else:
                        got = outputs.setdefault(table, _rows(practice, table))
                    key = (data, table)
                    if key not in refs:
                        rw = fresh(f"ref-{data}-{table.replace('.', '-')}", hidden if data == "hidden" and hidden else setup)
                        rrun = runner(rw, solution)
                        if rrun["status"] != "ok":
                            raise RuntimeError(f"{ex['id']}: the reference solution failed: {_error(rrun)}{_crash_frames(rrun)}")
                        refs[key] = _rows(rw, table)
                        if refs[key] is None:
                            raise RuntimeError(f"{ex['id']}: the reference solution didn't write {table}")
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
                        results.append(_check_result(kind, False, "Run a second time, " + _lower_first(_error(again))))
                    elif before != after:
                        n0, n1 = len(before[1]) if before else 0, len(after[1]) if after else 0
                        results.append(_check_result(kind, False, f"Running your code again changed {table} ({n0:,} rows, then {n1:,}). "
                                                                  "A rerun of a load should leave the same table."))
                    else:
                        results.append(_check_result(kind, True, f"Running your code again leaves {table} as it was."))
            except ws.Refused as exc:   # the answer left a table the engine won't read
                results.append(_check_result(kind, False, str(exc)))
    passed = sum(r["passed"] for r in results)
    return {"passed": passed == len(results), "score": round(passed / len(results), 2) if results else 0.0,
            "results": results}


def reference(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    ex = exercises.get(pack_id, exercise_id, root)
    grading = root / pack_id / "grading" / exercise_id
    hidden = grading / "hidden.sql"
    return {"assets": assets(ex, root / pack_id / "exercises" / exercise_id),"setup": exercises.file_text(pack_id, exercise_id, ex["setup"], root),
            "starter": exercises.file_text(pack_id, exercise_id, ex["starter"], root) if ex.get("starter") else "",
            "hidden": hidden.read_text(encoding="utf-8") if hidden.is_file() else None,
            "solution": (grading / "solution.py").read_text(encoding="utf-8"),
            "wrong": {p.name: p.read_text(encoding="utf-8") for p in sorted(grading.glob("wrong-*.py"))}}


def self_test(pack_id: str, exercise_id: str, root: Path = registry.ROOT) -> dict[str, Any]:
    """The reference solution must pass and every planted wrong answer must fail."""
    ex = exercises.get(pack_id, exercise_id, root)
    ref = reference(pack_id, exercise_id, root)
    run = lambda answer: grade(ex, ref["setup"], ref["hidden"], answer, ref["solution"], run_reference,  # noqa: E731
                               ref["assets"])
    return {"solution": run(ref["solution"]), "wrong": {n: run(code) for n, code in ref["wrong"].items()}}


def cleanup_package() -> None:
    global _PKG
    if _PKG is not None:
        shutil.rmtree(_PKG, ignore_errors=True)
        _PKG = None
