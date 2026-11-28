"""More Academy workspace tests, from a mutation run: the 1 MB answer limit at its edge for save, check
and a Git session, the row limit and the time each Run reports, check messages, hints, and what a pass
keeps. A Python exercise's script is never run here: the sandbox is stood in for."""

from __future__ import annotations

import json
import shutil

import pytest

from fabric_desktop.academy import exercises, lab, registry
from fabric_desktop.academy import workspace as ws

PACK, DUCK, QUIZ, SQLPROJ, GIT, PIPE = "dp-700", "E12", "E05", "E08", "E06", "E19"
DBX, DBSQL, PY, CONFIG = "dbx-de-associate", "E20", "E15", "E46"
MIB = 1024 * 1024


def _pad(sql, size):
    return sql + " " * (size - len(sql.encode("utf-8")))


class _Clock:
    """lab's clock, one second a call, so ms is a whole number of seconds."""

    def __init__(self):
        self.now = 100.0

    def perf_counter(self):
        self.now += 1.0
        return self.now


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(lab, "time", _Clock())


# ---- folders, exercises and state

def test_the_exercise_and_grading_folders_are_the_packs_own():
    assert lab._exercise_folder(PACK, SQLPROJ) == registry.ROOT / PACK / "exercises" / SQLPROJ
    assert lab._grading_folder(PACK, SQLPROJ) == registry.ROOT / PACK / "grading" / SQLPROJ


def test_nothing_exists_in_a_folder_that_isnt_there(tmp_path):
    assert lab._exists(tmp_path / "nope", "x") is False


def test_state_of_a_folder_without_one_is_empty(tmp_path):
    assert lab._state(tmp_path) == {}


def test_only_sql_exercises_open_a_sql_lab_and_only_lab_ones_a_workspace():
    with pytest.raises(ValueError, match=f"^{SQLPROJ} doesn't run in a SQL lab$"):
        lab._duckdb_exercise(PACK, SQLPROJ)
    with pytest.raises(ValueError, match=f"^{PIPE} doesn't have a workspace on this PC$"):
        lab._lab_exercise(PACK, PIPE)


def test_the_database_path_must_exist_unless_asked(tmp_path):
    with pytest.raises(ws.Refused):
        lab._db_path(tmp_path)
    assert lab._db_path(tmp_path, must_exist=False).endswith(lab.DB_FILE)


def test_state_is_written_with_two_space_indents(tmp_path):
    lab.start(tmp_path, PACK, DUCK)
    folder = lab.workspace(tmp_path, PACK, DUCK)
    text = (folder / lab.STATE_FILE).read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2)


def test_a_failed_write_leaves_no_temporary_file_and_says_so(tmp_path, monkeypatch):
    def broken(_src, _dst):
        raise OSError("disk full")
    monkeypatch.setattr(lab.jsonfile, "replace", broken)
    with pytest.raises(OSError):
        lab._write(tmp_path, "a.txt", "x")
    assert list(tmp_path.iterdir()) == []


# ---- the 1 MB limit

def test_an_answer_of_exactly_one_mib_is_saved_and_checked_and_one_byte_more_isnt(tmp_path):
    lab.start(tmp_path, PACK, DUCK)
    lab.save(tmp_path, PACK, DUCK, _pad("SELECT 1", MIB))
    with pytest.raises(ValueError, match="over 1 MB"):
        lab.save(tmp_path, PACK, DUCK, _pad("SELECT 1", MIB + 1))
    got = lab.validate(tmp_path, PACK, DUCK, _pad("SELECT 1", MIB))
    assert got["passed"] is False
    with pytest.raises(ValueError, match="over 1 MB"):
        lab.validate(tmp_path, PACK, DUCK, _pad("SELECT 1", MIB + 1))


def test_a_git_session_over_one_mib_is_refused_by_the_workspace(tmp_path):
    lab.start(tmp_path, PACK, GIT)
    with pytest.raises(ValueError, match="^That session is over 1 MB"):
        lab.run(tmp_path, PACK, GIT, _pad("git status", MIB + 1))
    with pytest.raises(ValueError) as err:   # at exactly 1 MiB the Git lab's own, smaller limit says no
        lab.run(tmp_path, PACK, GIT, _pad("git status", MIB))
    assert "over 1 MB" not in str(err.value)


# ---- Run

def test_sql_run_rows_stop_at_the_limit_and_at_1000(tmp_path):
    lab.start(tmp_path, PACK, DUCK)
    got = lab.run(tmp_path, PACK, DUCK, "SELECT range AS n FROM range(1500)", limit=5000)
    assert len(got["rows"]) == 1000 and got["more"] is True
    got = lab.run(tmp_path, PACK, DUCK, "SELECT range AS n FROM range(5)", limit=5)
    assert len(got["rows"]) == 5 and got["more"] is False
    assert lab.run(tmp_path, PACK, DUCK, "SELECT range AS n FROM range(5)", limit=4)["more"] is True


def test_translated_sql_runs_in_utc_and_duckdb_sql_in_the_pcs_zone(tmp_path, monkeypatch):
    zones = []
    real = lab._connect

    def spy(folder, zone=None):
        zones.append(zone)
        return real(folder, zone=zone)
    monkeypatch.setattr(lab, "_connect", spy)
    lab.start(tmp_path, DBX, DBSQL)
    lab.start(tmp_path, PACK, DUCK)
    lab.run(tmp_path, DBX, DBSQL, "SELECT 1 AS a")
    lab.run(tmp_path, PACK, DUCK, "SELECT 1 AS a")
    assert zones == ["UTC", None]


