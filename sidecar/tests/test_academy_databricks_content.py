"""Databricks Associate content (first PySpark batch): every exercise's reference solution passes on its
practice and hidden data, and every planted wrong answer fails, run the way CI proves an exercise is real."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import exercises

PACK = "dbx-de-associate"
BATCH = ("E21", "E23", "E24", "E31", "E33")


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
