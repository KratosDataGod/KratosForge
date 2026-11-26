"""ACD-008 engine: the learner's Academy workspace for DuckDB exercises. Work survives a restart, runs are
rolled back with no file access, validation never puts the reference answer in the workspace, and a pass
is recorded as evidence with the help it had."""

from __future__ import annotations

import sys

import duckdb
import pytest

from fabric_desktop import audit, progress, server
from fabric_desktop.academy import appcontainer, exercises, lab
from fabric_desktop.academy.errors import LabError
from fabric_desktop.academy_service import METHODS, AcademyMixin

PACK = "dp-700"


class _Svc(AcademyMixin):
    def __init__(self, root):
        self.root = root
        self.rows = []

    def _progress_record(self, row):
        self.rows.append(row)
        return []

    def _profile_path(self):
        return self.root / "profile.json"


@pytest.fixture
def svc(tmp_path):
    return _Svc(tmp_path)


def _solution(eid):
    return exercises.reference(PACK, eid)["solution"]


def test_methods_are_served_and_the_writes_are_audited():
    assert METHODS <= server.METHODS
    for m in ("academy_catalog", "academy_exercise", "academy_run", "academy_validate", "academy_hint", "academy_readiness",
              "academy_sandbox_status"):
        assert not audit.audited(m)
    for m in ("academy_start", "academy_save", "academy_explanation", "academy_reset", "academy_sandbox_remove_grants"):
        assert audit.audited(m)


def test_catalog_and_the_learner_view_never_carry_the_answer(svc):
    cat = svc.academy_catalog()
    pack = next(p for p in cat["packs"] if p["pack"]["id"] == PACK)
    assert {"E21", "E36", "E37"} <= {e["id"] for e in pack["exercises"]}
    for eid in ("E21", "E36", "E37"):
        view = svc.academy_exercise(PACK, eid)
        text = str(view)
        assert _solution(eid).strip() not in text
        assert exercises.get(PACK, eid)["explanation"] not in text
        assert "hints" in view and isinstance(view["hints"], int)
        assert view["workspace"]["started"] is False
        assert view["verification"]


def test_start_builds_the_workspace_without_grading_files_and_keeps_it(svc, tmp_path):
    st = svc.academy_start(PACK, "E37")
    folder = lab.workspace(tmp_path, PACK, "E37")
    assert st["started"] and st["answer"] == exercises.reference(PACK, "E37")["starter"]
    assert sorted(p.name for p in folder.iterdir()) == ["answer.sql", "lab.duckdb", "state.json"]
    svc.academy_save(PACK, "E37", "SELECT 42 AS answer")
    assert _Svc(tmp_path).academy_start(PACK, "E37")["answer"] == "SELECT 42 AS answer"   # a restart keeps it


def test_unknown_or_escaping_ids_are_refused(svc):
    for bad in ("../E37", "E37/..", "E999", ""):
        with pytest.raises((ValueError, KeyError)):
            svc.academy_start(PACK, bad)
    with pytest.raises((ValueError, KeyError)):
        svc.academy_start("../dp-700", "E37")


def test_actions_need_a_started_workspace(svc):
    with pytest.raises(ValueError, match="Start E37 first"):
        svc.academy_run(PACK, "E37", "SELECT 1")


def test_a_run_is_rolled_back_and_has_no_file_access(svc, tmp_path):
    svc.academy_start(PACK, "E37")
    before = svc.academy_run(PACK, "E37", "SELECT count(*) AS n FROM silver_orders")["rows"]
    svc.academy_run(PACK, "E37", "DELETE FROM silver_orders")
    assert svc.academy_run(PACK, "E37", "SELECT count(*) AS n FROM silver_orders")["rows"] == before
    (tmp_path / "secret.csv").write_text("a\n1\n", encoding="utf-8")
    for sql in (f"SELECT * FROM read_csv('{(tmp_path / 'secret.csv').as_posix()}')",
                "SET enable_external_access = true", "BEGIN; DELETE FROM silver_orders; COMMIT;"):
        with pytest.raises((duckdb.Error, ValueError)):
            svc.academy_run(PACK, "E37", sql)
    out = svc.academy_run(PACK, "E37", "SELECT * FROM range(5000)")
    assert len(out["rows"]) == 200 and out["more"]


