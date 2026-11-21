"""L3 behavioral semantic parity, SQL side (OSSIE-006c, semantic_parity_l3.parity_l3).

L3 runs each SQL side's measure on DuckDB against fixed controlled datasets and compares two SQL sides' values; a
DAX side never runs. Required (plan scenarios 7, 11 and 12, SQL side): simple aggregation run on the SQL side with
the DAX side recorded as not run; NULL and blank behavior; precision, with tolerances that are configuration; the
same input giving the same report; a DAX-side result never appearing without an executed query."""

import copy
import logging

import pytest

from fabric_desktop import semantic_parity as sp, semantic_parity_l3 as l3
from test_semantic_parity_l2 import _model, _ossie


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _measure(report, name="m"):
    hits = [o for o in report["objects"] if o["kind"] == "measure" and o["id"].endswith(f"measure '{name}'")]
    assert len(hits) == 1, [o["id"] for o in report["objects"]]
    return hits[0]


def _assert_l3(report):
    assert report["level"] == "L3"
    assert sorted(report["scenarios"]) == sorted(l3.SCENARIOS)
    assert sum(report["counts"].values()) == len(report["objects"])
    for o in report["objects"]:
        for b in (o.get("behavior") or {}).values():
            if b["dialect"] == "DAX":
                assert not b["ran"] and b["results"] == {} and b["sql"] is None
            if b["ran"]:
                assert set(b["results"]) == set(l3.SCENARIOS)
    return report


def _pair(left_sql, right_sql, **kw):
    bim = _model("SUM('fact_sales'[amount])")
    return _assert_l3(l3.parity_l3(_ossie(bim, left_sql), _ossie(bim, right_sql), **kw))


def _typed(doc, field, datatype):
    out = copy.deepcopy(doc)
    for d in out["datasets"]:
        for f in d.get("fields") or []:
            if f["name"] == field:
                f["datatype"] = datatype
    return out


def test_scenarios_line_up_so_the_columns_of_one_table_have_the_same_rows():
    for name, scenario in l3.SCENARIOS.items():
        assert {len(v) for v in scenario.values()} == {len(scenario["int"])}, name
        assert set(scenario) == {"int", "dec", "num", "text", "bool", "time", "date"}, name


@pytest.mark.parametrize(("dax", "sql"), [
    ("SUM('fact_sales'[amount])", "SUM(amount)"),
    ("AVERAGE('fact_sales'[amount])", "AVG(amount)"),
    ("MIN('fact_sales'[amount])", "MIN(amount)"),
    ("MAX('fact_sales'[amount])", "MAX(amount)"),
    ("COUNTA('fact_sales'[amount])", "COUNT(amount)"),
    ("DISTINCTCOUNTNOBLANK('fact_sales'[amount])", "COUNT(DISTINCT amount)"),
])
def test_scenario_7_the_sql_side_runs_and_the_dax_side_is_recorded_as_not_run(dax, sql):
    bim = _model(dax)
    m = _measure(_assert_l3(l3.parity_l3(bim, _ossie(bim, sql))))
    left, right = m["behavior"]["left"], m["behavior"]["right"]
    assert left["dialect"] == "DAX" and not left["ran"] and "no DAX engine" in left["reason"]
    assert right["ran"] and right["sql"].startswith("SELECT ")
    assert m["result"] == sp.COMPATIBLE  # never better than the ceiling against a DAX side
    assert "behavior_result" not in m
    assert any("evidence, not a match" in n for n in m["notes"])


def test_two_sql_sides_with_the_same_measure_are_verified_on_the_datasets_only():
    m = _measure(_pair("SUM(amount)", "SUM(amount)"))
    assert m["result"] == m["behavior_result"] == sp.VERIFIED
    assert l3._ON_DATASETS in m["notes"]
    assert m["behavior"]["left"]["results"] == m["behavior"]["right"]["results"]
    assert m["behavior"]["left"]["results"]["basic"] == "15.75"


def test_a_different_aggregate_is_different_with_the_datasets_and_both_values():
    report = _pair("SUM(amount)", "MAX(amount)")
    m = _measure(report)
    assert m["result"] == sp.DIFFERENT and report["summary"] == sp.DIFFERENT
    assert any("basic dataset" in c and "15.75" in c and "5" in c for c in m["changes"])


