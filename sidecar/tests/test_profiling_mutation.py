"""Profile (profiling.py): the TOML a suggestion carries, rules already in place, the statistics per column,
the accepted-values and case limits, far outliers, negatives, keys and nulls, pinned at their edges."""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from fabric_desktop import profiling
from fabric_desktop.profiling import profile, to_toml


def col(result, name):
    return next(c for c in result["columns"] if c["name"] == name)


def ids(result):
    return {s["id"] for s in result["suggestions"]}


def suggestion(result, sid):
    return next(s for s in result["suggestions"] if s["id"] == sid)


def test_toml_writes_each_kind_of_value():
    rule = {"a": True, "b": False, "c": 3, "d": 1.5, "e": {"min": 0}, "f": ["x", 2], "g": "it's"}
    assert to_toml("check", rule) == (
        '[[check]]\na = true\nb = false\nc = 3\nd = 1.5\ne = { min = 0 }\nf = ["x", 2]\ng = "it\'s"\n'
    )


def test_the_limits_are_twelve_accepted_values_and_five_top_values():
    assert profiling.MAX_ACCEPTED == 12
    assert profiling.TOP_VALUES == 5


def test_top_values_are_the_five_most_common_with_their_counts():
    values = ["a"] * 7 + ["b"] * 6 + ["c"] * 5 + ["d"] * 4 + ["e"] * 3 + ["f"] * 2 + ["g"]
    stat = col(profile(pl.DataFrame({"letter": values})), "letter")
    assert stat["top"] == [{"value": "a", "count": 7}, {"value": "b", "count": 6}, {"value": "c", "count": 5},
                           {"value": "d", "count": 4}, {"value": "e", "count": 3}]
    assert stat["distinct"] == 7


def test_an_all_null_column_has_no_distinct_values_or_top():
    stat = col(profile(pl.DataFrame({"x": pl.Series([None, None], dtype=pl.Int64)})), "x")
    assert stat["distinct"] == 0 and stat["top"] == [] and stat["null_pct"] == 100.0
    assert stat["issues"] == []  # every value null: not the "a few nulls" note


def test_the_null_share_is_a_percent_to_one_place_and_an_empty_table_has_none():
    assert col(profile(pl.DataFrame({"a": [1, None, 3]})), "a")["null_pct"] == 33.3
    empty = profile(pl.DataFrame({"a": pl.Series([], dtype=pl.Int64)}))
    assert empty["rows"] == 0 and col(empty, "a")["null_pct"] == 0.0


def test_a_clean_suggestion_has_no_on_fail_and_a_check_warns_by_default():
    result = profile(pl.DataFrame({"name": [" a", "b", "c"], "amount": [1, 2, 3]}))
    assert suggestion(result, "clean:name:trim")["rule"] == {"column": "name", "trim": True}
    assert suggestion(result, "check:amount:range")["rule"] == {"column": "amount", "range": {"min": 0},
                                                                "on_fail": "warn"}


def test_a_rule_already_in_place_is_not_suggested_again_and_the_issue_says_so():
    df = pl.DataFrame({"name": [" a", "b", "c"]})
    fresh = profile(df)
    assert "clean:name:trim" in ids(fresh)
    assert col(fresh, "name")["issues"] == ["1 value with spaces at the start or end"]
    handled = profile(df, existing=[{"kind": "clean", "column": "name", "trim": True}])
    assert "clean:name:trim" not in ids(handled)
    assert col(handled, "name")["issues"] == [
        "1 value with spaces at the start or end (a [[clean]] rule handles it)"]


def test_case_is_lower_when_exactly_half_the_values_are_lower_case():
    result = profile(pl.DataFrame({"city": ["oslo", "OSLO", "rome", "ROME"]}))
    assert suggestion(result, "clean:city:case")["rule"] == {"column": "city", "case": "lower"}
    assert col(result, "city")["issues"] == ["the same value in different case (2 extra spellings)"]


def _accepted(folded, rows):
    values = [f"v{i:02d}" for i in range(folded)]
    return "check:kind:accepted_values" in ids(profile(pl.DataFrame({"kind": (values * rows)[:rows]})))


def test_accepted_values_need_two_to_twelve_values_and_three_rows_each():
    assert not _accepted(1, 30)
    assert _accepted(2, 6)
    assert not _accepted(2, 5)
    assert _accepted(12, 36)
    assert not _accepted(12, 35)
    assert not _accepted(13, 60)


