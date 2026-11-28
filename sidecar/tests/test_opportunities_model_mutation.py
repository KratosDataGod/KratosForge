"""What the opportunity record refuses and reports, one rule at a time (opportunities/model.py)."""

import pytest

from fabric_desktop.opportunities import model

WL = {"workspace_id": "ws", "item_id": "nb-1", "type": "Notebook", "name": "Load sales"}


def good(**change):
    args = {"rule": "R1", "category": "scheduling", "action": "reschedule", "title": "t",
            "artifacts": [model.artifact(WL, "change")], "current": "c", "problem": "p",
            "evidence": [model.evidence("runs", "ran twice", "monitor")], "recommendation": "r",
            "alternatives": [], "effort": "low", "risk": "low", "confidence": "high",
            "validation": ["check"], "rollback": "undo", "source": "s", "next_step": {}}
    return args | change


def test_a_complete_record_has_no_problems_and_a_sixteen_digit_id():
    o = model.make(**good())
    assert model.problems(o) == []
    assert o["id"] == model.opportunity_id("R1", "nb-1") and len(o["id"]) == len("opp-") + 16


@pytest.mark.parametrize("change, says", [
    ({"category": "nope"}, "unknown category 'nope'"),
    ({"action": "nope"}, "unknown action 'nope'"),
    ({"confidence": "sure"}, "unknown confidence 'sure'"),
    ({"effort": "huge"}, "effort and risk"),
    ({"risk": "huge"}, "effort and risk"),
    ({"evidence": []}, "needs evidence"),
    ({"artifacts": []}, "needs the artifacts"),
    ({"validation": []}, "validation steps and a rollback"),
    ({"rollback": ""}, "validation steps and a rollback"),
])
def test_each_missing_or_unknown_field_is_refused(change, says):
    with pytest.raises(ValueError, match=says):
        model.make(**good(**change))


def test_evidence_without_a_source_is_reported():
    o = model.make(**good())
    o["evidence"] = [model.evidence("runs", "ran twice", "monitor"), model.evidence("runs", "ran", "")]
    assert model.problems(o) == ["evidence without a source"]
