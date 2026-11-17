"""Pipeline gotchas (orchestration/gotchas.py): the edges a mutation run showed no test pinned
(docs/quality/mutation-testing.md, batch 30). Reads definitions only; no network, no Fabric."""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from fabric_desktop.orchestration import gotchas, loads

GUID = "22222222-2222-2222-2222-222222222222"
WS = "00000000-0000-0000-0000-000000000000"


def pipeline(*activities):
    return loads(json.dumps({"name": "p", "properties": {"activities": list(activities)}}))


def act(name, typ, policy=None, **type_props):
    a = {"name": name, "type": typ, "dependsOn": [], "typeProperties": type_props}
    if policy is not None:
        a["policy"] = policy
    return a


def codes(imp):
    return [(d["code"], d["path"]) for d in gotchas.check(imp)]


@pytest.mark.parametrize("timeout, warned", [("0.12:00:00", False), ("0.12:00:01", True), ("12:00:01", True),
                                             ("0.11:59:59", False)])
def test_a_notebook_timeout_one_second_over_twelve_hours_is_warned_about(timeout, warned):
    imp = pipeline(act("N", "TridentNotebook", policy={"timeout": timeout}, notebookId=GUID, workspaceId=WS))
    assert codes(imp) == ([("notebook_timeout_over_12_hours", "$.properties.activities[0].policy.timeout")]
                          if warned else [])
    assert gotchas.NOTEBOOK_MAX_SECONDS == 43200


def test_the_global_parameter_warning_names_the_parameter_alone():
    imp = pipeline(act("S", "SetVariable", variableName="v",
                       value={"value": "@pipeline().globalParameters.region", "type": "Expression"}))
    [d] = gotchas.check(imp)
    assert d["message"].startswith("S: uses pipeline().globalParameters.region. Fabric pipelines")


def test_a_key_vault_secret_is_pointed_at_where_it_is_read():
    ref = act("Call", "WebActivity", method="GET", url="https://api.example.com",
              authentication={"type": "Basic", "username": "u", "password": {
                  "type": "AzureKeyVaultSecret", "secretName": "pw",  # pragma: allowlist secret (a secret's name in a test definition, not a value)
                  "store": {"referenceName": "Vault", "type": "LinkedServiceReference"}}})
    assert codes(pipeline(ref)) == [("key_vault_linked_service",
                                     "$.properties.activities[0].typeProperties.authentication.password")]
    linked = act("Use", "SetVariable", variableName="v", value="@linkedService().secret")
    assert codes(pipeline(linked)) == [("key_vault_linked_service", "$.properties.activities[0].typeProperties.value")]
    spaced = act("Use", "SetVariable", variableName="v", value="@linkedService ( ).secret")
    assert [c for c, _ in codes(pipeline(spaced))] == ["key_vault_linked_service"]


def test_a_key_vault_call_by_expression_url_is_checked_too():
    get = act("Get", "WebActivity", method="GET", url={"value": "https://KV.Vault.Azure.Net/secrets/x", "type": "Expression"})
    assert codes(pipeline(get)) == [("secret_in_run_history", "$.properties.activities[0].policy")]
    get["policy"] = {"secureOutput": "true"}   # only a real true makes it secure
    assert [c for c, _ in codes(pipeline(get))] == ["secret_in_run_history"]


def test_a_gotcha_is_fixed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        gotchas.GOTCHAS[0].title = "changed"


def _activity(name, container, typ="ForEach", **tp):
    return SimpleNamespace(name=name, container=container, type=typ, type_properties=tp, path=f"$.{name}")


class _Pipeline:
    def __init__(self, *acts):
        self.by_name = {a.name: a for a in acts}

    def find(self, name):
        return self.by_name.get(name)


def test_the_containers_walk_stops_after_a_hundred_even_on_a_loop():
    # A definition that names itself as its own container can't come from the loader, but the walk
    # still has to end: it stops at 100 rather than going round for ever.
    loop = _activity("L", "L")
    imp = SimpleNamespace(pipeline=_Pipeline(loop))
    out = gotchas._containers(loop, imp)
    assert len(out) == 100 and all(a is loop for a in out)


def test_the_containers_walk_ends_at_the_top_or_at_a_missing_parent():
    top = _activity("Top", None)
    mid = _activity("Mid", "Top")
    leaf = _activity("Leaf", "Mid", typ="Wait")
    imp = SimpleNamespace(pipeline=_Pipeline(top, mid, leaf))
    assert gotchas._containers(leaf, imp) == [mid, top]
    assert gotchas._containers(top, imp) == []
    assert gotchas._containers(_activity("Lost", "Gone"), imp) == []


def test_a_set_variable_in_a_sequential_foreach_inside_a_parallel_one_is_still_warned_about():
    outer = _activity("Outer", None)
    inner = _activity("Inner", "Outer", isSequential=True)
    setv = _activity("S", "Inner", typ="SetVariable")
    imp = SimpleNamespace(pipeline=_Pipeline(outer, inner, setv))
    [d] = gotchas._foreach_limits(setv, imp)
    assert d.code == "set_variable_in_parallel_foreach" and "ForEach Outer," in d.message
    alone = SimpleNamespace(pipeline=_Pipeline(_activity("Seq", None, isSequential=True)))
    assert gotchas._foreach_limits(_activity("S", "Seq", typ="SetVariable"), alone) == []
