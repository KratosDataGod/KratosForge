"""academy/lab.py paths that its first mutation run (batch 432, docs/quality/mutation/batch-432.md) showed
no test checked. The learner process (academy/sandbox.py), the Python grader, Git and the Databricks SQL
checks are replaced here by stand-ins that record what lab.py hands them, so no learner process or Git
starts; DuckDB runs in the test's own process. Nothing reaches the network."""

from __future__ import annotations

import contextlib
import json
import logging
import os

import duckdb
import pytest

from fabric_desktop.academy import configlab, exercises, lab, quizlab

FAB, DBX = "dp-700", "dbx-de-associate"
SQL, CFG, QUIZ, GIT, PROJ, PY, DBSQL, OTHER = "E37", "E01", "E05", "E06", "E08", "E15", "E20", "E19"
MB = 1024 * 1024


@pytest.fixture
def root(tmp_path):
    return tmp_path


def _folder(root, eid, pack=FAB):
    return lab.workspace(root, pack, eid)


def _st(root, eid, pack=FAB):
    return json.loads((_folder(root, eid, pack) / lab.STATE_FILE).read_text(encoding="utf-8"))


# ---- constants and small helpers

def test_names_and_limits():
    assert (lab.DB_FILE, lab.ANSWER_FILE, lab.STATE_FILE, lab.RUN_DIR, lab.CHECK_DIR) == (
        "lab.duckdb", "answer.sql", "state.json", "run", "check")
    assert lab.MAX_ANSWER_BYTES == MB
    assert lab.LAB_ADAPTERS == ("duckdb_lab", "dbsql_lab", "python_lab", "delta_lab", "config_model", "quiz",
                                "git_lab", "sqlproj_lab")
    assert lab.STARTER_ADAPTERS == ("python_lab", "delta_lab", "config_model", "git_lab", "sqlproj_lab")


def test_answer_file_by_adapter():
    assert lab._answer_file({"adapter": "quiz", "starter": "x"}) == "answers.json"
    assert lab._answer_file({"adapter": "duckdb_lab", "starter": "s.sql"}) == "answer.sql"
    assert lab._answer_file({"adapter": "dbsql_lab", "starter": "s.sql"}) == "answer.sql"
    for a in lab.STARTER_ADAPTERS:
        assert lab._answer_file({"adapter": a, "starter": "s.x"}) == "s.x"


def test_exercise_kinds_are_refused_in_words():
    with pytest.raises(ValueError) as err:
        lab._duckdb_exercise(FAB, CFG)
    assert str(err.value) == f"{CFG} doesn't run in a SQL lab"
    assert lab._duckdb_exercise(FAB, SQL)["id"] == SQL and lab._duckdb_exercise(DBX, DBSQL)["id"] == DBSQL
    with pytest.raises(ValueError) as err:
        lab._lab_exercise(FAB, OTHER)
    assert str(err.value) == f"{OTHER} doesn't have a workspace on this PC"


def test_exists_and_state_on_a_missing_folder(root):
    assert lab._exists(root / "nope", "x") is False
    assert lab._state(root / "nope") == {}
    (root / "f").mkdir()
    assert lab._exists(root / "f", "x") is False
    (root / "f" / "x").write_text("1", encoding="utf-8")
    assert lab._exists(root / "f", "x") is True


def test_state_that_isnt_an_object_is_empty(root):
    (root / lab.STATE_FILE).write_text("[1, 2]", encoding="utf-8")
    assert lab._state(root) == {}
    (root / lab.STATE_FILE).write_text('{"a": 1}', encoding="utf-8")
    assert lab._state(root) == {"a": 1}


def test_write_replaces_and_a_failed_write_leaves_no_temp(root, monkeypatch):
    lab._write(root, "a.txt", "one\r\ntwo")
    assert (root / "a.txt").read_bytes() == b"one\r\ntwo"
    lab._write(root, "a.txt", "three")
    assert (root / "a.txt").read_text(encoding="utf-8") == "three"

    def boom(*a):
        raise OSError("no room")
    monkeypatch.setattr(lab.jsonfile, "replace", boom)
    with pytest.raises(OSError, match="no room"):
        lab._write(root, "a.txt", "four")
    assert sorted(p.name for p in root.iterdir()) == ["a.txt"]
    assert (root / "a.txt").read_text(encoding="utf-8") == "three"


def test_write_state_is_indented_json(root):
    lab._write_state(root, {"a": 1})
    assert (root / lab.STATE_FILE).read_text(encoding="utf-8") == '{\n  "a": 1\n}'


def test_fresh_dir_empties_what_is_there(root):
    sub = root / "run"
    sub.mkdir()
    (sub / "old.txt").write_text("x", encoding="utf-8")
    got = lab._fresh_dir(root, "run")
    assert got == sub and list(sub.iterdir()) == []
    assert lab._fresh_dir(root, "new").is_dir()


def test_fresh_dir_refuses_a_link(root):
    (root / "elsewhere").mkdir()
    (root / "elsewhere" / "keep.txt").write_text("x", encoding="utf-8")
    try:
        os.symlink(root / "elsewhere", root / "run", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ValueError):
        lab._fresh_dir(root, "run")
    assert (root / "elsewhere" / "keep.txt").exists()


def test_folders():
    assert lab._exercise_folder(FAB, SQL) == lab.registry.ROOT / FAB / "exercises" / SQL
    assert lab._grading_folder(FAB, SQL) == lab.registry.ROOT / FAB / "grading" / SQL


