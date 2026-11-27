"""Mutation tests for semantic_parity.py (batch 450): exact reasons and notes at L1 and L2, relationship
endpoints, hierarchy levels, the redacted report, the tolerance list and the reader's warning cap."""

from __future__ import annotations

import copy
import logging

import pytest

from fabric_desktop import model_ossie as mo
from fabric_desktop import semantic_parity as sp
from test_model_ossie import _bim
from test_semantic_parity_l2 import _l2, _measure, _model, _ossie

SUM = "SUM('fact_sales'[amount])"
MATCHED_ONLY = [sp.MATCHED, sp._NO_QUERY]


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _dax(left, right=None):
    return _measure(sp.parity_l2(_model(left), _model(left if right is None else right)))


# ---- L2 reasons outside the subset


@pytest.mark.parametrize(("dax", "reason"), [
    ("=", "no expression"),
    ("DIVIDE(SUM('fact_sales'[amount]) COUNTROWS('fact_sales'))",
     "expected ',' between DIVIDE's numerator and divisor at token 8"),
    ("SUM(1)", "expected a table at token 3"),
])
def test_a_dax_expression_outside_the_subset_says_exactly_why(dax, reason):
    m = _dax(dax)
    assert m["result"] == sp.UNSUPPORTED
    assert f"outside the L2 subset on the left side: {reason}" in m["notes"]


@pytest.mark.parametrize(("sql", "reason"), [
    ("amount", "a column outside an aggregate"),
    ("SUM(amount) / NULLIF(COUNT(*), 1)", "a divisor guarded by NULLIF with anything but 0 (the converter's rule)"),
])
def test_a_sql_expression_outside_the_subset_says_exactly_why(sql, reason):
    m = _l2(SUM, sql, dataset_scoped=True)
    assert m["result"] == sp.UNSUPPORTED
    assert f"outside the L2 subset on the right side: {reason}" in m["notes"]


def test_different_texts_outside_the_subset_dont_say_the_text_is_the_same():
    m = _dax("=", "SUM(1)")
    assert m["result"] == sp.UNSUPPORTED
    assert not any("the expression text is the same" in n for n in m["notes"])


# ---- L2 forms and notes


def test_a_single_aggregate_and_a_divide_are_shown_as_forms():
    assert _dax(SUM)["expressions"]["left_form"] == "SUM(fact_sales[amount])"
    div = "DIVIDE(SUM('fact_sales'[amount]), COUNTROWS('fact_sales'), 0)"
    assert _dax(div)["expressions"]["left_form"] == \
        "DIVIDE(SUM(fact_sales[amount]), COUNTROWS(fact_sales); zero divisor: alternate 0)"


def test_dax_against_dax_division_carries_no_sql_caveat():
    m = _dax("DIVIDE(SUM('fact_sales'[amount]), COUNTROWS('fact_sales'), 0)")
    assert m["result"] == sp.COMPATIBLE and m["notes"] == MATCHED_ONLY


def test_dax_sum_against_sql_sum_carries_no_average_caveat():
    m = _l2(SUM, "SUM(amount)")
    assert m["result"] == sp.COMPATIBLE
    assert not any("AVG" in n for n in m["notes"])


def test_at_most_five_other_dialects_are_named():
    bim = _model(SUM)
    doc = _ossie(bim, "SUM(amount)")
    metric = next(m for m in doc["metrics"] if m["name"] == "m")
    metric["expression"]["dialects"] += [{"dialect": f"D{i}", "expression": "x"} for i in range(1, 8)]
    notes = _measure(sp.parity_l2(bim, doc))["notes"]
    assert "on the right side its D1, D2, D3, D4, D5 form wasn't compared; L2 compared its ANSI_SQL form" in notes


# ---- L1 measures, relationships, hierarchies, types


def test_two_tmsl_measures_with_a_home_table_note_nothing_about_it():
    m = _measure(sp.parity_l1(_model(SUM), _model(SUM)))
    assert m["notes"] == ["expressions weren't compared: that is L2 (OSSIE-006b)"]
    assert [e["values"] for e in m["evidence"] if e["source"] == "structure"] == \
        [{"home table": "fact_sales"}, {"home table": "fact_sales"}]


def _relationship(report):
    (r,) = [o for o in report["objects"] if o["kind"] == "relationship"]
    return r


def _reversed(bim, **card):
    bim["model"]["relationships"][0] = {"name": "r1", "fromTable": "dim_customer", "fromColumn": "customer_sk",
                                        "toTable": "fact_sales", "toColumn": "customer_sk", **card}
    return bim


def test_the_same_relationship_has_no_changes():
    r = _relationship(sp.parity_l1(_bim(), _bim()))
    assert r["result"] == sp.COMPATIBLE and r["changes"] == []


def test_a_many_to_one_relationship_turned_round_changed_its_endpoints():
    r = _relationship(sp.parity_l1(_bim(), _reversed(_bim())))
    assert r["result"] == sp.DIFFERENT
    assert r["changes"] == [("endpoints changed from 'fact_sales'[customer_sk] -> 'dim_customer'[customer_sk] to "
                             "'dim_customer'[customer_sk] -> 'fact_sales'[customer_sk]")]


