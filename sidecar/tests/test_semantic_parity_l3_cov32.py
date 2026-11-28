"""More L3 behavioral parity coverage (semantic_parity_l3): the query builder's refusals, the value text of each
DuckDB result type, chains of refused measures, rows-only tables, tolerances that aren't a mapping, and redaction of
a side that built no query. Everything goes through ``parity_l3``, ``_judge`` or ``_Run``."""

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


def _side(measures=(), column_types=None, which="left"):
    """A side as _Run reads it: measure entries (name -> dialects) and its casefolded column types."""
    entries = [{"kind": "measure", "key": ("measure", name), "expr": {"dialects": dialects}}
               for name, dialects in measures]
    return types.SimpleNamespace(which=which, entries=entries,
                                 column_types={("t", "c"): "int64"} if column_types is None else column_types)


# --- _Run: what the builder refuses, and what it builds ---------------------------------------------------------

def test_a_reference_to_a_measure_the_side_doesnt_have_is_not_run():
    run = l3._Run(_side())
    with pytest.raises(l3._NotRun, match=r"measure \[gone\] isn't on the left side"):
        run.build(("measure", "gone"))
    assert run.order == [] and run.terms == 0


def test_a_reference_to_a_dax_measure_is_not_run_without_a_reason_suffix():
    run = l3._Run(_side([("d", {"DAX": "SUM('t'[c])"})]))
    with pytest.raises(l3._NotRun) as info:
        run.build(("measure", "d"))
    assert str(info.value) == "measure [d] has no SQL form in the subset"
    assert "d" in run.parsed and run.made == {}


def test_a_reference_to_a_measure_with_no_expression_gives_the_reason():
    run = l3._Run(_side([("e", {})]))
    with pytest.raises(l3._NotRun, match=r"measure \[e\] has no SQL form in the subset: no DAX or SQL expression"):
        run.build(("measure", "e"))


def test_an_aggregate_with_no_sql_form_is_not_run():
    run = l3._Run(_side())
    with pytest.raises(l3._NotRun, match="^STDEV has no SQL form$"):
        run.build(("agg", "STDEV", "t", "c"))


def test_a_row_count_reads_a_table_the_side_has_columns_for():
    run = l3._Run(_side())
    assert run.build(("rows", "t")) == '(SELECT COUNT(*) FROM "left"."t")'
    assert run.tables == {"t"} and run.columns == {} and run.terms == 1


def test_a_row_count_of_a_table_with_no_columns_is_not_run():
    run = l3._Run(_side())
    with pytest.raises(l3._NotRun, match="the left side has no columns for table nope"):
        run.build(("rows", "nope"))
    assert run.tables == set()


def test_a_plain_sql_division_has_no_nullif():
    run = l3._Run(_side())
    sql = run.build(("div", ("agg", "SUM", "t", "c"), ("rows", "t"), "SQL /"))
    assert sql == '(SELECT SUM("c") FROM "left"."t") / (SELECT COUNT(*) FROM "left"."t")'
    assert run.terms == 2


def test_a_division_by_zero_giving_anything_else_has_no_sql_form():
    run = l3._Run(_side())
    with pytest.raises(l3._NotRun, match="a zero divisor giving ERROR has no SQL form"):
        run.build(("div", ("agg", "SUM", "t", "c"), ("rows", "t"), "ERROR"))


def test_a_side_with_no_column_types_builds_an_empty_type_map():
    side = _side(column_types={})
    side.column_types = None
    run = l3._Run(side)
    assert run.types == {} and side.column_types == {}
    with pytest.raises(l3._NotRun, match=r"column t\[c\] has no known type"):
        run.build(("agg", "SUM", "t", "c"))


# --- parity_l3: value text of each result type -------------------------------------------------------------------

def _results(doc, name="m"):
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    m = _measure(report, name)
    assert m["behavior"]["left"]["ran"] and m["behavior"]["right"]["ran"], m["behavior"]
    assert m["behavior"]["left"]["results"] == m["behavior"]["right"]["results"]
    return m, m["behavior"]["left"]["results"]


def test_min_of_a_boolean_column_is_true_or_false_text():
    m, r = _results(_typed(_doc(base="MIN(amount)"), "amount", "Boolean"))
    assert r["basic"] == "false" and r["all_null"] is None and r["zeros"] == "false"
    assert m["result"] == sp.VERIFIED


def test_max_of_a_boolean_column_is_true():
    _, r = _results(_typed(_doc(base="MAX(amount)"), "amount", "Boolean"))
    assert r["basic"] == "true" and r["repeats"] == "true" and r["no_rows"] is None


