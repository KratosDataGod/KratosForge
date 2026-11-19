"""Mutation batch 118: integrations/audit.py (docs/quality/mutation-testing.md). The survivors were the
rounding of a call's seconds, the length an error message is cut to, how many rows the store keeps, what
Recent activity returns and its limits, and which approvals have a line."""

from __future__ import annotations

import time

import pytest

from fabric_desktop.integrations import audit
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.store import IntegrationStore


@pytest.fixture
def trail(tmp_path):
    return IntegrationAudit(IntegrationStore(tmp_path / "integrations" / "integrations.sqlite"))


def _call(trail, n=1, approval_id="", **kw):
    ctx = CallContext(Caller("", "test", "Probe"))
    ctx.approval_id = approval_id
    return [trail.record(ctx, "ext_cap", "READ_METADATA", {"workspace_id": f"w{i}"}, time.time(), 0.5, **kw)
            for i in range(n)]


def test_seconds_are_rounded_to_milliseconds_and_an_error_is_cut_to_500_characters(trail):
    ctx = CallContext(Caller("", "test", "Probe"))
    line = trail.record(ctx, "ext_cap", "READ_METADATA", {}, time.time(), 1.23456,
                        error=IntegrationError("not_found", "x" * 900))
    assert line["seconds"] == 1.235
    assert line["error"] == "x" * 500


def test_the_store_keeps_fifty_thousand_rows():
    assert audit.KEEP_ROWS == 50_000


def test_recent_returns_the_newest_lines_first(trail):
    lines = _call(trail, 3)
    got = trail.recent()
    assert [g["hash"] for g in got] == [line["hash"] for line in reversed(lines)]


def test_recent_shows_100_lines_by_default(trail):
    _call(trail, 101)
    got = trail.recent()
    assert len(got) == 100
    assert got[-1]["arguments"] == {"workspace_id": "w1"}  # the oldest, w0, is the 101st


def test_recent_asks_for_at_least_one_and_at_most_1000_lines(trail, monkeypatch):
    _call(trail, 2)
    assert len(trail.recent(limit=0)) == 1
    asked = []
    rows = trail.store._rows
    monkeypatch.setattr(trail.store, "_rows", lambda sql, args=(): asked.append(list(args)) or rows(sql, args))
    trail.recent(limit=5000)
    trail.recent(limit=-3)
    assert asked == [[1000], [1]]


def test_approvals_recorded_names_the_approvals_that_have_a_line(trail):
    assert trail.approvals_recorded([]) == set()
    _call(trail, 1, approval_id="ap-1")
    _call(trail, 1, approval_id="ap-2", stage="started")
    assert trail.approvals_recorded(["ap-1", "ap-2", "ap-3"]) == {"ap-1", "ap-2"}
    assert trail.approvals_recorded(["ap-3"]) == set()