def test_scenario_11_nulls_count_values_and_rows_differently():
    m = _measure(_pair("COUNT(amount)", "COUNT(DISTINCT amount)"))
    left, right = m["behavior"]["left"]["results"], m["behavior"]["right"]["results"]
    assert (left["nulls"], left["all_null"], left["no_rows"]) == ("3", "0", "0")
    assert (right["repeats"], right["all_null"]) == ("2", "0")
    assert m["result"] == sp.DIFFERENT
    assert any("repeats dataset" in c for c in m["changes"])


def test_scenario_11_aggregates_over_no_values_are_null_on_the_sql_side():
    m = _measure(_pair("AVG(amount)", "AVG(amount)"))
    results = m["behavior"]["left"]["results"]
    assert results["all_null"] is None and results["no_rows"] is None
    assert results["nulls"] == "3.25"


def test_scenario_12_a_decimal_against_a_float_column_is_within_the_default_tolerance():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount)")
    report = _assert_l3(l3.parity_l3(doc, _typed(doc, "amount", "Float")))
    m = _measure(report)
    assert m["behavior"]["left"]["results"]["precision"] == "0.6"
    assert m["behavior"]["right"]["results"]["precision"] == "0.6000000000000001"
    assert m["behavior_result"] == sp.WITHIN_TOLERANCE and m["result"] == sp.WITHIN_TOLERANCE
    assert report["tolerances"]["used"] is True
    assert report["tolerances"]["defaults"] == l3.DEFAULT_TOLERANCES_L3


def test_scenario_12_tolerances_are_configuration_and_zero_makes_it_different():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount)")
    report = l3.parity_l3(doc, _typed(doc, "amount", "Float"), tolerances={"relative": 0, "absolute": 0})
    m = _measure(report)
    assert m["result"] == sp.DIFFERENT
    assert any("precision dataset" in c for c in m["changes"])
    assert report["tolerances"]["values"] == {"absolute": 0.0, "relative": 0.0}


def test_scenario_12_integers_past_2_53_stay_exact_on_an_integer_column():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount)")
    m = _measure(l3.parity_l3(_typed(doc, "amount", "Integer"), _typed(doc, "amount", "Integer")))
    assert m["behavior"]["left"]["results"]["precision"] == "9007199254740993"


def test_a_zero_divisor_guarded_by_nullif_is_null():
    m = _measure(_pair(*["SUM(amount) / NULLIF(SUM(fact_sales.customer_sk), 0)"] * 2))
    assert m["behavior"]["left"]["results"]["zeros"] is None
    assert m["result"] == sp.VERIFIED


def test_a_worse_l2_result_stands_whatever_l3_finds():
    for worse in (sp.DIFFERENT, sp.UNSUPPORTED, sp.TEST_FAILED):
        o = _row(worse)
        l3._judge(o, _ran({"basic": "1"}, {"basic": "1"}), l3._tolerances(None))
        assert o["result"] == worse and o["behavior_result"] == sp.VERIFIED


def _row(result=sp.COMPATIBLE):
    return {"result": result, "notes": [sp._NO_QUERY], "changes": []}


def _ran(left, right):
    every = dict.fromkeys(l3.SCENARIOS, "1")
    return {"left": {"ran": True, "results": {**every, **left}}, "right": {"ran": True, "results": {**every, **right}}}


def test_a_query_refused_on_both_sides_never_verifies():
    o = _row()
    l3._judge(o, _ran({"basic": "error: BinderException"}, {"basic": "error: BinderException"}),
              l3._tolerances(None))
    assert o["result"] == sp.COMPATIBLE and "behavior_result" not in o
    assert any("verifies nothing" in n for n in o["notes"]) and l3._ON_DATASETS not in o["notes"]


def test_a_query_refused_on_one_side_only_is_different():
    o = _row()
    l3._judge(o, _ran({"basic": "error: BinderException"}, {}), l3._tolerances(None))
    assert o["result"] == sp.DIFFERENT and o["behavior_result"] == sp.DIFFERENT
    assert o["changes"] == ["on the basic dataset only one side's query ran"]


