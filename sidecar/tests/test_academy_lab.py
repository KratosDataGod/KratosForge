"""ACD-008 engine: the learner's Academy workspace for DuckDB exercises. Work survives a restart, runs are
rolled back with no file access, validation never puts the reference answer in the workspace, and a pass
is recorded as evidence with the help it had."""

from __future__ import annotations

import pytest

from fabric_desktop import audit, progress, server
from fabric_desktop.academy import exercises, lab
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
    for m in ("academy_catalog", "academy_exercise", "academy_run", "academy_validate", "academy_hint", "academy_readiness"):
        assert not audit.audited(m)
    for m in ("academy_start", "academy_save", "academy_explanation", "academy_reset"):
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
        with pytest.raises(Exception):
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
    assert st["answer"] == exercises.reference(PACK, "E37")["starter"] and st["hints_used"] == 0
    assert (tmp_path / "academy" / "keep.txt").exists()
    assert svc.academy_exercise(PACK, "E36")["workspace"]["started"]


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
