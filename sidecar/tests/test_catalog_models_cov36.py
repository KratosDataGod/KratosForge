"""Semantic model controls (FEA-SM-001..006) on evidence that isn't a read model definition, and the
Direct Lake fallback control's applicability and its DirectQuery-only case. Each control's evaluate
is called with a Context holding one item.model record, the way assess calls it."""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import catalog_models as cm
from fabric_desktop.excellence import controls as ct

BY_ID = {c.id: c for c in cm.CONTROLS}
READ_SHAPE_CONTROLS = ["FEA-SM-001", "FEA-SM-002", "FEA-SM-004", "FEA-SM-005", "FEA-SM-006"]


def ctx(payload: dict | None) -> ct.Context:
    return ct.Context(subject={"id": "m"}, _evidence={cm.KIND: [{"id": "ev-1", "payload": payload}]})


def empty_ctx() -> ct.Context:
    return ct.Context(subject={"id": "m"}, _evidence={cm.KIND: []})


def model(**extra) -> dict:
    return {"kind": "model", "relationships": 3, **extra}


@pytest.mark.parametrize("cid", READ_SHAPE_CONTROLS)
@pytest.mark.parametrize("payload", [
    None,                                          # no payload at all
    {},                                            # empty payload
    {"kind": "error", "relationships": 3},         # a record that isn't a model
    {"kind": "model"},                             # a model without a relationship count
    {"kind": "model", "relationships": "3"},       # a count that isn't a whole number
], ids=["none", "empty", "not-model", "no-count", "text-count"])
def test_a_definition_that_wasnt_read_leaves_each_control_not_evaluated(cid, payload):
    got = BY_ID[cid].evaluate(ctx(payload))
    assert got == {"outcome": "not_evaluated", "evidence_ids": [],
                   "text": "The model's definition wasn't inspected.", "needs": "the semantic model's definition"}


@pytest.mark.parametrize("cid", ["FEA-SM-004", "FEA-SM-005", "FEA-SM-006"])
def test_a_rule_control_needs_the_rules_table_too(cid):
    got = BY_ID[cid].evaluate(ctx(model(rules=["SM001"])))
    assert got["outcome"] == "not_evaluated" and got["needs"] == "the semantic model's definition"


def test_fallback_applies_only_to_a_read_model_with_direct_lake_tables():
    applies = BY_ID["FEA-SM-003"].applies
    assert applies is cm._fallback_applies
    assert applies(empty_ctx()) is None                     # no record
    assert applies(ctx(None)) is None                       # record with no payload
    assert applies(ctx({"kind": "error"})) is None          # not a read model
    assert applies(ctx(model())) is False                   # no Direct Lake table
    assert applies(ctx(model(direct_lake_tables=0))) is False
    assert applies(ctx(model(direct_lake_tables=2))) is True


@pytest.mark.parametrize("behavior", ["DirectQueryOnly", "directqueryonly", "DIRECTQUERYONLY"])
def test_direct_query_only_fails_and_says_no_query_uses_direct_lake(behavior):
    got = BY_ID["FEA-SM-003"].evaluate(ctx(model(direct_lake_tables=1, direct_lake_behavior=behavior)))
    assert got["outcome"] == "fail" and got["evidence_ids"] == ["ev-1"]
    # The verb is pinned by test_excellence_models; this test checks the start and the end.
    assert got["text"].startswith("Its one Direct Lake table ")
    assert got["text"].endswith(" set to DirectQuery only, so no query uses Direct Lake.")
    assert got["detail"] == {"behavior": behavior, "tables": 1}


def test_direct_query_only_counts_tables_in_the_plural():
    got = BY_ID["FEA-SM-003"].evaluate(ctx(model(direct_lake_tables=4, direct_lake_behavior="DirectQueryOnly")))
    assert got["text"].startswith("Its 4 Direct Lake tables are set to DirectQuery only")


def test_onelake_wins_over_a_direct_query_only_setting():
    got = BY_ID["FEA-SM-003"].evaluate(ctx(model(direct_lake_tables=2, direct_lake_behavior="DirectQueryOnly",
                                                 direct_lake_sources=["onelake"])))
    assert got["outcome"] == "pass" and "through OneLake" in got["text"]


def test_a_missing_behavior_is_read_as_automatic():
    got = BY_ID["FEA-SM-003"].evaluate(ctx(model(direct_lake_tables=1)))
    assert got["outcome"] == "fail" and got["detail"] == {"behavior": "automatic", "tables": 1}


def test_bidirectional_names_at_most_five_and_counts_the_rest():
    names = [f"r{i}" for i in range(8)]
    got = BY_ID["FEA-SM-001"].evaluate(ctx(model(bidirectional_count=8, bidirectional=names)))
    assert got["outcome"] == "fail"
    assert got["text"] == "8 relationships filter in both directions: r0, r1, r2, r3, r4 and 3 more."
    one = BY_ID["FEA-SM-001"].evaluate(ctx(model(bidirectional_count=1, bidirectional=["r0"])))
    assert one["text"] == "1 relationship filters in both directions: r0."
