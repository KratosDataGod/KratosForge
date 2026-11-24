"""L2 expression semantic parity (OSSIE-006b, semantic_parity.parity_l2).

L2 runs L1 and, for each measure or metric on both sides, compares the expressions in a small canonical form, but
only inside the vendored converter's translatable subset (six aggregates, COUNT(*), division, measure references).
Required: scenario 7 (simple aggregation equivalence) against the converter's actual output in both directions,
TOTALYTD and filtered aggregates Unsupported (never Different), a translate_dax round trip with no Different row,
hostile input, redaction of expression text, and the ceiling guard. There is no test_ossie_vendor_translators.py
yet, so the converter's translators are exercised here."""

import copy
import json
import logging
import random
import time

import pytest
import yaml

from fabric_desktop import model_ossie, semantic_parity as sp, tmdl
from fabric_desktop._vendor.ossie_microsoft import _dax_to_sql, _sql_to_dax
from fabric_desktop._vendor.ossie_microsoft.ossie_to_semantic_model import _column_index
from test_model_ossie import _bim
from test_tmdl_fuzz import _Gen

ABOVE_CEILING = (sp.VERIFIED, sp.WITHIN_TOLERANCE)


@pytest.fixture(autouse=True)
def _quiet_converter_log():
    logging.getLogger("ossie_microsoft").disabled = True
    yield
    logging.getLogger("ossie_microsoft").disabled = False


def _assert_l2(report):
    """What every L2 report holds, whatever it compares."""
    assert report["level"] == "L2"
    assert report["ceiling"] == sp.COMPATIBLE
    assert report["summary"] not in ABOVE_CEILING
    assert all(o["result"] not in ABOVE_CEILING for o in report["objects"])
    assert all(o.get("expression_result") not in ABOVE_CEILING for o in report["objects"])
    assert report["counts"][sp.VERIFIED] == report["counts"][sp.WITHIN_TOLERANCE] == 0
    assert sum(report["counts"].values()) == len(report["objects"])
    assert [n["level"] for n in report["not_checked"]] == ["L3", "L4"]
    assert report["tolerances"]["used"] is False
    return report


def _measure(report, name="m"):
    hits = [o for o in report["objects"] if o["kind"] == "measure" and o["id"].endswith(f"measure '{name}'")]
    assert len(hits) == 1, [o["id"] for o in report["objects"]]
    return hits[0]


def _model(dax, name="m"):
    bim = _bim()
    bim["model"]["tables"][0]["measures"] = [{"name": name, "expression": dax}]
    return bim


def _ossie(bim, sql, name="m", dataset_scoped=False, dialect="ANSI_SQL"):
    """The model exported to Ossie, with the metric's expression replaced by ``sql`` (on its dataset if asked)."""
    doc = yaml.safe_load(model_ossie.export(bim, translate_dax=False)["yaml"])
    metric = next(m for m in doc["metrics"] if m["name"] == name)
    metric["expression"] = {"dialects": [{"dialect": dialect, "expression": sql}]}
    if dataset_scoped:
        doc["metrics"].remove(metric)
        metric.pop("custom_extensions", None)
        doc["datasets"][0].setdefault("metrics", []).append(metric)
    return doc


def _l2(dax, sql, **kw):
    bim = _model(dax)
    return _measure(_assert_l2(sp.parity_l2(bim, _ossie(bim, sql, **kw))))


def _vendor_dax(sql):
    """What the converter writes in DAX for ``sql`` on the test model (its own resolvers)."""
    tables = _bim()["model"]["tables"]
    index = _column_index(tables)
    dax, reason = _sql_to_dax.translate(sql, "ANSI_SQL", lambda n: index.get(n.casefold()), lambda: "fact_sales")
    assert dax is not None, reason
    return dax


