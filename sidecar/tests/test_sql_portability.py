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