def test_with_messages_pairs_by_position_else_by_kind():
    ex = {"validation": [{"kind": "a", "pass": "A1 ok", "fail": "A1 no"}, {"kind": "a", "pass": "A2 ok", "fail": "A2 no"},
                         {"kind": "b"}]}
    got = lab._with_messages(ex, [{"kind": "a", "passed": True}, {"kind": "a", "passed": False},
                                  {"kind": "b", "passed": False}])
    assert got == [{"kind": "a", "passed": True, "message": "A1 ok"}, {"kind": "a", "passed": False, "message": "A2 no"},
                   {"kind": "b", "passed": False}]
    by_kind = lab._with_messages(ex, [{"kind": "a", "passed": False}, {"kind": "z", "passed": False}])
    assert by_kind == [{"kind": "a", "passed": False, "message": "A2 no"}, {"kind": "z", "passed": False}]
    swapped = lab._with_messages(ex, [{"kind": "b", "passed": True}, {"kind": "a", "passed": True},
                                      {"kind": "a", "passed": True}])
    assert [r.get("message") for r in swapped] == [None, "A2 ok", "A2 ok"]


# ---- the workspace database

def test_db_path_and_a_wal_that_is_a_plain_file(root):
    assert lab._db_path(root, must_exist=False) == str(root / lab.DB_FILE)
    with pytest.raises(ValueError):
        lab._db_path(root)
    duckdb.connect(str(root / lab.DB_FILE)).close()
    (root / (lab.DB_FILE + ".wal")).write_bytes(b"")
    assert lab._db_path(root) == str(root / lab.DB_FILE)


def test_connect_is_locked_and_takes_a_zone(root, monkeypatch):
    duckdb.connect(str(root / lab.DB_FILE)).close()
    con = lab._connect(root)
    try:
        assert con.execute("SELECT current_setting('enable_external_access')").fetchone()[0] is False
        assert con.execute("SELECT current_setting('lock_configuration')").fetchone()[0] is True
        assert con.execute("SELECT current_setting('memory_limit')").fetchone()[0] != ""
    finally:
        con.close()
    calls = []

    class Con:
        def execute(self, sql):
            calls.append(sql)
    monkeypatch.setattr(lab.duckdb, "connect", lambda path: Con())
    lab._connect(root, zone="Z/One")
    lab._connect(root)
    assert calls == ["SET TimeZone = 'Z/One'", "SET enable_external_access = false",
                     f"SET memory_limit = '{lab.learn_labs.MEMORY_LIMIT}'", "SET lock_configuration = true",
                     "SET enable_external_access = false", f"SET memory_limit = '{lab.learn_labs.MEMORY_LIMIT}'",
                     "SET lock_configuration = true"]


def test_fresh_runs_the_setup_then_locks(monkeypatch):
    calls = []

    class Con:
        def execute(self, sql):
            calls.append(sql)
    monkeypatch.setattr(lab.duckdb, "connect", lambda path: calls.append(path) or Con())
    lab._fresh("CREATE TABLE t (x INT)")
    lab._fresh(None)
    lab._fresh("")
    tail = ["SET enable_external_access = false", f"SET memory_limit = '{lab.learn_labs.MEMORY_LIMIT}'",
            "SET lock_configuration = true"]
    assert calls == [":memory:", "CREATE TABLE t (x INT)", *tail, ":memory:", *tail, ":memory:", *tail]


# ---- start and state

def test_state_before_start(root):
    assert lab.state(root, FAB, SQL) == {"pack": FAB, "exercise": SQL, "started": False}


def test_start_a_settings_exercise_writes_only_the_starter(root):
    st = lab.start(root, FAB, CFG)
    ex = exercises.get(FAB, CFG)
    assert sorted(p.name for p in _folder(root, CFG).iterdir()) == [ex["starter"], lab.STATE_FILE]
    assert st == {"pack": FAB, "exercise": CFG, "started": True, "version": ex["version"],
                  "answer": exercises.file_text(FAB, CFG, ex["starter"]), "hints_used": 0, "explanation_seen": False,
                  "last": None}
    assert _st(root, CFG) == {"pack": FAB, "exercise": CFG, "version": ex["version"], "hints_used": 0,
                              "explanation_seen": False, "last": None}


def test_start_keeps_an_existing_answer_file(root):
    folder = _folder(root, CFG)
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text("{\"mine\": 1}", encoding="utf-8")
    assert lab.start(root, FAB, CFG)["answer"] == "{\"mine\": 1}"


def test_start_again_changes_nothing(root):
    lab.start(root, FAB, CFG)
    lab.save(root, FAB, CFG, "{}")
    lab._write_state(_folder(root, CFG), {**_st(root, CFG), "hints_used": 2})
    st = lab.start(root, FAB, CFG)
    assert st["answer"] == "{}" and st["hints_used"] == 2


def test_start_a_quiz_writes_empty_answers(root):
    st = lab.start(root, FAB, QUIZ)
    assert st["answer"] == "{}"
    assert sorted(p.name for p in _folder(root, QUIZ).iterdir()) == [quizlab.ANSWER_FILE, lab.STATE_FILE]


def test_start_a_quiz_keeps_existing_answers(root):
    folder = _folder(root, QUIZ)
    folder.mkdir(parents=True)
    (folder / quizlab.ANSWER_FILE).write_text('{"q1": ["a"]}', encoding="utf-8")
    assert lab.start(root, FAB, QUIZ)["answer"] == '{"q1": ["a"]}'


