"""Mutation-killing tests for wrangler.py (batch 43): the row, step and split limits, casts and the code's comments."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop import wrangler


def numbers(n):
    return pa.table({"id": pa.array(np.arange(n, dtype=np.int64))})


def test_a_million_rows_preview_whole_and_one_more_is_sampled():
    whole = wrangler.run(numbers(1_000_000), None, [])
    assert whole["sampled"] is False and whole["rows_before"] == 1_000_000
    over = wrangler.run(numbers(1_000_001), None, [])
    assert over["sampled"] is True and over["rows_before"] == 1_000_000


def test_a_sampled_preview_starts_at_the_first_row(monkeypatch):
    monkeypatch.setattr(wrangler, "WRANGLE_ROWS", 3)
    out = wrangler.run(numbers(5), None, [])
    assert [r[0] for r in out["rows"]] == [0, 1, 2]


def test_the_preview_shows_a_hundred_rows():
    out = wrangler.run(numbers(101), None, [])
    assert len(out["rows"]) == 100 and out["rows_after"] == 101


def test_fifty_steps_run_and_fifty_one_are_refused():
    assert wrangler.run(numbers(2), None, [{"op": "dedupe"}] * 50)["error"] is None
    with pytest.raises(ValueError, match="At most 50 steps"):
        wrangler.run(numbers(2), None, [{"op": "dedupe"}] * 51)


@pytest.mark.parametrize("parts", [2, 20])
def test_text_splits_into_two_to_twenty_parts(parts):
    df = pl.DataFrame({"t": ["a,b"]})
    _, names = wrangler.code_for(df, {"op": "split", "column": "t", "value": ",", "parts": parts})
    assert len(names) == parts


@pytest.mark.parametrize("parts", [1, 21])
def test_a_split_outside_two_to_twenty_parts_is_refused(parts):
    df = pl.DataFrame({"t": ["a,b"]})
    with pytest.raises(ValueError, match="Split into 2 to 20 parts"):
        wrangler.code_for(df, {"op": "split", "column": "t", "value": ",", "parts": parts})


def test_a_timestamp_column_becomes_a_date_by_a_cast_not_by_parsing_text():
    df = pl.DataFrame({"at": [dt.datetime(2026, 1, 2, 3, 4)]})
    line, _ = wrangler.code_for(df, {"op": "cast", "columns": ["at"], "to": "date"})
    assert line == "df = df.with_columns(pl.col('at').cast(pl.Date, strict=False))"


def test_each_step_is_commented_in_the_code_with_its_description():
    out = wrangler.run(pa.table({"a": [1, 1]}), None, [{"op": "dedupe", "columns": ["a"]}])
    assert "    # Remove duplicate rows: 'a'\n    df = df.unique(subset=['a'], keep='first', maintain_order=True)\n" \
        in out["code"]