def test_min_of_a_date_column_is_iso_text():
    _, r = _results(_typed(_doc(base="MIN(amount)"), "amount", "Date"))
    assert r["basic"] == "2024-01-01" and r["zeros"] == "1899-12-30" and r["precision"] == "1900-01-01"


def test_max_of_a_datetime_column_is_iso_text():
    _, r = _results(_typed(_doc(base="MAX(amount)"), "amount", "DateTime"))
    assert r["basic"] == "2024-01-05T23:59:59" and r["precision"] == "9999-12-31T00:00:00"


def test_min_of_a_text_column_is_the_text():
    _, r = _results(_typed(_doc(base="MIN(amount)"), "amount", "String"))
    assert r["basic"] == "a" and r["zeros"] == "" and r["precision"] == "A"


def test_a_plain_division_by_a_zero_sum_is_not_a_number_and_still_verifies_when_both_sides_agree():
    m, r = _results(_doc(("q", "SUM(amount) / SUM(amount)")), "q")
    assert r["basic"] == "1"
    # 0 / 0 on DOUBLE gives NaN on both DuckDB 1.5.6 and 2.0.0 (IEEE float division is on by default).
    assert r["zeros"] == "nan"
    assert m["behavior_result"] == sp.VERIFIED


def test_a_plain_division_by_zero_on_one_side_is_different_from_nullif_on_the_other():
    left = _doc(("q", "SUM(amount) / NULLIF(COUNT(amount), 0)"))
    right = _doc(("q", "SUM(amount) / COUNT(amount)"))
    report = l3.parity_l3(left, right)
    q = _measure(report, "q")
    assert q["behavior"]["right"]["sql"].count("NULLIF") == 0
    assert q["behavior"]["left"]["sql"].count("NULLIF") == 1
    assert q["behavior"]["left"]["ran"] and q["behavior"]["right"]["ran"]
    assert q["result"] not in (sp.VERIFIED, sp.WITHIN_TOLERANCE)


def test_count_rows_of_a_table_only_counts_rows_with_no_column_read():
    doc = _ossie(_model("COUNTROWS('fact_sales')"), "COUNT(*)", dataset_scoped=True)
    m, r = _results(doc)
    assert m["behavior"]["left"]["sql"] == 'SELECT (SELECT COUNT(*) FROM "left"."fact_sales")'
    assert r == {"basic": "5", "nulls": "5", "all_null": "4", "no_rows": "0", "repeats": "5", "zeros": "3",
                 "precision": "3"}
    assert m["result"] == sp.VERIFIED


# --- parity_l3: a refused measure read through a chain ------------------------------------------------------------

def test_a_refusal_passes_down_a_chain_of_referenced_measures(monkeypatch):
    monkeypatch.setattr(l3, "_NUMERIC_AGG", frozenset())  # let DuckDB refuse SUM over text at run time
    doc = _typed(_doc(("n", "m"), ("k", "n")), "amount", "String")
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    for name in ("m", "n", "k"):
        b = _measure(report, name)["behavior"]["left"]
        assert b["ran"] and all(str(v).startswith("error: ") for v in b["results"].values()), (name, b["results"])
    k = _measure(report, "k")
    assert k["result"] == sp.COMPATIBLE and "behavior_result" not in k
    assert any("verifies nothing" in n for n in k["notes"])


# --- parity_l3: rows that don't run -------------------------------------------------------------------------------

def test_a_sql_side_outside_the_subset_gives_the_l2_reason():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount) + 1")
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    b = _measure(report)["behavior"]
    for which in ("left", "right"):
        assert not b[which]["ran"] and b[which]["reason"].startswith("outside the L2 subset: ")
        assert b[which]["dialect"] == "ANSI_SQL" and b[which]["sql"] is None
    assert any(n == "L3 ran neither side" for n in _measure(report)["notes"])


def test_a_report_with_no_measures_stops_after_l2():
    bim = _model("SUM('fact_sales'[amount])")
    bim["model"]["tables"][0]["measures"] = []
    report = l3.parity_l3(bim, copy.deepcopy(bim))
    assert report["level"] == "L3" and not report["error"]
    assert not any(o["kind"] == "measure" for o in report["objects"])
    assert "counts" in report and report["tolerances"]["used"] is False


# --- tolerances ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("value", "shown"), [([0.1], "list"), ("0.1", "str"), (0.5, "float")])
def test_tolerances_that_arent_a_mapping_are_ignored_and_named(value, shown):
    bim = _model("SUM('fact_sales'[amount])")
    report = l3.parity_l3(bim, _ossie(bim, "SUM(amount)"), tolerances=value)
    assert report["tolerances"]["ignored"] == [f"(not a mapping: {shown})"]
    assert report["tolerances"]["values"] == l3.DEFAULT_TOLERANCES_L3


