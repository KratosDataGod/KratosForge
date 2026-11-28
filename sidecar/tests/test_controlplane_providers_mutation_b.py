"""controlplane/providers at its edges, from the mutation run (batch 469): a level or evidence outside
the lists is refused, every contract record is frozen, and the planned providers have no adapter."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.controlplane import providers as pv


def test_a_level_or_evidence_outside_the_lists_is_refused():
    with pytest.raises(ValueError, match="level must be one of supported, partial, unsupported"):
        pv.Capability("great", "unit_tests")
    with pytest.raises(ValueError, match="evidence must be one of"):
        pv.Capability("supported", "a blog post")


@pytest.mark.parametrize("record,field", [
    (pv.Capability(), "level"),
    (pv.ProviderDescriptor("x", "X", adapter=True), "label"),
    (pv.StoreRef("fabric", "w", "l"), "lakehouse_id"),
    (pv.Found(pv.StoreRef("fabric", "w", "l"), "environment"), "state"),
    (pv.PlanStep("find", "x", False), "blocked"),
    (pv.ProvisionPlan("fabric", ()), "provider"),
])
def test_every_contract_record_is_frozen(record, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(record, field, "changed")


def test_the_planned_providers_have_no_adapter():
    assert pv.DATABRICKS.adapter is False and pv.SNOWFLAKE.adapter is False
    assert pv.FABRIC.adapter is True and pv.LOCAL.adapter is True