def test_validate_fails_the_starter_and_the_planted_wrong_answers(svc):
    svc.academy_start(PACK, "E37")
    out = svc.academy_validate(PACK, "E37")
    assert not out["passed"] and not out["recorded"] and not svc.rows
    assert all(r.get("message") or r.get("detail") for r in out["results"] if not r["passed"])
    for name, wrong in exercises.reference(PACK, "E37")["wrong"].items():
        assert not svc.academy_validate(PACK, "E37", wrong)["passed"], name


def test_a_pass_is_labelled_and_recorded_with_the_help_it_had(svc):
    svc.academy_start(PACK, "E37")
    assert svc.academy_hint(PACK, "E37", 2)["level"] == 2
    svc.academy_hint(PACK, "E37", 1)                                # the highest level shown is kept
    out = svc.academy_validate(PACK, "E37", _solution("E37"))
    assert out["passed"] and out["recorded"] and out["hints_used"] == 2 and not out["explanation_seen"]
    assert out["verification"] == exercises.label(exercises.get(PACK, "E37"))
    row = svc.rows[-1]
    assert row["type"] == "academy.exercise" and row["detail"]["hints_used"] == 2
    assert row["detail"]["objectives"] == ["2.2.9"]


def test_opening_the_explanation_before_a_pass_is_remembered(svc):
    svc.academy_start(PACK, "E36")
    assert svc.academy_explanation(PACK, "E36")["text"] == exercises.get(PACK, "E36")["explanation"]
    out = svc.academy_validate(PACK, "E36", _solution("E36"))
    assert out["passed"] and out["explanation_seen"]
    assert svc.rows[-1]["detail"]["explanation_seen"] is True


def test_the_explanation_after_a_pass_doesnt_count_against_it(svc):
    svc.academy_start(PACK, "E21")
    assert svc.academy_validate(PACK, "E21", _solution("E21"))["passed"]
    svc.academy_explanation(PACK, "E21")
    assert svc.academy_exercise(PACK, "E21")["workspace"]["explanation_seen"] is False


def test_recording_trouble_never_changes_the_result(svc):
    def broken(row):
        raise OSError("disk full")
    svc._progress_record = broken
    svc.academy_start(PACK, "E37")
    out = svc.academy_validate(PACK, "E37", _solution("E37"))
    assert out["passed"] and not out["recorded"]


def test_reset_puts_the_starter_back_and_touches_nothing_else(svc, tmp_path):
    svc.academy_start(PACK, "E37")
    svc.academy_start(PACK, "E36")
    svc.academy_save(PACK, "E37", "SELECT 1")
    svc.academy_hint(PACK, "E37", 1)
    (tmp_path / "academy" / "keep.txt").write_text("x", encoding="utf-8")
    st = svc.academy_reset(PACK, "E37")
    # The hint already seen stays used: Reset starts the answer again, not the help.
    assert st["answer"] == exercises.reference(PACK, "E37")["starter"] and st["hints_used"] == 1
    assert (tmp_path / "academy" / "keep.txt").exists()
    assert svc.academy_exercise(PACK, "E36")["workspace"]["started"]


def test_reset_keeps_the_explanation_seen_so_a_reset_pass_isnt_independent(svc):
    svc.academy_start(PACK, "E36")
    svc.academy_explanation(PACK, "E36")
    st = svc.academy_reset(PACK, "E36")
    assert st["explanation_seen"]
    out = svc.academy_validate(PACK, "E36", _solution("E36"))
    assert out["passed"] and out["explanation_seen"] and svc.rows[-1]["detail"]["explanation_seen"] is True


def test_a_new_exercise_version_starts_the_help_again(svc, tmp_path):
    svc.academy_start(PACK, "E37")
    svc.academy_hint(PACK, "E37", 1)
    svc.academy_explanation(PACK, "E37")
    st = lab._state(_folder(tmp_path))
    st["version"] = "an older version"
    lab._write_state(_folder(tmp_path), st)
    st = svc.academy_reset(PACK, "E37")
    assert st["hints_used"] == 0 and not st["explanation_seen"]


