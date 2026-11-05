"""The Snowflake function coverage list: every listed function, operator and statement has one status,
each one marked as running has value checks that pass, and docs/snowflake-functions.md is up to date."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from fabric_desktop import snowflake_coverage, snowflake_sql, snowflake_sql_parity

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows():
    return snowflake_coverage.coverage()


def test_the_list_has_snowflakes_functions_once_each():
    data = snowflake_coverage.load()
    names = [f["name"] for f in data]
    assert len(names) == len(set(names)) and len(names) > 450
    for name in ("IFF", "DATEADD", "PARSE_JSON", "ARRAY_AGG", "LISTAGG", "HLL", "MERGE", "CREATE STREAM"):
        assert name in names
    assert all(f["group"] for f in data)
    assert all(f.get("probe") for f in data if f["group"] in ("Operators", "Statements"))


def test_every_parity_check_belongs_to_a_listed_function():
    names = {f["name"] for f in snowflake_coverage.load()}
    missing = {snowflake_coverage.case_name(c.function) for c in snowflake_sql_parity.CASES} - names
    assert not missing, f"add these to snowflake_builtins.json: {sorted(missing)}"


def test_every_function_has_one_status(rows):
    assert [r["name"] for r in rows] == [f["name"] for f in snowflake_coverage.load()]
    assert {r["status"] for r in rows} <= set(snowflake_coverage.STATUSES)


def test_every_function_marked_as_running_has_value_checks_that_pass(rows):
    cases: dict[str, list] = {}
    for c in snowflake_sql_parity.CASES:
        cases.setdefault(snowflake_coverage.case_name(c.function), []).append(c)
    for r in (r for r in rows if r["status"] == "runs"):
        assert cases.get(r["name"]), f"{r['name']} is marked as running with no value check"
        for c in cases[r["name"]]:
            assert snowflake_sql_parity.run(c)[0] == "same", c.function


def test_a_refusal_a_difference_and_a_failure_are_told_apart(rows):
    status = {r["name"]: r["status"] for r in rows}
    assert status["IFF"] == "runs"
    assert status["/"] == "differs" and status["LATERAL FLATTEN"] == "differs"  # SFD-003, SFD-005
    assert status["CREATE STREAM"] == "refused" and status["PARSE_URL"] == "refused"
    assert status["HLL"] == "fails"  # DuckDB has no HLL and nothing rewrites it
    assert status["MONTH"] == "not checked"  # runs, but no check compares its value yet


def test_sqlglot_stopping_while_it_writes_is_a_refusal_not_a_crash():
    translation = snowflake_sql.translate("SELECT TIME()")
    assert not translation.runnable and "runs only in Snowflake" in translation.first_refusal()


def _pinned_duckdb() -> str:
    [pin] = re.findall(r"^duckdb==(\S+)", (ROOT / "sidecar" / "constraints.txt").read_text(encoding="utf-8"), re.M)
    return pin


def test_what_the_docs_say_runs_still_runs(rows):
    page = (ROOT / "docs" / "snowflake-functions.md").read_text(encoding="utf-8")
    documented = {n.replace("\\|", "|") for n in re.findall(r"^\| `([^`]+)` \| Runs \|", page, re.M)}
    assert len(documented) > 90
    now = {r["name"] for r in rows if r["status"] == "runs"}
    assert documented <= now, f"no longer run as Snowflake documents: {sorted(documented - now)}"


def test_the_docs_page_is_up_to_date(rows):
    if duckdb.__version__ != _pinned_duckdb():
        pytest.skip("the page shows the pinned DuckDB's statuses; the next major only has to keep what runs")
    page = (ROOT / "docs" / "snowflake-functions.md").read_text(encoding="utf-8")
    assert page == snowflake_coverage.markdown(rows), (
        "docs/snowflake-functions.md is stale: run `python -m fabric_desktop.snowflake_coverage` in sidecar/ "
        "and commit it")


def test_the_docs_link_the_list():
    assert "snowflake-functions.md" in (ROOT / "docs" / "engines.md").read_text(encoding="utf-8")
    assert re.search(r'=\s*"snowflake-functions\.md"', (ROOT / "zensical.toml").read_text(encoding="utf-8"))
