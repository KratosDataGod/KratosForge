"""Mutation batch 357: the semantic model controls' exact texts and edge values (catalog_models.py),
which tests/test_excellence_models.py checks only by outcome."""

from __future__ import annotations

from fabric_desktop.excellence import catalog_models as cm
from fabric_desktop.excellence import controls as ct


def ctx(**payload):
    return ct.Context({"id": "sm"}, {cm.KIND: [{"id": "e1", "payload": payload}]})


def model(**extra):
    return {"kind": "model", "relationships": 3, **extra}


NOT_READ = {"outcome": "not_evaluated", "evidence_ids": [], "text": "The model's definition wasn't inspected.",
            "needs": "the semantic model's definition"}


def test_names_lists_five_and_counts_the_rest():
    assert cm._names(["a", "b"], 2) == "a, b"
    assert cm._names(list("abcdef"), 6) == "a, b, c, d, e and 1 more"
    assert cm._names(list("abcde"), 5) == "a, b, c, d, e"
    assert cm._names(["a"], 3) == "a and 2 more"


def test_one_and_many():
    assert cm._s(1, "one", "many") == "one"
    assert cm._s(0, "one", "many") == "many"
    assert cm._s(2, "one", "many") == "many"


def test_a_payload_is_read_only_with_a_model_kind_and_a_relationship_count():
    assert cm._read({"kind": "model", "relationships": 0})
    assert not cm._read({"kind": "model"})
    assert not cm._read({"kind": "report", "relationships": 2})


def test_every_control_says_not_evaluated_when_the_definition_wasnt_read():
    for f in (cm._bidirectional, cm._row_security, cm._types_differ, cm._text_keys, cm._visible_keys):
        assert f(ctx(kind="model")) == NOT_READ


def test_a_rule_without_a_rules_object_is_not_evaluated():
    assert cm._types_differ(ctx(**model(rules=None))) == NOT_READ


def test_bidirectional_texts():
    one = cm._bidirectional(ctx(**model(bidirectional_count=1, bidirectional=["r1"])))
    assert one["text"] == "1 relationship filters in both directions: r1."
    clean = cm._bidirectional(ctx(**model()))
    assert clean == {"outcome": "pass", "evidence_ids": ["e1"],
                     "text": "None of its 3 relationships filters in both directions."}


def test_row_security_texts():
    found = cm._row_security(ctx(**model(row_filter_roles=["Owners"], row_filter_tables=["Sales", "Region"])))
    assert found["outcome"] == "pass"
    assert found["text"] == "1 role filters rows (Owners), on Sales, Region."
    no_roles = cm._row_security(ctx(**model()))
    assert no_roles["text"].startswith("It has no security role, so everyone")
    assert no_roles["detail"] == {"roles": 0}
    two = cm._row_security(ctx(**model(roles=2)))
    assert two["text"].startswith("Its 2 roles filter no rows, so everyone")


def test_fallback_applies_from_one_direct_lake_table():
    assert cm._fallback_applies(ctx(**model(direct_lake_tables=1))) is True
    assert cm._fallback_applies(ctx(**model(direct_lake_tables=0))) is False
    assert cm._fallback_applies(ctx(**model())) is False
    assert cm._fallback_applies(ctx(kind="model")) is None


def test_fallback_texts():
    onelake = cm._fallback(ctx(**model(direct_lake_tables=2, direct_lake_sources=["onelake"])))
    assert onelake == {"outcome": "pass", "evidence_ids": ["e1"],
                       "text": "Its 2 Direct Lake tables read through OneLake, which doesn't fall back to DirectQuery."}
    mixed = cm._fallback(ctx(**model(direct_lake_tables=2, direct_lake_sources=["onelake", "sql"])))
    assert mixed["outcome"] == "fail"
    assert mixed["detail"] == {"behavior": "automatic", "tables": 2}
    none = cm._fallback(ctx(**model()))
    assert none["text"].startswith("Its 0 Direct Lake tables can fall back")
    assert none["detail"] == {"behavior": "automatic", "tables": 0}


def test_a_rule_hit_without_a_count_is_zero_found():
    r = cm._types_differ(ctx(**model(rules={"SM001": {"objects": ["Sales.k"]}})))
    assert r["text"] == "A relationship joins columns of different types (SM001). 0 found: Sales.k."
    assert r["detail"] == {"rule": "SM001", "count": 0, "objects": ["Sales.k"]}


def test_direct_query_only_is_named_as_such():
    r = cm._fallback(ctx(**model(direct_lake_tables=1, direct_lake_behavior="DirectQueryOnly")))
    assert r["text"] == "Its 1 Direct Lake table are set to DirectQuery only, so no query uses Direct Lake."
    assert r["detail"] == {"behavior": "DirectQueryOnly", "tables": 1}
