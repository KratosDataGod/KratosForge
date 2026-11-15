"""Workspace limits for connected tools: the checks mutation testing found untested (batch 27,
docs/quality/mutation-testing.md). The refusal after an approval wait, how deep a record is read,
how far a receipt chain is followed, and the resolvers that read a notebook, a record, an execution
and the metadata project."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fabric_desktop.integrations import scope
from fabric_desktop.integrations.core import IntegrationError


def ctx(workspaces):
    return SimpleNamespace(workspaces=frozenset(workspaces) if workspaces is not None else None)


# ---- enforce_on: what a handler reached after an approval wait

def test_reaching_only_the_tool_s_workspaces_is_allowed():
    scope.enforce_on(ctx({"a", "b"}), {"a"})
    scope.enforce_on(ctx({"a"}), set())


@pytest.mark.parametrize("reached", [{"b"}, {"a", "b"}, None])
def test_reaching_another_workspace_or_an_unknown_one_is_refused(reached):
    with pytest.raises(IntegrationError) as raised:
        scope.enforce_on(ctx({"a"}), reached)
    assert raised.value.code == "permission_denied"
    assert raised.value.message == "This tool is limited to other workspaces."
    assert "Settings > Integrations" in raised.value.hint


@pytest.mark.parametrize("reached", [{"b"}, None])
def test_a_tool_with_no_limit_reaches_anything(reached):
    scope.enforce_on(ctx(None), reached)
    scope.enforce_on(SimpleNamespace(), reached)


# ---- located: how deep a record is read

def nest(depth, inner):
    """``inner`` wrapped so that it is read at ``depth`` (a list at each level below the top)."""
    value = inner
    for _ in range(depth):
        value = [value]
    return value


def test_a_workspace_id_at_the_deepest_level_read_is_found():
    assert scope.located(nest(12, {"workspace_id": "w"})) == {"w"}


def test_a_record_deeper_than_that_can_t_be_checked():
    assert scope.located(nest(13, {"workspace_id": "w"})) is None


def test_the_depth_counts_dicts_as_well_as_lists():
    value = {"workspace_id": "w"}
    for _ in range(12):
        value = {"inner": value}
    assert scope.located(value) == {"w"}
    assert scope.located({"inner": value}) is None


def test_every_part_is_read_from_the_top():
    assert scope.located(nest(12, {"workspace_id": "a"}), {"workspace_id": "b"}) == {"a", "b"}
    assert scope.located({"workspace_id": "a"}, nest(13, {"workspace_id": "b"})) is None


# ---- receipt chains

def receipts(chain):
    """A service whose receipts are ``chain``: id -> body."""
    return SimpleNamespace(receipt_get=lambda rid: {"receipt": chain[rid]})


def verification(of):
    return {"kind": "verification", "proof": {"receipt": of}}


def test_a_chain_of_three_receipts_is_followed_to_its_proof():
    svc = receipts({"r1": verification("r2"), "r2": verification("r3"),
                    "r3": {"kind": "run", "target": {"workspace_id": "w"}}})
    assert scope.receipt_of(svc, "r1") == {"w"}


def test_a_chain_longer_than_three_can_t_be_checked():
    svc = receipts({"r1": verification("r2"), "r2": verification("r3"), "r3": verification("r4"),
                    "r4": {"kind": "run", "target": {"workspace_id": "w"}}})
    assert scope.receipt_of(svc, "r1") is None


# ---- resolvers

def test_a_notebook_reaches_its_lakehouse_s_workspace_and_where_it_publishes():
    nb = {"lakehouse": {"workspace_id": "lake-ws", "lakehouse_id": "l"}, "workspace": {"workspace_id": "pub-ws"}}
    assert scope.notebook_of(nb) == {"lake-ws", "pub-ws"}
    assert scope.notebook_of({"lakehouse": {"workspace_id": "lake-ws"}}) == {"lake-ws"}
    assert scope.notebook_of({"workspace": {"workspace_id": "pub-ws"}}) == {"pub-ws"}


def test_the_notebook_resolver_adds_the_workspace_the_call_names():
    svc = SimpleNamespace(notebooks=SimpleNamespace(load=lambda name: {"lakehouse": {"workspace_id": "lake-ws"}}))
    assert scope.notebook(svc, {"name": "nb", "workspace_id": "target"}, None) == {"lake-ws", "target"}
    assert scope.notebook(svc, {"name": "nb"}, None) == {"lake-ws"}


def test_the_notebook_resolver_can_t_tell_for_a_notebook_it_can_t_place():
    svc = SimpleNamespace(notebooks=SimpleNamespace(load=lambda name: {"lakehouse": {"lakehouse_name": "Sales"}}))
    assert scope.notebook(svc, {"name": "nb", "workspace_id": "target"}, None) is None


def test_a_record_whose_lakehouse_has_no_workspace_id_can_t_be_checked():
    # its lakehouse is named by id only, so nothing else in the record says where it is
    record = {"lakehouse": {"id": "l"}, "target": {"workspace_id": "w"}}
    assert scope.record_of(record) is None
    assert scope.record_of({"lakehouse": {"id": "l", "workspace_id": "w"}}) == {"w"}


def test_an_execution_with_no_call_context_is_read_for_no_client():
    seen = []

    class Executions:
        def get(self, execution_id, client):
            seen.append((execution_id, client))
            return {"target": {"workspace_id": "w"}}

    svc = SimpleNamespace(_integration_executions=Executions())
    assert scope.execution(svc, {"execution_id": "e1"}, None) == {"w"}
    caller = SimpleNamespace(caller=SimpleNamespace(client_id="tool-1"))
    assert scope.execution(svc, {"execution_id": "e2"}, caller) == {"w"}
    assert seen == [("e1", ""), ("e2", "tool-1")]


def test_the_metadata_project_reaches_the_workspaces_its_entities_name():
    meta = {"path": "/p", "entities": [{"target": {"workspace_id": "ent-ws"}}],
            "environments": {"test": {"workspace_id": "env-ws"}}}
    assert scope.metadata_of(meta) == {"ent-ws", "env-ws"}
    assert scope.metadata_of({"path": "/p", "entities": [{"workspace_id": "ent-ws"}]}) == {"ent-ws"}
