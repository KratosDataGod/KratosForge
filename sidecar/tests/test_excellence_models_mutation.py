"""excellence/catalog_models.py's controls, called directly (mutation testing, batch 349).

test_excellence_models.py runs the controls through an assessment, where a definition that wasn't read never
reaches a control. These call each control with a payload of their own: a payload that isn't a read model, the
exact wording of names, counts and plurals, and Direct Lake's applicability and behaviors.
"""

from __future__ import annotations

import pytest

from fabric_desktop.excellence import catalog_models as cm, controls as ct

RID = "rec-1"


def ctx(**payload) -> ct.Context:
    base = {"kind": "model", "relationships": 2}
    base.update(payload)
    return ct.Context({"type": "item", "id": "m1"}, {cm.KIND: [{"id": RID, "payload": base}]})


def raw(payload) -> ct.Context:
    return ct.Context({"type": "item", "id": "m1"}, {cm.KIND: [{"id": RID, "payload": payload}]})


NOT_READ = {"outcome": "not_evaluated", "evidence_ids": [], "text": "The model's definition wasn't inspected.",
            "needs": "the semantic model's definition"}
EVALUATORS = [cm._bidirectional, cm._row_security, cm._types_differ, cm._text_keys, cm._visible_keys]


@pytest.mark.parametrize("payload", [None, {"read": False}, {"kind": "model"}, {"relationships": 3},
                                     {"kind": "model", "relationships": "3"}, {"kind": "report", "relationships": 3}])
@pytest.mark.parametrize("evaluate", EVALUATORS)
def test_a_payload_that_isnt_a_read_model_is_not_evaluated(evaluate, payload):
    assert evaluate(raw(payload)) == NOT_READ


@pytest.mark.parametrize("evaluate", [cm._types_differ, cm._text_keys, cm._visible_keys])
def test_a_read_model_without_rule_results_is_not_evaluated(evaluate):
    assert evaluate(ctx()) == NOT_READ
    assert evaluate(ctx(rules=["SM001"])) == NOT_READ


def test_a_relationship_filtering_both_ways_is_named_with_a_singular_verb():
    got = cm._bidirectional(ctx(bidirectional_count=1, bidirectional=["A.k to B.k"]))
    assert got["outcome"] == "fail" and got["evidence_ids"] == [RID]
    assert got["text"] == "1 relationship filters in both directions: A.k to B.k."
    assert got["detail"] == {"relationships": ["A.k to B.k"], "count": 1}


def test_more_names_than_five_are_counted_not_listed():
    names = [f"r{i}" for i in range(7)]
    got = cm._bidirectional(ctx(bidirectional_count=7, bidirectional=names))
    assert got["text"] == "7 relationships filter in both directions: r0, r1, r2, r3, r4 and 2 more."
    got = cm._bidirectional(ctx(bidirectional_count=6, bidirectional=names[:6]))
    assert got["text"] == "6 relationships filter in both directions: r0, r1, r2, r3, r4 and 1 more."
    got = cm._bidirectional(ctx(bidirectional_count=5, bidirectional=names[:5]))
    assert got["text"] == "5 relationships filter in both directions: r0, r1, r2, r3, r4."
    got = cm._bidirectional(ctx(bidirectional_count=2, bidirectional=names[:2]))
    assert got["text"] == "2 relationships filter in both directions: r0, r1."


def test_no_relationship_filtering_both_ways_passes_with_the_count():
    got = cm._bidirectional(ctx(relationships=4))
    assert got == {"outcome": "pass", "evidence_ids": [RID],
                   "text": "None of its 4 relationships filters in both directions."}


def test_row_filters_name_the_roles_and_tables():
    got = cm._row_security(ctx(row_filter_roles=["Owners"], row_filter_tables=["Sales"]))
    assert got == {"outcome": "pass", "evidence_ids": [RID], "text": "1 role filters rows (Owners), on Sales."}
    got = cm._row_security(ctx(row_filter_roles=["A", "B"], row_filter_tables=["Sales", "Region"]))
    assert got["text"] == "2 roles filter rows (A, B), on Sales, Region."


@pytest.mark.parametrize("roles,why", [(None, "It has no security role"), (0, "It has no security role"),
                                       (1, "Its 1 role filters no rows"), (3, "Its 3 roles filter no rows")])
