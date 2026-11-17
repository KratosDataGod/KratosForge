"""SQL portability: one report on where else a query runs, read only, never rewriting the person's SQL
(KUDP-05)."""

from __future__ import annotations

import pytest

import test_snowflake_sql as sf_t
from fabric_desktop import sql_portability as sp
from fabric_desktop.sql_portability import GRADES, PLATFORMS, portability


def grades(sql, source, **kw):
    return {t["id"]: t["grade"] for t in portability(sql, source, **kw)["targets"]}


def target(report, tid):
    return next(t for t in report["targets"] if t["id"] == tid)


@pytest.mark.parametrize("name, sql, verdict, notes", sf_t.PROBE, ids=[p[0] for p in sf_t.PROBE])
def test_snowflake_on_duckdb_follows_the_snowflake_sql_labels(name, sql, verdict, notes):
    want = {"translated": "translated", "difference": "partial", "not_translatable": "unsupported"}[verdict]
    duck = target(portability(sql, "snowflake", targets=["duckdb"]), "duckdb")
    assert duck["grade"] == want and duck["checked_by"] == "rules"
    reasons = " ".join(duck["statements"][0]["reasons"])
    assert all(n in reasons for n in notes)


@pytest.mark.parametrize("name, sql, verdict, notes",
                         [p for p in sf_t.PROBE if any(n.startswith("SFN") for n in p[3])], ids=lambda v: str(v)[:20])
def test_what_runs_only_in_snowflake_is_unsupported_everywhere_else(name, sql, verdict, notes):
    got = grades(sql, "snowflake")
    assert set(got) == set(PLATFORMS) - {"snowflake"}
    assert set(got.values()) == {"unsupported"}


def test_fabric_targets_use_the_sqlcompat_rules_on_the_rewrite():
    r = portability("SELECT date_format(d, 'yyyy') AS y, explode(arr) FROM t", "spark")
    wh = target(r, "warehouse")
    assert wh["grade"] == "unsupported" and wh["checked_by"] == "rules"
    assert any(x.startswith("FN-004") for x in wh["statements"][0]["reasons"])
    duck = target(r, "duckdb")
    assert duck["grade"] == "translated" and "STRFTIME" in duck["statements"][0]["rewritten"]


def test_limit_and_top_translate_both_ways():
    wh = target(portability("SELECT a FROM t LIMIT 5", "spark"), "warehouse")
    assert wh["grade"] == "translated" and wh["statements"][0]["rewritten"] == "SELECT TOP 5 a FROM t"
    spark = target(portability("SELECT TOP 5 a FROM t", "warehouse"), "spark")
    assert spark["grade"] == "translated" and spark["statements"][0]["rewritten"] == "SELECT a FROM t LIMIT 5"


def test_sql_that_reads_the_same_is_compatible_and_carries_no_rewrite():
    r = portability("SELECT a, COUNT(*) AS n FROM t GROUP BY a", "spark", targets=["databricks", "duckdb"])
    for t in r["targets"]:
        assert t["grade"] == "compatible" and t["statements"][0]["rewritten"] is None
    # A target without rules of its own says only the rewrite was checked.
    assert target(r, "databricks")["checked_by"] == "rewrite"
    assert any("only the rewrite" in x for x in target(r, "databricks")["statements"][0]["reasons"])


def test_unknown_functions_are_unverified_never_compatible():
    got = grades("SELECT my_udf(a) FROM t", "spark")
    assert set(got.values()) == {"unverified"}
    r = target(portability("SELECT my_udf(a) FROM t", "spark"), "postgres")
    assert "my_udf" in r["statements"][0]["reasons"][0]


def test_a_rewrite_that_does_not_read_back_is_not_trusted(monkeypatch):
    real = sp._normal
    monkeypatch.setattr(sp, "_normal", lambda sql, d: None if d == "postgres" else real(sql, d))
    pg = target(portability("SELECT IFF(a > 1, 1, 0) FROM t", "snowflake"), "postgres")
    assert pg["grade"] == "unverified" and "can't be trusted" in pg["statements"][0]["reasons"][0]


def test_a_target_takes_its_worst_statement():
    wh = target(portability("SELECT a FROM t; SELECT explode(arr) FROM t", "spark"), "warehouse")
    assert [s["grade"] for s in wh["statements"]] == ["compatible", "unsupported"] and wh["grade"] == "unsupported"


def test_unreadable_empty_and_oversized_sql_are_reported_not_graded():
    for sql in ("SELECT FROM WHERE (", "", "SELECT 1" + " " * sp.MAX_SQL):
        r = portability(sql, "spark")
        assert r["problem"] and r["targets"] == []


def test_unknown_platforms_are_refused():
    with pytest.raises(ValueError, match="Unknown platform 'oracle'"):
        portability("SELECT 1", "oracle")
    with pytest.raises(ValueError, match="Unknown platform 'oracle'"):
        portability("SELECT 1", "spark", targets=["oracle"])


