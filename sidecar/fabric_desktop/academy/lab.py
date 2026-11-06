"""Academy exercise workspaces for SQL exercises (the ``duckdb_lab`` and ``dbsql_lab`` adapters): start,
run, validate, hint, explanation and reset, on this PC. A ``dbsql_lab`` answer is Databricks SQL, translated
to DuckDB SQL before it runs (academy/dbsql_lab.py), in UTC.

A workspace lives in ``<data>/academy/<pack>/<exercise>/``: ``lab.duckdb`` built from the exercise's
setup, ``answer.sql`` (the learner's work, which starts as the starter) and ``state.json`` (hints used,
whether the explanation was opened, the last result). The reference solution and planted wrong answers
are never written there: validation reads them from the pack's ``grading/`` folder in the engine.

The learner's SQL runs exactly as a Learn lab answer does (learn/labs.py, reviewed by Security for
LX-03): a connection with no file or network access, a memory limit and locked settings, a transaction
that is always rolled back, BEGIN/COMMIT/ROLLBACK refused, and a time limit. Reset removes only the
workspace folder, which is always a folder under ``<data>/academy/<pack>``.

Python and Delta exercises (``python_lab``, ``delta_lab``) keep the learner's script (``answer.py``) and
``state.json`` in the exercise folder, out of the learner process's reach. Run copies the script into
``run/``, a workspace the learner process may write in, and runs it there (academy/sandbox.py). Check
builds ``check/`` fresh from the exercise's files and the saved script every time, runs it, and grades
what it leaves (academy/pylab.py), so nothing an earlier run left behind can make an answer pass.

Settings exercises (``config_model``) keep the learner's settings document (the starter, such as
``settings.json``) and ``state.json`` in the exercise folder. Nothing runs: Run lists the settings the
document holds, and Check reads it as data against the exercise's rules (academy/configlab.py). Git
exercises (``git_lab``) keep the learner's session (the starter, such as ``session.git``): Run and Check
each replay it with real Git in a fresh repository built from the exercise's history (academy/gitlab.py).
Database-project exercises (``sqlproj_lab``) keep the learner's change set (such as ``changes.sql``); Run and
Check apply it to the exercise's project as text and build it in memory (academy/sqlprojlab.py). A quiz
(``quiz``) keeps the learner's picks in ``answers.json``; its questions and answer key stay in the pack's
``grading/`` folder and the screen gets the questions only (academy/quizlab.py).

Learner code writes in its workspace (ACD-005), so every file the engine reads or writes
there goes through academy/workspace.py: the path stays inside the folder and no part of it may be a
link, junction or hard link; reads are size-capped; writes go to a temporary file in the folder and
replace the target, which is checked first.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb

from .. import jsonfile
from ..learn import checks
from ..learn import labs as learn_labs
from . import configlab, dbsql_lab, exercises, gitlab, pylab, quizlab, registry, sandbox, sqlprojlab
from . import workspace as ws

DB_FILE = "lab.duckdb"
ANSWER_FILE = "answer.sql"
STATE_FILE = "state.json"
RUN_DIR = "run"        # python_lab: where Run runs the script
CHECK_DIR = "check"    # python_lab: made fresh for every Check
# Adapters with a workspace on this PC (lab.start and the rest).
LAB_ADAPTERS = (*exercises.SQL_ADAPTERS, *pylab.ADAPTERS, configlab.ADAPTER, quizlab.ADAPTER, gitlab.ADAPTER,
                sqlprojlab.ADAPTER)
# Adapters whose answer is the exercise's own starter file (a script, a settings document, a Git session or a
# database project's change set), not answer.sql.
STARTER_ADAPTERS = (*pylab.ADAPTERS, configlab.ADAPTER, gitlab.ADAPTER, sqlprojlab.ADAPTER)


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
    if ex["adapter"] not in exercises.SQL_ADAPTERS:
        raise ValueError(f"{exercise_id} doesn't run in a SQL lab")
    return ex


def _lab_exercise(pack_id: str, exercise_id: str) -> dict[str, Any]:
    ex = exercises.get(pack_id, exercise_id)
    if ex["adapter"] not in LAB_ADAPTERS:
        raise ValueError(f"{exercise_id} doesn't have a workspace on this PC")
    return ex


def _answer_file(ex: dict[str, Any]) -> str:
    if ex["adapter"] == quizlab.ADAPTER:
        return quizlab.ANSWER_FILE
    return str(ex["starter"]) if ex["adapter"] in STARTER_ADAPTERS else ANSWER_FILE


def _fresh_dir(folder: Path, name: str) -> Path:
    """``name`` in the exercise folder, emptied: a link there is refused, never followed."""
    sub = ws.inside(folder, name, must_exist=False)
    if os.path.lexists(sub):
        ws.inside(folder, name)   # refused when it is a link or junction
        shutil.rmtree(sub)
    sub.mkdir()
    return sub


def _exercise_folder(pack_id: str, exercise_id: str) -> Path:
    return registry.ROOT / pack_id / "exercises" / exercise_id


def _grading_folder(pack_id: str, exercise_id: str) -> Path:
    return registry.ROOT / pack_id / "grading" / exercise_id


def _state(folder: Path) -> dict[str, Any]:
    if not _exists(folder, STATE_FILE):
        return {}
    st = ws.read_json(folder, STATE_FILE)
    return dict(st) if isinstance(st, dict) else {}


def start(root: Path, pack_id: str, exercise_id: str) -> dict[str, Any]:
    """Make the workspace if it isn't there yet and return its state; an existing one is left as it is,
    so work survives an app restart."""
    ex = _lab_exercise(pack_id, exercise_id)
    folder = workspace(root, pack_id, exercise_id)
    if _exists(folder, STATE_FILE):
        return state(root, pack_id, exercise_id)
    folder.mkdir(parents=True, exist_ok=True)
    if ex["adapter"] in pylab.ADAPTERS:
        starter = exercises.file_text(pack_id, exercise_id, ex["starter"])
        if not _exists(folder, ex["starter"]):
            _write(folder, ex["starter"], starter)
        pylab.prepare(_fresh_dir(folder, RUN_DIR), _exercise_folder(pack_id, exercise_id), ex, starter)
    elif ex["adapter"] == quizlab.ADAPTER:
        if not _exists(folder, quizlab.ANSWER_FILE):
            _write(folder, quizlab.ANSWER_FILE, "{}")
    elif ex["adapter"] in (configlab.ADAPTER, gitlab.ADAPTER, sqlprojlab.ADAPTER):
        if not _exists(folder, ex["starter"]):
            _write(folder, ex["starter"], exercises.file_text(pack_id, exercise_id, ex["starter"]))
    else:
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
    name = _answer_file(exercises.get(pack_id, exercise_id))
    return {"pack": pack_id, "exercise": exercise_id, "started": True, "version": st.get("version"),
            "answer": _read_text(folder, name) if _exists(folder, name) else "",
            "hints_used": int(st.get("hints_used") or 0), "explanation_seen": bool(st.get("explanation_seen")),
            "last": st.get("last")}


def _started(root: Path, pack_id: str, exercise_id: str) -> Path:
    folder = workspace(root, pack_id, exercise_id)
    if not _exists(folder, STATE_FILE):
        raise ValueError(f"Start {exercise_id} first")
    return folder


def save(root: Path, pack_id: str, exercise_id: str, text: str) -> None:
    ex = _lab_exercise(pack_id, exercise_id)
    text = str(text)
    if len(text.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError("That answer is over 1 MB; an exercise answer is much smaller.")
    _write(_started(root, pack_id, exercise_id), _answer_file(ex), text)


def run(root: Path, pack_id: str, exercise_id: str, sql: str, limit: int = 200) -> dict[str, Any]:
    """The first ``limit`` rows of the learner's SQL, run in the workspace and rolled back. For a Python
    exercise, ``sql`` is the script: it runs in the learner process in ``run/`` (what it wrote there
    stays until Reset), and the result is its status, output and how long it took."""
    adapter = exercises.get(pack_id, exercise_id)["adapter"]
    if adapter in pylab.ADAPTERS:
        return _run_python(root, pack_id, exercise_id, sql)
    if adapter == quizlab.ADAPTER:
        raise ValueError("A quiz has nothing to run; answer the questions and check your work.")
    if adapter == configlab.ADAPTER:   # nothing runs: the settings the checks will read, as rows
        _started(root, pack_id, exercise_id)
        began = time.perf_counter()
        return {**configlab.outline(str(sql), min(max(1, int(limit)), 1000)), "ms": round((time.perf_counter() - began) * 1000)}
    if adapter == gitlab.ADAPTER:   # the session replayed in a fresh repository in run/, removed afterwards
        return _run_git(root, pack_id, exercise_id, str(sql), limit)
    if adapter == sqlprojlab.ADAPTER:   # the project after the change set, built in memory; nothing on disk
        ex = _lab_exercise(pack_id, exercise_id)
        _started(root, pack_id, exercise_id)
        began = time.perf_counter()
        got = sqlprojlab.run(_exercise_folder(pack_id, exercise_id), ex, str(sql), min(max(1, int(limit)), 1000))
        return {**got, "ms": round((time.perf_counter() - began) * 1000)}
    ex = _duckdb_exercise(pack_id, exercise_id)
    folder = _started(root, pack_id, exercise_id)
    limit = min(max(1, int(limit)), 1000)
    translated = ex["adapter"] == "dbsql_lab"
    if translated:
        sql = dbsql_lab.translate(sql)
    con = _connect(folder, zone=dbsql_lab.ZONE if translated else None)
    began = time.perf_counter()
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
    return {"columns": cols, "rows": [[learn_labs._cell(v) for v in r] for r in rows[:limit]], "more": len(rows) > limit,
            "ms": round((time.perf_counter() - began) * 1000)}


def _run_python(root: Path, pack_id: str, exercise_id: str, code: str) -> dict[str, Any]:
    ex = _lab_exercise(pack_id, exercise_id)
    folder = _started(root, pack_id, exercise_id)
    code = str(code)
    if len(code.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError("That script is over 1 MB; an exercise answer is much smaller.")
    work = ws.inside(folder, RUN_DIR, must_exist=False)
    if not work.is_dir():
        work = _fresh_dir(folder, RUN_DIR)
        pylab.prepare(work, _exercise_folder(pack_id, exercise_id), ex, code)
    else:
        jsonfile.write_text(ws.inside(work, ex["starter"], must_exist=False), code)   # refused if it's a link
    got = sandbox.run(work, ex["starter"], Path(root), appcontainer_ok=pylab.appcontainer_ok(ex))
    return {"status": got["status"], "exit_code": got["exit_code"], "stdout": got["stdout"][-20_000:],
            "stderr": got["stderr"][-20_000:], "seconds": got["seconds"], "runs_as": got["runs_as"],
            "ms": round(got["seconds"] * 1000)}


def _run_git(root: Path, pack_id: str, exercise_id: str, text: str, limit: int) -> dict[str, Any]:
    ex = _lab_exercise(pack_id, exercise_id)
    folder = _started(root, pack_id, exercise_id)
    if len(text.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError("That session is over 1 MB; an exercise answer is much smaller.")
    began = time.perf_counter()
    work = _fresh_dir(folder, RUN_DIR)
    try:
        got = gitlab.run(work, _exercise_folder(pack_id, exercise_id), ex, text, min(max(1, int(limit)), 1000))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {**got, "ms": round((time.perf_counter() - began) * 1000)}


def _check_python(root: Path, pack_id: str, exercise_id: str, ex: dict[str, Any], answer: str) -> list[dict[str, Any]]:
    """Every check of a Python exercise on ``answer``: a fresh ``check/`` from the exercise's files, the
    script run there in the learner process, then the checks on what it left. Removed afterwards."""
    folder = workspace(root, pack_id, exercise_id)
    work = _fresh_dir(folder, CHECK_DIR)
    try:
        pylab.prepare(work, _exercise_folder(pack_id, exercise_id), ex, answer)
        got = pylab.grade(work, ex["starter"], ex["validation"], Path(root), appcontainer_ok=pylab.appcontainer_ok(ex))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return _with_messages(ex, got["results"])


def _with_messages(ex: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each result with its check's own ``pass`` or ``fail`` sentence, when the exercise gives one. Results
    that line up one for one with the checks are paired by position, so two checks of the same kind keep
    their own sentences; otherwise by kind."""
    checks_ = ex["validation"]
    paired = len(results) == len(checks_) and all(r["kind"] == c["kind"] for r, c in zip(results, checks_, strict=False))
    by_kind = {c["kind"]: c for c in checks_}
    out = []
    for n, r in enumerate(results):
        c = checks_[n] if paired else by_kind.get(r["kind"], {})
        message = c.get("pass") if r["passed"] else c.get("fail")
        out.append({**r, "message": message} if message else r)
    return out