def test_an_unreadable_state_still_resets(svc, tmp_path):
    svc.academy_start(PACK, "E37")
    (_folder(tmp_path) / "state.json").write_text("{not json", encoding="utf-8")
    st = svc.academy_reset(PACK, "E37")
    assert st["answer"] == exercises.reference(PACK, "E37")["starter"] and st["hints_used"] == 0


def test_readiness_reads_the_profile(svc):
    out = svc.academy_readiness(PACK)
    assert out["label"] == "KratosForge readiness estimate"
    progress.save(svc._profile_path(), progress.empty())
    assert svc.academy_readiness(PACK)["earlier_version_rows"] == 0


def test_validation_ignores_an_edited_workspace_database(svc, tmp_path):
    import duckdb
    svc.academy_start(PACK, "E37")
    con = duckdb.connect(str(lab.workspace(tmp_path, PACK, "E37") / lab.DB_FILE))   # edited with another tool
    con.execute("DELETE FROM silver_orders")
    con.close()
    wrong = exercises.reference(PACK, "E37")["wrong"]["wrong-distinct.sql"]
    assert not svc.academy_validate(PACK, "E37", wrong)["passed"]
    assert svc.academy_validate(PACK, "E37", _solution("E37"))["passed"]


# Learner code may write in the workspace one day (ACD-005), so the engine never follows a link there.

def _folder(tmp_path, eid="E37"):
    return lab.workspace(tmp_path, PACK, eid)


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="no symlinks here")
def test_a_linked_answer_is_never_read_or_written_through(svc, tmp_path):
    import os
    svc.academy_start(PACK, "E37")
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    answer = _folder(tmp_path) / lab.ANSWER_FILE
    answer.unlink()
    os.symlink(secret, answer)
    with pytest.raises(ValueError, match="link"):
        svc.academy_exercise(PACK, "E37")
    with pytest.raises(ValueError, match="link"):
        svc.academy_save(PACK, "E37", "SELECT 1")
    with pytest.raises(ValueError, match="link"):
        svc.academy_validate(PACK, "E37")
    assert secret.read_text(encoding="utf-8") == "private"


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="no symlinks here")
def test_a_linked_database_or_state_is_refused(svc, tmp_path):
    import os
    svc.academy_start(PACK, "E37")
    other = tmp_path / "other.duckdb"
    db = _folder(tmp_path) / lab.DB_FILE
    db.rename(other)
    os.symlink(other, db)
    with pytest.raises(ValueError, match="link"):
        svc.academy_run(PACK, "E37", "SELECT 1")
    db.unlink()
    other.rename(db)
    state = _folder(tmp_path) / lab.STATE_FILE
    (tmp_path / "state.json").write_text(state.read_text(encoding="utf-8"), encoding="utf-8")
    state.unlink()
    os.symlink(tmp_path / "state.json", state)
    with pytest.raises(ValueError, match="link"):
        svc.academy_hint(PACK, "E37", 1)


def test_a_hard_linked_answer_is_refused(svc, tmp_path):
    import os
    svc.academy_start(PACK, "E37")
    answer = _folder(tmp_path) / lab.ANSWER_FILE
    outside = tmp_path / "outside.sql"
    outside.write_text("SELECT 1", encoding="utf-8")
    answer.unlink()
    os.link(outside, answer)
    with pytest.raises(ValueError, match="hard link"):
        svc.academy_save(PACK, "E37", "SELECT 2")
    assert outside.read_text(encoding="utf-8") == "SELECT 1"


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="no symlinks here")
def test_reset_refuses_a_workspace_that_is_a_link(svc, tmp_path):
    import os
    svc.academy_start(PACK, "E37")
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "precious.txt").write_text("x", encoding="utf-8")
    folder = _folder(tmp_path)
    import shutil
    shutil.rmtree(folder)
    os.symlink(keep, folder, target_is_directory=True)
    with pytest.raises(ValueError):
        svc.academy_reset(PACK, "E37")
    assert (keep / "precious.txt").exists()