def _vendor_sql(dax):
    """What the converter writes in OSSIE_SQL_2026 for a DAX measure on the test model."""
    known = {(t["name"], c["name"]) for t in _bim()["model"]["tables"] for c in t["columns"]}
    sql, reason = _dax_to_sql.translate_measure(dax, lambda t, c: (t, c) if (t, c) in known else None)
    assert sql is not None, reason
    return sql


# ---- scenario 7: simple aggregation equivalence against the converter's own output

@pytest.mark.parametrize(("sql", "scoped"), [
    ("SUM(amount)", False), ("AVG(amount)", False), ("MIN(amount)", False), ("MAX(amount)", False),
    ("COUNT(amount)", False), ("COUNT(DISTINCT fact_sales.customer_sk)", False), ("COUNT(*)", True),
    ("SUM(amount) / NULLIF(COUNT(*), 0)", True), ("SUM(amount) / NULLIF(MAX(amount), 0)", False),
])
def test_scenario_7_sql_and_the_dax_the_converter_writes_for_it_are_compatible(sql, scoped):
    dax = _vendor_dax(sql)
    m = _l2(dax, sql, dataset_scoped=scoped)
    assert m["result"] == m["expression_result"] == sp.COMPATIBLE, m
    assert sp.MATCHED in m["notes"]
    assert m["expressions"]["left_form"] == m["expressions"]["right_form"]
    assert m["expressions"]["left"] == dax and m["expressions"]["right"] == sql
    assert m["expressions"]["left_dialect"] == "DAX" and m["expressions"]["right_dialect"] == "ANSI_SQL"
    assert not any("expressions weren't compared: that is L2" in n for n in m["notes"])


@pytest.mark.parametrize("dax", [
    "SUM('fact_sales'[amount])", "AVERAGE('fact_sales'[amount])", "MIN('fact_sales'[amount])",
    "MAX('fact_sales'[amount])", "COUNTA('fact_sales'[amount])", "DISTINCTCOUNTNOBLANK('fact_sales'[customer_sk])",
])
def test_scenario_7_dax_and_the_sql_the_converter_writes_for_it_are_compatible(dax):
    sql = _vendor_sql(dax)
    m = _l2(dax, sql, dialect="OSSIE_SQL_2026")
    assert m["result"] == sp.COMPATIBLE, m
    assert sp.MATCHED in m["notes"]


def test_scenario_7_counts_carry_their_blank_notes():
    m = _l2("COUNTA('fact_sales'[amount])", "COUNT(amount)")
    assert any("empty string" in n for n in m["notes"])
    assert any("BLANK and SQL gives 0" in n for n in m["notes"])
    m = _l2("DISTINCTCOUNTNOBLANK('fact_sales'[customer_sk])", "COUNT(DISTINCT fact_sales.customer_sk)")
    assert any("DISTINCTCOUNTNOBLANK and back" in n and "L3" in n for n in m["notes"])


def test_distinctcount_against_count_distinct_is_different_and_the_converter_refuses_it_too():
    _, reason = _dax_to_sql.translate_measure("DISTINCTCOUNT('fact_sales'[customer_sk])", lambda t, c: (t, c))
    assert "BLANK" in reason
    m = _l2("DISTINCTCOUNT('fact_sales'[customer_sk])", "COUNT(DISTINCT fact_sales.customer_sk)")
    assert m["result"] == sp.DIFFERENT
    assert any("counts BLANK as a value" in c and "differ by one" in c for c in m["changes"])


