"""The Snowflake function list's status (snowflake_coverage.py): the verdicts no real function reaches
today. The value checks and DuckDB probes are stood in for, so each verdict is reached on purpose."""

from __future__ import annotations

from types import SimpleNamespace

from fabric_desktop import snowflake_coverage as sc
from fabric_desktop import snowflake_sql_parity


def test_a_case_that_fails_is_reported_with_what_went_wrong(monkeypatch):
    monkeypatch.setattr(snowflake_sql_parity, "run", lambda case: ("error", "Conversion Error: bad date"))
    case = SimpleNamespace(function="TO_DATE", note="", sql="SELECT TO_DATE('x')")
    assert sc._from_cases([case]) == ("fails", "TO_DATE: Conversion Error: bad date")


def probe_with(monkeypatch, answers):
    tried = iter(answers)
    monkeypatch.setattr(sc, "_try", lambda sql, setup=False: next(tried))


def test_a_function_duckdb_doesnt_have_at_any_arity_says_so(monkeypatch):
    probe_with(monkeypatch, [("fails", "Catalog Error: Scalar Function with name zzz does not exist!")] * sc._ARITIES)
    assert sc._probe({"name": "ZZZ"}) == ("fails", "DuckDB has no ZZZ, and the rules don't rewrite it")


def test_a_real_error_is_preferred_over_a_missing_arity(monkeypatch):
    probe_with(monkeypatch, [("fails", "Catalog Error: zzz does not exist")]
               + [("fails", "Binder Error: wrong argument type")] * (sc._ARITIES - 1))
    assert sc._probe({"name": "ZZZ"}) == ("fails", "Binder Error: wrong argument type")


def test_a_function_sqlglot_cant_read_at_any_arity_says_so(monkeypatch):
    probe_with(monkeypatch, [("unread", "Expecting )")] * sc._ARITIES)
    assert sc._probe({"name": "ZZZ"}) == ("fails", "sqlglot can't read it: Expecting )")
