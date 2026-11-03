"""Academy exercise workspaces for DuckDB exercises (the ``duckdb_lab`` adapter): start, run, validate,
hint, explanation and reset, on this PC.

A workspace lives in ``<data>/academy/<pack>/<exercise>/``: ``lab.duckdb`` built from the exercise's
setup, ``answer.sql`` (the learner's work, which starts as the starter) and ``state.json`` (hints used,
whether the explanation was opened, the last result). The reference solution and planted wrong answers
are never written there: validation reads them from the pack's ``grading/`` folder in the engine.

The learner's SQL runs exactly as a Learn lab answer does (learn/labs.py, reviewed by Security for
LX-03): a connection with no file or network access, a memory limit and locked settings, a transaction
that is always rolled back, BEGIN/COMMIT/ROLLBACK refused, and a time limit. Reset removes only the
workspace folder, which is always a folder under ``<data>/academy/<pack>``.

Learner code may one day write in the workspace (ACD-005), so every file the engine reads or writes
there goes through academy/workspace.py: the path stays inside the folder and no part of it may be a
link, junction or hard link; reads are size-capped; writes go to a temporary file in the folder and
replace the target, which is checked first.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import duckdb

from .. import jsonfile
from ..learn import checks
from ..learn import labs as learn_labs
from . import exercises
from . import workspace as ws

DB_FILE = "lab.duckdb"
ANSWER_FILE = "answer.sql"
STATE_FILE = "state.json"


MAX_ANSWER_BYTES = 1024 * 1024


def workspace(root: Path, pack_id: str, exercise_id: str) -> Path:
    """The exercise's folder under ``<root>/academy/<pack>``; refuses anything that isn't a known exercise."""
    exercises.get(pack_id, exercise_id)   # raises for an unknown pack or exercise id
    return ws.workspace_dir(Path(root), pack_id, exercise_id)


def _exists(folder: Path, name: str) -> bool:
    """Whether ``name`` is in the workspace (refused if any part of it is a link)."""
    if not folder.is_dir():
        return False
    return ws.inside(folder, name, must_exist=False).exists()


def _read_text(folder: Path, name: str) -> str:
    return ws.read_bytes(folder, name, MAX_ANSWER_BYTES).decode("utf-8", errors="replace")


def _write(folder: Path, name: str, data: str) -> None:
    """Write ``name`` in the workspace: a temporary file in the folder, then replace the checked target."""
    target = ws.inside(folder, name, must_exist=False)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(data)
        jsonfile.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_state(folder: Path, st: dict[str, Any]) -> None:
    _write(folder, STATE_FILE, json.dumps(st, indent=2))


def _duckdb_exercise(pack_id: str, exercise_id: str) -> dict[str, Any]:
    ex = exercises.get(pack_id, exercise_id)
    if ex["adapter"] != "duckdb_lab":
        raise ValueError(f"{exercise_id} doesn't run in a DuckDB lab")
    return ex


def _state(folder: Path) -> dict[str, Any]:
    if not _exists(folder, STATE_FILE):
        return {}
    st = ws.read_json(folder, STATE_FILE)
    return dict(st) if isinstance(st, dict) else {}


def start(root: Path, pack_id: str, exercise_id: str) -> dict[str, Any]:
    """Make the workspace if it isn't there yet and return its state; an existing one is left as it is,
    so work survives an app restart."""
    ex = _duckdb_exercise(pack_id, exercise_id)
    folder = workspace(root, pack_id, exercise_id)
    if _exists(folder, STATE_FILE):
        return state(root, pack_id, exercise_id)
    folder.mkdir(parents=True, exist_ok=True)
    ref = exercises.reference(pack_id, exercise_id)
    con = duckdb.connect(_db_path(folder, must_exist=False))
    try:
        if ref["setup"]:
            con.execute(ref["setup"])
    finally:
        con.close()
    if not _exists(folder, ANSWER_FILE):
        _write(folder, ANSWER_FILE, ref["starter"])
    _write_state(folder, {"pack": pack_id, "exercise": exercise_id, "version": ex["version"],
                                              "hints_used": 0, "explanation_seen": False, "last": None})
    return state(root, pack_id, exercise_id)


def state(root: Path, pack_id: str, exercise_id: str) -> dict[str, Any]:
    folder = workspace(root, pack_id, exercise_id)
    st = _state(folder)
    if not st:
        return {"pack": pack_id, "exercise": exercise_id, "started": False}
    return {"pack": pack_id, "exercise": exercise_id, "started": True, "version": st.get("version"),
            "answer": _read_text(folder, ANSWER_FILE) if _exists(folder, ANSWER_FILE) else "",
            "hints_used": int(st.get("hints_used") or 0), "explanation_seen": bool(st.get("explanation_seen")),
            "last": st.get("last")}


def _started(root: Path, pack_id: str, exercise_id: str) -> Path:
    folder = workspace(root, pack_id, exercise_id)
    if not _exists(folder, STATE_FILE):
        raise ValueError(f"Start {exercise_id} first")
    return folder


def save(root: Path, pack_id: str, exercise_id: str, text: str) -> None:
    _duckdb_exercise(pack_id, exercise_id)
    text = str(text)
    if len(text.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError("That answer is over 1 MB; an exercise answer is much smaller.")
    _write(_started(root, pack_id, exercise_id), ANSWER_FILE, text)


def run(root: Path, pack_id: str, exercise_id: str, sql: str, limit: int = 200) -> dict[str, Any]:
    """The first ``limit`` rows of the learner's SQL, run in the workspace and rolled back."""
    _duckdb_exercise(pack_id, exercise_id)
    folder = _started(root, pack_id, exercise_id)
    limit = min(max(1, int(limit)), 1000)
    con = _connect(folder)
    try:
        checks.guard(con, sql)
        con.begin()
        try:
            with learn_labs._time_limit(con):
                try:
                    cur = con.execute(sql)
                    cols = [d[0] for d in cur.description or []]
                    rows = cur.fetchmany(limit + 1) if cols else []
                except duckdb.InterruptException as exc:
                    raise ValueError(learn_labs._stopped()) from exc
        finally:
            con.rollback()
    finally:
        con.close()
    return {"columns": cols, "rows": [[learn_labs._cell(v) for v in r] for r in rows[:limit]], "more": len(rows) > limit}