@pytest.mark.parametrize("eid", [GIT, PROJ])
def test_start_git_and_project_exercises_write_the_starter(root, eid):
    ex = exercises.get(FAB, eid)
    st = lab.start(root, FAB, eid)
    assert st["answer"] == exercises.file_text(FAB, eid, ex["starter"])
    assert sorted(p.name for p in _folder(root, eid).iterdir()) == sorted([ex["starter"], lab.STATE_FILE])


def test_start_a_python_exercise_prepares_run(root, monkeypatch):
    calls = []
    monkeypatch.setattr(lab.pylab, "prepare", lambda work, folder, ex, code: calls.append((work, folder, ex["id"], code)))
    ex = exercises.get(DBX, PY)
    starter = exercises.file_text(DBX, PY, ex["starter"])
    st = lab.start(root, DBX, PY)
    folder = _folder(root, PY, DBX)
    assert st["answer"] == starter and (folder / lab.RUN_DIR).is_dir()
    assert calls == [(folder / lab.RUN_DIR, lab._exercise_folder(DBX, PY), PY, starter)]


def test_start_a_python_exercise_keeps_an_existing_script(root, monkeypatch):
    monkeypatch.setattr(lab.pylab, "prepare", lambda *a: None)
    folder = _folder(root, PY, DBX)
    folder.mkdir(parents=True)
    (folder / "read_orders.py").write_text("print('mine')", encoding="utf-8")
    assert lab.start(root, DBX, PY)["answer"] == "print('mine')"


def test_start_a_sql_exercise_builds_the_database(root):
    lab.start(root, FAB, SQL)
    con = duckdb.connect(str(_folder(root, SQL) / lab.DB_FILE))
    try:
        assert con.execute("SELECT count(*) FROM silver_orders").fetchone()[0] > 0
    finally:
        con.close()


def test_start_a_sql_exercise_with_no_setup_and_an_existing_answer(root, monkeypatch):
    real = exercises.reference
    monkeypatch.setattr(lab.exercises, "reference", lambda p, e: {**real(p, e), "setup": ""})
    folder = _folder(root, SQL)
    folder.mkdir(parents=True)
    (folder / lab.ANSWER_FILE).write_text("SELECT 7", encoding="utf-8")
    assert lab.start(root, FAB, SQL)["answer"] == "SELECT 7"
    assert (folder / lab.DB_FILE).is_file()


def test_state_reads_help_as_numbers_and_flags(root):
    lab.start(root, FAB, CFG)
    folder = _folder(root, CFG)
    lab._write_state(folder, {"version": 3, "hints_used": None, "explanation_seen": 1, "last": {"passed": True}})
    assert lab.state(root, FAB, CFG) == {"pack": FAB, "exercise": CFG, "started": True, "version": 3,
                                         "answer": exercises.file_text(FAB, CFG, "settings.json"), "hints_used": 0,
                                         "explanation_seen": True, "last": {"passed": True}}
    (folder / "settings.json").unlink()
    assert lab.state(root, FAB, CFG)["answer"] == ""
    lab._write_state(folder, {"hints_used": "2"})
    assert lab.state(root, FAB, CFG)["hints_used"] == 2