def test_dax_count_matches_sql_count_only_on_a_column_that_isnt_a_boolean():
    _, reason = _dax_to_sql.translate_measure("COUNT('fact_sales'[amount])", lambda t, c: (t, c))
    assert "TRUE/FALSE" in reason
    m = _l2("COUNT('fact_sales'[amount])", "COUNT(amount)")
    assert m["result"] == sp.COMPATIBLE
    assert any("TRUE/FALSE" in n and "isn't a boolean" in n for n in m["notes"])
    bim = _model("COUNT('fact_sales'[flag])")
    bim["model"]["tables"][0]["columns"].append({"name": "flag", "dataType": "boolean", "sourceColumn": "flag"})
    m = _measure(_assert_l2(sp.parity_l2(bim, _ossie(bim, "COUNT(flag)"))))
    assert m["result"] == sp.UNSUPPORTED
    assert any("TRUE/FALSE" in n and "boolean" in n for n in m["notes"])
    doc = _ossie(bim, "COUNT(flag)")
    bim["model"]["tables"][0]["measures"][0]["expression"] = "COUNTA('fact_sales'[flag])"
    m = _measure(_assert_l2(sp.parity_l2(bim, doc)))
    assert m["result"] == sp.COMPATIBLE  # COUNTA counts TRUE/FALSE as SQL COUNT does


def test_divide_against_an_unguarded_division_is_different_though_the_converter_writes_divide_for_both():
    assert _vendor_dax("SUM(amount) / MAX(amount)") == _vendor_dax("SUM(amount) / NULLIF(MAX(amount), 0)")
    m = _l2("DIVIDE(SUM('fact_sales'[amount]), MAX('fact_sales'[amount]))", "SUM(amount) / MAX(amount)")
    assert m["result"] == sp.DIFFERENT
    assert m["changes"] == [m["changes"][0]] and "zero divisor" in m["changes"][0]
    assert "BLANK" in m["changes"][0] and "error or NULL" in m["changes"][0]


@pytest.mark.parametrize(("dax", "sql", "expected"), [
    ("DIVIDE(SUM('fact_sales'[amount]), MAX('fact_sales'[amount]), 0)", "SUM(amount) / NULLIF(MAX(amount), 0)",
     "alternate result"),
    ("SUM('fact_sales'[amount]) / MAX('fact_sales'[amount])", "SUM(amount) / NULLIF(MAX(amount), 0)", "infinity"),
    ("DIVIDE(SUM('fact_sales'[amount]), MAX('fact_sales'[amount]))", "SUM(amount) / NULLIF(MIN(amount), 0)",
     "divisor: aggregate MAX"),
    ("DIVIDE(SUM('fact_sales'[amount]), MAX('fact_sales'[amount]))", "SUM(amount)", "a division on the left"),
    ("SUM('fact_sales'[amount])", "AVG(amount)", "aggregate SUM on the left side, AVERAGE"),
    ("SUM('fact_sales'[amount])", "SUM(fact_sales.customer_sk)", "column fact_sales[amount] on the left"),
    ("COUNTROWS('dim_customer')", "COUNT(*)", "COUNTROWS(dim_customer) on the left"),
])
def test_scenario_7_a_mismatched_aggregate_column_divisor_or_alternate_is_different(dax, sql, expected):
    m = _l2(dax, sql, dataset_scoped=True)
    assert m["result"] == m["expression_result"] == sp.DIFFERENT, m
    assert any(expected in c for c in m["changes"]), m["changes"]


def test_dax_against_dax_compares_divide_alternates_and_measure_references():
    left, right = _model("DIVIDE([a], [b], 0)"), _model("DIVIDE([a], [b])")
    for bim in (left, right):
        bim["model"]["tables"][0]["measures"] += [{"name": "a", "expression": "SUM('fact_sales'[amount])"},
                                                  {"name": "b", "expression": "COUNTROWS('fact_sales')"}]
    m = _measure(_assert_l2(sp.parity_l2(left, right)))
    assert m["result"] == sp.DIFFERENT and any("alternate result" in c for c in m["changes"])
    m = _measure(_assert_l2(sp.parity_l2(left, copy.deepcopy(left))))
    assert m["result"] == sp.COMPATIBLE and any("by name only" in n for n in m["notes"])
    right["model"]["tables"][0]["measures"][0]["expression"] = "divide( [A] , [B], 0.0 )"  # case and spacing
    assert _measure(sp.parity_l2(left, right))["result"] == sp.COMPATIBLE


