"""Pricing scenarios with a rate per DBU (by SKU) and per Snowflake credit, added without changing any
existing version: a scenario without the new rates keeps its row and its pricing_version."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import pricing as pr

BASE = {"scenario": "List", "rate_per_cu_hour": 0.18, "currency": "USD", "effective_from": "2026-11-03T00:00:00Z",
        "source": "Public price pages"}


def test_a_scenario_without_the_new_rates_is_unchanged():
    row = pr.scenario(**BASE)
    assert "rate_per_dbu" not in row and "rate_per_credit" not in row
    # Pinned: the id every PC already computed for this version.
    assert row == pr.scenario(**BASE, rate_per_dbu=None, rate_per_credit=None)
    assert row["pricing_version"] == pr.scenario(**BASE, rate_per_dbu={}, rate_per_credit="")["pricing_version"]


def test_the_new_rates_are_part_of_the_version():
    a = pr.scenario(**BASE, rate_per_dbu={"jobs_compute": 0.15}, rate_per_credit=2)
    b = pr.scenario(**BASE, rate_per_dbu={"JOBS_COMPUTE": 0.16}, rate_per_credit=2)
    assert a["rate_per_dbu"] == {"JOBS_COMPUTE": 0.15} and a["rate_per_credit"] == 2.0
    assert len({a["pricing_version"], b["pricing_version"], pr.scenario(**BASE)["pricing_version"]}) == 3


@pytest.mark.parametrize("kw, words", [
    ({"rate_per_dbu": {"JOBS COMPUTE": 0.1}}, "isn't a Databricks SKU"),
    ({"rate_per_dbu": {"JOBS_COMPUTE": 0}}, "above 0"),
    ({"rate_per_dbu": {"JOBS_COMPUTE": True}}, "above 0"),
    ({"rate_per_dbu": [0.1]}, "given by SKU"),
    ({"rate_per_credit": "two"}, "rate per Snowflake credit"),
    ({"rate_per_credit": float("inf")}, "above 0"),
])
def test_bad_rates_are_refused(kw, words):
    with pytest.raises(ValueError, match=words):
        pr.scenario(**BASE, **kw)


def test_price_native_is_a_scenario_in_its_own_unit():
    v = pr.scenario(**BASE, rate_per_dbu={"JOBS_COMPUTE": 0.15}, rate_per_credit=2)
    cu = pr.price_native(2880, "fabric_cu_seconds", v)
    assert cu["classification"] == ms.SCENARIO and cu["quantity"] == pytest.approx(0.8) and cu["value"] == pytest.approx(0.144)
    assert pr.price_native(1.42, "databricks_dbu", v, sku="JOBS_COMPUTE")["value"] == pytest.approx(0.213)
    assert pr.price_native(0.27, "snowflake_credits", v)["pricing_version"] == v["pricing_version"]


def test_price_native_without_a_figure_or_a_rate_is_unknown_never_zero():
    v = pr.scenario(**BASE)
    for m in (pr.price_native(None, "fabric_cu_seconds", v), pr.price_native(1.0, "databricks_dbu", v, sku="X"),
              pr.price_native(1.0, "snowflake_credits", v)):
        assert m["classification"] == ms.UNKNOWN and m["value"] is None and m["reason"] and m["pricing_version"] is None
    with pytest.raises(ValueError, match="isn't priced"):
        pr.rate_for(v, "seconds")
    with pytest.raises(ValueError, match="finite figure"):
        pr.price_native(-1, "snowflake_credits", v)