def _db_path(folder: Path, must_exist: bool = True) -> str:
    """lab.duckdb, checked, and its write-ahead log beside it: DuckDB opens that file too, so it must
    not be a link either (refused by inside()) and must be a plain file when it's there."""
    db = ws.inside(folder, DB_FILE, must_exist=must_exist)
    wal = ws.inside(folder, DB_FILE + ".wal", must_exist=False)
    if os.path.lexists(wal) and not wal.is_file():
        raise ws.Refused(f"{DB_FILE}.wal isn't a plain file; reset the exercise.")
    return str(db)


def _connect(folder: Path, zone: str | None = None) -> duckdb.DuckDBPyConnection:
    """The workspace database with no file or network access, a memory limit and locked settings: the
    same connection Learn labs use. ``zone``: the session time zone (translated SQL runs in UTC)."""
    con = duckdb.connect(_db_path(folder))
    if zone:
        con.execute(f"SET TimeZone = '{zone}'")
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


def _check_duckdb(ex: dict[str, Any], ref: dict[str, Any], answer: str) -> list[dict[str, Any]]:
    """Every check of a duckdb_lab exercise on ``answer``, in a fresh copy of the setup."""
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
    return results


def validate(root: Path, pack_id: str, exercise_id: str, answer: str | None = None) -> dict[str, Any]:
    """Run the exercise's validation on the learner's answer (the saved one when ``answer`` is None), in a
    fresh copy of the exercise's data rather than the workspace database.
    Returns the results, the verification label (what a pass proves and doesn't), and the hints and
    explanation use the evidence row needs."""
    ex = _lab_exercise(pack_id, exercise_id)
    folder = _started(root, pack_id, exercise_id)
    if answer is None:
        answer = _read_text(folder, _answer_file(ex))
    elif len(answer.encode("utf-8")) > MAX_ANSWER_BYTES:
        raise ValueError("That answer is over 1 MB; an exercise answer is much smaller.")
    if ex["adapter"] in pylab.ADAPTERS:
        results = _check_python(root, pack_id, exercise_id, ex, answer)
    elif ex["adapter"] == configlab.ADAPTER:
        results = _with_messages(ex, configlab.grade(answer, ex)["results"])
    elif ex["adapter"] == gitlab.ADAPTER:   # replayed in a fresh repository of its own, outside the workspace
        results = _with_messages(ex, gitlab.grade(answer, _exercise_folder(pack_id, exercise_id), ex)["results"])
    elif ex["adapter"] == sqlprojlab.ADAPTER:   # read as text and built in memory against the reference
        results = _with_messages(ex, sqlprojlab.grade(answer, _exercise_folder(pack_id, exercise_id),
                                                      _grading_folder(pack_id, exercise_id), ex)["results"])
    elif ex["adapter"] == quizlab.ADAPTER:
        questions = quizlab.load(_grading_folder(pack_id, exercise_id))
        results = _with_messages(ex, quizlab.grade(answer, ex, questions)["results"])
    elif ex["adapter"] == "dbsql_lab":
        results = dbsql_lab.check_all(ex, dbsql_lab.reference(pack_id, exercise_id), answer,
                                      connect_to=lambda setup: dbsql_lab.connect(setup, learn_labs.MEMORY_LIMIT),
                                      limit=learn_labs._time_limit, stopped=learn_labs._stopped())
    else:
        results = _check_duckdb(ex, exercises.reference(pack_id, exercise_id), answer)
    st = _state(folder)
    passed = all(r["passed"] for r in results)
    if ex["adapter"] == quizlab.ADAPTER:   # answers changed after a failed check: the pass isn't independent
        picks = quizlab.parse(answer)[0] or {}
        raw = st.get("quiz_checked")
        checked: dict[str, list[str]] = dict(raw) if isinstance(raw, dict) else {}
        if quizlab.changed(questions, checked, picks):
            st["answers_changed"] = True
        if not passed:
            st["quiz_checked"] = {**checked, **{k: v for k, v in picks.items() if v}}
    st["last"] = {"passed": passed, "checks": [{"kind": r["kind"], "passed": r["passed"]} for r in results]}
    _write_state(folder, st)
    return {"pack": pack_id, "exercise": exercise_id, "version": ex["version"], "passed": passed, "results": results,
            "verification": exercises.label(ex), "hints_used": int(st.get("hints_used") or 0),
            "explanation_seen": bool(st.get("explanation_seen")),
            "answers_changed": bool(st.get("answers_changed"))}


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
    out = {"text": ex["explanation"], "remediation": list(ex.get("remediation") or [])}
    if ex["adapter"] == quizlab.ADAPTER:   # each question's answer, why and its page
        out["questions"] = quizlab.review(quizlab.load(_grading_folder(pack_id, exercise_id)))
    return out


def reset(root: Path, pack_id: str, exercise_id: str) -> dict[str, Any]:
    """Throw the workspace away, answer included, and start it again."""
    folder = workspace(root, pack_id, exercise_id)
    if os.path.lexists(folder):
        ws.inside(folder, ".")   # refused when the folder itself is a link or junction
        shutil.rmtree(folder)
    return start(root, pack_id, exercise_id)