def test_a_sql_metric_may_name_another_metric():
    bim = _model("DIVIDE([total], COUNTROWS('fact_sales'))")
    bim["model"]["tables"][0]["measures"].append({"name": "total", "expression": "SUM('fact_sales'[amount])"})
    doc = _ossie(bim, '"total" / NULLIF(COUNT(*), 0)', dataset_scoped=True)
    m = _measure(_assert_l2(sp.parity_l2(bim, doc)))
    assert m["result"] == sp.COMPATIBLE, m


# ---- outside the subset: Unsupported, never Different

@pytest.mark.parametrize(("dax", "sql", "reason"), [
    ("TOTALYTD(SUM('fact_sales'[amount]), 'dim_customer'[city])", "SUM(amount)", "time intelligence"),
    ("CALCULATE(SUM('fact_sales'[amount]), 'dim_customer'[country] = \"DE\")", "SUM(amount)", "filter context"),
    ("SUM('fact_sales'[amount])", "SUM(amount) FILTER (WHERE amount > 0)", "filtered aggregate"),
    ("SUM('fact_sales'[amount])", "SUM(CASE WHEN amount > 0 THEN amount END)", "conditional"),
    ("SUM('fact_sales'[amount])", "SUM(amount) OVER ()", "window function"),
    ("SUMX('fact_sales', 'fact_sales'[amount])", "SUM(amount)", "iterator"),
    ("VAR x = SUM('fact_sales'[amount]) RETURN x", "SUM(amount)", "variables"),
    ("IF(1 > 0, SUM('fact_sales'[amount]))", "SUM(amount)", "conditional"),
    ("SUM('fact_sales'[amount]) + 1", "SUM(amount)", "arithmetic"),
    ("SUM('fact_sales'[amount])", "SUM(amount) + 1", "arithmetic"),
    ("SUM('fact_sales'[amount])", "COALESCE(SUM(amount), 0)", "COALESCE"),
    ("SUM('fact_sales'[amount])", "SUM(nowhere)", "doesn't resolve"),
    ("SUM('fact_sales'[amount])", "SUM(customer_sk)", "doesn't resolve"),  # in two datasets: ambiguous
    ("SUM('fact_sales'[amount])", "SUM(", "SQL reader refused"),
    ("SUM('fact_sales'[amount]) // note", "SUM(amount)", "comment"),
    ("SUM(amount)", "SUM(amount)", "fully qualified column"),
])
def test_an_expression_outside_the_subset_is_unsupported_never_different(dax, sql, reason):
    m = _l2(dax, sql)
    assert m["result"] == m["expression_result"] == sp.UNSUPPORTED, m
    assert m["changes"] == []
    assert any(reason in n for n in m["notes"]), m["notes"]


def test_the_same_text_outside_the_subset_is_still_unsupported_and_says_so():
    bim = _model("TOTALYTD(SUM('fact_sales'[amount]), 'dim_customer'[city])")
    m = _measure(_assert_l2(sp.parity_l2(bim, copy.deepcopy(bim))))
    assert m["result"] == sp.UNSUPPORTED
    assert any("text is the same on both sides" in n for n in m["notes"])


def test_count_star_on_a_model_level_metric_with_two_datasets_is_unsupported_as_the_converter_refuses_it():
    m = _l2("COUNTROWS('fact_sales')", "COUNT(*)")
    assert m["result"] == sp.UNSUPPORTED and any("exactly one dataset" in n for n in m["notes"])


# ---- the L1 rows and the round trip

def test_l2_keeps_every_l1_row_and_a_measure_on_one_side_keeps_its_l1_result():
    left, right = _bim(), _bim()
    right["model"]["tables"][0]["measures"].pop()
    l1, l2 = sp.parity_l1(left, right), _assert_l2(sp.parity_l2(left, right))
    assert [o["id"] for o in l1["objects"]] == [o["id"] for o in l2["objects"]]
    gone = _measure(l2, "fact_sales total amount")
    assert gone["result"] == sp.DIFFERENT and gone["changes"] == _measure(l1, "fact_sales total amount")["changes"]
    assert any("on one side only" in n for n in gone["notes"]) and "expression_result" not in gone
    for o in l2["objects"]:
        if o["kind"] != "measure":
            assert o == next(x for x in l1["objects"] if x["id"] == o["id"])