def test_save_limit_is_1_mb(root):
    lab.start(root, FAB, CFG)
    lab.save(root, FAB, CFG, "x" * MB)
    assert len(lab.state(root, FAB, CFG)["answer"]) == MB
    with pytest.raises(ValueError) as err:
        lab.save(root, FAB, CFG, "é" * (MB // 2 + 1))
    assert str(err.value) == "That answer is over 1 MB; an exercise answer is much smaller."


def test_save_needs_a_start(root):
    with pytest.raises(ValueError) as err:
        lab.save(root, FAB, CFG, "{}")
    assert str(err.value) == f"Start {CFG} first"


# ---- run

def test_run_a_quiz_says_there_is_nothing_to_run(root):
    lab.start(root, FAB, QUIZ)
    with pytest.raises(ValueError) as err:
        lab.run(root, FAB, QUIZ, "{}")
    assert str(err.value) == "A quiz has nothing to run; answer the questions and check your work."


def test_run_a_settings_document_clamps_the_limit(root, monkeypatch):
    lab.start(root, FAB, CFG)
    seen = []
    monkeypatch.setattr(lab.configlab, "outline", lambda text, limit: seen.append((text, limit)) or {"rows": []})
    for given in (0, -5, 7, 1000, 1001, "3"):
        got = lab.run(root, FAB, CFG, "{}", given)
        assert set(got) == {"rows", "ms"} and isinstance(got["ms"], int)
    assert [lim for _, lim in seen] == [1, 1, 7, 1000, 1000, 3]
    assert lab.run(root, FAB, CFG, "{}") is not None and seen[-1] == ("{}", 200)


def test_run_a_settings_document_needs_a_start(root):
    with pytest.raises(ValueError, match=f"Start {CFG} first"):
        lab.run(root, FAB, CFG, "{}")


def test_run_a_settings_document_for_real(root):
    lab.start(root, FAB, CFG)
    got = lab.run(root, FAB, CFG, json.dumps({"a": [1, 2, 3]}), 2)
    assert got["rows"] == configlab.outline(json.dumps({"a": [1, 2, 3]}), 2)["rows"]


def test_run_a_project_clamps_the_limit_and_times_it(root, monkeypatch):
    lab.start(root, FAB, PROJ)
    seen = []

    def run(folder, ex, text, limit):
        seen.append((folder, ex["id"], text, limit))
        return {"columns": ["file"], "rows": [], "more": False}
    monkeypatch.setattr(lab.sqlprojlab, "run", run)
    got = lab.run(root, FAB, PROJ, "-- nothing", 0)
    assert got["columns"] == ["file"] and isinstance(got["ms"], int)
    lab.run(root, FAB, PROJ, "-- nothing", 5000)
    lab.run(root, FAB, PROJ, "-- nothing")
    assert seen == [(lab._exercise_folder(FAB, PROJ), PROJ, "-- nothing", 1),
                    (lab._exercise_folder(FAB, PROJ), PROJ, "-- nothing", 1000),
                    (lab._exercise_folder(FAB, PROJ), PROJ, "-- nothing", 200)]


def test_run_a_project_needs_a_start(root):
    with pytest.raises(ValueError, match=f"Start {PROJ} first"):
        lab.run(root, FAB, PROJ, "")


def test_run_sql_limit_more_and_cells(root):
    lab.start(root, FAB, SQL)
    one = lab.run(root, FAB, SQL, "SELECT * FROM range(3)", 0)
    assert one["rows"] == [[0]] and one["more"] is True and one["columns"] == ["range"]
    big = lab.run(root, FAB, SQL, "SELECT * FROM range(1500)", 5000)
    assert len(big["rows"]) == 1000 and big["more"] is True
    exact = lab.run(root, FAB, SQL, "SELECT * FROM range(3)", 3)
    assert len(exact["rows"]) == 3 and exact["more"] is False
    cell = lab.run(root, FAB, SQL, "SELECT CAST(1.5 AS DECIMAL(3, 1)) AS d, DATE '2024-01-02' AS t")
    assert cell["rows"] == [[1.5, "2024-01-02"]] and isinstance(cell["ms"], int)


def test_run_sql_says_stopped_when_the_time_limit_fires(root, monkeypatch):
    lab.start(root, FAB, SQL)
    monkeypatch.setattr(lab.learn_labs, "TIME_LIMIT", 0.05)
    with pytest.raises(ValueError, match="^Stopped after"):
        lab.run(root, FAB, SQL, "SELECT count(*) FROM range(1000000) a, range(1000000) b")


def test_run_sql_oversized_and_exactly_1_mb(root):
    lab.start(root, FAB, SQL)
    with pytest.raises(ValueError) as err:
        lab.run(root, FAB, SQL, "é" * (MB // 2 + 1))
    assert str(err.value) == "That answer is over 1 MB, so it wasn't run; an exercise answer is much smaller."


def test_run_dbsql_translates_and_runs_in_utc(root, monkeypatch):
    lab.start(root, DBX, DBSQL)
    seen = []
    real = lab._connect
    monkeypatch.setattr(lab.dbsql_lab, "translate", lambda sql: seen.append(sql) or "SELECT 5 AS five")
    monkeypatch.setattr(lab, "_connect", lambda folder, zone=None: seen.append(zone) or real(folder, zone))
    assert lab.run(root, DBX, DBSQL, "SELECT anything")["rows"] == [[5]]
    assert seen == ["SELECT anything", "UTC"]
    seen.clear()
    lab.start(root, FAB, SQL)
    lab.run(root, FAB, SQL, "SELECT 1")
    assert seen == [None]


# ---- run: Python and Git, with stand-ins

def _sandbox(calls, stdout="", stderr="", seconds=0.25):
    def run(work, script, data_root, appcontainer_ok=True):
        calls.append({"work": work, "script": script, "root": data_root, "appcontainer_ok": appcontainer_ok,
                      "code": (work / script).read_text(encoding="utf-8") if (work / script).exists() else None})
        return {"status": "ok", "exit_code": 0, "stdout": stdout, "stderr": stderr, "seconds": seconds,
                "runs_as": "learner", "extra": "dropped"}
    return run


def test_run_python_in_run_and_keep_the_tail_of_the_output(root, monkeypatch):
    prepared = []

    def prepare(work, folder, ex, code):
        prepared.append(code)
        (work / ex["starter"]).write_text(code, encoding="utf-8")
    monkeypatch.setattr(lab.pylab, "prepare", prepare)
    monkeypatch.setattr(lab.pylab, "appcontainer_ok", lambda ex: "flag")
    calls = []
    monkeypatch.setattr(lab.sandbox, "run", _sandbox(calls, "o" * 20_000 + "OUT", "e" * 20_000 + "ERR", 1.2344))
    lab.start(root, DBX, PY)
    got = lab.run(root, DBX, PY, "print(1)")
    assert got == {"status": "ok", "exit_code": 0, "stdout": "o" * 19_997 + "OUT", "stderr": "e" * 19_997 + "ERR",
                   "seconds": 1.2344, "runs_as": "learner", "ms": 1234}
    work = _folder(root, PY, DBX) / lab.RUN_DIR
    assert calls[-1]["work"] == work and calls[-1]["script"] == "read_orders.py" and calls[-1]["root"] == root
    assert calls[-1]["appcontainer_ok"] == "flag" and calls[-1]["code"] == "print(1)"
    assert len(prepared) == 1      # run/ was there: the script is written in place, not prepared again


def test_run_python_prepares_run_when_it_is_gone(root, monkeypatch):
    prepared = []
    monkeypatch.setattr(lab.pylab, "prepare", lambda work, folder, ex, code: prepared.append((work, code)))
    calls = []
    monkeypatch.setattr(lab.sandbox, "run", _sandbox(calls))
    lab.start(root, DBX, PY)
    work = _folder(root, PY, DBX) / lab.RUN_DIR
    work.rmdir()
    lab.run(root, DBX, PY, "print(2)")
    assert prepared[-1] == (work, "print(2)") and work.is_dir()


def test_run_python_limits(root, monkeypatch):
    monkeypatch.setattr(lab.pylab, "prepare", lambda *a: None)
    calls = []
    monkeypatch.setattr(lab.sandbox, "run", _sandbox(calls))
    with pytest.raises(ValueError, match=f"Start {PY} first"):
        lab.run(root, DBX, PY, "x")
    lab.start(root, DBX, PY)
    lab.run(root, DBX, PY, "#" * MB)
    with pytest.raises(ValueError) as err:
        lab.run(root, DBX, PY, "é" * (MB // 2 + 1))
    assert str(err.value) == "That script is over 1 MB; an exercise answer is much smaller."
    assert len(calls) == 1


def _git(monkeypatch, removed, fail=False):
    seen = []

    def run(work, folder, ex, text, limit):
        seen.append({"work": work, "folder": folder, "id": ex["id"], "text": text, "limit": limit,
                     "fresh": list(work.iterdir())})
        (work / "made.txt").write_text("x", encoding="utf-8")
        return {"columns": ["step"], "rows": [["ok"]]}

    def remove(path):
        removed.append(path)
        if fail:
            raise OSError("locked")
        lab.trees.remove(path)
    monkeypatch.setattr(lab.gitlab, "run", run)
    monkeypatch.setattr(lab.git_ops, "remove_clone", remove)
    return seen


def test_run_git_in_a_fresh_run_folder_removed_afterwards(root, monkeypatch):
    removed = []
    seen = _git(monkeypatch, removed)
    lab.start(root, FAB, GIT)
    work = _folder(root, GIT) / lab.RUN_DIR
    got = lab.run(root, FAB, GIT, "git status", 0)
    assert got["columns"] == ["step"] and isinstance(got["ms"], int)
    assert seen[-1] == {"work": work, "folder": lab._exercise_folder(FAB, GIT), "id": GIT, "text": "git status",
                        "limit": 1, "fresh": []}
    assert removed == [work] and not work.exists()
    lab.run(root, FAB, GIT, "git status", 5000)
    lab.run(root, FAB, GIT, "git status")
    assert [s["limit"] for s in seen] == [1, 1000, 200]


def test_run_git_clears_what_an_earlier_run_left(root, monkeypatch):
    removed = []
    seen = _git(monkeypatch, removed)
    lab.start(root, FAB, GIT)
    work = _folder(root, GIT) / lab.RUN_DIR
    work.mkdir()
    (work / "left.txt").write_text("x", encoding="utf-8")
    lab.run(root, FAB, GIT, "git status")
    assert removed == [work, work] and seen[-1]["fresh"] == []


def test_run_git_keeps_its_result_when_the_folder_is_locked(root, monkeypatch, caplog):
    removed = []
    _git(monkeypatch, removed, fail=True)
    lab.start(root, FAB, GIT)
    with caplog.at_level(logging.WARNING, logger=lab.__name__):
        got = lab.run(root, FAB, GIT, "git status")
    assert got["rows"] == [["ok"]]
    assert "The exercise's run folder couldn't be removed yet: locked" in caplog.text


def test_run_git_limits(root, monkeypatch):
    removed = []
    seen = _git(monkeypatch, removed)
    with pytest.raises(ValueError, match=f"Start {GIT} first"):
        lab.run(root, FAB, GIT, "x")
    lab.start(root, FAB, GIT)
    lab.run(root, FAB, GIT, "#" * MB)
    with pytest.raises(ValueError) as err:
        lab.run(root, FAB, GIT, "é" * (MB // 2 + 1))
    assert str(err.value) == "That session is over 1 MB; an exercise answer is much smaller."
    assert len(seen) == 1


# ---- validate

def test_validate_python_in_a_fresh_check_folder(root, monkeypatch):
    monkeypatch.setattr(lab.pylab, "prepare", lambda work, folder, ex, code: (work / "made.txt").write_text(code))
    monkeypatch.setattr(lab.pylab, "appcontainer_ok", lambda ex: "flag")
    seen = []

    def grade(work, script, validation, data_root, appcontainer_ok=True):
        seen.append((work, script, [c["kind"] for c in validation], data_root, appcontainer_ok,
                     (work / "made.txt").read_text()))
        return {"results": [{"kind": c["kind"], "passed": True} for c in validation]}
    monkeypatch.setattr(lab.pylab, "grade", grade)
    lab.start(root, DBX, PY)
    out = lab.validate(root, DBX, PY, "print(3)")
    ex = exercises.get(DBX, PY)
    work = _folder(root, PY, DBX) / lab.CHECK_DIR
    assert seen == [(work, ex["starter"], [c["kind"] for c in ex["validation"]], root, "flag", "print(3)")]
    assert not work.exists()
    assert out["passed"] is True and [r["message"] for r in out["results"]] == [c["pass"] for c in ex["validation"]]


def test_validate_python_removes_check_even_when_grading_fails(root, monkeypatch):
    monkeypatch.setattr(lab.pylab, "prepare", lambda *a: None)

    def grade(*a, **k):
        raise RuntimeError("grader broke")
    monkeypatch.setattr(lab.pylab, "grade", grade)
    lab.start(root, DBX, PY)
    with pytest.raises(RuntimeError):
        lab.validate(root, DBX, PY, "x")
    assert not (_folder(root, PY, DBX) / lab.CHECK_DIR).exists()


def test_validate_settings_reads_the_saved_answer_and_records_last(root):
    lab.start(root, FAB, CFG)
    out = lab.validate(root, FAB, CFG)
    ex = exercises.get(FAB, CFG)
    assert out["pack"] == FAB and out["exercise"] == CFG and out["version"] == ex["version"]
    assert out["passed"] is False and out["verification"] == exercises.label(ex)
    assert out["hints_used"] == 0 and out["explanation_seen"] is False and out["answers_changed"] is False
    assert all("message" in r for r in out["results"])
    assert _st(root, CFG)["last"] == {"passed": False, "checks": [{"kind": r["kind"], "passed": r["passed"]}
                                                                   for r in out["results"]]}


def test_validate_an_oversized_answer(root):
    lab.start(root, FAB, CFG)
    with pytest.raises(ValueError) as err:
        lab.validate(root, FAB, CFG, "é" * (MB // 2 + 1))
    assert str(err.value) == "That answer is over 1 MB; an exercise answer is much smaller."
    assert lab.validate(root, FAB, CFG, " " * MB)["passed"] is False


def test_validate_git_with_a_stand_in(root, monkeypatch):
    seen = []
    monkeypatch.setattr(lab.gitlab, "grade", lambda text, folder, ex: seen.append((text, folder)) or {
        "results": [{"kind": c["kind"], "passed": True} for c in ex["validation"]]})
    lab.start(root, FAB, GIT)
    out = lab.validate(root, FAB, GIT, "git status")
    assert seen == [("git status", lab._exercise_folder(FAB, GIT))] and out["passed"] is True
    assert all(r["message"] for r in out["results"])


def test_validate_project_with_a_stand_in(root, monkeypatch):
    seen = []
    monkeypatch.setattr(lab.sqlprojlab, "grade", lambda text, folder, grading, ex: seen.append((text, folder, grading)) or {
        "results": [{"kind": c["kind"], "passed": False} for c in ex["validation"]]})
    lab.start(root, FAB, PROJ)
    out = lab.validate(root, FAB, PROJ, "-- x")
    assert seen == [("-- x", lab._exercise_folder(FAB, PROJ), lab._grading_folder(FAB, PROJ))]
    assert out["passed"] is False and all(r["message"] for r in out["results"])


def test_validate_dbsql_with_a_stand_in(root, monkeypatch):
    seen = {}

    def check_all(ex, ref, answer, connect_to, limit, stopped):
        seen.update(ex=ex["id"], ref=ref, answer=answer, limit=limit, stopped=stopped,
                    con=connect_to("CREATE TABLE z (x INT)"))
        return [{"kind": "result_matches", "passed": True}]
    monkeypatch.setattr(lab.dbsql_lab, "check_all", check_all)
    monkeypatch.setattr(lab.dbsql_lab, "reference", lambda p, e: {"ref": (p, e)})
    connects = []
    monkeypatch.setattr(lab.dbsql_lab, "connect", lambda setup, mem: connects.append((setup, mem)) or "con")
    lab.start(root, DBX, DBSQL)
    out = lab.validate(root, DBX, DBSQL, "SELECT 1")
    assert seen == {"ex": DBSQL, "ref": {"ref": (DBX, DBSQL)}, "answer": "SELECT 1", "limit": lab.learn_labs._time_limit,
                    "stopped": lab.learn_labs._stopped(), "con": "con"}
    assert connects == [("CREATE TABLE z (x INT)", lab.learn_labs.MEMORY_LIMIT)]
    assert out["passed"] is True


def test_validate_sql_messages_and_the_time_limit(root, monkeypatch):
    lab.start(root, FAB, SQL)
    ex = {"validation": [{"kind": "a", "pass": "P", "fail": "F"}, {"kind": "b"}]}
    monkeypatch.setattr(lab.exercises, "run_check", lambda con, c, answer, solution: {"kind": c["kind"], "passed": True})
    got = lab._check_duckdb(ex, {"setup": None, "solution": "S"}, "A")
    assert got == [{"kind": "a", "passed": True, "message": "P"}, {"kind": "b", "passed": True}]

    class Fired:
        fired = True

    @contextlib.contextmanager
    def fired(con):
        yield Fired()
    monkeypatch.setattr(lab.learn_labs, "_time_limit", fired)
    got = lab._check_duckdb(ex, {"setup": None, "solution": "S"}, "A")
    assert got == [{"kind": "a", "passed": False, "detail": lab.learn_labs._stopped(), "message": "F"},
                   {"kind": "b", "passed": False, "detail": lab.learn_labs._stopped()}]


def test_validate_sql_runs_each_check_with_answer_and_solution(root, monkeypatch):
    seen = []
    monkeypatch.setattr(lab.exercises, "run_check",
                        lambda con, c, answer, solution: seen.append((c["kind"], answer, solution)) or
                        {"kind": c["kind"], "passed": False})
    ref = {"setup": "CREATE TABLE q (x INT)", "solution": "SOL"}
    lab._check_duckdb({"validation": [{"kind": "a"}, {"kind": "b"}]}, ref, "ANS")
    assert seen == [("a", "ANS", "SOL"), ("b", "ANS", "SOL")]


def test_validate_one_failing_check_fails(root, monkeypatch):
    lab.start(root, FAB, CFG)
    monkeypatch.setattr(lab.configlab, "grade", lambda answer, ex: {"results": [{"kind": "config_matches", "passed": True},
                                                                               {"kind": "config_matches", "passed": False}]})
    assert lab.validate(root, FAB, CFG, "{}")["passed"] is False
    monkeypatch.setattr(lab.configlab, "grade", lambda answer, ex: {"results": [{"kind": "config_matches", "passed": True}]})
    assert lab.validate(root, FAB, CFG, "{}")["passed"] is True


# ---- a quiz: answers changed after a failed check

def _quiz_answer(questions, right=True):
    picks = {}
    for q in questions:
        key = q.get("answer") or q.get("key") or q.get("correct")
        picks[q["id"]] = list(key) if right else ["zz"]
    return json.dumps(picks)


def test_quiz_check_records_picks_and_a_change(root, monkeypatch):
    lab.start(root, FAB, QUIZ)
    monkeypatch.setattr(lab.quizlab, "grade", lambda answer, ex, qs: {"results": [{"kind": "quiz_score", "passed": False}]})
    lab.validate(root, FAB, QUIZ, json.dumps({"q1": ["a"], "q2": []}))
    st = _st(root, QUIZ)
    assert st["quiz_checked"] == {"q1": ["a"]} and "answers_changed" not in st
    monkeypatch.setattr(lab.quizlab, "changed", lambda qs, checked, picks: checked == {"q1": ["a"]} and picks != checked)
    out = lab.validate(root, FAB, QUIZ, json.dumps({"q1": ["b"]}))
    assert out["answers_changed"] is True
    st = _st(root, QUIZ)
    assert st["answers_changed"] is True and st["quiz_checked"] == {"q1": ["b"]}


def test_quiz_pass_doesnt_record_picks(root, monkeypatch):
    lab.start(root, FAB, QUIZ)
    monkeypatch.setattr(lab.quizlab, "grade", lambda answer, ex, qs: {"results": [{"kind": "quiz_score", "passed": True}]})
    out = lab.validate(root, FAB, QUIZ, json.dumps({"q1": ["a"]}))
    assert out["passed"] is True and out["answers_changed"] is False
    assert "quiz_checked" not in _st(root, QUIZ)


def test_quiz_checked_that_isnt_an_object_starts_again(root, monkeypatch):
    lab.start(root, FAB, QUIZ)
    lab._write_state(_folder(root, QUIZ), {**_st(root, QUIZ), "quiz_checked": ["bad"]})
    seen = []
    monkeypatch.setattr(lab.quizlab, "changed", lambda qs, checked, picks: seen.append(checked) or False)
    monkeypatch.setattr(lab.quizlab, "grade", lambda answer, ex, qs: {"results": [{"kind": "quiz_score", "passed": False}]})
    lab.validate(root, FAB, QUIZ, json.dumps({"q1": ["a"]}))
    assert seen == [{}] and _st(root, QUIZ)["quiz_checked"] == {"q1": ["a"]}


def test_quiz_unreadable_answer_checks_nothing(root, monkeypatch):
    lab.start(root, FAB, QUIZ)
    monkeypatch.setattr(lab.quizlab, "grade", lambda answer, ex, qs: {"results": [{"kind": "quiz_score", "passed": False}]})
    lab.validate(root, FAB, QUIZ, "{not json")
    assert _st(root, QUIZ)["quiz_checked"] == {}


def test_quiz_validate_uses_the_grading_questions(root, monkeypatch):
    lab.start(root, FAB, QUIZ)
    seen = []
    monkeypatch.setattr(lab.quizlab, "load", lambda grading: seen.append(grading) or [])
    monkeypatch.setattr(lab.quizlab, "grade", lambda answer, ex, qs: {"results": [{"kind": "quiz_score", "passed": True}]})
    out = lab.validate(root, FAB, QUIZ, "{}")
    assert seen == [lab._grading_folder(FAB, QUIZ)]
    assert out["results"][0]["message"] == exercises.get(FAB, QUIZ)["validation"][0]["pass"]


# ---- hints and the explanation

def test_hints_clamp_and_keep_the_highest(root):
    lab.start(root, FAB, CFG)
    hints = exercises.get(FAB, CFG)["hints"]
    assert lab.hint(root, FAB, CFG, 0) == {"level": 1, "of": len(hints), "text": hints[0]}
    assert lab.hint(root, FAB, CFG, 99) == {"level": len(hints), "of": len(hints), "text": hints[-1]}
    assert lab.hint(root, FAB, CFG, "2")["level"] == 2
    assert _st(root, CFG)["hints_used"] == len(hints)


def test_hints_from_an_empty_state(root):
    lab.start(root, FAB, CFG)
    lab._write_state(_folder(root, CFG), {"hints_used": None})
    lab.hint(root, FAB, CFG, 2)
    assert _st(root, CFG)["hints_used"] == 2


def test_no_hints(root):
    assert lab.hint(root, FAB, QUIZ, 1) == {"level": 0, "of": 0, "text": "This exercise has no hints."}


def test_hint_needs_a_start(root):
    with pytest.raises(ValueError, match=f"Start {CFG} first"):
        lab.hint(root, FAB, CFG, 1)


def test_explanation_fields_and_when_it_counts(root):
    lab.start(root, FAB, CFG)
    ex = exercises.get(FAB, CFG)
    out = lab.explanation(root, FAB, CFG)
    assert out == {"text": ex["explanation"], "remediation": list(ex["remediation"])}
    assert _st(root, CFG)["explanation_seen"] is True
    lab._write_state(_folder(root, CFG), {**_st(root, CFG), "explanation_seen": False, "last": {"passed": True}})
    lab.explanation(root, FAB, CFG)
    assert _st(root, CFG)["explanation_seen"] is False
    lab._write_state(_folder(root, CFG), {**_st(root, CFG), "last": {"passed": False}})
    lab.explanation(root, FAB, CFG)
    assert _st(root, CFG)["explanation_seen"] is True


def test_explanation_of_a_quiz_has_its_questions(root):
    lab.start(root, FAB, QUIZ)
    out = lab.explanation(root, FAB, QUIZ)
    assert out["questions"] == quizlab.review(quizlab.load(lab._grading_folder(FAB, QUIZ)))


def test_explanation_without_remediation(root, monkeypatch):
    lab.start(root, FAB, CFG)
    real = exercises.get
    monkeypatch.setattr(lab.exercises, "get", lambda p, e: {**real(p, e), "remediation": None})
    assert lab.explanation(root, FAB, CFG)["remediation"] == []


def test_explanation_needs_a_start(root):
    with pytest.raises(ValueError, match=f"Start {CFG} first"):
        lab.explanation(root, FAB, CFG)


# ---- reset

def test_reset_keeps_only_the_help_that_was_used(root):
    lab.start(root, FAB, CFG)
    folder = _folder(root, CFG)
    lab.save(root, FAB, CFG, "{}")
    ver = exercises.get(FAB, CFG)["version"]
    lab._write_state(folder, {"version": ver, "hints_used": 2, "explanation_seen": False, "answers_changed": True,
                              "quiz_checked": {}, "last": {"passed": False}, "other": 1})
    st = lab.reset(root, FAB, CFG)
    assert st["answer"] == exercises.file_text(FAB, CFG, "settings.json") and st["last"] is None
    assert _st(root, CFG) == {"pack": FAB, "exercise": CFG, "version": ver, "hints_used": 2,
                              "explanation_seen": False, "last": None, "answers_changed": True}


def test_reset_with_nothing_to_keep_starts_clean(root):
    lab.start(root, FAB, CFG)
    lab.save(root, FAB, CFG, "{}")
    st = lab.reset(root, FAB, CFG)
    assert st["hints_used"] == 0 and _st(root, CFG)["hints_used"] == 0


def test_reset_of_a_workspace_never_started(root):
    st = lab.reset(root, FAB, CFG)
    assert st["started"] is True and st["hints_used"] == 0


def test_reset_with_an_unreadable_state_keeps_nothing(root, monkeypatch):
    lab.start(root, FAB, CFG)
    lab.hint(root, FAB, CFG, 1)

    def broken(folder):
        raise OSError("unreadable")
    real = lab._state
    calls = []
    monkeypatch.setattr(lab, "_state", lambda folder: calls.append(1) or (broken(folder) if len(calls) == 1 else real(folder)))
    st = lab.reset(root, FAB, CFG)
    assert st["hints_used"] == 0


# ---- how long Run took, and cleanup that meets a file already gone

class _Clock:
    """``lab.time`` with a ``perf_counter`` that reads 10.0 first and 12.0 after: Run took 2,000 ms."""

    def __init__(self):
        self.reads = iter([10.0])

    def perf_counter(self):
        return next(self.reads, 12.0)

    def __getattr__(self, name):
        import time
        return getattr(time, name)


def test_run_reports_its_milliseconds_for_each_kind(root, monkeypatch):
    removed = []
    _git(monkeypatch, removed)
    monkeypatch.setattr(lab.sqlprojlab, "run", lambda folder, ex, text, limit: {"rows": []})
    for pack, eid, text in ((FAB, CFG, "{}"), (FAB, PROJ, "-- x"), (FAB, SQL, "SELECT 1"), (FAB, GIT, "git status")):
        lab.start(root, pack, eid)
        monkeypatch.setattr(lab, "time", _Clock())
        assert lab.run(root, pack, eid, text)["ms"] == 2000, eid


def test_a_write_whose_temp_file_is_already_gone_raises_its_own_error(root, monkeypatch):
    def half(tmp, target):
        os.replace(tmp, target)
        raise OSError("replaced, then failed")
    monkeypatch.setattr(lab.jsonfile, "replace", half)
    with pytest.raises(OSError, match="replaced, then failed"):
        lab._write(root, "a.txt", "x")


def test_a_check_folder_the_grader_removed_is_no_error(root, monkeypatch):
    monkeypatch.setattr(lab.pylab, "prepare", lambda *a: None)

    def grade(work, script, validation, data_root, appcontainer_ok=True):
        lab.trees.remove(work)
        return {"results": [{"kind": c["kind"], "passed": True} for c in validation]}
    monkeypatch.setattr(lab.pylab, "grade", grade)
    lab.start(root, DBX, PY)
    assert lab.validate(root, DBX, PY, "x")["passed"] is True
