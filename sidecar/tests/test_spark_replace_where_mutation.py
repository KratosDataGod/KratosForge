"""Mutation batch 265: spark_replace_where.py, first score (docs/quality/mutation-testing.md)."""

import pyarrow as pa
import pytest

from fabric_desktop import spark_replace_where


def test_a_column_is_quoted_as_the_table_spells_it():
    assert spark_replace_where.predicate("region = 'EU'", "t", ["Region"]) == "\"Region\" = 'EU'"
    assert spark_replace_where.predicate("`my col` = 1", "t", ["My Col"]) == '"My Col" = 1'


def test_a_check_that_cant_run_says_the_first_line_it_stopped_on():
    with pytest.raises(ValueError) as caught:
        spark_replace_where.check_rows(pa.table({"region": ["EU"]}), "nosuchcol = 1", "nosuchcol = 1", "t")
    assert ('The check stopped on: Binder Error: Referenced column "nosuchcol" not found in FROM clause!. '
            "Nothing was written to t.") in str(caught.value)
