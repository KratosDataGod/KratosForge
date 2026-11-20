"""A metadata project at its exact edges (boundary re-score of batch 14, mutation batch 159,
docs/quality/mutation-testing.md): a lookback and a delete count of 0, an exception on its last day
and exactly EXCEPTION_WARN_DAYS before it, and reads from an entity's own layer. Nothing runs."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop.meta import project as P
from fabric_desktop.meta.project import describe_exceptions, validate
from fabric_desktop.meta.templates import init_project

TODAY = dt.date(2026, 11, 14)


@pytest.fixture
def proj(tmp_path):
    return init_project(tmp_path / "project", "3", "Chris", True)


def test_a_lookback_and_a_delete_count_of_zero_are_allowed():
    assert P.parse_lookback(0) == 0
    assert P.parse_max_deletes(0) == ("count", 0.0)
    with pytest.raises(ValueError):
        P.parse_lookback(-1)
    with pytest.raises(ValueError):
        P.parse_max_deletes(-1)


def _status(proj, days):
    proj.exceptions = [{"entity": "gold.fact_sales", "rule": "owner",
                        "expires": (TODAY + dt.timedelta(days=days)).isoformat()}]
    (row,) = describe_exceptions(proj, TODAY)
    return row["status"]


def test_an_exception_on_its_last_day_is_expiring_not_expired(proj):
    assert _status(proj, 0) == "expiring"
    assert _status(proj, -1) == "expired"


def test_an_exception_exactly_warn_days_away_is_expiring(proj):
    assert _status(proj, P.EXCEPTION_WARN_DAYS) == "expiring"
    assert _status(proj, P.EXCEPTION_WARN_DAYS + 1) == "active"


def test_an_exception_on_its_last_day_passes_the_gate(proj):
    proj.exceptions = [{"entity": "gold.fact_sales", "rule": "owner", "expires": TODAY.isoformat()}]
    assert not [e for e in validate(proj, TODAY) if "expired on" in e]
    proj.exceptions[0]["expires"] = (TODAY - dt.timedelta(days=1)).isoformat()
    assert [e for e in validate(proj, TODAY) if "expired on" in e]


def test_a_read_from_its_own_layer_is_neither_later_nor_another_layer(proj):
    proj.layer("gold").reads_from = ["silver"]
    errors = validate(proj, TODAY)
    assert not [e for e in errors if "gold.dim_customer" in e and ("later layer" in e or "may only read" in e)]
    assert not [e for e in errors if e.startswith("gold.fact_sales:") and ("later layer" in e or "may only read" in e)]
