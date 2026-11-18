"""Mutation batch 90: verify.py, comparing two outputs of the same table. Pins what the earlier tests
let through: the examples a comparison lists (which side, how many times, which columns and keys),
the largest difference it reports per column, the digits whole rows are rounded to, the counts when
no column is shared, and the rules it refuses."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import pyarrow as pa
import pytest

from fabric_desktop import verify


@pytest.fixture(autouse=True)
def _own_home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


# ---- rules


def test_an_unknown_timestamp_unit_is_refused():
    with pytest.raises(ValueError, match="^timestamps is us, ms or s$"):
        verify.rules({"timestamps": "ns"})


def test_zero_examples_and_the_default_row_limit_are_accepted():
    r = verify.rules({"examples": 0})
    assert r["examples"] == 0
    assert r["limit_rows"] == 5_000_000


def test_a_key_that_is_not_in_both_outputs_is_refused_by_name():
    t = pa.table({"k": [1], "v": [1]})
    with pytest.raises(ValueError, match=r"^Key column zz isn't in both outputs \(or is ignored\)\.$"):
        verify.compare(t, t, {"keys": ["zz"]})


# ---- digits for whole rows


def test_significant_digits_are_seventeen_with_no_tolerance_and_between_one_and_fifteen_otherwise():
    assert verify._sig_digits(0) == 17
    assert verify._sig_digits(0.5) == 1
    assert verify._sig_digits(10) == 1
    assert verify._sig_digits(1e-20) == 15
    assert verify._sig_digits(1e-3) == 3


def test_a_number_is_not_compared_as_text_unless_asked():
    r = verify.rules({"rel": 0})
    assert verify._norm("x", "number", r) == "CAST(x AS DOUBLE)"
    assert verify._norm("x", "number", r, exact=True) == verify._exact("x")


def test_whole_rows_round_numbers_to_the_digits_the_tolerance_leaves():
    a = pa.table({"n": [1.2344]})
    b = pa.table({"n": [1.2346]})
    out = verify.compare(a, b, {"rel": 1e-3})  # three significant digits: 1.23 on both sides
    assert out["result"] == "match"
    assert "rounded to 3 significant digits" in out["note"]
    assert verify.compare(a, b, {"rel": 1e-4})["result"] == "differs"  # 1.234 vs 1.235


def test_with_no_tolerance_the_note_says_seventeen_digits():
    t = pa.table({"n": [1.5]})
    assert "rounded to 17 significant digits" in verify.compare(t, t, {"rel": 0})["note"]


# ---- whole rows


def test_whole_row_examples_say_which_side_has_the_row_and_how_many_more_times():
    a = pa.table({"Amount": [1, 1, 1, 5]})
    b = pa.table({"amount": [1, 2, 5]})
    out = verify.compare(a, b)
    assert out["method"] == "whole rows"
    assert out["counts"] == {"only_pc": 2, "only_fabric": 1}
    assert out["examples"] == [{"row": {"Amount": 1}, "status": "only_pc", "times": 2},
                               {"row": {"Amount": 2}, "status": "only_fabric", "times": 1}]


def test_the_side_with_more_copies_of_a_row_is_named_whatever_the_row_holds():
    out = verify.compare(pa.table({"n": [100, 100, 100]}), pa.table({"n": [100]}))
    assert out["examples"] == [{"row": {"n": 100}, "status": "only_pc", "times": 2}]


@pytest.mark.parametrize("a, b", [([1, 2, 3], [1, 2]), ([1, 2], [1, 2, 3])])
def test_rows_on_one_side_only_make_the_tables_differ(a, b):
    assert verify.compare(pa.table({"n": a}), pa.table({"n": b}))["result"] == "differs"
    assert verify.compare(pa.table({"n": a}), pa.table({"n": a}))["result"] == "match"


def test_matching_rows_still_differ_when_a_column_is_only_on_one_side():
    a = pa.table({"k": [1, 2], "extra": ["x", "y"]})
    b = pa.table({"k": [1, 2]})
    out = verify.compare(a, b)
    assert out["counts"] == {"only_pc": 0, "only_fabric": 0}
    assert out["columns"]["only_pc"] == ["extra"]
    assert out["result"] == "differs"


@pytest.mark.parametrize("na, nb, only_pc, only_fabric", [(3, 1, 2, 0), (1, 3, 0, 2), (2, 2, 0, 0)])
def test_with_no_shared_column_only_the_row_counts_are_compared(na, nb, only_pc, only_fabric):
    out = verify.compare(pa.table({"x": list(range(na))}), pa.table({"y": list(range(nb))}))
    assert {k: out[k] for k in ("level", "method", "counts", "examples", "offset")} == {
        "level": "complete", "method": "whole rows", "counts": {"only_pc": only_pc, "only_fabric": only_fabric},
        "examples": [], "offset": 0}
    assert out["result"] == "differs"  # the columns don't line up


# ---- keyed


def test_keyed_examples_name_the_side_a_missing_row_is_on():
    a = pa.table({"k": [1, 2], "v": [10, 20]})
    b = pa.table({"k": [1, 3], "v": [10, 30]})
    out = verify.compare(a, b, {"keys": ["k"]})
    assert out["counts"] == {"only_pc": 1, "only_fabric": 1, "changed": 0}
    assert out["examples"] == [{"key": {"k": 2}, "status": "only_pc"}, {"key": {"k": 3}, "status": "only_fabric"}]


def test_a_changed_row_lists_only_its_changed_columns_with_both_values():
    a = pa.table({"k1": [1, 1], "k2": ["a", "b"], "s": ["x", "y"], "n": [1.0, 2.0], "t": ["p", "q"]})
    b = pa.table({"k1": [1, 1], "k2": ["a", "b"], "s": ["x", "y"], "n": [1.0, 2.5], "t": ["p", "Q"]})
    out = verify.compare(a, b, {"keys": ["k1", "k2"], "rel": 0})
    assert out["counts"] == {"only_pc": 0, "only_fabric": 0, "changed": 1}
    assert out["examples"] == [{"key": {"k1": 1, "k2": "b"}, "status": "changed",
                                "columns": {"n": {"pc": 2.0, "fabric": 2.5}, "t": {"pc": "q", "fabric": "Q"}}}]


def test_the_largest_difference_is_reported_only_for_numbers_that_changed():
    a = pa.table({"k": [1, 2], "n": [10.0, 5.0], "same": [1.0, 1.0], "s": ["x", "y"]})
    b = pa.table({"k": [1, 2], "n": [12.0, 5.0], "same": [1.0, 1.0], "s": ["x", "z"]})
    cols = verify.compare(a, b, {"keys": ["k"], "rel": 0})["column_diffs"]
    assert cols["n"] == {"rows": 1, "max_abs": 2.0, "max_rel": pytest.approx(2 / 12)}
    assert cols["same"] == {"rows": 0}
    assert cols["s"] == {"rows": 1}


def test_an_exact_column_that_differs_reports_its_largest_difference():
    a = pa.table({"k": [1], "d": pa.array([Decimal("1.50")], pa.decimal128(10, 2))})
    b = pa.table({"k": [1], "d": pa.array([Decimal("2.75")], pa.decimal128(10, 2))})
    cols = verify.compare(a, b, {"keys": ["k"], "rel": 0})["column_diffs"]
    assert cols["d"] == {"rows": 1, "max_abs": 1.25, "max_rel": pytest.approx(1.25 / 2.75)}


def test_timestamps_are_compared_to_the_unit_asked_for():
    t0 = dt.datetime(2026, 1, 1, 12, 0, 0, 100)
    a = pa.table({"k": [1], "ts": pa.array([t0], pa.timestamp("us"))})
    b = pa.table({"k": [1], "ts": pa.array([t0 + dt.timedelta(microseconds=300)], pa.timestamp("us"))})
    assert verify.compare(a, b, {"keys": ["k"], "timestamps": "ms"})["result"] == "match"
    assert verify.compare(a, b, {"keys": ["k"]})["result"] == "differs"


# ---- values in a result


def test_values_are_given_back_as_json_can_hold_them():
    assert verify._json(None) is None
    assert verify._json(True) is True
    assert verify._json(3) == 3 and verify._json("x") == "x"
    assert verify._json(1.5) == 1.5
    assert verify._json(math.inf) == "inf" and verify._json(math.nan) == "nan"
    assert verify._json(Decimal("1.50")) == "1.50"
    assert verify._json(dt.date(2026, 1, 2)) == "2026-01-02"


def test_a_largest_difference_that_is_not_a_finite_number_is_left_out():
    assert verify._finite(None) is None
    assert verify._finite(math.inf) is None and verify._finite(math.nan) is None
    assert verify._finite(2) == 2.0 and isinstance(verify._finite(2), float)
    assert verify._finite(0.25) == 0.25
