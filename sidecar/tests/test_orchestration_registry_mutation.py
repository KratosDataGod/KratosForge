"""Mutation-kill tests for orchestration/registry.py: the classification order, what register and
StaticAdapter return or raise, and the pipeline report summary. No network and no Fabric."""

from __future__ import annotations

import dataclasses
import json
from itertools import pairwise

import pytest

from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration.registry import (ActivityRegistry, ActivitySupport, Planned, PropertySupport,
                                                   StaticAdapter, check_entry)


class Runner:
    can_execute = True

    def validate(self, activity, ctx):
        return []

    def execute(self, activity, run):
        return None


def entry(**kw):
    base = dict(type="X", title="X", classification="static", evidence="unit_tests",
                fabric_capacity="metered_in_fabric", adapter=StaticAdapter())
    return ActivitySupport(**(base | kw))


def by_class(classification):
    extra = {"local": {"adapter": Runner()}, "simulated": {"adapter": Runner()},
             "cloud": {"fabric_capacity": "fabric_only"}, "unsupported": {"adapter": None}}
    return entry(type=classification.title(), classification=classification, **extra.get(classification, {}))


def registry(*classes):
    reg = ActivityRegistry()
    for c in classes:
        reg.register(by_class(c))
    return reg


def pipeline(*types):
    acts = [{"name": f"a{i}", "type": t, "dependsOn": [], "typeProperties": {}} for i, t in enumerate(types)]
    return loads(json.dumps({"name": "p", "properties": {"activities": acts}}))


ORDER = ["local", "simulated", "static", "cloud", "unsupported"]


@pytest.mark.parametrize("lower,higher", list(pairwise(ORDER)))
def test_overall_is_the_less_capable_of_neighbours_in_either_order(lower, higher):
    reg = registry(*ORDER)
    for types in ((lower.title(), higher.title()), (higher.title(), lower.title())):
        assert reg.compatibility(pipeline(*types))["summary"]["overall"] == higher


def test_overall_of_an_empty_pipeline_is_static_and_it_does_not_run_here():
    s = registry().compatibility(pipeline())["summary"]
    assert s["overall"] == "static"
    assert s["activities"] == 0
    assert s["runs_on_this_pc"] is False


@pytest.mark.parametrize("today,planned", [(b, a) for a, b in pairwise(ORDER)])
def test_a_plan_one_step_more_capable_is_accepted(today, planned):
    e = by_class(today)
    e = ActivitySupport(**{**e.__dict__, "planned": Planned(planned, "ORCH-009")})
    assert not [p for p in check_entry(e) if "more capable" in p]


@pytest.mark.parametrize("today,planned", list(pairwise(ORDER)) + [(c, c) for c in ORDER])
def test_a_plan_that_is_not_more_capable_is_refused(today, planned):
    e = by_class(today)
    e = ActivitySupport(**{**e.__dict__, "planned": Planned(planned, "ORCH-009")})
    assert "planned must be more capable than today's classification" in check_entry(e)


def test_an_unknown_planned_classification_is_reported_not_raised():
    e = entry(planned=Planned("magic", "ORCH-009"))
    assert "planned classification is unknown" in check_entry(e)


def test_static_adapter_refuses_to_execute():
    act = pipeline("Static").pipeline.all_activities()[0]
    with pytest.raises(NotImplementedError, match="Static can't run on this PC yet"):
        StaticAdapter().execute(act, None)


def test_register_returns_the_entry():
    e = by_class("static")
    assert ActivityRegistry().register(e) is e


@pytest.mark.parametrize("value,field", [
    (lambda: PropertySupport("checked"), "status"),
    (lambda: Planned("local", "ORCH-003"), "stage"),
    (lambda: entry(), "classification"),
])
def test_registry_records_are_frozen(value, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value(), field, "changed")


@pytest.mark.parametrize("evidence,verified", [("fabric_tested", True), ("unit_tests", False)])
def test_a_row_is_verified_only_when_fabric_tested(evidence, verified):
    reg = ActivityRegistry()
    reg.register(entry(type="Static", evidence=evidence))
    rep = reg.compatibility(pipeline("Static"))
    assert rep["activities"][0]["verified"] is verified
    assert rep["summary"]["verified_against_fabric"] == (["a0"] if verified else [])


@pytest.mark.parametrize("types,here", [(["Local"], True), (["Simulated", "Local"], True), (["Static"], False),
                                        (["Local", "Cloud"], False), (["Unsupported"], False)])
def test_runs_on_this_pc_only_when_every_activity_runs_here(types, here):
    s = registry(*ORDER).compatibility(pipeline(*types))["summary"]
    assert s["runs_on_this_pc"] is here
    assert s["needs_fabric_capacity"] == [f"a{i}" for i, t in enumerate(types) if t not in ("Local", "Simulated")]


@pytest.mark.parametrize("classification", ["local", "simulated"])
def test_running_here_without_any_adapter_is_refused(classification):
    assert f"{classification} needs an adapter that can execute" in check_entry(
        entry(classification=classification, adapter=None))
