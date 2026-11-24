"""Mutation-testing follow-ups for semantic_parity_l3.py (batch 458): the depth, measure and aggregate caps at
their edges, a referenced measure's aggregates counted once, the reason a numeric aggregate over a text column
isn't run, a refusal read by a measure as its referenced measure's own error, rows L3 doesn't run, a reader that
fails the second time, a DAX side marked as run, and two values exactly at the tolerance."""

import copy
import logging
import types

import pytest

from fabric_desktop import semantic_parity as sp, semantic_parity_l3 as l3
from test_semantic_parity_l2 import _model, _ossie


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log = logging.getLogger("ossie_microsoft")
    monkeypatch.setattr(log, "disabled", True)


def _measure(report, name="m"):
    hits = [o for o in report["objects"] if o["kind"] == "measure" and o["id"].endswith(f"measure '{name}'")]
    assert len(hits) == 1, [o["id"] for o in report["objects"]]
    return hits[0]


def _metric(name, sql):
    return {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}}


def _doc(*metrics, base="SUM(amount)"):
    doc = _ossie(_model("SUM('fact_sales'[amount])"), base)
    doc["metrics"] += [_metric(n, sql) for n, sql in metrics]
    return doc


def _typed(doc, field, datatype):
    out = copy.deepcopy(doc)
    for d in out["datasets"]:
        for f in d.get("fields") or []:
            if f["name"] == field:
                f["datatype"] = datatype
    return out


def _side(column_types=None):
    return types.SimpleNamespace(which="left", entries=[],
                                 column_types={("t", "c"): "int64"} if column_types is None else column_types)


# ---- caps


def test_a_reference_eight_levels_deep_is_past_the_depth_cap():
    run = l3._Run(_side())
    with pytest.raises(l3._NotRun, match=r"^measure \[x\] refers back to itself or past 8 levels$"):
        run.measure("x", tuple(f"m{i}" for i in range(8)))
    with pytest.raises(l3._NotRun, match=r"^measure \[x\] isn't on the left side$"):
        run.measure("x", tuple(f"m{i}" for i in range(7)))


def test_sixty_four_aggregates_in_one_measure_are_allowed_and_one_more_isnt():
    run = l3._Run(_side())
    for _ in range(64):
        run.count()
    with pytest.raises(l3._NotRun, match="^more than 64 aggregates in one measure$"):
        run.count()


def test_a_side_may_reach_two_thousand_aggregates_and_not_one_more():
    run = l3._Run(_side())
    run.terms = 1999
    run.count()
    run.building, run.terms = 0, 2000
    with pytest.raises(l3._NotRun, match="^the left side's measures reach 2000 aggregates in all$"):
        run.count()


def test_two_hundred_measures_run_on_a_side_and_the_rest_are_noted():
    doc = _doc(*[(f"x{i:03d}", "SUM(amount)") for i in range(200)])
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    reasons = [o["behavior"]["left"]["reason"] for o in report["objects"] if "behavior" in o]
    ran = [o for o in report["objects"] if "behavior" in o and o["behavior"]["left"]["ran"]]
    assert len(reasons) == 201 and len(ran) == 200
    assert reasons.count("not run: past 200 measures on one side") == 1


# ---- building


def test_a_referenced_measures_aggregates_are_counted_once():
    doc = _doc(("n", "m"))
    side = sp._Side("left")
    sp._read(side, doc, None)
    run = l3._Run(side)
    form = sp._side_form(side, run.measures["n"], run.names)["form"]
    run.build(form)
    assert run.terms == 1


def test_a_numeric_aggregate_over_a_text_column_names_the_value_class():
    run = l3._Run(_side({("t", "c"): "string"}))
    with pytest.raises(l3._NotRun, match=r"^SUM over text column t\[c\]: "):
        run.build(("agg", "SUM", "t", "c"))


# ---- running


def test_a_measure_reading_a_refused_measure_takes_its_error(monkeypatch):
    monkeypatch.setattr(l3, "_NUMERIC_AGG", frozenset())  # let DuckDB refuse SUM over text at run time
    doc = _typed(_doc(("n", "m"), ("k", "n")), "amount", "String")
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    first = _measure(report, "m")["behavior"]["left"]["results"]
    assert all(str(v).startswith("error: ") for v in first.values())
    for name in ("n", "k"):
        assert _measure(report, name)["behavior"]["left"]["results"] == first, name


def test_only_measure_rows_on_both_sides_get_a_behavior():
    left = _doc(("extra", "SUM(amount)"))
    report = l3.parity_l3(left, _doc())
    assert "behavior" not in _measure(report, "extra")
    assert all("behavior" not in o for o in report["objects"] if o["kind"] != "measure")
    assert "behavior" in _measure(report)


def test_a_side_that_cant_be_read_again_is_an_error(monkeypatch):
    calls = []
    real = sp._read

    def flaky(side, value, kind):
        calls.append(side.which)
        if len(calls) > 2:
            raise ValueError("changed")
        return real(side, value, kind)
    monkeypatch.setattr(sp, "_read", flaky)
    doc = _doc()
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    assert report["error"] == "the left side couldn't be read again for L3: ValueError"
    assert "counts" not in report or all("behavior" not in o for o in report["objects"])


def test_a_dax_side_marked_as_run_without_results_is_refused():
    with pytest.raises(AssertionError, match="never reports a DAX-side result"):
        l3._check_no_dax_result([{"behavior": {"left": {"dialect": "DAX", "ran": True, "results": {}}}}])


# ---- values


def test_equal_values_are_the_same():
    assert l3._close("1.5", "1.5", {"absolute": 0.0, "relative": 0.0}) == "same"


def test_a_gap_exactly_at_the_tolerance_is_within_it():
    assert l3._close("1", "2", {"absolute": 1.0, "relative": 0.0}) == "tolerance"
    assert l3._close("1", "2.5", {"absolute": 1.0, "relative": 0.0}) == "different"
