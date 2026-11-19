"""Local T-SQL compares text with an integer or a float as T-SQL does (WR-254).

T-SQL's data type precedence ranks a number above text, so the text side is converted to the number's type, as a
CAST would convert it (https://learn.microsoft.com/en-us/sql/t-sql/data-types/data-type-precedence-transact-sql):
'' is 0 for an integer or a float, ' 1 ' is 1, and text that isn't a whole number is a conversion error for an
integer. DuckDB stopped on '' and rounded '1.5' to 2, so ``2 = '1.5'`` was true where T-SQL stops.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("tsql-text-compare") / "app" / "local-data")
    return local.engine(local.create("Compare")["id"])


def answer(engine, condition: str, source: str = "") -> int:
    rows = engine.execute(f"SELECT CASE WHEN {condition} THEN 1 ELSE 0 END AS v {source}", dialect="tsql")["rows"]
    return rows[0][0]


@pytest.mark.parametrize(("condition", "source", "expected"), [
    ("0 = ''", "", 1),
    ("1 = ' 1 '", "", 1),
    ("CAST(0 AS FLOAT) = ''", "", 1),
    ("CAST(1 AS FLOAT) = '1e0'", "", 1),
    ("1 IN ('1', '2')", "", 1),
    ("'2' IN (1, 2)", "", 1),
    ("'2' BETWEEN 1 AND 3", "", 1),
    ("x = 0", "FROM (VALUES ('')) AS t(x)", 1),
    ("x > 9", "FROM (VALUES ('10')) AS t(x)", 1),
    ("'a ' = 'a'", "", 1),  # text with text keeps T-SQL's padding rule
    ("'10' < '9'", "", 1),
])
def test_text_compared_with_a_number_is_converted_as_in_tsql(engine, condition, source, expected):
    assert answer(engine, condition, source) == expected


@pytest.mark.parametrize(("condition", "source"), [
    ("2 = '1.5'", ""),
    ("100 = '1e2'", ""),
    ("'x' = 1", ""),
    ("2 IN ('1.5')", ""),
    ("'1.5' BETWEEN 1 AND 3", ""),
    ("CAST(2 AS BIGINT) = '1.5'", ""),
    ("x = 2", "FROM (VALUES ('1.5')) AS t(x)"),
])
def test_text_that_is_not_a_whole_number_stops_as_in_tsql(engine, condition, source):
    # T-SQL: "Conversion failed when converting the varchar value '1.5' to data type int."
    with pytest.raises(Exception, match="Conversion failed when converting"):
        answer(engine, condition, source)