@pytest.mark.parametrize("card", ["one", "many"])
def test_a_symmetric_relationship_turned_round_is_the_same(card):
    left = _bim()
    left["model"]["relationships"][0].update(fromCardinality=card, toCardinality=card)
    r = _relationship(sp.parity_l1(left, _reversed(_bim(), fromCardinality=card, toCardinality=card)))
    assert r["changes"] == []


@pytest.mark.parametrize("ordinals", [(None, None), (1, 1)])
def test_hierarchy_levels_without_or_with_equal_ordinals_still_compare(ordinals):
    bim = _bim()
    levels = bim["model"]["tables"][1]["hierarchies"][0]["levels"]
    for lv, o in zip(levels, ordinals, strict=True):
        lv.pop("ordinal")
        if o is not None:
            lv["ordinal"] = o
    report = sp.parity_l1(bim, copy.deepcopy(bim))
    (h,) = [o for o in report["objects"] if o["kind"] == "hierarchy"]
    assert report["error"] == "" and h["result"] == sp.COMPATIBLE and h["changes"] == []


def test_an_empty_ossie_data_type_is_no_data_type():
    def doc(field):
        return {"version": "0.3.0.dev0", "name": "m", "datasets": [{"name": "t", "fields": [field]}]}
    report = sp.parity_l1(doc({"name": "c", "datatype": ""}), doc({"name": "c"}))
    (c,) = [o for o in report["objects"] if o["kind"] == "column"]
    assert c["result"] == sp.COMPATIBLE and c["changes"] == []


def test_the_id_of_an_object_only_the_fidelity_report_knows():
    assert sp._ident("column", ("column", "t", "c"), "x") == "table 't' column 'c'"
    assert sp._ident("hierarchy", ("hierarchy", "t", "h"), "x") == "table 't' hierarchy 'h'"
    assert sp._ident("role", ("role", "r"), "x") == "role 'r'"
    assert sp._ident("perspective", ("perspective", "p"), "x") == "perspective 'p'"


def test_a_lost_object_of_a_kind_l1_doesnt_compare_is_no_row(monkeypatch):
    real = mo._ossie_to_tmsl

    def lossy(doc, bim, fid):
        out = real(doc, bim, fid)
        fid.add("culture", "culture 'fr-FR'", "unsupported", key=("culture", "fr-FR"))
        return out

    monkeypatch.setattr(mo, "_ossie_to_tmsl", lossy)
    bim = _bim()
    report = sp.parity_l1(bim, _ossie(bim, "SUM(amount)", name="fact_sales total amount"))
    assert report["error"] == ""
    assert {o["kind"] for o in report["objects"]} <= set(sp._COMPARED)
    assert not any("culture" in o["id"] for o in report["objects"])


# ---- the report


def test_a_comparison_that_trips_is_test_failed_with_both_sides(monkeypatch):
    def trip(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(sp, "_compare", trip)
    bim = _bim()
    report = sp.parity_l1(bim, _ossie(bim, "SUM(amount)", name="fact_sales total amount"))
    assert report["summary"] == sp.TEST_FAILED
    assert report["error"] == "the comparison couldn't be completed: ValueError: boom"
    assert report["sides"]["left"]["kind"] == "tmsl" and report["sides"]["right"]["kind"] == "ossie"


def test_the_tolerance_note_names_what_each_level_compares():
    assert sp.parity_l1(_bim(), _bim())["tolerances"]["note"].startswith("L1 compares structure only, so no ")
    assert sp.parity_l2(_bim(), _bim())["tolerances"]["note"].startswith("L2 compares expression forms only, so no ")


def test_at_most_twenty_ignored_tolerances_are_listed():
    tol = sp.parity_l1(_bim(), _bim(), tolerances={f"bad{i}": 1 for i in range(25)})["tolerances"]
    assert tol["ignored"] == [f"bad{i}" for i in range(20)]


def test_a_side_keeps_fifty_warnings_and_counts_them_all():
    side = sp._Side("left")
    for i in range(sp.MAX_WARNINGS + 3):
        side.warn({"object": f"o{i}"})
    assert len(side.warnings) == sp.MAX_WARNINGS and side.warnings_total == sp.MAX_WARNINGS + 3


def test_redacted_withholds_role_warnings_and_counts_role_changes():
    report = {"objects": [{"kind": "role", "changes": ["a", "b"], "evidence": []},
                          {"kind": "role", "changes": [], "evidence": []}],
              "sides": {"left": {"warnings": [{"object": "role 'EU'"}, {"file": "definition/roles/EU.tmdl"},
                                              {"object": "table 't'", "file": "definition/tables/t.tmdl"}]}}}
    out = sp.redacted(report)
    assert [o["changes"] for o in out["objects"]] == [["2 change(s) withheld"], []]
    kept = out["sides"]["left"]["warnings"]
    assert ["withheld" in w for w in kept] == [True, True, False]
    assert kept[2] == {"object": "table 't'", "file": "definition/tables/t.tmdl"}