def _rich_model():
    bim = _bim()
    bim["model"]["tables"][0]["measures"] += [
        {"name": n, "expression": e} for n, e in (
            ("avg", "AVERAGE('fact_sales'[amount])"), ("lo", "MIN('fact_sales'[amount])"),
            ("hi", "MAX(fact_sales[amount])"), ("n", "COUNTA('fact_sales'[amount])"),
            ("n2", "COUNT('fact_sales'[amount])"), ("dc", "DISTINCTCOUNTNOBLANK('fact_sales'[customer_sk])"),
            ("dc2", "DISTINCTCOUNT('fact_sales'[customer_sk])"),
            ("ratio", "DIVIDE([fact_sales total amount], [fact_sales rows], 0)"),
            ("ytd", "TOTALYTD(SUM('fact_sales'[amount]), 'dim_customer'[city])"),
            ("de", "CALCULATE(SUM('fact_sales'[amount]), 'dim_customer'[country] = \"DE\")"))]
    return bim


def test_round_trip_a_model_exported_with_translate_dax_and_compared_back_has_no_different_rows():
    bim = _rich_model()
    out = model_ossie.export(bim, translate_dax=True)
    doc = yaml.safe_load(out["yaml"])
    assert out["counts"]["portable_metrics"] >= 6
    back, _ = model_ossie.to_tmsl(doc)
    files, _ = tmdl.from_bim(bim)
    for left, right in ((bim, doc), (doc, bim), (bim, back), (files, out["yaml"]), (doc, doc)):
        report = _assert_l2(sp.parity_l2(left, right))
        assert report["counts"][sp.DIFFERENT] == 0, [o for o in report["objects"] if o["result"] == sp.DIFFERENT]
    report = sp.parity_l2(bim, doc)
    assert _measure(report, "avg")["expressions"]["right_dialect"] == "OSSIE_SQL_2026"
    assert _measure(report, "avg")["result"] == sp.COMPATIBLE
    assert _measure(report, "ytd")["result"] == _measure(report, "de")["result"] == sp.UNSUPPORTED


def test_property_a_random_model_against_its_translate_dax_export_has_no_different_rows():
    for seed in range(60):
        bim = _Gen(seed).model()
        try:
            text = model_ossie.export(bim, translate_dax=True)["yaml"]
        except ValueError:  # what the converter refuses by name (test_model_ossie_fuzz)
            continue
        report = _assert_l2(sp.parity_l2(bim, text))
        assert report["counts"][sp.DIFFERENT] == 0, (seed, [o for o in report["objects"] if o["result"] == sp.DIFFERENT][:2])


# ---- hostile input


def _short_id(v):
    # pytest puts the id in PYTEST_CURRENT_TEST; Windows caps an environment value at 32 KB.
    return None if len(repr(v)) <= 40 else f"{len(v)}-chars-{v[:12]!r}"

@pytest.mark.parametrize("dax", [
    "SUM('fact_sales'[amount]) " + "x" * 100_000,
    "(" * 100_000 + "SUM('fact_sales'[amount])" + ")" * 100_000,
    "(" * 2000 + ")" * 2000,
    "DIVIDE(" * 30 + "1" + ")" * 30,
    "SUM('fact_sales'[amount])\u202e", "\uff33\uff35\uff2d('fact_sales'[amount])", "SUM\u200b('fact_sales'[amount])",
    "SUM('fact_sales'[amount]\x00)", "\ud800", "'" * 3000, "[" * 3000, "SUM('fact_sales'[amount]" + ", 1" * 40 + ")",
], ids=_short_id)
def test_hostile_dax_is_unsupported_quickly_without_recursion(dax):
    bim = _model(dax)
    start = time.perf_counter()
    m = _measure(_assert_l2(sp.parity_l2(bim, _ossie(bim, "SUM(amount)"))))
    assert time.perf_counter() - start < 30  # a blow-up guard, not a speed budget
    assert m["result"] == sp.UNSUPPORTED, m
    assert len(m["expressions"]["left"]) < 2 * model_ossie.MAX_NAME


