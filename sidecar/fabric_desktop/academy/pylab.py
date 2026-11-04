"""Python and Delta exercises (ACD-011): the ``python_lab`` and ``delta_lab`` adapters.

The learner's script (Python with Polars, DuckDB, pandas, pyarrow or delta-rs) runs in a learner
process (``sandbox.run``: no sign-in, no network from Python, time and memory limits, writes only in
its workspace). Once that process and its job have ended, the checks below read what it left in the
workspace through the validators (``validators.py``), in the engine. Nothing from the grading
folder is copied to the workspace; a check never imports or runs anything the learner wrote.

An exercise's folder holds the starter script (``starter``, a ``.py`` file) and any data files it
reads (``files``). Its grading folder holds ``solution.py`` and at least two ``wrong-*.py`` that
validation must fail. Checks (``validation``):

    notebook_ran      the script ran to the end without an error (``prints``: text it printed)
    artifact_present  files and folders it made: ``items`` as validators.artifact_present
    config_matches    a JSON file it wrote: ``path`` and ``rules``
    schema_matches    a table file it wrote: ``path``, ``format`` (parquet, csv or json), ``columns``
    data_quality      rules over a table file it wrote: ``path``, ``format``, ``rules``
    delta_table       a Delta table it wrote: ``path`` and the validators.delta_table options
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .. import jsonfile
from . import sandbox, validators
from . import workspace as ws

ADAPTERS = ("python_lab", "delta_lab")
# delta-rs resolves table paths with a call an AppContainer is denied (docs/academy/grading-sandbox.md),
# so these run in the v1 learner process even where the AppContainer is on. Prototype: Chris decides
# between this and waiting for a delta-rs that works inside one.
V1_ONLY_ADAPTERS = ("delta_lab",)


def appcontainer_ok(ex: dict[str, Any]) -> bool:
    """Whether this exercise's script can run in an AppContainer: not one that reads or writes Delta
    tables (a delta_lab exercise, or any Python exercise a delta_table check grades)."""
    if ex.get("adapter") in V1_ONLY_ADAPTERS:
        return False
    return not any(isinstance(c, dict) and c.get("kind") == "delta_table" for c in ex.get("validation") or [])
CHECKS = ("notebook_ran", "artifact_present", "config_matches", "schema_matches", "data_quality", "delta_table")
NEEDS: dict[str, tuple[str, ...]] = {"artifact_present": ("items",), "config_matches": ("path", "rules"),
                                     "schema_matches": ("path", "columns"), "data_quality": ("path", "rules"),
                                     "delta_table": ("path",)}
FORMATS = ("parquet", "csv", "json")
_DELTA_OPTIONS = ("versions_min", "versions_max", "operations", "operations_in_order", "partition_by", "files_min",
                  "files_max", "avg_file_bytes_min", "columns")
_SAID = {"time_limit": "Your script ran past its time limit and was stopped.",
         "memory_limit": "Your script used more memory than the exercise allows and was stopped.",
         "refused": "Your script wasn't run: the app couldn't start it inside its limits."}


def check_problems(c: dict[str, Any]) -> list[str]:
    """What's wrong with one check's fields, in words (for exercises.problems)."""
    kind = c.get("kind")
    out = [f"check {kind} needs {k}" for k in NEEDS.get(str(kind), ()) if c.get(k) in (None, "", [])]
    if kind in ("schema_matches", "data_quality") and c.get("format", "parquet") not in FORMATS:
        out.append(f"check {kind}'s format is one of {', '.join(FORMATS)}")
    return out


def _ran(run: dict[str, Any], prints: str | None) -> dict[str, Any]:
    kind = "notebook_ran"
    if run["status"] != "ok":
        said = _SAID.get(run["status"])
        if said is None:
            last = [line for line in run["stderr"].strip().splitlines() if line.strip()][-1:] or ["no message"]
            said = f"Your script stopped with an error: {last[0][:300]}"
        return {"kind": kind, "passed": False, "detail": said}
    if prints and prints not in run["stdout"]:
        return {"kind": kind, "passed": False, "detail": f"Your script ran, but it didn't print {prints!r}."}
    return {"kind": kind, "passed": True, "detail": f"Your script ran to the end in {run['seconds']:.1f} s."}


def _table_check(work: Path, c: dict[str, Any]) -> dict[str, Any]:
    kind = c["kind"]
    table = re.sub(r"\W", "_", Path(str(c["path"])).stem, flags=re.ASCII)[:40] or "answer"   # named in details
    table = table if table[0].isalpha() else f"t_{table}"
    con = ws.connect(work)
    try:
        try:
            ws.load_table(con, work, c["path"], table, c.get("format", "parquet"))
        except ws.Refused as exc:
            return {"kind": kind, "passed": False, "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - a file the learner wrote that DuckDB can't read is a fail, in words
            return {"kind": kind, "passed": False, "detail": f"{c['path']} couldn't be read as {c.get('format', 'parquet')}: {str(exc)[:200]}"}
        if kind == "schema_matches":
            return validators.schema_matches(con, table, c["columns"], c.get("key"), bool(c.get("exact")),
                                             bool(c.get("ordered")))
        return validators.data_quality(con, table, c["rules"])
    finally:
        con.close()


def run_check(work: Path, c: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """One check on the workspace after the learner process has ended."""
    kind = c["kind"]
    if kind == "notebook_ran":
        return _ran(run, c.get("prints"))
    if kind == "artifact_present":
        return validators.artifact_present(work, c["items"])
    if kind == "config_matches":
        return validators.config_file_matches(work, c["path"], c["rules"])
    if kind in ("schema_matches", "data_quality"):
        return _table_check(work, c)
    if kind == "delta_table":
        return validators.delta_table(work, c["path"], **{k: c[k] for k in _DELTA_OPTIONS if k in c})
    return {"kind": kind, "passed": False, "detail": f"A Python exercise can't check {kind}."}


def grade(work: Path, script: str, validation: list[dict[str, Any]], data_root: Path,
          lib_roots: list[Path] | None = None, **limits: Any) -> dict[str, Any]:
    """Run ``script`` in the learner process, then ``validation`` on what it left in ``work``.
    ``{passed, results, run}``. When the script didn't finish, only that is reported: checks of a
    half-written workspace would only add noise."""
    run = sandbox.run(work, script, data_root, lib_roots=lib_roots, **limits)   # returns after the job has closed
    ran = _ran(run, None)
    if not ran["passed"]:
        results = [ran]
    else:
        results = [run_check(Path(work), c, run) for c in validation]
    shown = {k: run[k] for k in ("status", "exit_code", "seconds", "limits", "runs_as")}
    shown["stdout"], shown["stderr"] = run["stdout"][-20_000:], run["stderr"][-20_000:]
    return {"passed": bool(results) and all(r["passed"] for r in results), "results": results, "run": shown}


def prepare(work: Path, folder: Path, ex: dict[str, Any], answer: str) -> None:
    """Fill an empty workspace: the exercise's data files, then ``answer`` as its starter script."""
    for name in ex.get("files") or []:
        shutil.copyfile(folder / name, ws.inside(work, name, must_exist=False))
    jsonfile.write_text(ws.inside(work, ex["starter"], must_exist=False), answer)


def self_test(folder: Path, grading: Path, ex: dict[str, Any], lib_roots: list[Path] | None = None) -> dict[str, Any]:
    """The reference solution must pass and every planted wrong answer fail, each in a fresh
    workspace of its own. ``{"solution": result, "wrong": {name: result}}``."""
    def one(source: Path) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="kf-academy-selftest-") as tmp:
            data_root = Path(tmp)
            work = ws.workspace_dir(data_root, "selftest", ex["id"])
            work.mkdir(parents=True)
            prepare(work, folder, ex, source.read_text(encoding="utf-8"))
            return grade(work, ex["starter"], ex["validation"], data_root, lib_roots)

    return {"solution": one(grading / "solution.py"),
            "wrong": {p.name: one(p) for p in sorted(grading.glob("wrong-*.py"))}}