def _db_path(folder: Path, must_exist: bool = True) -> str:
    """lab.duckdb, checked, and its write-ahead log beside it: DuckDB opens that file too, so it must
    not be a link either (refused by inside()) and must be a plain file when it's there."""
    db = ws.inside(folder, DB_FILE, must_exist=must_exist)
    wal = ws.inside(folder, DB_FILE + ".wal", must_exist=False)
    if os.path.lexists(wal) and not wal.is_file():
        raise ws.Refused(f"{DB_FILE}.wal isn't a plain file; reset the exercise.")
    return str(db)


def _connect(folder: Path) -> duckdb.DuckDBPyConnection:
    """The workspace database with no file or network access, a memory limit and locked settings: the
    same connection Learn labs use."""
    con = duckdb.connect(_db_path(folder))
    con.execute("SET enable_external_access = false")
    con.execute(f"SET memory_limit = '{learn_labs.MEMORY_LIMIT}'")
    con.execute("SET lock_configuration = true")
    return con


def _fresh(setup: str | None) -> duckdb.DuckDBPyConnection:
    """A new in-memory database built from the exercise's setup, locked down like ``_connect``. Validation
    runs here, never on ``lab.duckdb``, so a stale or edited workspace can't make a wrong answer pass."""
    con = duckdb.connect(":memory:")
    if setup:
        con.execute(setup)
    con.execute("SET enable_external_access = false")
    con.execute(f"SET memory_limit = '{learn_labs.MEMORY_LIMIT}'")
    con.execute("SET lock_configuration = true")
    return con


def validate(root: Path, pack_id: str, exercise_id: str, answer: str | None = None) -> dict[str, Any]:
    """Run the exercise's validation on the learner's answer (the saved one when ``answer`` is None), in a
    fresh copy of the exercise's data rather than the workspace database.
    Returns the results, the verification label (what a pass proves and doesn't), and the hints and
    explanation use the evidence row needs."""
    ex = _duckdb_exercise(pack_id, exercise_id)
    folder = _started(root, pack_id, exercise_id)
    if answer is None:
        answer = _read_text(folder, ANSWER_FILE)
    elif len(answer.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError("That answer is over 1 MB; an exercise answer is much smaller.")
    ref = exercises.reference(pack_id, exercise_id)
    solution = ref["solution"]
    results = []
    con = _fresh(ref["setup"])
    try:
        for c in ex["validation"]:
            with learn_labs._time_limit(con) as stop:
                r = exercises.run_check(con, c, answer, solution)
            r = {**r, "passed": False, "detail": learn_labs._stopped()} if stop.fired else r
            message = c.get("pass") if r["passed"] else c.get("fail")
            results.append({**r, "message": message} if message else r)
    finally:
        con.close()
    st = _state(folder)
    passed = all(r["passed"] for r in results)
    st["last"] = {"passed": passed, "checks": [{"kind": r["kind"], "passed": r["passed"]} for r in results]}
    _write_state(folder, st)
    return {"pack": pack_id, "exercise": exercise_id, "version": ex["version"], "passed": passed, "results": results,
            "verification": exercises.label(ex), "hints_used": int(st.get("hints_used") or 0),
            "explanation_seen": bool(st.get("explanation_seen"))}


def hint(root: Path, pack_id: str, exercise_id: str, level: int) -> dict[str, Any]:
    """Hint ``level`` (1 first), from a nudge to nearly the answer. The highest level shown is kept, so a
    pass knows how much help it had."""
    hints = exercises.get(pack_id, exercise_id).get("hints") or []
    if not hints:
        return {"level": 0, "of": 0, "text": "This exercise has no hints."}
    level = min(max(1, int(level)), len(hints))
    folder = _started(root, pack_id, exercise_id)
    st = _state(folder)
    st["hints_used"] = max(int(st.get("hints_used") or 0), level)
    _write_state(folder, st)
    return {"level": level, "of": len(hints), "text": hints[level - 1]}


def explanation(root: Path, pack_id: str, exercise_id: str) -> dict[str, Any]:
    """The correct approach and why. Opening it before a pass means that pass doesn't count as independent."""
    ex = exercises.get(pack_id, exercise_id)
    folder = _started(root, pack_id, exercise_id)
    st = _state(folder)
    if not (st.get("last") or {}).get("passed"):
        st["explanation_seen"] = True
        _write_state(folder, st)
    return {"text": ex["explanation"], "remediation": list(ex.get("remediation") or [])}


def reset(root: Path, pack_id: str, exercise_id: str) -> dict[str, Any]:
    """Throw the workspace away, answer included, and start it again."""
    folder = workspace(root, pack_id, exercise_id)
    if os.path.lexists(folder):
        ws.inside(folder, ".")   # refused when the folder itself is a link or junction
        shutil.rmtree(folder)
    return start(root, pack_id, exercise_id)

