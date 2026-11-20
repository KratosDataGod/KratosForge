"""Pricing edges the main tests don't pin down (mutation batch 193, docs/quality/mutation-testing.md):
what a version keeps, its id, the dates at their boundary, a zero or fractional CU figure, and net as
the default basis."""

from __future__ import annotations

import re

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import attribution as at
from fabric_desktop.savings import pricing as pr


def v(rate=0.18, start="2026-01-01T00:00:00Z", end=None, **kw):
    return pr.scenario(scenario=kw.pop("name", "List"), rate_per_cu_hour=rate, currency="USD", effective_from=start,
                       effective_to=end, pricing_model=pr.PAY_AS_YOU_GO, confidence="confirmed",
                       source=kw.pop("source", "Azure price sheet"), **kw)


def test_a_version_keeps_its_rate_to_six_places_its_source_trimmed_and_a_16_digit_id():
    row = v(rate=0.1234567, source="  Azure price sheet  ")
    assert row["rate_per_cu_hour"] == 0.123457
    assert row["source"] == "Azure price sheet"
    assert re.fullmatch(r"pv-[0-9a-f]{16}", row["pricing_version"])


def test_a_version_that_ends_when_it_starts_is_refused():
    with pytest.raises(ValueError, match="after the start"):
        v(start="2026-11-01T00:00:00Z", end="2026-11-01T00:00:00Z")


def test_an_overlap_names_the_scenario():
    a = v(start="2026-01-01T00:00:00Z", end="2026-12-01T00:00:00Z", sku="F64")
    b = v(rate=0.2, start="2026-11-01T00:00:00Z", sku="F64")
    with pytest.raises(ValueError, match="scenario 'List' overlap"):
        pr.check([a, b])


def test_zero_and_fractional_cu_are_priced():
    assert pr.value(0, "2026-11-10", [v()], "List")["value"] == 0
    assert pr.value(0.5, "2026-11-10", [v()], "List")["value"] == pytest.approx(0.09)
    with pytest.raises(ValueError, match="zero or more"):
        pr.value(-0.5, "2026-11-10", [v()], "List")


def _cu(cls, x):
    if cls == ms.MEASURED:
        return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_HOURS, x, run_id="r", source="Capacity Metrics export",
                       collected_at="2026-11-01T00:00:00Z")
    return ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_HOURS, x, method="calibration", assumptions=["median"])


def test_runs_are_priced_on_net_by_default():
    run = {"execution_id": "e1", "workload_key": "nb:sales", "baseline_version": "b1",
           "at": "2026-11-10T00:00:00Z", "ran": at.PC}
    r = at.attribute(run, _cu(ms.MEASURED, 10), local_avoided=_cu(ms.ESTIMATED, 6), overhead=_cu(ms.MEASURED, 1))
    out = pr.value_runs([r], [v(rate=1)], "List")
    assert out["basis"] == "net" and out["runs"][0]["cu_hours"] == 5 and out["total"]["value"] == 5
    gross = pr.value_runs([r], [v(rate=1)], "List", net=False)
    assert gross["basis"] == "gross" and gross["total"]["value"] == 6
