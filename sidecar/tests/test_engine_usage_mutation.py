"""Mutation-killing tests for engines/usage.py (batch 47): unknown units, billed usage with no source,
zero usage, a record as a dict, rounding of totals and records that can't change."""

from __future__ import annotations

import dataclasses

import pytest

from fabric_desktop.engines.usage import UNITS, UsageRecord, totals


def test_an_unknown_unit_is_refused_and_names_the_known_ones():
    with pytest.raises(ValueError, match=r"Unknown unit 'minutes'\. Units: seconds, cpu_seconds"):
        UsageRecord("local-duckdb", "minutes", 1.0, "measured")


def test_billed_usage_needs_to_say_where_the_bill_came_from():
    with pytest.raises(ValueError, match="where the bill came from"):
        UsageRecord("databricks-sql", "databricks_dbu", 0.4, "billed")
    assert UsageRecord("databricks-sql", "databricks_dbu", 0.4, "billed", "system.billing.usage").amount == 0.4


def test_zero_usage_is_a_real_amount():
    assert UsageRecord("local-duckdb", "seconds", 0, "measured").amount == 0


def test_a_record_as_a_dict_has_every_field():
    r = UsageRecord("local-duckdb", "seconds", 2.5, "estimated", "measure.py", ("one core",), at="2027-01-15T08:00:00Z")
    assert r.as_dict() == {"engine": "local-duckdb", "unit": "seconds", "amount": 2.5, "kind": "estimated",
                           "source": "measure.py", "assumptions": ["one core"], "at": "2027-01-15T08:00:00Z"}


def test_totals_round_to_six_places():
    rows = totals([UsageRecord("local-duckdb", "seconds", 0.12345674, "measured")])
    assert rows == [{"kind": "measured", "unit": "seconds", "label": UNITS["seconds"], "amount": 0.123457}]


def test_a_record_cant_be_changed_after_its_checks():
    r = UsageRecord("local-duckdb", "seconds", 1.0, "measured")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.amount = -5.0  # type: ignore[misc]
