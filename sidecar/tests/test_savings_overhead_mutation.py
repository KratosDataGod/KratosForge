"""Overhead per execution: what the first mutation run of savings/overhead.py left alive (batch 333)."""

from __future__ import annotations

import pytest

from fabric_desktop import measurement as ms
from fabric_desktop.savings import overhead as oh


def execution(**kw) -> dict:
    row = {"execution_id": "e1", "artifact_key": "nb:sales", "workspace_id": "w1", "capacity_id": "c1",
           "finished_at": "2026-11-10T00:05:00Z", "reads": 3}
    return row | kw


def estimated_sync(sync_id: str = "s1") -> list[dict]:
    """A sync whose figures are estimated, with a method, assumptions and a period given as a dict."""
    out = []
    for metric, unit, value in (("optimize_sync_seconds", ms.SECONDS, 2.0), ("optimize_sync_rows", ms.COUNT, 4),
                                ("optimize_sync_commits", ms.COUNT, 1)):
        m = ms.make(ms.ESTIMATED, metric, unit, value, run_id=sync_id, method="from the sync log",
                    assumptions=["the log is complete"], period_start="2026-11-10T00:59:58Z",
                    period_end="2026-11-10T01:00:00Z")
        out.append(m)
    return out


def test_a_transfer_figure_keeps_the_runs_workspace_and_capacity():
    m = oh.transfer(execution())["reads"]
    assert (m["workspace_id"], m["capacity_id"], m["workload_id"]) == ("w1", "c1", "nb:sales")
    m = oh.transfer(execution(workspace_id="", capacity_id=""))["reads"]
    assert (m["workspace_id"], m["capacity_id"]) == (None, None)


def test_a_sync_keeps_each_figures_method_assumptions_and_period():
    s = oh.sync(estimated_sync())
    for name in oh.SYNC:
        assert s[name]["method"] == "from the sync log"
        assert s[name]["assumptions"] == ["the log is complete"]
        assert s[name]["period"]["start"].startswith("2026-11-10T00:59:58")
        assert s[name]["period"]["end"].startswith("2026-11-10T01:00:00")


def test_a_shared_rows_figure_keeps_the_syncs_method_and_assumptions():
    shares = oh.share_sync(oh.sync(estimated_sync()), {"a": 3, "b": 1})
    for share in shares:
        assert share["rows"]["method"] == "from the sync log"
        assert share["rows"]["assumptions"] == ["the log is complete"]
        assert share["commits"]["metric"] == "optimize_sync_commits"
    assert [s["rows"]["value"] for s in shares] == [3, 1]


def test_a_run_without_an_execution_id_cant_share_a_sync():
    with pytest.raises(ValueError, match="Each run needs its execution_id"):
        oh.share_sync(oh.sync(estimated_sync()), {"": 4})


def test_a_run_synced_once_keeps_that_syncs_figures_as_they_are():
    (share,) = oh.share_sync(oh.sync(estimated_sync()), {"e1": 4})
    out = oh.per_execution(execution(), [share])
    for name in oh.SYNC:
        assert out["measurements"][f"sync_{name}"] == share[name]