def test_an_oversized_answer_is_refused_and_writes_leave_no_temp_files(svc, tmp_path):
    svc.academy_start(PACK, "E37")
    with pytest.raises(ValueError, match="1 MB"):
        svc.academy_save(PACK, "E37", "x" * (lab.MAX_ANSWER_BYTES + 1))
    svc.academy_save(PACK, "E37", "SELECT 1")
    assert sorted(p.name for p in _folder(tmp_path).iterdir()) == ["answer.sql", "lab.duckdb", "state.json"]


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="no symlinks here")
def test_a_linked_write_ahead_log_is_refused(svc, tmp_path):
    import os
    svc.academy_start(PACK, "E37")
    target = tmp_path / "elsewhere.wal"
    target.write_bytes(b"")
    os.symlink(target, _folder(tmp_path) / (lab.DB_FILE + ".wal"))
    with pytest.raises(ValueError, match="link"):
        svc.academy_run(PACK, "E37", "SELECT 1")
    assert target.read_bytes() == b""


def test_a_folder_named_like_the_write_ahead_log_is_refused(svc, tmp_path):
    svc.academy_start(PACK, "E37")
    (_folder(tmp_path) / (lab.DB_FILE + ".wal")).mkdir()
    with pytest.raises(ValueError, match="plain file"):
        svc.academy_run(PACK, "E37", "SELECT 1")


def test_an_oversized_answer_to_check_is_refused(svc):
    svc.academy_start(PACK, "E37")
    with pytest.raises(ValueError, match="1 MB"):
        svc.academy_validate(PACK, "E37", "x" * (lab.MAX_ANSWER_BYTES + 1))


@pytest.mark.parametrize("eid", ("E37", "E53"))   # a SQL exercise and a settings exercise
def test_an_oversized_answer_to_run_is_refused_before_it_runs(svc, eid):
    svc.academy_start(PACK, eid)
    with pytest.raises(ValueError, match="over 1 MB, so it wasn't run"):
        svc.academy_run(PACK, eid, "x" * (lab.MAX_ANSWER_BYTES + 1))


def test_an_answer_of_exactly_1_mb_still_runs(svc):
    svc.academy_start(PACK, "E37")
    sql = "SELECT 1 AS n -- "
    sql += "x" * (lab.MAX_ANSWER_BYTES - len(sql))
    assert svc.academy_run(PACK, "E37", sql)["rows"] == [[1]]


def test_sandbox_status_counts_the_runtime_grants_on_record(svc, tmp_path):
    from fabric_desktop import jsonfile
    got = svc.academy_sandbox_status()
    assert got["granted"] == 0 and got["enabled"] is True and "available" in got and "why_not" in got
    real = tmp_path / "py"
    real.mkdir()
    record = tmp_path / "academy" / "appcontainer-grants.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    jsonfile.write_json(record, {"sid": "S-1-15-3-1", "paths": [str(real), str(tmp_path / "gone"), "-bad", "rel/x", 7]})
    # Only absolute, plain Windows paths that still exist (a record path starting "/" or "-" is never used).
    assert svc.academy_sandbox_status()["granted"] == (1 if sys.platform == "win32" else 0)


def test_removing_the_sandbox_grants_answers_removed_and_failed(svc, monkeypatch):
    # Settings calls it; off Windows there is nothing to remove, and the answer has the same shape.
    calls = []
    monkeypatch.setattr(appcontainer, "revoke_runtime", lambda root: calls.append(root) or {"removed": 2, "failed": ["C:\\x"]})
    assert svc.academy_sandbox_remove_grants() == {"removed": 2, "failed": ["C:\\x"]}
    assert calls == [svc.root]
    monkeypatch.undo()
    if sys.platform != "win32":
        assert svc.academy_sandbox_remove_grants() == {"removed": 0, "failed": []}


# --- the offline audit (2026-11-26): what the learner sees when a lab answer or the pack goes wrong -------


@pytest.mark.parametrize(("sql", "said"), [
    ("SELEC 1 FROM", "Parser Error"),
    ("SELECT * FROM no_such_table", "Catalog Error"),
    ("SELECT * FROM read_csv('https://x.invalid/a.csv')", "Permission Error"),
], ids=["syntax", "unknown-table", "blocked-url"])
def test_run_says_duckdbs_own_reason_for_a_sql_mistake(svc, sql, said):   # D4
    svc.academy_start(PACK, "E21")
    with pytest.raises(LabError) as got:
        svc.academy_run(PACK, "E21", sql)
    assert str(got.value).startswith(f"Your SQL didn't run: {said}")
    assert "Fabric" not in str(got.value)


