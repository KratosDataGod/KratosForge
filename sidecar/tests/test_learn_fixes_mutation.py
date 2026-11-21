"""GD-02 Try the fix (learn/fixes.py): where a finding points, the end of a statement, names with a
qualifier, tables after FROM, the SQ005 text, and Compare for views, CREATE TABLE and a closing
semicolon. Findings are given by line and column, as the page sends them."""

from __future__ import annotations

from fabric_desktop.learn import fixes

CATALOG = {"tables": [{"schema": "dbo", "name": "sales"}, {"schema": "dbo", "name": "returns"},
                      {"schema": "dbo", "name": "order details"}],
           "columns": {"dbo.sales": [{"name": "order_date", "type": "date"},
                                     {"name": "shipped", "type": "datetime2(6)"}],
                       "dbo.returns": [{"name": "order_date", "type": "varchar(10)"}]}}


def at(sql: str, word: str, rule: str = "SQ002", nth: int = 0) -> dict:
    """A finding on the nth place ``word`` starts in ``sql``."""
    pos = -1
    for _ in range(nth + 1):
        pos = sql.index(word, pos + 1)
    line = sql.count("\n", 0, pos) + 1
    col = pos - (sql.rfind("\n", 0, pos) + 1) + 1
    return {"rule_id": rule, "line": line, "col": col}


def propose(sql: str, *findings: dict) -> list[dict]:
    return fixes.propose(sql, "warehouse", list(findings), CATALOG)["fixes"]


def test_a_finding_on_line_one_gets_its_fix_and_line_zero_none():
    sql = "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026"
    finding = at(sql, "YEAR")
    assert finding["line"] == 1
    assert [f["text"] for f in propose(sql, finding)] == [
        "(order_date >= '2026-01-01' AND order_date < '2027-01-01')"]
    assert propose(sql, finding | {"line": 0}) == []


def test_year_at_the_end_of_a_statement_is_left_alone():
    for sql in ("SELECT 1 FROM dbo.sales WHERE YEAR",
                "SELECT 1 FROM dbo.sales WHERE YEAR(order_date)",
                "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) ="):
        assert propose(sql, at(sql, "YEAR")) == []


def test_a_year_followed_by_one_more_operator_is_left_alone():
    sql = "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026 +"
    assert propose(sql, at(sql, "YEAR")) == []


def test_a_year_followed_by_and_is_fixed():
    sql = "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026 AND shipped IS NULL"
    assert [f["text"] for f in propose(sql, at(sql, "YEAR"))] == [
        "(order_date >= '2026-01-01' AND order_date < '2027-01-01')"]


def test_why_safe_names_the_column_not_its_qualifier():
    sql = "SELECT 1 FROM dbo.sales s WHERE YEAR(s.shipped) = 1999"
    (fix,) = propose(sql, at(sql, "YEAR"))
    assert fix["why_safe"] == "shipped is a datetime2(6) column, so the range returns exactly the same rows."


def test_a_column_named_with_three_parts_is_left_alone():
    sql = "SELECT 1 FROM dbo.sales WHERE YEAR(x.y.order_date) = 2026"
    assert propose(sql, at(sql, "YEAR")) == []


def test_from_as_the_last_word_is_not_read_as_a_table():
    sql = "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026 UNION SELECT 1 FROM"
    assert [f["text"] for f in propose(sql, at(sql, "YEAR"))] == [
        "(order_date >= '2026-01-01' AND order_date < '2027-01-01')"]


def test_a_table_at_the_very_end_of_the_statement_is_read():
    sql = "SELECT (YEAR(order_date) = 2026) FROM dbo.sales"
    assert [f["text"] for f in propose(sql, at(sql, "YEAR"))] == [
        "(order_date >= '2026-01-01' AND order_date < '2027-01-01')"]


def test_two_tables_after_one_from_are_left_alone_even_with_aliases():
    sql = "SELECT 1 FROM dbo.sales s, dbo.returns r WHERE YEAR(s.order_date) = 2026"
    assert propose(sql, at(sql, "YEAR")) == []


def test_sq005_puts_dbo_before_the_name_and_says_why():
    sql = "SELECT 1 FROM sales"
    (fix,) = propose(sql, at(sql, "sales", "SQ005"))
    assert fix["text"] == "dbo.sales"
    assert fix["why_safe"] == "sales is only in the dbo schema, the default one, so it names the same table."


def test_sq005_keeps_a_bracketed_name_as_written():
    sql = "SELECT 1 FROM [order details]"
    (fix,) = propose(sql, at(sql, "[order details]", "SQ005"))
    assert fix["text"] == "dbo.[order details]"


def test_compare_for_a_view_runs_its_query():
    sql = "CREATE VIEW v AS SELECT 1 AS one FROM dbo.sales WHERE YEAR(order_date) = 2026"
    (fix,) = propose(sql, at(sql, "YEAR"))
    assert fix["compare"]["before"] == "SELECT 1 AS one FROM dbo.sales WHERE YEAR(order_date) = 2026"
    assert fix["compare"]["after"].startswith("SELECT 1 AS one FROM dbo.sales WHERE (order_date >= ")


def test_no_compare_for_create_table_as_select():
    sql = "CREATE TABLE t AS SELECT 1 AS one FROM dbo.sales WHERE YEAR(order_date) = 2026"
    fixes_ = propose(sql, at(sql, "YEAR"))
    assert all("compare" not in f for f in fixes_)


def test_no_compare_for_a_view_with_nothing_after_as():
    sql = "CREATE VIEW sales AS"
    fixes_ = propose(sql, at(sql, "sales", "SQ005"))
    assert all("compare" not in f for f in fixes_)


def test_compare_leaves_out_a_closing_semicolon():
    sql = "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026;"
    (fix,) = propose(sql, at(sql, "YEAR"))
    assert fix["compare"]["before"] == "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026"
    assert fix["compare"]["after"] == (
        "SELECT 1 FROM dbo.sales WHERE (order_date >= '2026-01-01' AND order_date < '2027-01-01')")


def test_a_finding_in_the_first_column_of_a_later_line_gets_its_fix():
    sql = "SELECT 1\nFROM\nsales"
    finding = at(sql, "sales", "SQ005")
    assert (finding["line"], finding["col"]) == (3, 1)
    assert [f["text"] for f in propose(sql, finding)] == ["dbo.sales"]
