"""The time, memory and process limits hold for every exercise type that runs a learner's answer.

Where each one is already covered: the v1 learner process (time, memory, child processes) in
test_academy_sandbox; python_lab grading in test_academy_pylab; pipeline_lab in test_academy_pipelab;
git_lab steps in test_academy_gitlab. This file covers the rest: grading of the SQL exercise types
(duckdb_lab, dbsql_lab), PySpark exercises, and the AppContainer (on by default on Windows).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fabric_desktop.academy import appcontainer, exercises, lab, pyspark_lab, sandbox
from fabric_desktop.learn import labs as learn_labs

# One exercise of each SQL type that grades in DuckDB.
SQL_EXERCISES = [("dp-700", "E12"), ("dbx-de-associate", "E20")]
# A query that would run for hours, and one that asks for far more memory than the limit.
RUNAWAY = "SELECT count(*) AS n FROM range(100000000000) a, range(100000000000) b"
RAISE_MEMORY = "SET memory_limit = '64GB'"


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(learn_labs, "TIME_LIMIT", 1)


@pytest.mark.parametrize(("pack", "ex"), SQL_EXERCISES)
def test_grading_a_runaway_sql_answer_is_stopped_and_fails(tmp_path: Path, quick: None, pack: str, ex: str) -> None:
    lab.start(tmp_path, pack, ex)
    got = lab.validate(tmp_path, pack, ex, RUNAWAY)
    assert not all(r["passed"] for r in got["results"])
    assert any("Stopped after 1 seconds" in str(r.get("detail", "")) for r in got["results"]), got["results"]


@pytest.mark.parametrize(("pack", "ex"), SQL_EXERCISES)
def test_running_a_runaway_sql_answer_is_stopped(tmp_path: Path, quick: None, pack: str, ex: str) -> None:
    lab.start(tmp_path, pack, ex)
    with pytest.raises(ValueError, match="Stopped after 1 seconds"):
        lab.run(tmp_path, pack, ex, RUNAWAY)


@pytest.mark.parametrize(("pack", "ex"), SQL_EXERCISES)
def test_an_answer_cant_raise_the_memory_limit(tmp_path: Path, pack: str, ex: str) -> None:
    lab.start(tmp_path, pack, ex)
    with pytest.raises(Exception, match="(?i)lock|configuration"):
        lab.run(tmp_path, pack, ex, RAISE_MEMORY)
    got = lab.validate(tmp_path, pack, ex, RAISE_MEMORY)
    assert not all(r["passed"] for r in got["results"])


def test_a_pyspark_answer_that_never_ends_is_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("deltalake")
    monkeypatch.setattr(pyspark_lab, "RUN_SECONDS", 3)
    work = tmp_path / "data" / "academy" / "w"
    work.mkdir(parents=True)
    pyspark_lab.prepare(work, exercises.file_text("dbx-de-associate", "E19", "setup.sql"))
    run = pyspark_lab.run_learner(work, "while True:\n    pass\n", data_root=tmp_path / "data")
    assert run["status"] == "time_limit", run


# ---- the AppContainer (Windows) -----------------------------------------------------------------

on_windows = pytest.mark.skipif(sys.platform != "win32" or not appcontainer.status().get("available"),
                                reason="AppContainers are a Windows feature")


@pytest.fixture(autouse=True)
def _fresh_appcontainer(monkeypatch: pytest.MonkeyPatch) -> None:
    # As test_academy_appcontainer: on (the default, whatever KRATOSFORGE_ACADEMY_APPCONTAINER says),
    # and an earlier test's setup failure (status() remembers it) doesn't turn it off for these.
    monkeypatch.setattr(sandbox, "USE_APPCONTAINER", True)
    monkeypatch.setattr(appcontainer, "_why_not", "")


def _box_run(tmp_path: Path, code: str, **limits: object) -> dict:
    work = tmp_path / "w"
    work.mkdir()
    (work / "main.py").write_text(code, encoding="utf-8")
    got = sandbox.run(work, "main.py", tmp_path, **limits)  # type: ignore[arg-type]
    # The limits below were checked in the AppContainer, not in the v1 process it falls back to.
    assert got["runs_as"] == appcontainer.RUNS_AS, appcontainer.status()
    return got


@on_windows
def test_the_appcontainer_keeps_the_time_limit(tmp_path: Path) -> None:
    got = _box_run(tmp_path, "while True:\n    pass\n", time_limit=3)
    assert got["status"] == "time_limit", got


@on_windows
def test_the_appcontainer_keeps_the_memory_limit(tmp_path: Path) -> None:
    got = _box_run(tmp_path, "x = bytearray(3 * 1024 ** 3)\nprint('got it')\n", time_limit=60,
                   memory_limit=512 * 1024 ** 2)
    assert got["status"] == "memory_limit", got
    assert "got it" not in got["stdout"]


@on_windows
def test_the_appcontainer_starts_no_child_process(tmp_path: Path) -> None:
    got = _box_run(tmp_path, "import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'pass'])\nprint('started')\n",
                   time_limit=60)
    assert got["status"] == "error" and "started" not in got["stdout"], got
