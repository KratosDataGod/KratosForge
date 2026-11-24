"""replaceWhere (spark_replace_where.py): what mutation testing found unchecked. Columns are quoted as the
table spells them, and a check DuckDB can't run names only the first line of its error."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop import spark_replace_where as rw


def test_columns_are_quoted_as_the_table_spells_them():
    assert rw.predicate("Day = 1 AND region = 'EU'", "t", ["day", "Region"]) == "\"day\" = 1 AND \"Region\" = 'EU'"


def test_a_check_that_cant_run_names_the_first_line_of_duckdbs_error():
    with pytest.raises(ValueError) as err:
        rw.check_rows(pa.table({"day": [1]}), '"nope" = 1', "nope = 1", "sales")
    message = str(err.value)
    assert "The check stopped on: Binder Error: Referenced column \"nope\" not found" in message
    assert "LINE 1" not in message and message.endswith("Write the condition as column comparisons, such as region = 'EU'.")