def test_a_value_that_differs_counts_even_when_another_dataset_is_refused():
    o = _row()
    err = "error: BinderException"
    l3._judge(o, _ran({"basic": err, "nulls": "2"}, {"basic": err, "nulls": "3"}), l3._tolerances(None))
    assert o["result"] == sp.DIFFERENT and o["behavior_result"] == sp.DIFFERENT
    assert o["changes"] == ["on the nulls dataset the left side gives 2 and the right side 3"]


def test_a_promoted_row_drops_the_l2_no_query_note():
    o = _row()
    l3._judge(o, _ran({}, {}), l3._tolerances(None))
    assert o["result"] == sp.VERIFIED and sp._NO_QUERY not in o["notes"] and l3._ON_DATASETS in o["notes"]


def test_a_measure_reference_runs_the_referenced_measure():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount)")
    for d in (doc,):
        d["metrics"].append({"name": "n", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "m"}]}})
    m = _measure(_assert_l3(l3.parity_l3(doc, copy.deepcopy(doc))), "n")
    assert m["behavior"]["left"]["results"]["basic"] == "15.75"
    assert m["result"] == sp.VERIFIED


def test_a_type_with_no_controlled_values_is_not_run_with_the_reason():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _typed(_ossie(bim, "SUM(amount)"), "amount", "Opaque")
    m = _measure(l3.parity_l3(doc, copy.deepcopy(doc)))
    assert not m["behavior"]["left"]["ran"]
    assert "Opaque" in m["behavior"]["left"]["reason"]
    assert "behavior_result" not in m and m["result"] != sp.VERIFIED


@pytest.mark.parametrize("datatype", ["String", "Boolean", "Date"])
@pytest.mark.parametrize(("dax", "sql"), [("SUM", "SUM"), ("AVERAGE", "AVG")])
def test_sum_or_average_of_a_non_number_is_not_run_or_verified(datatype, dax, sql):
    bim = _model(f"{dax}('fact_sales'[amount])")
    doc = _typed(_ossie(bim, f"{sql}(amount)"), "amount", datatype)
    m = _measure(l3.parity_l3(doc, copy.deepcopy(doc)))
    assert not m["behavior"]["left"]["ran"] and "type rules" in m["behavior"]["left"]["reason"]
    assert m["result"] not in (sp.VERIFIED, sp.WITHIN_TOLERANCE)


def test_sum_against_average_of_text_is_never_verified():
    bim = _model("SUM('fact_sales'[amount])")
    left = _typed(_ossie(bim, "SUM(amount)"), "amount", "String")
    right = _typed(_ossie(bim, "AVG(amount)"), "amount", "String")
    assert _measure(l3.parity_l3(left, right))["result"] not in (sp.VERIFIED, sp.WITHIN_TOLERANCE)


def test_the_same_input_gives_the_same_report():
    bim = _model("AVERAGE('fact_sales'[amount])")
    doc = _ossie(bim, "AVG(amount)")
    assert l3.parity_l3(bim, doc) == l3.parity_l3(bim, doc)
    assert l3.parity_l3(doc, _typed(doc, "amount", "Float")) == l3.parity_l3(doc, _typed(doc, "amount", "Float"))


def test_a_dax_side_result_is_never_reported():
    with pytest.raises(AssertionError, match="DAX"):
        l3._check_no_dax_result([{"behavior": {"left": {"dialect": "DAX", "ran": True, "results": {"basic": "1"}}}}])


def test_both_sides_dax_runs_nothing():
    bim = _model("SUM('fact_sales'[amount])")
    m = _measure(_assert_l3(l3.parity_l3(bim, copy.deepcopy(bim))))
    assert not m["behavior"]["left"]["ran"] and not m["behavior"]["right"]["ran"]
    assert m["result"] == sp.COMPATIBLE and "L3 ran neither side" in m["notes"]