@pytest.mark.parametrize("sql", [
    "SUM(amount) " + "x" * 100_000, "(" * 1000 + "SUM(amount)" + ")" * 1000, "(" * 9 + "SUM(amount)" + ")" * 9,
    "SUM(amount) / " * 40 + "1", "SUM(amount)\u202e", "\uff33\uff35\uff2d(amount)", "SUM(\"\ud800\")", "SUM(amount); DROP TABLE x",
], ids=_short_id)
def test_hostile_sql_is_unsupported_quickly_without_recursion(sql):
    bim = _model("SUM('fact_sales'[amount])")
    start = time.perf_counter()
    m = _measure(_assert_l2(sp.parity_l2(bim, _ossie(bim, sql))))
    assert time.perf_counter() - start < 30  # a blow-up guard, not a speed budget
    assert m["result"] == sp.UNSUPPORTED, m


def test_property_random_token_soup_never_raises_and_is_never_compatible_with_a_different_form():
    pieces = ["SUM", "AVERAGE", "DIVIDE", "COUNTROWS", "(", ")", ",", "/", "'fact_sales'", "[amount]", "[m]", "0",
              "-", "BLANK()", "CALCULATE", " ", "\u00e9", "\"x\"", "VAR", "=", "''", "]]"]
    rng = random.Random(7)
    for _ in range(300):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 20)))
        try:
            form, _ = sp._dax_form(text, frozenset({"m"}))
        except sp._Outside:
            continue
        assert form[0] in ("agg", "rows", "measure", "div"), text


# ---- redaction and the ceiling

def test_redacted_hashes_expression_text_and_forms_so_no_literal_leaves():
    bim = _model("CALCULATE(SUM('fact_sales'[amount]), 'dim_customer'[country] = \"secret-DE-4711\")")
    bim["model"]["tables"][0]["measures"].append(
        {"name": "ratio", "expression": "DIVIDE(SUM('fact_sales'[amount]), COUNTROWS('fact_sales'), 98765)"})
    doc = _ossie(bim, "SUM(amount) FILTER (WHERE country = 'secret-DE-4711')")
    report = _assert_l2(sp.parity_l2(bim, doc))
    shown = json.dumps(report)
    assert "secret-DE-4711" in shown and "98765" in shown
    red = sp.redacted(report)
    text = json.dumps(red)
    assert "secret-DE-4711" not in text and "98765" not in text
    m = _measure(red)
    assert m["expressions"]["left"].endswith(sp._fingerprint(_measure(report)["expressions"]["left"]).split(", ")[1])
    assert m["expressions"]["left_dialect"] == "DAX"
    assert red["redacted"] is True and report.get("redacted") is None


def test_l2_refuses_to_report_anything_above_the_ceiling(monkeypatch):
    with pytest.raises(AssertionError):
        sp._check_ceiling([{"result": sp.COMPATIBLE, "expression_result": sp.VERIFIED}], sp.COMPATIBLE, "L2")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sp, "_compare_forms", lambda *a: (sp.VERIFIED, [], []))
        with pytest.raises(AssertionError):
            sp.parity_l2(_bim(), _bim())
    real = sp._pair
    monkeypatch.setattr(sp, "_pair", lambda *args: real(*args) | {"result": sp.WITHIN_TOLERANCE})
    with pytest.raises(AssertionError):
        sp.parity_l2(_bim(), _bim())


