"""Kill tests for integrations/prompts.py (docs/quality/mutation-testing.md, batch 114).

Every argument a workflow names is required, so a prompt with none of them given names each one, and
a prompt can't be changed once made.
"""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.integrations import prompts

NEEDS = {
    "explore_lakehouse": ["workspace_id", "lakehouse_id"],
    "review_notebook": ["name"],
    "prepare_proof": ["name"],
    "investigate_proof": ["id"],
    "answer_with_sql": ["workspace_id", "lakehouse_id", "question"],
}


def test_each_workflow_needs_every_argument_it_names():
    for p in prompts.PROMPTS:
        if not p.arguments:
            assert p.render({}).startswith(prompts.GROUND_RULES)
            continue
        assert [a for a, _, required in p.arguments if required] == [a for a, _, _ in p.arguments], p.name
        with pytest.raises(ValueError, match=f"{p.name} needs {', '.join(a for a, _, _ in p.arguments)}$"):
            p.render({})


def test_the_workflows_with_arguments_are_the_known_ones():
    assert {p.name: [a for a, _, _ in p.arguments] for p in prompts.PROMPTS if p.arguments} == NEEDS


def test_a_prompt_cannot_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        prompts.PROMPTS[0].steps = "something else"  # type: ignore[misc]
