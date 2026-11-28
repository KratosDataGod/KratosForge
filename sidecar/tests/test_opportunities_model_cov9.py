"""opportunities/model.py: make() and problems() on incomplete records."""

import pytest

from fabric_desktop.opportunities import model

W = {"workspace_id": "ws", "item_id": "nb1", "type": "Notebook", "name": "Load sales"}


def good(**over):
    kw = dict(rule="dup", category="duplicate_processing", action="eliminate", title="t",
              artifacts=[model.artifact(W, "remove")], current="c", problem="p",
              evidence=[model.evidence("code", "same code", "notebook source", lines=3)], recommendation="r",
              alternatives=[], effort="low", risk="low", confidence="proven", validation=["compare"],
              rollback="restore", source="scan", next_step={"kind": "open"})
    kw.update(over)
    return model.make(**kw)


def test_a_complete_record_has_no_problems():
    o = good()
    assert model.problems(o) == []
    assert o["id"] == model.opportunity_id("dup", "nb1") and o["affects"] == ["nb1"]
    assert o["artifacts"][0] == {"workspace_id": "ws", "item_id": "nb1", "type": "Notebook", "name": "Load sales",
                                 "role": "remove"}
    assert o["evidence"][0]["detail"] == {"lines": 3}


def test_key_and_affects_override_the_artifacts():
    o = good(key=["a", "b"], affects=[], dependencies=("x",), impact={"cu": 1})
    assert o["id"] == model.opportunity_id("dup", "b", "a")  # order of keys doesn't matter
    assert o["affects"] == [] and o["dependencies"] == ["x"] and o["impact"] == {"cu": 1}


@pytest.mark.parametrize("over, msg", [
    ({"category": "x"}, "unknown category"), ({"action": "x"}, "unknown action"),
    ({"confidence": "sure"}, "unknown confidence"), ({"effort": "huge"}, "effort and risk"),
    ({"risk": "none"}, "effort and risk"), ({"evidence": []}, "needs evidence"),
    ({"artifacts": []}, "needs the artifacts"), ({"validation": []}, "validation steps"),
    ({"rollback": ""}, "validation steps")])
def test_make_refuses_an_incomplete_opportunity(over, msg):
    with pytest.raises(ValueError, match=msg):
        good(**over)


def test_problems_lists_everything_wrong():
    o = good()
    o["status"] = "dreaming"
    o["evidence"] = [{"kind": "k", "text": "t", "source": ""}, {"kind": "k", "text": "t", "source": "s"}]
    del o["owner"]
    assert model.problems(o) == ["missing owner", "unknown status 'dreaming'", "evidence without a source"]
    o["evidence"] = []
    assert "no evidence" in model.problems(o)
    empty = model.problems({})
    assert empty[: len(model.REQUIRED)] == [f"missing {k}" for k in model.REQUIRED]
    assert empty[len(model.REQUIRED):] == ["unknown status None", "no evidence"]
