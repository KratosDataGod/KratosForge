"""Mutation batch 409: academy_service.py. The learner view carries an exercise's own lists and its shared
Learn lesson, a save says it saved, and a pass is recorded without claiming quiz answers changed."""

from __future__ import annotations

import pytest

from fabric_desktop.academy import exercises
from fabric_desktop.academy_service import AcademyMixin

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


def test_the_learner_view_carries_the_exercises_lists_and_its_learn_lesson(svc) -> None:
    ex = exercises.get(PACK, "E37")
    assert ex.get("uses") and not ex.get("lesson")
    view = svc.academy_exercise(PACK, "E37")
    assert view["lesson"].strip()
    assert view["prerequisites"] == list(ex["prerequisites"]) and view["prerequisites"]
    assert view["expected"] == ex["expected"] and view["expected"]
    assert view["remediation"] == list(ex["remediation"]) and view["remediation"]
    assert view["hints"] == len(ex["hints"]) == 3


def test_a_save_says_it_saved(svc) -> None:
    svc.academy_start(PACK, "E37")
    assert svc.academy_save(PACK, "E37", "SELECT 1") == {"saved": True}


def test_a_pass_is_recorded_without_saying_answers_changed(svc) -> None:
    svc.academy_start(PACK, "E37")
    out = svc.academy_validate(PACK, "E37", exercises.reference(PACK, "E37")["solution"])
    assert out["passed"] and out["recorded"] is True
    assert "answers_changed" not in svc.rows[0]["detail"]
