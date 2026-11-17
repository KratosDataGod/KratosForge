"""Workspace limits for connected tools: the edges the main tests don't reach. A resolver with no
engine, a resolver's own refusal, an undeclared capability, rows that can't be placed, records too
deep or from before workspace ids, receipt chains that go on, and workloads that can't be placed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import scope
from fabric_desktop.integrations.core import IntegrationError


def cap(workspaces, title="Run a notebook"):
    return SimpleNamespace(id="x.run", title=title, workspaces=workspaces, input_schema={"properties": {}})


def test_a_resolver_without_an_engine_cant_tell():
    assert scope.targets(cap(lambda s, a, c: {"ws"}), None, {}, None) is None


def test_a_resolver_s_own_refusal_is_raised_as_it_is():
    refusal = IntegrationError("not_found", "No notebook called x")

    def resolver(service, arguments, ctx):
        raise refusal

    with pytest.raises(IntegrationError) as raised:
        scope.targets(cap(resolver), object(), {}, None)
    assert raised.value is refusal


def test_any_other_resolver_failure_becomes_the_engine_s_message():
    def resolver(service, arguments, ctx):
        raise KeyError("name")

    with pytest.raises(IntegrationError):
        scope.targets(cap(resolver), object(), {}, None)


def test_a_resolver_s_answer_is_read_as_workspace_ids():
    assert scope.targets(cap(lambda s, a, c: ["ws-1", 2]), object(), {}, None) == {"ws-1", "2"}
    assert scope.targets(cap(lambda s, a, c: None), object(), {}, None) is None
    assert scope.targets(cap(scope.UNKNOWN), object(), {}, None) is None
    assert scope.targets(cap(None), object(), {}, None) is None


def test_a_limited_tool_is_refused_a_capability_that_cant_be_confined():
    for declared in (None, scope.UNKNOWN):
        with pytest.raises(IntegrationError) as raised:
            scope.enforce(cap(declared), frozenset({"ws-1"}), lambda: set())
        assert raised.value.code == "permission_denied" and "run a notebook" in raised.value.message
    scope.enforce(cap(None), None, lambda: None)  # no limit: anything goes


def test_a_row_that_cant_be_placed_or_fails_is_left_out():
    ctx = SimpleNamespace(workspaces=frozenset({"ws-1"}))

    def where(row):
        if row == "broken":
            raise ValueError("unreadable")
        return {"ws-1": {"ws-1"}, "other": {"ws-2"}, "nowhere": None}[row]

    assert scope.keep(ctx, ["ws-1", "broken", "other", "nowhere"], where) == ["ws-1"]


def test_a_record_too_deep_to_read_cant_be_placed():
    deep: dict = {"workspace_id": "ws-1"}
    for _ in range(scope._MAX_DEPTH + 2):
        deep = {"child": deep}
    assert scope.located(deep) is None
    assert scope.located({"lakehouse_name": "LH_Gold"}) is None
    assert scope.located({"items": [{"workspace_id": "a"}, {"workspace_id": "b"}]}, "text") == {"a", "b"}


def test_a_record_that_isnt_a_dict_or_predates_workspace_ids_cant_be_placed():
    assert scope.record_of(["not", "a", "record"]) is None
    assert scope.record_of({"lakehouse": {"name": "LH_Gold", "workspace_name": "Sales"}}) is None
    assert scope.record_of({}) is None  # must be somewhere
    assert scope.record_of({"lakehouse": {"workspace_id": "ws-1", "id": "lh"}}) == {"ws-1"}


class Receipts:
    def __init__(self, bodies):
        self.bodies = bodies

    def receipt_get(self, rid):
        return {"receipt": self.bodies[rid]}


def test_a_receipt_chain_longer_than_its_limit_cant_be_placed():
    bodies = {f"r{i}": {"kind": "verification", "proof": {"receipt": f"r{i + 1}"}, "target": {"workspace_id": "ws"}}
              for i in range(scope._MAX_PARENTS + 1)}
    assert scope.receipt_of(Receipts(bodies), "r0") is None


def test_a_receipt_naming_a_place_without_its_id_cant_be_placed():
    bodies = {"r0": {"kind": "verification", "proof": {"receipt": "r1"}},
              "r1": {"kind": "proof", "lakehouse": {"lakehouse_name": "LH"}}}
    assert scope.receipt_of(Receipts(bodies), "r0") is None
    bodies["r1"] = {"kind": "proof", "lakehouse": {"workspace_id": "ws-9", "lakehouse_id": "lh"}}
    assert scope.receipt_of(Receipts(bodies), "r0") == {"ws-9"}


def test_no_metadata_project_open_reaches_no_workspace():
    assert scope.metadata_of({}) == set()
    assert scope.metadata_of({"path": "/p", "entities": [], "environments": {"dev": {"workspace_name": "Sales"}}}) is None


def test_an_environment_s_designations_are_read_and_odd_ones_skipped():
    cfg = {"workspace_id": "ws-main", "workspaces": {"gold": {"id": "ws-gold"}, "odd": "ws-text", "empty": {}}}
    assert scope.environment_of(cfg) == {"ws-main", "ws-gold"}
    assert scope.environment_of({"workspaces": {"gold": {"name": "Gold"}}}) is None


class Meta:
    def __init__(self, envs):
        self.envs = envs

    def meta_project(self):
        return {"path": "/p", "environments": self.envs}


def test_a_meta_entity_run_whose_inputs_cant_be_placed_is_unknown():
    record = {"kind": "meta_entity", "deployments": [
        {"env": "dev", "actions": ["run"], "location": {"kind": "local"}}]}
    assert scope.workload_of(Meta({"dev": {"workspaces": {"gold": {"name": "Gold"}}}}), record, "run", "dev") is None
    assert scope.workload_of(Meta({"dev": {"workspace_id": "ws-dev"}}), record, "run", "dev") == {"ws-dev"}
    assert scope.workload_of(Meta({}), record, "run_schedule", "dev") is None  # no deployment for that action
