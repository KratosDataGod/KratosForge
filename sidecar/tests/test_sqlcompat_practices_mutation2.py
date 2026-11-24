"""SQL best practices at more of their exact edges (sqlcompat/practices.py's boundary mutants, re-scored
on main after batch 39): a statement that ends right after a CAST's ROW_NUMBER(, a TRY_CONVERT's or a
correlated subquery's last token, and an INSERT column list shorter than the select list. Each must be read
without an error, with the findings these give. Reads text only: nothing runs and nothing is written."""

from __future__ import annotations

import pytest

from fabric_desktop.sqlcompat import practices as p


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def ids(query):
    return [f["rule_id"] for f in p.check(query)["findings"]]


@pytest.mark.parametrize("query", [
    "INSERT INTO dbo.d (customer_key, id) SELECT CAST(ROW_NUMBER(",
    "INSERT INTO dbo.d (customer_key, id) SELECT CAST(ROW_NUMBER()",
    "INSERT INTO dbo.d (customer_key, id) SELECT CAST(ROW_NUMBER() OVER (",
])
def test_a_cast_of_row_number_cut_short_is_read_without_an_error(query):
    assert "DM002" not in ids(query)


@pytest.mark.parametrize("query", [
    "INSERT INTO dbo.d (customer_key, id) SELECT TRY_CONVERT(BIGINT, ROW_NUMBER(",
    "INSERT INTO dbo.d (customer_key, id) SELECT TRY_CONVERT(BIGINT, ROW_NUMBER()",
    "INSERT INTO dbo.d (customer_key, id) SELECT TRY_CONVERT(BIGINT, ROW_NUMBER() OVER (",
])
def test_a_try_convert_of_row_number_cut_short_still_numbers_the_key(query):
    assert ids(query) == ["DM002"]


@pytest.mark.parametrize("query", [
    "SELECT a, (SELECT dbo.fn(",
    "SELECT o.id, (SELECT MAX(p.amount) FROM dbo.payments p",
])
def test_a_correlated_subquery_cut_at_its_last_token_is_still_found(query):
    assert ids(query) == ["SQ015"]


def test_row_number_past_the_insert_column_list_is_read_by_its_alias():
    query = "INSERT INTO dbo.d (id) SELECT name, ROW_NUMBER() OVER (ORDER BY name) AS row_key FROM dbo.s"
    assert ids(query) == ["DM002"]
