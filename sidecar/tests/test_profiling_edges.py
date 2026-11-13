"""Profile's edges the main tests don't reach: numbers kept as text, far outliers, repeated keys,
a few nulls in an ordinary column, and adding no blocks to an entity file."""

import polars as pl

from fabric_desktop import profiling as pr


def _col(out, name):
    return next(c for c in out["columns"] if c["name"] == name)


def test_numbers_stored_as_text_are_reported_with_a_typing_hint():
    df = pl.DataFrame({"amount": ["1,200", "3", "4.5", "7"]})
    out = pr.profile(df)
    assert "numbers stored as text" in _col(out, "amount")["issues"]
    hint = next(s for s in out["suggestions"] if s["id"] == "info:amount:numeric")
    assert hint["kind"] == "info" and hint["rule"] == {} and hint["toml"] == ""


def test_a_far_outlier_is_counted_on_a_measure():
    df = pl.DataFrame({"qty": [10, 11, 12, 10, 11, 12, 10, 11, 12, 10_000]})
    assert "1 far outlier" in _col(pr.profile(df), "qty")["issues"]


def test_rows_that_share_a_key_are_reported_on_the_column_and_the_table():
    df = pl.DataFrame({"order_id": [1, 1, 2, 3], "status": ["a", "b", "c", "d"]})
    out = pr.profile(df, keys=["order_id"])
    assert "2 rows sharing a key value" in _col(out, "order_id")["issues"]
    dupes = next(s for s in out["suggestions"] if s["id"] == "info:keys:dupes")
    assert dupes["column"] == "order_id" and "1 rows repeat a key" in dupes["why"]


def test_a_few_nulls_in_an_ordinary_column_are_counted():
    df = pl.DataFrame({"note": [f"n{i}" for i in range(199)] + [None]})
    assert _col(pr.profile(df), "note")["issues"] == ["1 null"]


def test_no_accepted_blocks_leave_the_entity_file_as_it_is():
    assert pr.append_blocks("name = 'x'\n", []) == "name = 'x'\n"
