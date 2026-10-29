"""Learn labs (LX-03): start, check, hint and reset a module's exercises on this PC.

A lab lives in ``<data>/learn/<module id>/``: ``lab.duckdb`` with the module's generated datasets and
setup tables, ``lab.json`` (the dataset manifest with its seeded defects, and the content version),
and ``answers/<exercise id>.sql``, the learner's work, which starts as the exercise's starter.

Labs are local by construction. This module imports nothing that talks to Fabric or OneLake, the
lab's DuckDB connection is opened without file or network access, and the learner's SQL runs in a
transaction that is rolled back, so checking an answer never changes the lab. Reset removes the
lab's folder (only ever a folder under ``<data>/learn``) and starts it again.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import duckdb

from .. import jsonfile
from . import checks, content, datasets

LAB_FILE = "lab.json"
DB_FILE = "lab.duckdb"


def lab_dir(root: Path, module_id: str) -> Path:
    """The lab's folder under ``<root>/learn``; refuses anything that isn't a module id."""
    if not content.is_id(module_id):
        raise ValueError(f"Not a learning module: {module_id}")
    base = (Path(root) / "learn").resolve()
    folder = (base / module_id).resolve()
    if folder.parent != base:
        raise ValueError(f"Not a learning module: {module_id}")
    return folder


def _connect(folder: Path) -> duckdb.DuckDBPyConnection:
    """The lab's database, with no way to read or write files or the network, and settings locked."""
    con = duckdb.connect(str(folder / DB_FILE))
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def _exercise(module: dict[str, Any], exercise_id: str) -> dict[str, Any]:
    for ex in module["exercises"]:
        if ex["id"] == exercise_id:
            return ex
    raise ValueError(f"{module['id']} has no exercise {exercise_id}")


def start(root: Path, module_id: str, size: str | None = None, seed: int = 7) -> dict[str, Any]:
    """Make the lab if it isn't there yet and return its state. An existing lab is left as it is. The
    datasets' size is the module's own (``size``) unless one is given."""
    module = content.get(module_id)
    size = size or module.get("size") or "S"
    folder = lab_dir(root, module_id)
    if (folder / LAB_FILE).is_file():
        return state(root, module_id)
    folder.mkdir(parents=True, exist_ok=True)
    manifests = []
    con = duckdb.connect(str(folder / DB_FILE))
    try:
        for name in module.get("datasets") or []:
            manifests.append(datasets.generate(con, name, size=size, seed=seed))
        for ex in module["exercises"]:
            if ex.get("setup"):
                con.execute(content.file_text(module_id, ex["setup"]))
    finally:
        con.close()
    answers = folder / "answers"
    answers.mkdir(exist_ok=True)
    for ex in module["exercises"]:
        target = answers / f"{ex['id']}.sql"
        if not target.exists():
            jsonfile.write_text(target, content.file_text(module_id, ex["starter"]) if ex.get("starter") else "")
    jsonfile.write_json(folder / LAB_FILE, {"module": module_id, "version": module["version"], "size": size,
                                            "seed": seed, "datasets": manifests})
    return state(root, module_id)


def state(root: Path, module_id: str) -> dict[str, Any]:
    folder = lab_dir(root, module_id)
    lab = jsonfile.read_json(folder / LAB_FILE, None)
    if lab is None:
        return {"module": module_id, "started": False}
    return {"module": module_id, "started": True, "folder": str(folder), "version": lab["version"],
            "size": lab["size"], "seed": lab["seed"],
            "answers": {p.stem: p.read_text(encoding="utf-8") for p in sorted((folder / "answers").glob("*.sql"))}}


def save_answer(root: Path, module_id: str, exercise_id: str, text: str) -> None:
    _exercise(content.get(module_id), exercise_id)
    jsonfile.write_text(lab_dir(root, module_id) / "answers" / f"{exercise_id}.sql", text)


def reset(root: Path, module_id: str, size: str | None = None, seed: int = 7) -> dict[str, Any]:
    """Throw the lab away, answers included, and start it again."""
    folder = lab_dir(root, module_id)
    if folder.exists():
        shutil.rmtree(folder)
    return start(root, module_id, size=size, seed=seed)


def hint(module_id: str, exercise_id: str, level: int) -> dict[str, Any]:
    """Hint ``level`` (1 first). Hints go from a nudge to nearly the answer, one at a time."""
    hints = _exercise(content.get(module_id), exercise_id).get("hints") or []
    if not hints:
        return {"level": 0, "of": 0, "text": "This exercise has no hints."}
    level = min(max(1, int(level)), len(hints))
    return {"level": level, "of": len(hints), "text": hints[level - 1]}


def check(root: Path, module_id: str, exercise_id: str, answer: str | None = None) -> dict[str, Any]:
    """Run the exercise's checks on the learner's answer (the saved one when ``answer`` is None)."""
    module = content.get(module_id)
    ex = _exercise(module, exercise_id)
    folder = lab_dir(root, module_id)
    lab = jsonfile.read_json(folder / LAB_FILE, None)
    if lab is None:
        raise ValueError(f"Start {module_id} first")
    if answer is None:
        answer = (folder / "answers" / f"{exercise_id}.sql").read_text(encoding="utf-8")
    defects = [d for m in lab["datasets"] for d in m["defects"]]
    reference = content.file_text(module_id, ex["solution"])
    results = []
    con = _connect(folder)
    try:
        for c in ex["checks"]:
            results.append(_run(con, c, answer, reference, ex, module_id, defects))
    finally:
        con.close()
    return {"module": module_id, "exercise": exercise_id, "version": module["version"],
            "passed": all(r["passed"] for r in results), "results": results}


def _run(con: duckdb.DuckDBPyConnection, c: dict[str, Any], answer: str, reference: str, ex: dict[str, Any],
         module_id: str, defects: list[dict[str, Any]]) -> dict[str, Any]:
    kind = c["kind"]
    if kind == "result_matches":
        return checks.result_matches(con, answer, reference=reference, expected=ex.get("expected"),
                                     ordered=bool(c.get("ordered")))
    if kind == "no_findings":
        return checks.no_findings(answer, list(c.get("rules") or []))
    if kind == "faster_than":
        return checks.faster_than(con, answer, content.file_text(module_id, c["baseline"]),
                                  factor=float(c.get("factor", 2.0)), runs=int(c.get("runs", 3)))
    if kind == "defects_found":
        return checks.defects_found(con, answer, defects, kinds=c.get("kinds"))
    if kind in ("table_shape", "integrity"):
        # These look at tables the answer makes: run it in a transaction, check, then roll back.
        con.begin()
        try:
            con.execute(answer)
            if kind == "table_shape":
                return checks.table_shape(con, c["table"], c.get("columns"), c.get("unique"), c.get("not_null"))
            return checks.integrity(con, c["child"], c["column"], c["parent"], c.get("parent_column"))
        except duckdb.Error as exc:
            return {"kind": kind, "passed": False, "detail": f"Your SQL didn't run: {exc}"}
        finally:
            con.rollback()
    return {"kind": kind, "passed": False, "detail": f"This app can't check {kind} yet."}