def test_far_outliers_are_beyond_three_spreads_of_the_middle_half():
    values = [10.0] * 10 + [20.0] * 10 + [50.0, -20.0, 51.0]  # quartiles 10 and 20: fences at -20 and 50
    stat = col(profile(pl.DataFrame({"amount": values})), "amount")
    assert stat["issues"] == ["1 far outlier", "1 negative value"]


def test_no_far_outliers_when_the_middle_half_has_no_spread():
    stat = col(profile(pl.DataFrame({"amount": [5.0] * 7 + [100.0]})), "amount")
    assert "far outlier" not in " ".join(stat["issues"])


def test_a_key_like_column_has_no_outlier_note_and_no_range_suggestion():
    result = profile(pl.DataFrame({"store_id": [10.0] * 10 + [20.0] * 10 + [500.0]}))
    assert col(result, "store_id")["issues"] == []
    assert "check:store_id:range" not in ids(result)


def test_a_range_needs_three_values_none_below_zero():
    assert "check:n:range" in ids(profile(pl.DataFrame({"n": [0, 1, 2]})))
    assert "check:n:range" not in ids(profile(pl.DataFrame({"n": [1, 2]})))


def test_a_few_negatives_are_noted_up_to_one_in_a_hundred():
    one = col(profile(pl.DataFrame({"n": [-1, 2, 3]})), "n")
    assert one["issues"] == ["1 negative value"]
    assert col(profile(pl.DataFrame({"n": [-1, -2] + [5] * 148})), "n")["issues"] == []
    assert col(profile(pl.DataFrame({"n": [-1, -2] + [5] * 198})), "n")["issues"] == ["2 negative values"]


def test_date_columns_show_their_first_and_last_values():
    result = profile(pl.DataFrame({"d": [date(2026, 1, 2), date(2026, 3, 4)],
                                   "t": [datetime(2026, 1, 2, 3, 4), datetime(2026, 5, 6, 7, 8)]}))
    assert (col(result, "d")["min"], col(result, "d")["max"]) == ("2026-01-02", "2026-03-04")
    assert (col(result, "t")["min"], col(result, "t")["max"]) == ("2026-01-02 03:04:00", "2026-05-06 07:08:00")
    empty = col(profile(pl.DataFrame({"d": pl.Series([None], dtype=pl.Date)})), "d")
    assert "min" not in empty


def test_a_key_must_not_be_null_and_be_unique_and_stops_the_run():
    result = profile(pl.DataFrame({"code": ["a", "b"]}), keys=["code"])
    assert suggestion(result, "check:code:not_null")["rule"] == {"column": "code", "not_null": True,
                                                                 "on_fail": "stop"}
    assert suggestion(result, "check:code:unique")["rule"] == {"column": "code", "unique": True,
                                                               "on_fail": "stop"}


def test_an_id_column_with_no_keys_named_gets_warning_checks():
    result = profile(pl.DataFrame({"order_id": [1, 2, 3]}))
    assert suggestion(result, "check:order_id:not_null")["rule"]["on_fail"] == "warn"
    assert suggestion(result, "check:order_id:unique")["rule"] == {"column": "order_id", "unique": True,
                                                                   "on_fail": "warn"}


def test_unique_is_not_suggested_for_one_row_another_key_or_a_non_id_name():
    assert "check:k:unique" not in ids(profile(pl.DataFrame({"k": ["a"]}), keys=["k"]))
    assert "check:order_id:unique" not in ids(profile(pl.DataFrame({"k": [1, 2], "order_id": [1, 2]}), keys=["k"]))
    assert "check:store_key:unique" not in ids(profile(pl.DataFrame({"store_key": [1, 2]})))


def test_repeated_values_are_noted_only_for_a_named_key():
    result = profile(pl.DataFrame({"k": [1, 1, 2], "order_id": [1, 1, 2]}), keys=["k"])
    assert col(result, "k")["issues"] == ["2 rows sharing a key value"]
    assert col(result, "order_id")["issues"] == []


def test_a_few_nulls_are_noted_up_to_one_in_a_hundred():
    one = [None] + list(range(99))
    assert col(profile(pl.DataFrame({"n": one})), "n")["issues"] == ["1 null"]
    two = [None, None] + list(range(98))
    assert col(profile(pl.DataFrame({"n": two})), "n")["issues"] == []
