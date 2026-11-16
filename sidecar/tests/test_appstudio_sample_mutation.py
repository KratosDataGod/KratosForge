"""Mutation-driven checks for appstudio/sample.py (batch 50).

Sample numbers are a stable function of their inputs (sha256, first 4 bytes, 1,000..99,999), a
slicer on another field shrinks a number by 0.15..0.39, cards and field-less charts show one
value, and a long category list is cut to 12 rows.
"""

from __future__ import annotations

import hashlib

from fabric_desktop.appstudio import sample

NAMES = {"m_rev": "Revenue", "m_profit": "Profit"}


def _expected_number(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return 1_000 + int.from_bytes(digest[:4], "big") % 99_000


def _expected_factor(selection: dict[str, str]) -> float:
    key = "|".join(f"{k}={v}" for k, v in sorted(selection.items()))
    return 0.15 + (_expected_number(key) % 25) / 100


def test_number_is_the_documented_hash_and_stays_in_range():
    for parts in [("a",), ("c_rev", "m_rev", "North"), ("x", "y", "")]:
        n = sample._number(*parts)
        assert n == _expected_number(*parts)
        assert 1_000 <= n < 100_000


def test_factor_is_one_without_a_selection_and_between_015_and_039_with_one():
    assert sample._factor({}) == 1.0
    for sel in [{"Region[Name]": "North"}, {"Date[Month]": "Jan", "Region[Name]": "West"}]:
        f = sample._factor(sel)
        assert f == _expected_factor(sel)
        assert 0.15 <= f <= 0.39


def test_column_and_unknown_field_categories():
    assert sample._column("Sales[Amount]") == "Amount"
    assert sample._column("'My table'[Order date]") == "Order date"
    assert sample.categories("Foo[Bar]") == ["Bar 1", "Bar 2", "Bar 3", "Bar 4", "Bar 5"]
    assert sample.categories("Region[Name]") == ["North", "South", "East", "West", "Central"]


def _card(**over):
    c = {"id": "c1", "type": "card", "title": "T", "layout": {}, "measures": ["m_rev"], "fields": []}
    c.update(over)
    return c


def test_card_without_a_field_or_selection_is_the_plain_number():
    out = sample._component(_card(), NAMES, {})
    assert out["value"] == _expected_number("c1", "m_rev", "")
    assert out["measure"] == "Revenue"


def test_card_without_a_field_shrinks_once_by_the_selection():
    sel = {"Region[Name]": "North"}
    out = sample._component(_card(), NAMES, sel)
    assert out["value"] == round(_expected_number("c1", "m_rev", "") * _expected_factor(sel))


def test_card_with_a_field_sums_its_categories_and_shrinks_by_another_slicer():
    sel = {"Date[Month]": "Jan"}
    out = sample._component(_card(fields=["Region[Name]"]), NAMES, sel)
    total = sum(_expected_number("c1", "m_rev", r) for r in sample.categories("Region[Name]"))
    assert out["value"] == round(total * _expected_factor(sel))


def test_card_without_measures_has_no_measure_name():
    out = sample._component(_card(measures=[]), NAMES, {})
    assert out["value"] == 0
    assert out["measure"] == ""


def test_chart_without_a_field_shows_one_value_like_a_card():
    out = sample._component(_card(type="bar"), NAMES, {})
    assert out["value"] == _expected_number("c1", "m_rev", "")
    assert "series" not in out


def test_a_long_category_list_is_cut_to_twelve_rows(monkeypatch):
    weeks = [f"W{i}" for i in range(1, 21)]
    monkeypatch.setitem(sample._CATEGORIES, "week", weeks)
    out = sample._component(_card(type="bar", fields=["Date[Week]"]), NAMES, {})
    assert out["labels"] == weeks[:12]
    assert len(out["series"][0]["values"]) == 12
