"""The environment's own cost, kept apart from savings (CP-011): measured overhead only, unknown stays
unknown, and net savings only when both sides are known and comparable."""

from __future__ import annotations

import datetime as dt

from fabric_desktop import measurement as ms
from fabric_desktop.controlplane import cost

NOW = dt.datetime(2026, 11, 20, 12, tzinfo=dt.timezone.utc)


def _row(metric, value, *, days_ago=1, workload="optimize.sync.analysis", unit=None, pc="pc-a"):
    return {"installation_id": pc, "workload_key": workload, "metric": metric, "classification": ms.MEASURED, "value": value,
            "unit": unit or (ms.SECONDS if metric.endswith("seconds") else ms.COUNT), "currency": None,
            "source": "Optimize sync on this PC", "method": None, "assumptions": None,
            "collected_at": (NOW - dt.timedelta(days=days_ago)).isoformat()}


def test_only_this_periods_sync_rows_are_added_and_labelled_measured():
    rows = [_row("optimize_sync_seconds", 2.0), _row("optimize_sync_seconds", 3.0, days_ago=3, pc="pc-b"),
            _row("optimize_sync_seconds", 99.0, days_ago=40),                  # before the period
            _row("optimize_sync_seconds", 50.0, workload="xray.scan"),        # not the environment's sync
            _row("optimize_sync_commits", 2), {"metric": "optimize_sync_rows", "workload_key": "optimize.sync.x",
                                               "collected_at": "not a time"}]
    got = cost.summary(rows, days=30, now=NOW, installation_id="pc-a")
    seconds = got["sync_overhead"]["optimize_sync_seconds"]
    assert seconds["value"] == 5.0 and seconds["label"] == "Measured" and seconds["complete"]
    assert got["sync_scope"] == cost.SYNC_SCOPE and got["pcs"] == 2  # every installation's rows
    assert got["this_pc"]["optimize_sync_seconds"]["value"] == 2.0  # and this PC's own share
    assert cost.summary(rows, now=NOW)["this_pc"] is None
    assert got["syncs"] == 2 and got["sync_overhead"]["optimize_sync_commits"]["value"] == 2
    assert got["sync_overhead"]["optimize_sync_rows"]["value"] == 0  # nothing ran in the period: observed zero
    # A row without an installation id still counts toward the overhead, but isn't another PC.
    assert cost.summary(rows + [_row("optimize_sync_seconds", 1.0, pc="")], now=NOW)["pcs"] == 2


def test_fabric_capacity_and_money_are_unknown_never_estimated():
    got = cost.summary([], now=NOW)
    for key in ("fabric_cu", "cost"):
        assert got[key]["classification"] == ms.UNKNOWN and got[key]["value"] is None and got[key]["reason"]
    assert got["net"] == {"value": None, "reason": cost.NO_NET}


def test_net_needs_both_sides_known_and_comparable():
    usd = lambda cls, v: {"classification": cls, "value": v, "unit": ms.CURRENCY, "currency": "USD"}  # noqa: E731
    assert cost.net(usd(ms.MEASURED, 100.0), ms.unknown("c", ms.CURRENCY, "x"))["value"] is None
    assert cost.net(None, usd(ms.MEASURED, 1.0))["value"] is None
    assert "same unit" in cost.net(usd(ms.MEASURED, 100.0), {**usd(ms.MEASURED, 1.0), "currency": "EUR"})["reason"]
    assert "scenario" in cost.net(usd(ms.SCENARIO, 100.0), usd(ms.MEASURED, 1.0))["reason"].lower()
    got = cost.net(usd(ms.ESTIMATED, 100.0), usd(ms.MEASURED, 30.0))
    assert got["value"] == 70.0 and got["label"] == "Estimated"
    assert cost.net(usd(ms.SCENARIO, 10.0), usd(ms.SCENARIO, 4.0))["label"] == "Scenario"
    assert cost.net(usd(ms.MEASURED, 10.0), usd(ms.MEASURED, 4.0))["label"] == "Measured"