def test_no_row_filter_says_whether_there_are_roles(roles, why):
    got = cm._row_security(ctx(roles=roles))
    assert got["outcome"] == "fail" and got["evidence_ids"] == [RID]
    assert got["text"] == (f"{why}, so everyone who can read the model sees every row. That is fine when every "
                           "reader may see all of it.")
    assert got["detail"] == {"roles": roles or 0}


@pytest.mark.parametrize("tables,applies", [(0, False), (None, False), (1, True), (3, True)])
def test_fallback_applies_only_with_a_direct_lake_table(tables, applies):
    assert cm._fallback_applies(ctx(direct_lake_tables=tables)) is applies


def test_fallback_applicability_is_unknown_when_the_model_wasnt_read():
    assert cm._fallback_applies(raw({"read": False})) is None
    assert cm._fallback_applies(raw(None)) is None


def test_fallback_wording_and_detail_per_behavior():
    got = cm._fallback(ctx(direct_lake_tables=1, direct_lake_behavior="DirectQueryOnly"))
    assert got["outcome"] == "fail"
    assert got["text"] == "Its one Direct Lake table is set to DirectQuery only, so no query uses Direct Lake."
    assert got["detail"] == {"behavior": "DirectQueryOnly", "tables": 1}
    got = cm._fallback(ctx(direct_lake_tables=2, direct_lake_behavior="Automatic"))
    assert got["text"] == ("Its 2 Direct Lake tables can fall back to DirectQuery without telling anyone (Direct "
                           "Lake behavior is Automatic).")
    assert got["detail"] == {"behavior": "Automatic", "tables": 2}
    got = cm._fallback(ctx(direct_lake_tables=0))
    assert got["detail"] == {"behavior": "automatic", "tables": 0}
    assert got["text"].startswith("Its 0 Direct Lake tables can fall back")
    got = cm._fallback(ctx(direct_lake_tables=None))
    assert got["detail"] == {"behavior": "automatic", "tables": 0}


def test_fallback_passes_on_direct_lake_only_and_on_onelake():
    got = cm._fallback(ctx(direct_lake_tables=1, direct_lake_behavior="directLakeOnly"))
    assert got["outcome"] == "pass" and got["text"].startswith("Its one Direct Lake table is set to Direct Lake only")
    got = cm._fallback(ctx(direct_lake_tables=3, direct_lake_behavior="Automatic", direct_lake_sources=["onelake"]))
    assert got == {"outcome": "pass", "evidence_ids": [RID], "text": "Its 3 Direct Lake tables read through "
                   "OneLake, which doesn't fall back to DirectQuery."}


def test_a_matched_rule_fails_with_its_count_and_objects():
    got = cm._types_differ(ctx(rules={"SM001": {"count": 1, "objects": ["Sales.k"]}}))
    assert got == {"outcome": "fail", "evidence_ids": [RID], "benefit": None,
                   "text": "A relationship joins columns of different types (SM001). 1 found: Sales.k.",
                   "detail": {"rule": "SM001", "count": 1, "objects": ["Sales.k"]}}
    got = cm._visible_keys(ctx(rules={"SM004": {"objects": ["Sales.k"]}}))
    assert got["detail"] == {"rule": "SM004", "count": 0, "objects": ["Sales.k"]}
    assert got["text"] == "A relationship's key column is visible in the fact table (SM004). 0 found: Sales.k."


def test_a_rule_that_couldnt_be_checked_names_the_relationships():
    got = cm._text_keys(ctx(rules={}, types_unknown=["A.k to B.k"]))
    assert got == {"outcome": "not_evaluated", "evidence_ids": [], "needs": "the relationship columns' definitions",
                   "text": "The definition doesn't show what this checks for 1 relationship (A.k to B.k)."}
    got = cm._visible_keys(ctx(rules={}, columns_unknown=["a", "b"], types_unknown=["x"]))
    assert got["text"] == "The definition doesn't show what this checks for 2 relationships (a, b)."


def test_a_clean_rule_passes_with_the_relationships_checked():
    got = cm._text_keys(ctx(rules={"SM001": {"count": 1, "objects": ["x"]}}, relationships=1))
    assert got == {"outcome": "pass", "evidence_ids": [RID],
                   "text": "Every relationship key is a whole number (SM002, 1 relationship checked)."}
    got = cm._visible_keys(ctx(rules={}, relationships=0))
    assert got["text"] == "Every fact-side relationship key is hidden (SM004, 0 relationships checked)."
