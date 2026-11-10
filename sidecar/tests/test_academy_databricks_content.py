"""Databricks Associate content (first PySpark batch): every exercise's reference solution passes on its
practice and hidden data, and every planted wrong answer fails, run the way CI proves an exercise is real."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from fabric_desktop.academy import exercises

PACK = "dbx-de-associate"
BATCH = ("E17", "E21", "E23", "E24", "E31", "E33")


def test_the_batch_has_no_problems():
    assert exercises.problems() == []


@pytest.mark.parametrize("eid", BATCH)
def test_the_reference_passes_and_every_wrong_answer_fails(eid):
    result = exercises.self_test(PACK, eid)
    assert result["solution"]["passed"], result["solution"]
    assert len(result["wrong"]) >= 4
    for name, r in result["wrong"].items():
        assert not r["passed"], f"{eid} {name} passed"


@pytest.mark.parametrize("eid", BATCH)
def test_each_exercise_checks_hidden_data_and_a_rerun(eid):
    kinds = {(c["kind"], c.get("data")) for c in exercises.get(PACK, eid)["validation"]}
    assert ("result_matches", "hidden") in kinds and ("idempotent", None) in kinds


def test_the_troubleshooting_exercise_starts_from_the_broken_job():
    ex = exercises.get(PACK, "E33")
    starter = exercises.file_text(PACK, "E33", ex["starter"])
    grading = exercises.registry.ROOT / PACK / "grading" / "E33"
    assert ex["mode"] == "troubleshoot" and "try_cast" in starter
    assert (grading / "wrong-unchanged.py").read_text(encoding="utf-8") == starter   # so the broken job must fail


def test_the_packs_python_has_no_undefined_names_or_syntax_errors():
    """The packs folder is outside the repo's ruff and mypy runs (its code runs with the exercise's
    ``spark``, and a Fabric notebook's with ``notebookutils``), so typos in a starter, solution or wrong answer are caught here. Starters import what the
    learner will need, so an unused import is fine."""
    ruff = shutil.which("ruff")
    if not ruff:
        pytest.skip("ruff isn't installed here")
    packs = Path(exercises.registry.ROOT)
    p = subprocess.run([ruff, "check", "--isolated", "--select", "F,E9", "--ignore", "F401",
                        "--config", 'builtins=["spark", "notebookutils"]', "--output-format", "concise", str(packs)],
                       capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("eid", ("E14", "E46", "E48"))
def test_the_api_bundle_and_ci_exercises_pass_their_reference_and_fail_each_wrong_answer(eid):
    result = exercises.self_test(PACK, eid)
    assert result["solution"]["passed"], result["solution"]
    assert len(result["wrong"]) >= 4
    for name, r in result["wrong"].items():
        assert not r["passed"], f"{eid} {name} passed"


def test_the_ci_workflow_accepts_other_spellings_of_the_same_answer():
    import copy
    import json

    from fabric_desktop.academy import configlab
    grading = exercises.registry.ROOT / PACK / "grading" / "E48"
    doc = json.loads((grading / "solution.json").read_text(encoding="utf-8"))
    alt = copy.deepcopy(doc)
    alt["steps"][2]["run"] = "databricks bundle deploy --target prod"
    alt["env"]["DATABRICKS_CLIENT_SECRET"] = "${{secrets.DATABRICKS_CLIENT_SECRET}}"
    checks = exercises.get(PACK, "E48")["validation"]
    assert all(configlab.validators.config_matches(alt, c["rules"])["passed"] for c in checks)


def test_an_exercise_that_writes_delta_runs_outside_the_appcontainer():
    """delta-rs is denied a call it needs inside an AppContainer, so a Delta-writing script runs as delta_lab."""
    from fabric_desktop.academy import pylab
    assert not pylab.appcontainer_ok(exercises.get(PACK, "E14"))
