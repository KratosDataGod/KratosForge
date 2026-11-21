"""Receipts (receipts.py): the store's and the report's edges the receipt tests don't reach. Receipts go
to a temporary folder."""

from __future__ import annotations

import pytest

from fabric_desktop import receipts
from fabric_desktop.receipts import ReceiptError, ReceiptStore


def test_a_key_that_isnt_text_is_refused_before_hashing():
    with pytest.raises(ReceiptError, match="keys must be text"):
        receipts.canonical({"outputs": [{1: "x"}]})


def test_an_unknown_receipt_is_a_lookup_error_and_an_empty_store_lists_nothing(tmp_path):
    store = ReceiptStore(tmp_path / "receipts")
    assert store.list() == []
    with pytest.raises(LookupError, match="No receipt 0123456789ab"):
        store.get("0123456789ab" + "0" * 52)


def test_a_body_from_elsewhere_is_checked_against_its_id(tmp_path):
    body = receipts.new_receipt("proof", notebook={"name": "Sales"})
    rid = receipts.receipt_id(body)
    store = ReceiptStore(tmp_path)
    assert store.verify(body, rid) is True
    assert store.verify(body | {"kind": "run"}, rid) is False


def test_the_report_shows_missing_counts_text_details_and_cost_assumptions():
    body = receipts.new_receipt("proof", notebook={"name": "Sales"},
                                runs={"pc": {"seconds": 2.5, "status": "ok", "engine": "duckdb"}},
                                cost={"text": "PC runs use no capacity.", "assumptions": ["Prices as listed today."]},
                                outputs=[{"table": "gold.sales", "result": "same", "pc": {"rows": None}}])
    md = receipts.to_markdown(body, receipts.receipt_id(body))
    assert "engine duckdb" in md  # a text detail is shown as it is
    assert "- Prices as listed today." in md
    assert "| gold.sales | same |  | – |" in md  # an unknown count reads as a dash


def test_an_html_report_that_ends_on_a_table_closes_it():
    body = receipts.new_receipt("proof", notebook={"name": "Sales"},
                                outputs=[{"table": "gold.sales", "result": "same", "pc": {"rows": 3}}])
    page = receipts.to_html(body, receipts.receipt_id(body))
    assert page.count("<table>") == page.count("</table>") == 1
    assert page.index("</table>") > page.index("gold.sales")


def test_canonical_checks_every_level_and_redaction_reaches_every_level():
    nested = {"b": [1, {"x": float("nan")}], "a": 1}
    with pytest.raises(receipts.ReceiptError, match="NaN"):
        receipts.canonical(nested)
    with pytest.raises(receipts.ReceiptError, match="keys must be text"):
        receipts.canonical({"a": [({1: 2},)]})
    assert receipts.canonical({"b": [(1, 2), {"y": None}], "a": "é"}) == '{"a":"é","b":[[1,2],{"y":null}]}'.encode()
    body = {"identity": {"user": "u"}, "outputs": [{"examples": [1], "host": "h", "keep": {"uri": "x", "n": [1, {"machine": 2}]}}],
            "workspace": "w", "z": (1, 2)}
    assert receipts.redact(body, ["identity", "locations", "values", "machine"]) == {
        "identity": "hidden", "outputs": [{"examples": "hidden", "host": "hidden", "keep": {"uri": "hidden", "n": [1, {"machine": "hidden"}]}}],
        "workspace": "hidden", "z": [1, 2]}
    assert receipts.redact(body, []) == {"identity": {"user": "u"}, "outputs": [{"examples": [1], "host": "h",
                                         "keep": {"uri": "x", "n": [1, {"machine": 2}]}}], "workspace": "w", "z": [1, 2]}


def test_a_receipt_nested_thousands_deep_is_stored_redacted_and_checked_without_overflowing(tmp_path):
    deep: object = "secret"
    for _ in range(3000):
        deep = {"host": "h", "n": [deep]}
    body = receipts.new_receipt("proof", notebook={"name": "n", "parameters": {"p": deep}})
    store = receipts.ReceiptStore(tmp_path)
    rid = store.put(body)
    assert store.verify(store.get(rid), rid)
    out = receipts.redact(store.get(rid), ["machine"])["notebook"]["parameters"]["p"]
    for _ in range(3000):
        assert out["host"] == "hidden"
        out = out["n"][0]
    assert out == "secret"
    bad = {"n": [float("inf")]}
    for _ in range(3000):
        bad = {"n": [bad]}
    with pytest.raises(receipts.ReceiptError, match="NaN"):
        receipts.canonical(bad)
    too_deep: object = 1
    for _ in range(200_000):
        too_deep = [too_deep]
    with pytest.raises(receipts.ReceiptError, match="nested too deeply"):
        receipts.canonical({"x": too_deep})
