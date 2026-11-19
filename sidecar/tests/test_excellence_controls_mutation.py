"""Kill tests for excellence/controls.py (docs/quality/mutation-testing.md, batch 111).

A context refuses a kind it wasn't given by name, a failure keeps its detail through to the outcome,
a control can't be changed once made, a reference needs a title as well as an https url, and
registering gives the control back.
"""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.excellence import controls as ct

TEMPLATE = {"why": "w", "solution": "s", "steps": ["one"], "benefit": "may help", "effort": "low",
            "risk": "low", "validation": "v", "rollback": "r"}
REF = ({"title": "Roles", "url": "https://learn.microsoft.com/fabric/roles"},)


def _control(**over) -> ct.Control:
    base = dict(id="FEA-SEC-001", version="1.0.0", dimension="security", title="Too many admins",
                severity="high", requirement="microsoft_recommendation", scope="workspace",
                evidence_kinds=("workspace.roles",), evaluate=lambda ctx: {}, remediation=TEMPLATE,
                references=REF)
    return ct.Control(**{**base, **over})


def test_a_context_refuses_a_kind_it_was_not_given_by_name():
    ctx = ct.Context({"id": "ws-1"}, {"workspace.roles": []})
    with pytest.raises(KeyError, match="isn't declared by this control"):
        ctx.get("capacity.usage")


def test_a_failure_keeps_its_detail():
    assert ct.failed(["e1"], "5 admins", detail={"admins": 5})["detail"] == {"admins": 5}
    assert ct.failed(["e1"], "5 admins")["detail"] == {}


def test_the_detail_reaches_the_outcome():
    rec = {"id": "e1", "kind": "workspace.roles", "confidence": "measured"}
    c = _control(evaluate=lambda ctx: ct.failed(["e1"], "5 admins", detail={"admins": 5}))
    got = ct.evaluate(c, {"id": "ws-1"}, lambda kind: [rec])
    assert got["outcome"] == "fail" and got["detail"] == {"admins": 5}


def test_a_control_cannot_be_changed_once_made():
    c = _control()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.severity = "low"  # type: ignore[misc]


def test_a_reference_needs_a_title_as_well_as_an_https_url():
    c = _control(references=({"title": "", "url": "https://learn.microsoft.com/x"},))
    assert "a reference has a title and an https url" in ct.problems(c)
    assert ct.problems(_control()) == []


def test_registering_gives_the_control_back():
    c = _control()
    assert ct.Registry().register(c) is c
