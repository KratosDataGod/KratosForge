"""SQL best practices: what the uncapped mutation run (batch 224) found unchecked. Reads text only:
nothing runs and nothing is written."""

from __future__ import annotations

import pytest

from fabric_desktop.sqlcompat import practices as p


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def ids(query, **kw):
    return [f["rule_id"] for f in p.check(query, **kw)["findings"]]


def test_each_rule_links_its_docs_section():
    assert p.url("SQ001") == "sql-best-practices.md#sq001"
    finding = p.check("CREATE VIEW dbo.v AS SELECT * FROM dbo.x")["findings"][0]
    assert finding["url"] == "sql-best-practices.md#sq001"


def test_a_query_that_isnt_text_is_refused():
    with pytest.raises(TypeError, match="SQL text"):
        p.check(123)


def test_create_table_as_select_star_is_not_a_view_or_procedure():
    assert ids("CREATE TABLE dbo.t AS SELECT * FROM dbo.x") == []
    assert ids("CREATE TABLE dbo.t AS SELECT a FROM dbo.x ORDER BY a") == []


def test_a_join_at_the_end_with_no_on_is_a_cross_join():
    assert ids("SELECT a FROM dbo.x JOIN dbo.y") == ["SQ006"]


def test_not_in_a_subquery_selecting_an_expression_is_reported_even_if_a_column_is_not_null():
    # b + c is NULL when c is, so filtering b's NULLs out doesn't help
    assert ids("SELECT a FROM dbo.x WHERE a NOT IN (SELECT b + c FROM dbo.y WHERE b IS NOT NULL)") == ["SQ012"]


SECRET = "Server=tcp:x.database.windows.net;Password=Sup3rS3cretValue9"  # pragma: allowlist secret (fake, for the scan)


def _secret_findings(query):
    return [(f["rule_id"], f["line"], f["col"], f["object"], f["statement"]) for f in p.check(query)["findings"]]


def test_a_secret_in_a_statement_points_at_its_line_start_and_statement():
    assert _secret_findings(f"DECLARE @c varchar(100) = '{SECRET}'") == [("SE001", 1, 1, "statement 1, line 1", 1)]


def test_a_secret_outside_any_statement_names_only_its_line():
    assert _secret_findings(f"SELECT 1;\n-- {SECRET}") == [("SE001", 2, 1, "line 2", 0)]


def test_a_bracketed_select_on_its_own_is_not_in_a_select_list():
    assert ids("(SELECT o.a FROM dbo.x AS x)") == []


def cols(query):
    return [(f["rule_id"], f["col"]) for f in p.check(query)["findings"]]


def test_only_a_max_length_column_is_reported():
    assert cols("CREATE TABLE dbo.t (a varchar(10))") == []
    assert cols("CREATE TABLE dbo.t (a varchar(10), b varchar(max))") == [("SQ003", 38)]


def test_only_row_number_renumbers_a_key_and_it_is_reported_at_row_number():
    assert cols("INSERT INTO dbo.k SELECT ROW_NUMBER() OVER (ORDER BY a) AS customer_key FROM dbo.x") == [("DM002", 26)]
    assert cols("INSERT INTO dbo.k SELECT RANK() OVER (ORDER BY a) AS customer_key FROM dbo.x") == []


def test_insert_select_top_n_star_is_reported():
    assert ids("INSERT INTO dbo.k SELECT TOP (5) * FROM dbo.x") == ["SQ010", "SQ008"]


def test_a_select_star_after_insert_values_in_one_block_doesnt_feed_the_insert():
    assert ids("BEGIN INSERT INTO dbo.k VALUES (1) SELECT * FROM dbo.x END") == []


def test_the_first_of_two_joins_with_one_on_is_a_cross_join():
    assert cols("SELECT a FROM dbo.x JOIN dbo.y JOIN dbo.z ON y.k = z.k") == [("SQ006", 21)]


def test_update_alias_set_with_a_subquery_then_from_reads_the_alias():
    assert ids("UPDATE t SET a = (SELECT 1) FROM dbo.t AS t") == []


def test_select_into_after_a_subquery_in_the_select_list_is_reported():
    assert cols("SELECT (SELECT 1 FROM dbo.y) AS c INTO dbo.t FROM dbo.x") == [("SQ004", 1)]


def test_a_subquery_inside_a_function_in_where_is_not_in_the_select_list():
    assert ids("SELECT a FROM dbo.x AS o WHERE a = COALESCE((SELECT o.b FROM dbo.y), 0)") == []


@pytest.mark.parametrize(("query", "found"), [
    ("SELECT a, (SELECT o.b FROM dbo.y", [("SQ015", 12)]),
    ("CREATE TABLE dbo.t (b varchar(max)", [("SQ003", 23)]),
    ("SELECT a FROM dbo.x WHERE a NOT IN (SELECT", [("SQ012", 29)]),
])
def test_a_statement_that_ends_early_is_still_read(query, found):
    assert cols(query) == found


def test_a_wrapped_column_in_where_after_case_end_is_reported():
    assert cols("UPDATE dbo.t SET a = CASE WHEN b = 1 THEN 2 END WHERE YEAR(d) = 2026") == [("SQ002", 55)]


def test_a_wrapped_column_not_followed_by_a_comparison_is_not_reported():
    assert ids("SELECT a FROM dbo.x WHERE a = UPPER(b) AND c = 1") == []


def test_a_full_reload_names_the_truncate_statement():
    found = p.check("SELECT 1;\nTRUNCATE TABLE dbo.t;\nINSERT INTO dbo.t SELECT a FROM dbo.s")["findings"]
    assert [(f["rule_id"], f["object"], f["statement"]) for f in found] == [("WH001", "statement 2, line 2", 2)]
