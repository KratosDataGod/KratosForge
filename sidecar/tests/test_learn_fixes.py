"""GD-02: Try the fix beside a SQL best-practice finding. A fix is offered only where it keeps the
result, and Compare reads two runs that only read."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_desktop import audit, server
from fabric_desktop.learn import fixes
from fabric_desktop.learn_service import METHODS, LearnMixin
from fabric_desktop.sqlcompat import practices

CATALOG = {"tables": [{"schema": "dbo", "name": "sales"}, {"schema": "dbo", "name": "returns"},
                      {"schema": "stage", "name": "orders"}, {"schema": "dbo", "name": "orders"},
                      {"schema": "stage", "name": "only_here"}],
           "columns": {"dbo.sales": [{"name": "order_date", "type": "date"}, {"name": "shipped", "type": "datetime2(6)"},
                                     {"name": "stamp", "type": "datetimeoffset"}, {"name": "label", "type": "varchar(10)"}],
                       "dbo.returns": [{"name": "order_date", "type": "varchar(10)"}]}}


def _fixes(sql: str, catalog: dict | None = CATALOG) -> dict:
    return fixes.propose(sql, "warehouse", practices.check(sql, "warehouse")["findings"], catalog)


def _texts(sql: str, rule: str = "SQ002", catalog: dict | None = CATALOG) -> list[str]:
    return [f["text"] for f in _fixes(sql, catalog)["fixes"] if f["rule_id"] == rule]


# ---- SQ002: a function on a column in WHERE


@pytest.mark.parametrize("sql, text", [
    ("SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026",
     "(order_date >= '2026-01-01' AND order_date < '2027-01-01')"),
    ("SELECT 1 FROM dbo.sales s WHERE YEAR(s.shipped) = 1999 AND 1 = 1",
     "(s.shipped >= '1999-01-01' AND s.shipped < '2000-01-01')"),
    ("SELECT 1 FROM dbo.sales s JOIN dbo.returns r ON 1 = 1 WHERE YEAR(s.order_date) = 2026",
     "(s.order_date >= '2026-01-01' AND s.order_date < '2027-01-01')"),
])
def test_year_on_a_date_column_becomes_a_range(sql, text):
    assert _texts(sql) == [text]


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM dbo.sales WHERE YEAR(label) = 2026",                    # not a date
    "SELECT 1 FROM dbo.sales WHERE YEAR(stamp) = 2026",                    # datetimeoffset: left alone
    "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026 + 1",           # more than a year
    "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 9999",               # no year after it
    "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) > 2026",               # only =
    "SELECT 1 FROM dbo.sales WHERE MONTH(order_date) = 3",                 # only YEAR
    "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = @y",                 # a parameter
    "SELECT 1 FROM dbo.sales s JOIN dbo.returns r ON 1 = 1 WHERE YEAR(r.order_date) = 2026",  # r's is text
    "SELECT 1 FROM dbo.sales s JOIN dbo.returns r ON 1 = 1 WHERE YEAR(order_date) = 2026",    # which one?
    "SELECT 1 FROM dbo.sales, dbo.returns WHERE YEAR(order_date) = 2026",  # FROM a, b
    "SELECT 1 FROM (SELECT * FROM dbo.sales) x WHERE YEAR(order_date) = 2026",  # derived table
    "WITH c AS (SELECT * FROM dbo.sales) SELECT 1 FROM c WHERE YEAR(order_date) = 2026",  # a CTE
    "SELECT 1 FROM dbo.nowhere WHERE YEAR(order_date) = 2026",             # not in the catalog
])
def test_no_fix_where_the_result_could_change(sql):
    assert _texts(sql) == []


def test_without_the_catalog_there_is_no_fix_and_it_asks_for_the_columns():
    sql = "SELECT 1 FROM sales WHERE YEAR(order_date) = 2026"
    assert _fixes(sql, None)["fixes"] == []
    out = _fixes(sql, {"tables": CATALOG["tables"], "columns": {}})
    assert out["needs"] == [{"schema": "dbo", "name": "sales"}] and _texts(sql, "SQ002", {**CATALOG, "columns": {}}) == []
    assert _texts(sql) == ["(order_date >= '2026-01-01' AND order_date < '2027-01-01')"]


def test_a_fix_is_an_edit_of_the_finding_only_and_says_why_it_is_safe():
    sql = "SELECT region\nFROM dbo.sales\nWHERE YEAR(order_date) = 2026\nGROUP BY region;"
    (fix,) = _fixes(sql)["fixes"]
    assert sql[fix["start"]:fix["end"]] == fix["before"] == "YEAR(order_date) = 2026"
    assert "date column" in fix["why_safe"] and fix["principle"].startswith("Compare the column itself")
    after = sql[:fix["start"]] + fix["text"] + sql[fix["end"]:]
    assert practices.check(after, "warehouse")["counts"] == {"Error": 0, "Warning": 0, "Info": 0}
    assert fix["compare"] == {"before": sql.rstrip(";"), "after": after.rstrip(";")}


def test_not_keeps_its_meaning():
    (text,) = _texts("SELECT 1 FROM dbo.sales WHERE NOT YEAR(order_date) = 2026 OR 1 = 0")
    assert text.startswith("(") and text.endswith(")")  # NOT (a AND b), never NOT a AND b


# ---- SQ005: a table named without its schema


def test_a_name_only_dbo_has_gets_dbo():
    assert _texts("SELECT 1 FROM sales", "SQ005") == ["dbo.sales"]
    assert _texts("SELECT 1 FROM [sales]", "SQ005") == ["dbo.[sales]"]


@pytest.mark.parametrize("sql", ["SELECT 1 FROM orders", "SELECT 1 FROM only_here", "SELECT 1 FROM nowhere"])
def test_no_schema_is_guessed(sql):
    assert _texts(sql, "SQ005") == []   # two schemas, only another schema, or not in the catalog


# ---- what Compare runs


def test_compare_runs_the_select_or_the_view_query_and_nothing_else():
    view = "CREATE VIEW dbo.v AS\nSELECT * FROM sales\nWHERE YEAR(order_date) = 2026;"
    for fix in _fixes(view)["fixes"]:
        assert fix["compare"]["before"] == "SELECT * FROM sales\nWHERE YEAR(order_date) = 2026"
        assert fix["compare"]["after"].startswith("SELECT * FROM ")
    insert = "INSERT INTO dbo.t SELECT * FROM dbo.sales WHERE YEAR(order_date) = 2026"
    assert [f.get("compare") for f in _fixes(insert)["fixes"]] == [None]


# ---- the service


class _Runs:
    def __init__(self, runs):
        self.runs = runs

    def table(self, run_id):
        return self.runs[run_id]


class _Svc(LearnMixin):
    def __init__(self, runs):
        self.sql_runs = _Runs(runs)


def test_methods_are_served_and_read_only():
    assert {"learn_fixes", "learn_fix_compare"} <= METHODS <= server.METHODS
    assert not audit.audited("learn_fixes") and not audit.audited("learn_fix_compare")


def test_compare_says_same_rows_and_each_measured_time():
    t = pa.table({"region": ["n", "s"], "revenue": [1.5, 2.0]})
    svc = _Svc({"a": ({"seconds": 1.84, "target": "warehouse"}, t), "b": ({"seconds": 0.21, "target": "warehouse"}, t)})
    out = svc.learn_fix_compare("a", "b")
    assert (out["result"], out["level"], out["rows"], out["seconds"]) == \
        ("match", "complete", {"before": 2, "after": 2}, {"before": 1.84, "after": 0.21})


def test_compare_says_when_rows_differ_and_refuses_a_run_that_changed_something():
    a = pa.table({"x": [1, 2]})
    svc = _Svc({"a": ({"seconds": 1}, a), "b": ({"seconds": 1}, pa.table({"x": [1]})),
                "w": ({"seconds": 1, "change": True}, a)})
    assert svc.learn_fix_compare("a", "b")["result"] == "differs"
    with pytest.raises(ValueError, match="only read"):
        svc.learn_fix_compare("a", "w")