def test_l2_reports_its_level_subset_and_tolerances_and_bad_input_is_test_failed():
    report = _assert_l2(sp.parity_l2(_bim(), _bim(), tolerances={"absolute": 0.5}))
    assert report["summary"] == sp.COMPATIBLE
    assert report["tolerances"]["values"]["absolute"] == 0.5 and "L2" in report["tolerances"]["note"]
    assert {r["outcome"] for r in report["subset"]} == {sp.COMPATIBLE, sp.DIFFERENT, sp.UNSUPPORTED}
    assert sp.parity_l1(_bim(), _bim())["level"] == "L1" and "subset" not in sp.parity_l1(_bim(), _bim())
    bad = _assert_l2(sp.parity_l2(None, _bim()))
    assert bad["summary"] == sp.TEST_FAILED


# ---- review follow-ups

def test_a_bare_dax_column_reference_is_never_read_as_a_measure():
    with pytest.raises(sp._Outside, match="unqualified column reference"):
        sp._dax_form("[Amount]", frozenset({"total"}))
    assert sp._dax_form("[Total]", frozenset({"total"}))[0] == ("measure", "total")
    m = _l2("[amount]", "SUM(amount)")
    assert m["result"] == sp.UNSUPPORTED and any("unqualified column reference" in n for n in m["notes"])
    # a column [amount] against a SQL metric that names a metric "amount": never a match
    bim = _model("[amount]")
    doc = _ossie(bim, '"amount"')
    doc["metrics"].append({"name": "amount", "expression": {"dialects": [{"dialect": "ANSI_SQL",
                                                                          "expression": "SUM(fact_sales.amount)"}]}})
    m = _measure(_assert_l2(sp.parity_l2(bim, doc)))
    assert m["result"] != sp.COMPATIBLE and any("unqualified column reference" in n for n in m["notes"])


def test_dax_count_against_dax_counta_is_unsupported_on_a_boolean_or_untyped_column():
    def pair(column, data_type):
        left, right = _model(f"COUNT('fact_sales'[{column}])"), _model(f"COUNTA('fact_sales'[{column}])")
        for bim in (left, right):
            if data_type is not None:
                bim["model"]["tables"][0]["columns"].append(
                    {"name": column, "dataType": data_type, "sourceColumn": column})
        return _measure(_assert_l2(sp.parity_l2(left, right)))
    for column, data_type in (("flag", "boolean"), ("ghost", None)):
        m = pair(column, data_type)
        assert m["result"] == sp.UNSUPPORTED, m
        assert any("DAX COUNTA" in n and "boolean or has no known type" in n for n in m["notes"])
    m = pair("amount", None)  # decimal in the test model
    assert m["result"] == sp.COMPATIBLE and any("this column isn't a boolean" in n for n in m["notes"])
    both = _model("COUNT('fact_sales'[ghost])")
    assert _measure(sp.parity_l2(both, copy.deepcopy(both)))["result"] == sp.COMPATIBLE  # the same function


def test_a_bare_sql_name_that_is_both_a_metric_and_a_column_is_ambiguous():
    bim = _model("[amount]")
    bim["model"]["tables"][0]["measures"].append({"name": "amount", "expression": "SUM('fact_sales'[amount])"})
    m = _measure(_assert_l2(sp.parity_l2(bim, _ossie(bim, "amount"))))
    assert m["result"] == sp.UNSUPPORTED, m
    assert any("names both a measure and a column" in n and "ambiguous" in n for n in m["notes"])


@pytest.mark.parametrize("alternate", ["1e3", "1.5E-3", "2.e1", "1."])
def test_a_divide_alternate_with_an_exponent_is_outside_as_not_a_plain_decimal(alternate):
    with pytest.raises(sp._Outside, match="a plain decimal number"):
        sp._dax_form(f"DIVIDE([a], [b], {alternate})", frozenset({"a", "b"}))
    assert sp._dax_form("DIVIDE([a], [b], -1.50)", frozenset({"a", "b"}))[0][3] == "ALT:-1.5"