def test_at_most_twenty_ignored_tolerance_keys_are_named():
    bad = {f"k{i}": 1 for i in range(25)} | {"absolute": -1, "relative": float("inf")}
    bim = _model("SUM('fact_sales'[amount])")
    report = l3.parity_l3(bim, _ossie(bim, "SUM(amount)"), tolerances=bad)
    assert len(report["tolerances"]["ignored"]) == 20
    assert report["tolerances"]["values"] == l3.DEFAULT_TOLERANCES_L3


def test_an_empty_tolerance_mapping_is_the_defaults():
    bim = _model("SUM('fact_sales'[amount])")
    report = l3.parity_l3(bim, _ossie(bim, "SUM(amount)"), tolerances={})
    assert report["tolerances"]["ignored"] == [] and report["tolerances"]["values"] == l3.DEFAULT_TOLERANCES_L3


# --- _judge: values that aren't numbers ---------------------------------------------------------------------------

def _row(result=sp.COMPATIBLE):
    return {"result": result, "notes": [sp._NO_QUERY], "changes": []}


def _ran(left, right):
    every = dict.fromkeys(l3.SCENARIOS, "1")
    return {"left": {"ran": True, "results": {**every, **left}}, "right": {"ran": True, "results": {**every, **right}}}


@pytest.mark.parametrize(("a", "b"), [(None, "1"), ("1", None), ("abc", "abd"), ("inf", "1"), ("nan", "nan "),
                                      ("2024-01-01", "2024-01-02"), ("true", "false")])
def test_two_values_that_arent_both_finite_numbers_are_different(a, b):
    o = _row()
    l3._judge(o, _ran({"basic": a}, {"basic": b}), l3._tolerances({"absolute": 1e9, "relative": 1}))
    assert o["result"] == sp.DIFFERENT and o["behavior_result"] == sp.DIFFERENT
    assert o["changes"] == [f"on the basic dataset the left side gives {a!s} and the right side {b!s}"]


def test_two_nulls_are_the_same():
    o = _row()
    l3._judge(o, _ran({"basic": None}, {"basic": None}), l3._tolerances(None))
    assert o["result"] == sp.VERIFIED and o["changes"] == []


def test_a_large_absolute_tolerance_makes_two_numbers_within_tolerance():
    o = _row()
    tol = l3._tolerances({"absolute": 0.5, "relative": 0})
    l3._judge(o, _ran({"basic": "1.25"}, {"basic": "1.5"}), tol)
    assert o["result"] == sp.WITHIN_TOLERANCE and tol["used"] is True
    assert l3._ON_DATASETS in o["notes"] and sp._NO_QUERY not in o["notes"]


def test_a_worse_row_keeps_its_l2_notes_when_l3_verifies():
    o = _row(sp.UNSUPPORTED)
    l3._judge(o, _ran({}, {}), l3._tolerances(None))
    assert o["result"] == sp.UNSUPPORTED and o["behavior_result"] == sp.VERIFIED
    assert o["notes"] == [sp._NO_QUERY, l3._ON_DATASETS]


def test_a_test_failed_row_stays_test_failed_when_l3_finds_a_difference():
    o = _row(sp.TEST_FAILED)
    l3._judge(o, _ran({"basic": "1"}, {"basic": "2"}), l3._tolerances(None))
    assert o["result"] == sp.TEST_FAILED and o["behavior_result"] == sp.DIFFERENT


def test_only_the_right_side_ran_is_named():
    o = _row()
    l3._judge(o, {"left": {"ran": False}, "right": {"ran": True, "results": {}}}, l3._tolerances(None))
    assert o["notes"][-1] == "L3 ran the right side's SQL only; its values are evidence, not a match"
    assert "behavior_result" not in o


# --- redacted -----------------------------------------------------------------------------------------------------

def test_redacted_leaves_a_side_with_no_query_and_hides_the_one_with_a_query():
    bim = _model("SUM('fact_sales'[amount])")
    report = l3.parity_l3(bim, _ossie(bim, "SUM(amount)"))
    out = l3.redacted(report)
    b = _measure(out)["behavior"]
    assert b["left"]["sql"] is None
    assert isinstance(b["right"]["sql"], (str, dict)) and "SUM" not in str(b["right"]["sql"])
    assert "fact_sales" not in str(b["right"]["sql"])
    # the original report keeps its built query
    assert _measure(report)["behavior"]["right"]["sql"].startswith("SELECT ")