def test_sql_run_reports_its_time_in_ms(tmp_path, clock):
    lab.start(tmp_path, PACK, DUCK)
    assert lab.run(tmp_path, PACK, DUCK, "SELECT 1 AS a")["ms"] == 1000


@pytest.mark.parametrize(("pack", "eid", "module", "fn"), [
    (DBX, CONFIG, "configlab", "outline"),
    (PACK, SQLPROJ, "sqlprojlab", "run"),
    (PACK, GIT, "gitlab", "run"),
])
def test_each_run_caps_its_limit_from_1_to_1000_and_reports_ms(tmp_path, monkeypatch, clock, pack, eid, module, fn):
    limits = []

    def spy(*args):
        limits.append(args[-1])
        return {"columns": [], "rows": [], "more": False}
    monkeypatch.setattr(getattr(lab, module), fn, spy)
    lab.start(tmp_path, pack, eid)
    for limit in (0, 5000):
        got = lab.run(tmp_path, pack, eid, "x", limit=limit)
        assert got["ms"] == 1000
    assert limits == [1, 1000]


def test_a_python_run_returns_the_tail_of_its_output_and_its_time(tmp_path, monkeypatch):
    calls = []

    def fake(work, starter, root, appcontainer_ok):
        calls.append((work / starter).read_text(encoding="utf-8"))
        return {"status": "ok", "exit_code": 0, "stdout": "a" * 20_000 + "b", "stderr": "c" * 20_000 + "d",
                "seconds": 1.5, "runs_as": "test"}
    monkeypatch.setattr(lab.sandbox, "run", fake)
    lab.start(tmp_path, DBX, PY)
    folder = lab.workspace(tmp_path, DBX, PY)
    got = lab.run(tmp_path, DBX, PY, "print(1)")
    assert got["stdout"] == "a" * 19_999 + "b" and got["stderr"] == "c" * 19_999 + "d"
    assert got["ms"] == 1500 and got["seconds"] == 1.5
    starter = exercises.get(DBX, PY)["starter"]
    (folder / lab.RUN_DIR / starter).unlink()          # run/ is there, its script isn't
    lab.run(tmp_path, DBX, PY, "print(2)")
    shutil.rmtree(folder / lab.RUN_DIR)                # run/ itself is gone
    lab.run(tmp_path, DBX, PY, "print(3)")
    assert calls == ["print(1)", "print(2)", "print(3)"]


def test_a_python_check_folder_already_gone_is_no_error(tmp_path, monkeypatch):
    def grade(work, *args, **kwargs):
        shutil.rmtree(work)
        return {"results": []}
    monkeypatch.setattr(lab.pylab, "grade", grade)
    lab.start(tmp_path, DBX, PY)
    assert lab._check_python(tmp_path, DBX, PY, exercises.get(DBX, PY), "print(1)") == []


# ---- checks, hints and the explanation

def test_results_pair_with_checks_by_position_only_when_the_kinds_line_up():
    ex = {"validation": [{"kind": "a", "pass": "PA"}, {"kind": "b", "pass": "PB"}]}
    same = lab._with_messages(ex, [{"kind": "a", "passed": True}, {"kind": "b", "passed": True}])
    assert [r["message"] for r in same] == ["PA", "PB"]
    swapped = lab._with_messages(ex, [{"kind": "b", "passed": True}, {"kind": "a", "passed": True}])
    assert [r["message"] for r in swapped] == ["PB", "PA"]


def test_a_duckdb_check_carries_its_pass_or_fail_sentence(tmp_path):
    lab.start(tmp_path, PACK, DUCK)
    ex, ref = exercises.get(PACK, DUCK), exercises.reference(PACK, DUCK)
    good = lab._check_duckdb(ex, ref, ref["solution"])
    assert [r["message"] for r in good] == [c["pass"] for c in ex["validation"]]
    bad = lab._check_duckdb(ex, ref, "SELECT 1 AS nope")
    assert bad[0]["message"] == ex["validation"][0]["fail"]
    plain = {**ex, "validation": [{k: v for k, v in c.items() if k not in ("pass", "fail")} for c in ex["validation"]]}
    assert all("message" not in r for r in lab._check_duckdb(plain, ref, ref["solution"]))


def test_a_check_with_no_hints_used_says_none(tmp_path):
    lab.start(tmp_path, PACK, DUCK)
    assert lab.validate(tmp_path, PACK, DUCK, "SELECT 1")["hints_used"] == 0


def test_an_exercise_without_hints_says_so(tmp_path):
    assert exercises.get(PACK, QUIZ).get("hints") in (None, [])
    assert lab.hint(tmp_path, PACK, QUIZ, 1) == {"level": 0, "of": 0, "text": "This exercise has no hints."}


def test_the_explanation_after_a_pass_doesnt_mark_it_seen(tmp_path):
    lab.start(tmp_path, PACK, DUCK)
    assert lab.validate(tmp_path, PACK, DUCK, exercises.reference(PACK, DUCK)["solution"])["passed"]
    lab.explanation(tmp_path, PACK, DUCK)
    assert lab.state(tmp_path, PACK, DUCK)["explanation_seen"] is False


def test_the_explanation_carries_the_exercises_remediation(tmp_path):
    lab.start(tmp_path, PACK, DUCK)
    ex = exercises.get(PACK, DUCK)
    assert ex["remediation"]
    got = lab.explanation(tmp_path, PACK, DUCK)
    assert got == {"text": ex["explanation"], "remediation": list(ex["remediation"])}
