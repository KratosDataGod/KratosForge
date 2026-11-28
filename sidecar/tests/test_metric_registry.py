"""Metric registry engine slice (OSSIE-008a): metric rows, conflicts by name and normalized expression, governed
only when passed, and no expression text in the output."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from fabric_desktop import metric_registry as mr
from tests import ossie_corpus as oc


def _bim() -> dict[str, Any]:
    return copy.deepcopy(oc.load(oc.entry("tmsl/02_rich.bim")))


def _star() -> str:
    return str(oc.load(oc.entry("ossie/v02_02_star.yaml")))


def _set(bim: dict[str, Any], name: str, expression: Any) -> dict[str, Any]:
    for t in bim["model"]["tables"]:
        for m in t.get("measures", []):
            if m["name"] == name:
                m["expression"] = expression
                return bim
    t = bim["model"]["tables"][0]
    t.setdefault("measures", []).append({"name": name, "expression": expression})
    return bim


def test_rows_carry_the_fields() -> None:
    rows = mr.public(mr.metrics({"sales": _bim()}))
    total = next(r for r in rows if r["name"] == "total amount")
    assert total["source"] == "sales" and total["table"] == "orders" and total["kind"] == "tmsl"
    assert total["basis"] == mr.L2 and total["l2_form"] == "SUM(orders[amount])"
    assert total["expression_chars"] == len("SUM('orders'[amount])")
    assert total["expression_fingerprint"].startswith(f"{total['expression_chars']} characters, fingerprint ")
    assert total["format_string"] == "#,0.00" and total["has_description"] is False
    assert total["dialects"] == ["DAX"] and total["governance"] == mr.LOCAL
    assert {r["name"] for r in rows} == {"order rows", "total amount", "amount per order"}


def test_two_models_with_different_expressions_give_one_conflict() -> None:
    other = _set(_bim(), "total amount", "SUM('orders'[order_id])")
    report = mr.conflicts(mr.metrics({"finance": _bim(), "sales": other}))
    assert report["counts"]["conflicts"] == 1
    (c,) = report["conflicts"]
    assert c["name"] == "total amount"
    assert c["sources"] == ["finance (orders)", "sales (orders)"]
    assert c["agree"] == [["finance (orders)"], ["sales (orders)"]]
    (ev,) = c["evidence"]
    assert ev["changes"] == ["column orders[amount] in finance (orders), orders[order_id] in sales (orders)"]


@pytest.mark.parametrize("left,right", [
    ("CALCULATE(SUM('orders'[amount]), 'orders'[amount] > 0)",
     "calculate ( sum ( 'orders'[amount] ) ,\n   'orders'[amount]>0 )"),
    ("SUM('orders'[amount])", "  sum( 'orders'[amount] )\n"),
    ("SUMX('orders', 'orders'[amount] * 2)", "SumX ('orders','orders'[amount]*2)"),
], ids=["calculate", "l2-sum", "iterator"])
def test_same_expression_written_differently_is_no_conflict(left: str, right: str) -> None:
    rows = mr.metrics({"a": _set(_bim(), "m", left), "b": _set(_bim(), "m", right)})
    assert mr.conflicts(rows)["conflicts"] == []
    m = [r for r in rows if r["name"] == "m"]
    assert m[0]["key"] == m[1]["key"]


def test_text_only_is_labelled_and_differs_without_quoting() -> None:
    rows = mr.metrics({"a": _set(_bim(), "m", "CALCULATE(SUM('orders'[amount]), 'orders'[amount] > 0)"),
                       "b": _set(_bim(), "m", "CALCULATE(SUM('orders'[amount]), 'orders'[amount] > 1)")})
    m = [r for r in rows if r["name"] == "m"]
    assert {r["basis"] for r in m} == {mr.TEXT_ONLY} and "CALCULATE" in m[0]["l2_reason"]
    (c,) = mr.conflicts(rows)["conflicts"]
    assert c["evidence"][0]["changes"] == [mr.TEXT_DIFFERS]


def test_quoted_names_keep_their_case_and_spacing_out_of_the_fold() -> None:
    assert mr._folded("CALCULATE ( [a b] )") == "calculate([a b])"
    assert mr._folded("VAR x = 1 RETURN x") == "var x=1 return x"
    assert mr._folded("'Sales Table'[x]") != mr._folded("'SalesTable'[x]")


def test_names_match_trimmed_and_casefolded() -> None:
    other = _set(_bim(), "total amount", "SUM('orders'[order_id])")
    for t in other["model"]["tables"]:
        for m in t.get("measures", []):
            if m["name"] == "total amount":
                m["name"] = " Total Amount "
    found = mr.conflicts(mr.metrics({"a": _bim(), "b": other}))["conflicts"]
    # (the renamed measure also changes what [total amount] in 'amount per order' refers to on side b)
    assert [c["sources"] for c in found if c["name"] == "total amount"] == [["a (orders)", "b (orders)"]]


def test_identical_text_read_by_l2_on_one_side_only_is_no_conflict() -> None:
    # [helper] is a measure in a, so L2 reads the reference; b has no such measure, so L2 can't, and b is text only
    a = _set(_set(_bim(), "helper", "SUM('orders'[amount])"), "m", "[helper]")
    b = _set(_bim(), "m", "[helper]")
    rows = mr.metrics({"a": a, "b": b})
    m = {r["source"]: r for r in rows if r["name"] == "m"}
    assert (m["a"]["basis"], m["b"]["basis"]) == (mr.L2, mr.TEXT_ONLY) and m["a"]["key"] != m["b"]["key"]
    assert [c["name"] for c in mr.conflicts(rows)["conflicts"]] == []


def test_agreement_is_transitive() -> None:
    # b = a by text (b has no measure [helper], so L2 can't read it); a = c by L2 (the table written unquoted);
    # b and c share neither key, yet all three are one group
    expr = "DIVIDE([helper], SUM('orders'[amount]))"
    a = _set(_set(_bim(), "helper", "SUM('orders'[amount])"), "m", expr)
    b = _set(_bim(), "m", expr)
    c = _set(_set(_bim(), "helper", "SUM('orders'[amount])"), "m", "DIVIDE([helper], SUM(orders[amount]))")
    d = _set(_bim(), "m", "COUNTROWS('orders')")
    rows = mr.metrics({"b": b, "c": c, "a": a, "d": d})
    m = {r["source"]: r for r in rows if r["name"] == "m"}
    assert m["b"]["key"] != m["c"]["key"] and m["b"]["_text_key"] != m["c"]["_text_key"]
    (conflict,) = [x for x in mr.conflicts(rows)["conflicts"] if x["name"] == "m"]
    assert conflict["agree"] == [["b (orders)", "c (orders)", "a (orders)"], ["d (orders)"]]
    assert [x for x in mr.conflicts(mr.metrics({"b": b, "c": c, "a": a}))["conflicts"] if x["name"] == "m"] == []


def test_public_rows_hold_no_internal_keys() -> None:
    assert all(not k.startswith("_") for r in mr.public(mr.metrics({"a": _bim()})) for k in r)


def test_possible_duplicates_are_inferred_and_apart() -> None:
    bim = _set(_bim(), "revenue", "SUM('orders'[amount])")
    report = mr.conflicts(mr.metrics({"a": bim}))
    assert report["conflicts"] == []
    (d,) = report["possible_duplicates"]
    assert d["inferred"] is True and "inferred" in d["label"]
    assert d["names"] == ["total amount", "revenue"]
    assert d["sources"] == ["a (orders): total amount", "a (orders): revenue"]


def test_governed_only_when_passed() -> None:
    rows = mr.metrics({"a": _bim(), "b": _bim()}, governed=[("a", "orders", "total amount")])
    governed = [(r["source"], r["name"]) for r in rows if r["governance"] == mr.GOVERNED]
    assert governed == [("a", "total amount")]
    assert all(r["governance"] == mr.LOCAL for r in mr.metrics({"a": _bim(), "b": _bim()}))
    # a near miss (another table, another case) is still local: nothing is inferred
    rows = mr.metrics({"a": _bim()}, governed=[("a", "customers", "total amount"), ("a", "orders", "Total Amount")])
    assert all(r["governance"] == mr.LOCAL for r in rows)


def test_local_metrics_never_show_as_governed_in_a_conflict() -> None:
    other = _set(_bim(), "total amount", "SUM('orders'[order_id])")
    rows = mr.metrics({"a": _bim(), "b": other}, governed=[("b", "orders", "total amount")])
    (c,) = mr.conflicts(rows)["conflicts"]
    assert c["governed"] == ["b (orders)"]


def test_mixed_kinds_bim_and_ossie() -> None:
    tmdl_files = oc.load(oc.entry("tmdl/rich.SemanticModel"))
    v03 = (oc.CORPUS / "versions" / "v03_star.yaml").read_text(encoding="utf-8")
    rows = mr.metrics({"bim": _bim(), "star": _star(), "v03": v03, "tmdl": tmdl_files})
    totals = {r["source"]: r for r in rows if r["name"] == "total amount"}
    assert {r["kind"] for r in totals.values()} == {"tmsl", "ossie", "tmdl"}
    assert totals["star"]["dialect"] == "ANSI_SQL" and totals["bim"]["dialect"] == "DAX"
    assert len({r["key"] for r in totals.values()}) == 1
    assert mr.conflicts(rows)["conflicts"] == []
    other = _set(_bim(), "total amount", "AVERAGE('orders'[amount])")
    (c,) = mr.conflicts(mr.metrics({"bim": other, "star": _star()}))["conflicts"]
    assert c["agree"] == [["bim (orders)"], ["star"]]
    assert "aggregate AVERAGE" in c["evidence"][0]["changes"][0]


def test_bad_source_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="'broken'"):
        mr.metrics({"broken": {"nothing": 1}})


SECRET = "sk_live_" + "Zq9" * 40  # pragma: allowlist secret


@pytest.mark.parametrize("expression", [
    f'CALCULATE(SUM(\'orders\'[amount]), \'orders\'[note] = "{SECRET}")',
    f'IF(1 = 1, "{SECRET}", BLANK())',
    f"SUM('orders'[amount]) // {SECRET}",
], ids=["filter", "if", "comment"])
def test_no_long_expression_text_or_secret_in_output(expression: str) -> None:
    bim = _set(_bim(), "secret", expression)
    bim["model"]["roles"] = [{"name": "west", "modelPermission": "read",
                              "members": [{"memberName": "person@example.com"}],
                              "tablePermissions": [{"name": "orders",
                                                    "filterExpression": f"[note] = \"{SECRET}\""}]}]
    other = _set(_bim(), "secret", expression.replace("amount", "order_id"))
    rows = mr.metrics({"a": bim, "b": other})
    out = json.dumps([mr.public(rows), mr.conflicts(rows)], default=str)
    assert SECRET[:12] not in out and "person@example.com" not in out and "filterExpression" not in out
    assert expression[:40] not in out
    for value in _strings([mr.public(rows), mr.conflicts(rows)]):
        assert len(value) <= mr.MAX_TEXT + 30, value[:40]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


@pytest.mark.parametrize("opener", ["[", "'", '"'], ids=["bracket", "quote", "dquote"])
def test_an_unclosed_quote_or_bracket_is_one_token_to_the_end(opener):
    """An unclosed "[" (or quote) takes the rest of the text in one step, so a long run of them is read once,
    not rescanned from each one: 100,000 of them fold to one token."""
    from fabric_desktop import metric_registry
    text = "SUM(x) + " + opener + "a" * 10 + opener * 100_000
    tokens = metric_registry._TOKEN.findall(text)
    assert len(tokens) <= 8 and tokens[-1].startswith(opener)
    assert metric_registry._folded(text).startswith("sum(x)+")


def test_an_unclosed_backtick_is_one_token_to_the_end():
    from fabric_desktop import metric_registry
    tokens = metric_registry._TOKEN.findall("SUM(x) + `" + "a [ ' " * 20_000)
    assert len(tokens) <= 8 and tokens[-1].startswith("`")


def _avg_model(measures):
    return {"model": {"tables": [{"name": "orders", "columns": [{"name": "amount", "dataType": "double"}],
                                  "measures": measures}]}}


def test_divides_alternate_result_is_never_shown():
    """The L2 form keeps DIVIDE's alternate result, a literal from the expression: rows and evidence show it as a
    keyed fingerprint, never the value."""
    from fabric_desktop import metric_registry
    rows = metric_registry.metrics({
        side: _avg_model([{"name": "avg", "expression": f"DIVIDE(SUM('orders'[amount]), COUNTROWS('orders'), {alt})"}])
        for side, alt in (("a", 98765), ("b", 12345))})
    text = json.dumps({"rows": metric_registry.public(rows), "conflicts": metric_registry.conflicts(rows)})
    assert "98765" not in text and "12345" not in text
    assert all("alternate" in r["l2_form"] for r in metric_registry.public(rows))


@pytest.mark.parametrize("missing", [{"name": "avg"}, {"name": "avg", "expression": ""}], ids=["absent", "empty"])
def test_evidence_names_the_side_with_no_expression(missing):
    from fabric_desktop import metric_registry
    rows = metric_registry.metrics({"a": _avg_model([{"name": "avg", "expression": "SUM('orders'[amount])"}]),
                                    "b": _avg_model([missing])})
    (conflict,) = metric_registry.conflicts(rows)["conflicts"]
    assert conflict["evidence"][0]["changes"] == ["b (orders) has no expression; a (orders) has one"]