def test_every_report_says_it_is_static_and_lists_the_grades():
    r = portability("SELECT 1", "duckdb")
    assert r["schema"] == sp.SCHEMA and r["grades"] == GRADES
    assert any("nothing was sent" in n for n in r["notes"]) and any("not proof" in n for n in r["notes"])
    assert all(t["grade"] in GRADES for t in r["targets"])


# QUALIFY with ORDER BY or LIMIT: sqlglot's own rewrite for platforms without QUALIFY left LIMIT (and
# ORDER BY) inside the subquery, so rows were cut before the window filter. Each case runs the
# Snowflake query and the rewrite on DuckDB, which has QUALIFY, and compares the rows.
_QUALIFY_TAILS = [
    # The first row per a by c, ordered and cut afterwards (the #1904 demo's case).
    "SELECT a, c FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1 ORDER BY c DESC LIMIT 3",
    "SELECT a, b FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1 ORDER BY t.c DESC LIMIT 3 OFFSET 1",
    "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c DESC) = 1 ORDER BY c, a LIMIT 4",
    "SELECT a, c AS n FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) <= 2 ORDER BY 2 DESC LIMIT 5",
    ("SELECT t.a, t.c FROM t JOIN s ON t.a = s.a "
    "QUALIFY ROW_NUMBER() OVER (PARTITION BY t.a ORDER BY s.x DESC, t.c) = 1 ORDER BY s.x, t.a LIMIT 3"),
    ("SELECT a, COUNT(*) AS n FROM t GROUP BY a QUALIFY RANK() OVER (ORDER BY COUNT(*) DESC, a) <= 5 "
    "ORDER BY SUM(c) LIMIT 2"),
    ("SELECT x FROM (SELECT a AS x, c FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1 "
    "ORDER BY c DESC LIMIT 3) AS q ORDER BY x"),
    # Both levels have QUALIFY and a tail: the inner one must be moved too.
    ("SELECT a, c FROM (SELECT a, b, c FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c DESC) = 1 "
    "ORDER BY c LIMIT 4) AS q QUALIFY ROW_NUMBER() OVER (ORDER BY c) <= 3 ORDER BY c DESC LIMIT 2"),
]


@pytest.fixture(scope="module")
def qualify_tables():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT i % 7 AS a, (i * 37) % 11 AS b, i AS c FROM range(60) r(i)")
    con.execute("CREATE TABLE s AS SELECT i % 7 AS a, i AS x FROM range(30) r(i)")
    yield con
    con.close()


@pytest.mark.parametrize("sql", _QUALIFY_TAILS)
@pytest.mark.parametrize("tid", ["spark", "warehouse", "postgres"])
def test_qualify_with_order_by_and_limit_keeps_its_rows(qualify_tables, sql, tid):
    import sqlglot
    want = qualify_tables.execute(sqlglot.transpile(sql, read="snowflake", write="duckdb")[0]).fetchall()
    finding = target(portability(sql, "snowflake", targets=[tid]), tid)["statements"][0]
    if tid == "warehouse" and "ORDER BY 2" in sql:  # T-SQL can't sort NULLs last by position
        assert finding["grade"] == "unsupported" and "positional" in finding["reasons"][0]
        return
    assert finding["grade"] == "translated" and "QUALIFY" not in finding["rewritten"].upper()
    assert any("still apply after the filter" in r for r in finding["reasons"])
    rewritten = sqlglot.transpile(finding["rewritten"], read=PLATFORMS[tid]["sqlglot"], write="duckdb")[0]
    assert qualify_tables.execute(rewritten).fetchall() == want, finding["rewritten"]


def test_qualify_without_a_tail_says_how_it_was_rewritten():
    finding = target(portability("SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1",
                                 "snowflake", targets=["spark"]), "spark")["statements"][0]
    assert finding["grade"] == "translated" and finding["reasons"][0] == sp.QUALIFY_NOTE


def test_qualify_that_cannot_be_rewritten_safely_is_unverified():
    for sql in ("SELECT a, a + 1 FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1 LIMIT 5",
                "SELECT * FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1 ORDER BY a + b LIMIT 5",
                # sqlglot would read t.b in the window as the alias b, which is s.x.
                "SELECT s.x AS b FROM t JOIN s ON t.a = s.a QUALIFY ROW_NUMBER() OVER (ORDER BY t.b) = 1"):
        for t in portability(sql, "snowflake", targets=["spark", "warehouse"])["targets"]:
            assert t["grade"] == "unverified" and t["statements"][0]["rewritten"] is None, (sql, t)


def test_qualify_with_select_star_is_partial_for_its_extra_column():
    finding = target(portability("SELECT * FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1 LIMIT 5",
                                 "snowflake", targets=["spark"]), "spark")["statements"][0]
    assert finding["grade"] == "partial" and sp.QUALIFY_STAR_NOTE in finding["reasons"]
    assert finding["rewritten"].endswith("WHERE _w = 1 LIMIT 5")


def test_platforms_with_qualify_keep_it_as_written():
    sql = "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY c) = 1 LIMIT 5"
    for t in portability(sql, "snowflake", targets=["duckdb", "databricks"])["targets"]:
        assert not any("QUALIFY is rewritten" in r for r in t["statements"][0]["reasons"])
