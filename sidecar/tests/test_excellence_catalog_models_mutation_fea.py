"""Mutation batch 261: excellence/catalog_models.py (docs/quality/mutation-testing.md). The survivors were
the not-evaluated answer for a model that wasn't inspected (dropping it made the control break instead),
the rule that an inspection needs both its kind and a relationship count, a model set to DirectQuery only,
whether the Direct Lake control applies to a model with a single Direct Lake table, and the wording of
the findings: singular and plural, the first five names and "and N more"."""

from __future__ import annotations

from fabric_desktop.excellence import catalog_models as cm
from fabric_desktop.excellence import controls as ct

RID = "ev-0000000000000001"
READ = {"kind": "model", "relationships": 4}


def ctx(payload: dict | None) -> ct.Context:
    return ct.Context({"type": "item", "id": "sm"}, {cm.KIND: [{"id": RID, "payload": payload}]})


def test_the_names_show_five_and_count_the_rest():
    names = ["a", "b", "c", "d", "e", "f"]
    assert cm._names(names, 7) == "a, b, c, d, e and 2 more"
    assert cm._names(names, 6) == "a, b, c, d, e and 1 more"
    assert cm._names(names[:5], 5) == "a, b, c, d, e"
    assert cm._names(["a"], 1) == "a"


def test_one_and_many():
    assert cm._s(1, "one", "many") == "one"
    assert cm._s(0, "one", "many") == "many"
    assert cm._s(2, "one", "many") == "many"


def test_a_model_not_inspected_is_not_evaluated_by_every_control():
    for evaluate in (cm._bidirectional, cm._row_security, cm._types_differ, cm._text_keys, cm._visible_keys):
        for payload in (None, {}, {"kind": "model"}, {"relationships": 4}, {"kind": "notebook", "relationships": 4}):
            got = evaluate(ctx(payload))
            assert got == {"outcome": "not_evaluated", "evidence_ids": [], "needs": "the semantic model's definition",
                           "text": "The model's definition wasn't inspected."}, (evaluate.__name__, payload)


def test_a_rule_control_needs_the_rules_the_inspection_kept():
    got = cm._types_differ(ctx({**READ, "types_unknown": []}))
    assert got["outcome"] == "not_evaluated" and got["needs"] == "the semantic model's definition"


def test_bidirectional_wording_singular_plural_and_more():
    got = cm._bidirectional(ctx({**READ, "bidirectional_count": 1, "bidirectional": ["A.k to B.k"]}))
    assert got["outcome"] == "fail" and got["text"] == "1 relationship filters in both directions: A.k to B.k."
    names = [f"R{i}" for i in range(6)]
    got = cm._bidirectional(ctx({**READ, "bidirectional_count": 8, "bidirectional": names}))
    assert got["text"] == "8 relationships filter in both directions: R0, R1, R2, R3, R4 and 3 more."
    assert got["detail"] == {"relationships": names, "count": 8}
    assert cm._bidirectional(ctx(READ))["text"] == "None of its 4 relationships filters in both directions."


def test_row_security_names_the_roles_and_the_tables_they_filter():
    got = cm._row_security(ctx({**READ, "row_filter_roles": ["Owners"], "row_filter_tables": ["Sales", "Region"]}))
    assert got == {"outcome": "pass", "evidence_ids": [RID], "text": "1 role filters rows (Owners), on Sales, Region."}
    got = cm._row_security(ctx({**READ, "roles": 2, "row_filter_roles": [], "row_filter_tables": []}))
    assert got["outcome"] == "fail" and got["text"].startswith("Its 2 roles filter no rows, so everyone")
    assert got["detail"] == {"roles": 2}
    got = cm._row_security(ctx({**READ, "roles": 0}))
    assert got["text"].startswith("It has no security role, so everyone")


def test_the_direct_lake_control_applies_from_one_direct_lake_table():
    assert cm._fallback_applies(ctx({**READ, "direct_lake_tables": 1})) is True
    assert cm._fallback_applies(ctx({**READ, "direct_lake_tables": 0})) is False
    assert cm._fallback_applies(ctx({"kind": "model"})) is None


def test_direct_query_only_and_automatic_both_fail_with_their_own_reason():
    got = cm._fallback(ctx({**READ, "direct_lake_tables": 3, "direct_lake_behavior": "DirectQueryOnly",
                             "direct_lake_sources": ["sql"]}))
    assert got["outcome"] == "fail"
    assert got["text"] == "Its 3 Direct Lake tables are set to DirectQuery only, so no query uses Direct Lake."
    assert got["detail"] == {"behavior": "DirectQueryOnly", "tables": 3}
    got = cm._fallback(ctx({**READ, "direct_lake_tables": 1, "direct_lake_behavior": "Automatic",
                             "direct_lake_sources": ["sql"]}))
    assert got["outcome"] == "fail" and got["text"].startswith("Its 1 Direct Lake table can fall back to DirectQuery")
    got = cm._fallback(ctx({**READ, "direct_lake_tables": 2, "direct_lake_behavior": "DirectLakeOnly"}))
    assert got["outcome"] == "pass" and got["text"].startswith("Its 2 Direct Lake tables are set to Direct Lake only")


def test_a_rule_finding_and_an_unknown_relationship_are_worded_with_their_counts():
    got = cm._types_differ(ctx({**READ, "rules": {"SM001": {"count": 6, "objects": list("abcdef")}}}))
    assert got["text"] == "A relationship joins columns of different types (SM001). 6 found: a, b, c, d, e and 1 more."
    got = cm._types_differ(ctx({**READ, "rules": {}, "types_unknown": ["A.k to B.k"]}))
    assert got["outcome"] == "not_evaluated"
    assert got["text"] == "The definition doesn't show what this checks for 1 relationship (A.k to B.k)."
    got = cm._visible_keys(ctx({**READ, "relationships": 1, "rules": {}, "columns_unknown": []}))
    assert got["text"] == "Every fact-side relationship key is hidden (SM004, 1 relationship checked)."