@pytest.mark.parametrize("sql", ["", "   \n", "-- just a note\n", "/* nothing */"])
def test_an_empty_answer_asks_for_a_query_in_run_and_check(svc, sql):   # D5
    svc.academy_start(PACK, "E21")
    with pytest.raises(LabError, match="^Write a query first"):
        svc.academy_run(PACK, "E21", sql)
    got = svc.academy_validate(PACK, "E21", sql)
    assert got["passed"] is False and got["results"]
    assert all(r["detail"] == lab.NO_QUERY and "message" not in r for r in got["results"]), got["results"]
    assert lab.state(svc.root, PACK, "E21")["last"]["passed"] is False   # the result was saved


def test_an_empty_answer_fails_a_learn_check_in_words():   # D5: learn/checks.py, shared with Learn labs
    from fabric_desktop.learn import checks
    con = duckdb.connect(":memory:")
    try:
        r = checks.result_matches(con, "-- nothing yet\n", reference="SELECT 1 AS x")
    finally:
        con.close()
    assert r == {"kind": "result_matches", "passed": False, "detail": checks.NO_QUERY}


def test_an_answer_that_didnt_run_shows_the_engines_reason_not_the_fail_sentence(svc, monkeypatch):   # D6
    ex = exercises.get(PACK, "E21")
    ex["validation"] = [{**c, "fail": "Your result doesn't match yet."} for c in ex["validation"]]
    monkeypatch.setattr(exercises, "get", lambda *a, **k: ex)
    svc.academy_start(PACK, "E21")
    got = svc.academy_validate(PACK, "E21", "SELEC 1")
    for r in got["results"]:
        assert r["ungraded"] and "message" not in r and "Parser Error" in r["detail"], r
    wrong = svc.academy_validate(PACK, "E21", "SELECT 1 AS x")   # it runs and is wrong: the sentence stays
    assert any(r.get("message") == "Your result doesn't match yet." for r in wrong["results"]), wrong["results"]


def test_a_start_that_fails_partway_can_be_started_again(svc, tmp_path, monkeypatch):   # D7
    real = exercises.learner_files
    monkeypatch.setattr(exercises, "learner_files", lambda *a, **k: {
        **real(*a, **k), "setup": real(*a, **k)["setup"] + "\nSELECT error('the setup broke');"})
    with pytest.raises(LabError, match="couldn't be made on this PC"):
        svc.academy_start(PACK, "E21")
    folder = lab.workspace(tmp_path, PACK, "E21")
    assert not (folder / lab.DB_FILE).exists() and not (folder / lab.STATE_FILE).exists()
    monkeypatch.setattr(exercises, "learner_files", real)
    assert svc.academy_start(PACK, "E21")["started"]
    assert svc.academy_validate(PACK, "E21", _solution("E21"))["passed"]


def test_a_half_built_database_from_an_earlier_start_is_replaced(svc, tmp_path):   # D7: a crash during Start
    folder = lab.workspace(tmp_path, PACK, "E21")
    folder.mkdir(parents=True)
    con = duckdb.connect(str(folder / lab.DB_FILE))
    con.execute(exercises.learner_files(PACK, "E21")["setup"].split(";")[0])   # one table of the setup, then "closed"
    con.close()
    assert svc.academy_start(PACK, "E21")["started"]
    assert svc.academy_validate(PACK, "E21", _solution("E21"))["passed"]


def test_start_doesnt_need_the_grading_files_and_check_says_they_are_damaged(svc, tmp_path, monkeypatch):   # D12
    monkeypatch.setattr(exercises, "_grading", lambda *a: tmp_path / "no-grading")
    assert svc.academy_start(PACK, "E21")["started"]
    with pytest.raises(LabError) as got:
        svc.academy_validate(PACK, "E21", "SELECT 1")
    assert str(got.value) == "This exercise's files are damaged on this PC. Reinstall or update KratosForge to repair them."
    assert "grading" not in str(got.value) and str(tmp_path) not in str(got.value)