@pytest.mark.parametrize("bad", [None, 42, "version: [unclosed"], ids=["none", "int", "yaml"])
def test_bad_input_is_test_failed_never_an_exception(bad):
    report = l3.parity_l3(bad, _model("SUM('fact_sales'[amount])"))
    assert report["summary"] == sp.TEST_FAILED and report["level"] == "L3"


def test_names_are_quoted_and_control_characters_refused():
    assert l3._q('a"b') == '"a""b"'
    with pytest.raises(l3._NotRun):
        l3._q("a\x00b")


def test_a_column_name_with_quotes_runs_as_a_name_only():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount)")
    weird = "x\") FROM read_csv('/etc/passwd') --"
    field = doc["datasets"][0]["fields"][1]
    field["name"] = field["expression"]["dialects"][0]["expression"] = weird
    doc["metrics"][0]["expression"]["dialects"][0]["expression"] = 'SUM(fact_sales."x"") FROM read_csv(\'/etc/passwd\') --")'
    m = _measure(l3.parity_l3(doc, copy.deepcopy(doc)))
    for b in m["behavior"].values():
        assert b["ran"], b
        assert b["results"]["basic"] == "15.75"  # the name was a quoted identifier, never SQL
    assert m["result"] == sp.VERIFIED


def test_tolerances_that_arent_numbers_are_ignored_and_named():
    report = _pair("SUM(amount)", "SUM(amount)", tolerances={"relative": "big", "absolute": -1, "other": 1})
    assert report["tolerances"]["values"] == l3.DEFAULT_TOLERANCES_L3
    assert set(report["tolerances"]["ignored"]) == {"relative", "absolute", "other"}


def test_redacted_hides_the_built_query():
    report = _pair("SUM(amount)", "SUM(amount)")
    out = l3.redacted(report)
    b = _measure(out)["behavior"]["left"]
    assert "amount" not in b["sql"] and out["redacted"] is True
    assert "amount" in _measure(report)["behavior"]["left"]["sql"]  # the report itself is unchanged


def test_a_diamond_chain_of_measure_references_computes_each_measure_once():
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount)")
    previous = "m"
    for i in range(1, 9):  # d_i = d_(i-1) / d_(i-1): inlined, the aggregates would double at every level
        doc["metrics"].append({"name": f"d{i}", "expression": {"dialects": [
            {"dialect": "ANSI_SQL", "expression": f"{previous} / NULLIF({previous}, 0)"}]}})
        previous = f"d{i}"
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    for i in range(1, 9):
        b = _measure(report, f"d{i}")["behavior"]["left"]
        assert b["ran"], b
        assert b["sql"].count("SELECT v FROM") == 2 and "SUM(" not in b["sql"]  # reads d_(i-1) twice, once computed
        assert b["results"]["basic"] == "1" and b["results"]["zeros"] is None
    assert _measure(report, "d8")["result"] == sp.VERIFIED


def test_a_measure_that_errors_passes_its_error_to_the_measures_that_read_it(monkeypatch):
    monkeypatch.setattr(l3, "_NUMERIC_AGG", frozenset())  # let DuckDB refuse SUM over text at run time
    bim = _model("SUM('fact_sales'[amount])")
    doc = _typed(_ossie(bim, "SUM(amount)"), "amount", "String")
    doc["metrics"].append({"name": "n", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "m"}]}})
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    assert _measure(report, "n")["behavior"]["left"]["results"]["basic"].startswith("error:")
    assert {_measure(report, k)["result"] for k in ("m", "n")} == {sp.COMPATIBLE}  # same refusals never verify


def test_the_aggregates_one_side_runs_are_capped_in_all(monkeypatch):
    monkeypatch.setattr(l3, "MAX_TERMS_PER_SIDE", 3)
    bim = _model("SUM('fact_sales'[amount])")
    doc = _ossie(bim, "SUM(amount)")
    doc["metrics"] += [{"name": f"s{i}", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]}}
                       for i in range(4)]
    report = l3.parity_l3(doc, copy.deepcopy(doc))
    rows = [o["behavior"]["left"] for o in report["objects"] if o.get("behavior")]
    assert sum(b["ran"] for b in rows) == 3
    assert all("aggregates in all" in b["reason"] for b in rows if not b["ran"])
