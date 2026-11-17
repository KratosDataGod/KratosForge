"""usage/metrics_app.py paths that mutation testing (batch 59, docs/quality/mutation-testing.md) showed no
test pinned: each reason a row is skipped, read one row at a time; the edges of a zero CU value and an
operation that ends as it starts; the period an operation and an item total cover; how each kind of row
is keyed; and the optional fields left empty."""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest

from fabric_desktop.usage import metrics_app as ma
from fabric_desktop.usage import providers

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
P0 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
P1 = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
START = "2026-10-16T14:00:00Z"
END = "2026-10-16T14:05:00Z"


def _ctx(period=(None, None)):
    return providers.Context(file_name="f.csv", imported_at=NOW, options=providers.Options(), tz=dt.UTC,
                             decimal=".", date_order="mdy", period=period)


def _op(**cells):
    return ma.MetricsApp().row({"workspace": "W", "item": "i", "cu": "5", "start": START} | cells, ma.OPERATIONS, _ctx())


def _expected_key(*parts):
    return "metrics_app:" + hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:24]


@pytest.mark.parametrize("cells, reason", [
    ({"workspace": "", "item": ""}, "No workspace or item"),
    ({"start": ""}, "No start time"),
    ({"start": "soon"}, "The start time can't be read"),
    ({"end": "later"}, "The end time can't be read"),
    ({"end": "2026-10-16T13:59:59Z"}, "Ends before it starts"),
    ({"cu": "-0.5"}, "Negative CU value"),
])
def test_each_skipped_row_says_why(cells, reason):
    assert _op(**cells) == reason


@pytest.mark.parametrize("cells", [{"workspace": ""}, {"item": ""}])
def test_a_workspace_or_an_item_alone_is_enough(cells):
    assert isinstance(_op(**cells), dict)


@pytest.mark.parametrize("cu", ["0", "0.5"])
def test_zero_and_fractions_of_a_cu_second_are_kept(cu):
    assert _op(cu=cu)["cu"]["value"] == float(cu)


def test_an_operation_may_end_as_it_starts():
    row = _op(end=START)
    assert row["start"] == row["end"] == "2026-10-16T14:00:00+00:00"


def test_an_operation_covers_its_start_to_its_end_or_just_its_start():
    assert _op(end=END)["cu"]["period"] == {"start": "2026-10-16T14:00:00+00:00", "end": "2026-10-16T14:05:00+00:00"}
    assert _op()["cu"]["period"] == {"start": "2026-10-16T14:00:00+00:00", "end": "2026-10-16T14:00:00+00:00"}


@pytest.mark.parametrize("period", [(None, None), (P0, None), (None, P1)])
def test_an_item_total_needs_both_ends_of_its_period(period):
    got = ma.MetricsApp().row({"workspace": "W", "item": "i", "cu": "5"}, ma.ITEMS, _ctx(period))
    assert got == "Enter the period this file covers"


def test_an_item_total_covers_the_period_given_and_is_keyed_by_it():
    row = ma.MetricsApp().row({"workspace": "W", "item": "i", "item_kind": "Notebook", "cu": "5"}, ma.ITEMS, _ctx((P0, P1)))
    assert row["cu"]["period"] == {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-15T00:00:00+00:00"}
    assert row["cu"]["metric"] == "fabric_item_cu" and row["start"] is None and row["end"] is None
    assert row["key"] == _expected_key("items", "W", "i", "Notebook", "2026-10-01T00:00:00+00:00",
                                       "2026-10-15T00:00:00+00:00", 5.0)
    assert len(row["key"]) == len("metrics_app:") + 24


def test_an_operation_is_keyed_by_its_id_or_else_by_what_it_ran():
    assert _op(operation_id=" OP-1 ")["key"] == "metrics_app:op:op-1"
    row = _op(item_kind="Notebook", operation="Run", end=END)
    assert row["key"] == _expected_key("W", "i", "Notebook", "Run", "2026-10-16T14:00:00+00:00",
                                       "2026-10-16T14:05:00+00:00", 5.0)


def test_optional_fields_are_kept_when_given_and_none_when_empty():
    full = _op(item_kind="Notebook", operation="Run", operation_id="op-1", status="Succeeded")
    assert (full["item_kind"], full["operation"], full["operation_id"], full["status"]) == (
        "Notebook", "Run", "op-1", "Succeeded")
    empty = _op(item_kind="", operation="", operation_id="", status="")
    assert (empty["item_kind"], empty["operation"], empty["operation_id"], empty["status"]) == (None, None, None, None)