def test_a_missing_starter_says_the_files_are_damaged(svc, monkeypatch):   # D12
    def gone(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "/somewhere/grading/E21/starter.sql")
    monkeypatch.setattr(exercises, "file_text", gone)
    with pytest.raises(LabError, match="^This exercise's files are damaged on this PC"):
        svc.academy_start(PACK, "E21")


@pytest.mark.parametrize("eid", exercises.ids(PACK))
def test_every_exercise_starts_in_the_app(svc, eid):   # D10: pyspark_lab and pipeline_lab too
    ex = exercises.get(PACK, eid)
    if ex["adapter"] not in lab.LAB_ADAPTERS:
        pytest.skip(f"{ex['adapter']} has no workspace on this PC by design")
    st = svc.academy_start(PACK, eid)
    assert st["started"], st
    assert svc.academy_exercise(PACK, eid)["workspace"]["started"]


def test_only_platform_adapters_have_no_workspace():   # D10
    local = set(exercises.ADAPTERS) - {p["adapter"] for p in exercises.registry.PLATFORMS.values()}
    assert local <= set(lab.LAB_ADAPTERS)


def test_a_pipeline_exercise_runs_and_checks_in_the_app(svc):   # D10
    eid = "E19"
    svc.academy_start(PACK, eid)
    solution = (exercises.registry.ROOT / PACK / "grading" / eid / "solution.json").read_text(encoding="utf-8")
    got = svc.academy_run(PACK, eid, solution)
    assert got["columns"] == ["activity", "type", "status", "output"] and got["rows"][-1][:3] == ["(pipeline)", "", "Succeeded"]
    assert svc.academy_validate(PACK, eid, solution)["passed"]
    bad = svc.academy_validate(PACK, eid, "{ nope")
    assert not bad["passed"] and all(r["ungraded"] and "isn't valid JSON" in r["detail"] for r in bad["results"])
    with pytest.raises(LabError, match="isn't valid JSON"):
        svc.academy_run(PACK, eid, "{ nope")


def test_a_pyspark_exercise_runs_and_checks_in_the_app(svc, tmp_path):   # D10
    eid = "E26"
    svc.academy_start(PACK, eid)
    solution = (exercises.registry.ROOT / PACK / "grading" / eid / "solution.py").read_text(encoding="utf-8")
    run = svc.academy_run(PACK, eid, "print(spark.range(3).count())\nraise ValueError('boom')\n")
    assert run["status"] == "error" and "3" in run["stdout"] and "ValueError: boom" in run["stderr"], run
    assert str(tmp_path) not in run["stderr"]
    got = svc.academy_validate(PACK, eid, solution)
    assert got["passed"], got["results"]
    wrong = svc.academy_validate(PACK, eid, "raise ValueError('boom')\n")
    assert not wrong["passed"] and all(r["ungraded"] and "ValueError: boom" in r["detail"] for r in wrong["results"])


def test_check_details_name_paths_from_the_workspace_and_home(tmp_path, monkeypatch):   # D14
    monkeypatch.setattr(lab.Path, "home", classmethod(lambda cls: tmp_path / "me"))
    folder = tmp_path / "me" / "data" / "academy" / "dp-700" / "E22"
    text = f'Cannot open "{folder}/check/out.parquet" or "{tmp_path}/me/.duckdb/extensions/x"'
    assert lab._tidy(text, folder) == 'Cannot open "check/out.parquet" or "~/.duckdb/extensions/x"'
    trace = ('Traceback (most recent call last):\n  File "/opt/kf/fabric_desktop/academy/_bootstrap.py", line 552, in <module>\n'
             '  File "/opt/kf/fabric_desktop/academy/_bootstrap.py", line 128, in _main\n    exec(code)\n'
             f'  File "{folder}/run/load.py", line 1, in <module>\n    raise RuntimeError("x")\nRuntimeError: x\n')
    assert lab._tidy(trace, folder / "run") == ('Traceback (most recent call last):\n  File "load.py", line 1, in <module>\n'
                                               '    raise RuntimeError("x")\nRuntimeError: x\n')
