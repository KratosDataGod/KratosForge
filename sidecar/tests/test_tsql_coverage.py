"""The T-SQL function coverage list: every listed function, operator and statement has one status, each
value check belongs to something listed, and docs/tsql-functions.md is up to date."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from tests import tsql_coverage

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    return tsql_coverage.coverage(tmp_path_factory.mktemp("tsql-coverage") / "local-data")


def test_the_list_has_tsqls_functions_once_each():
    data = tsql_coverage.load()
    names = [f["name"] for f in data]
    assert len(names) == len(set(names)) and len(names) > 150
    for name in ("CHARINDEX", "DATEADD", "TRY_CONVERT", "STRING_AGG", "ROW_NUMBER", "JSON_VALUE", "MERGE", "TOP"):
        assert name in names
    assert all(f["group"] for f in data)
    assert all(f.get("probe") and f.get("match") for f in data if f["group"] == "Statements and operators")


def test_every_value_check_belongs_to_something_listed():
    data = tsql_coverage.load()
    alone = [c["id"] for c in tsql_coverage.checks() if not any(tsql_coverage.calls(f, c["sql"]) for f in data)]
    assert not alone, f"add what these check to tsql_contract/builtins.json: {alone}"


def test_every_function_has_one_status(rows):
    assert [r["name"] for r in rows] == [f["name"] for f in tsql_coverage.load()]
    assert {r["status"] for r in rows} <= set(tsql_coverage.STATUSES)


def test_a_check_a_refusal_and_a_failure_are_told_apart(rows):
    status = {r["name"]: r["status"] for r in rows}
    assert status["CHARINDEX"] == "runs" and status["DATEADD"] == "runs"
    assert status["ISNUMERIC"] == "refused" and status["DECLARE and variables"] == "refused"
    assert status["ABS"] == "not checked"  # runs, but no check compares its answer yet


def test_nothing_is_marked_as_differing_without_its_check():
    """A differs row names the check that gave another answer, so it can be fixed or refused."""
    page = (ROOT / "docs" / "tsql-functions.md").read_text(encoding="utf-8")
    for line in re.findall(r"^\| `[^`]+` \| Differs \|.*$", page, re.M):
        assert " here, " in line and " in T-SQL" in line, line


def _pinned_duckdb() -> str:
    [pin] = re.findall(r"^duckdb==(\S+)", (ROOT / "sidecar" / "constraints.txt").read_text(encoding="utf-8"), re.M)
    return pin


def test_the_docs_page_is_up_to_date(rows):
    if duckdb.__version__ != _pinned_duckdb():
        pytest.skip("the page shows the pinned DuckDB's statuses")
    page = (ROOT / "docs" / "tsql-functions.md").read_text(encoding="utf-8")
    assert page == tsql_coverage.markdown(rows), (
        "docs/tsql-functions.md is stale: run `python -m tests.tsql_coverage` in sidecar/ and commit it")


def test_the_docs_link_the_list():
    assert "tsql-functions.md" in (ROOT / "docs" / "engines.md").read_text(encoding="utf-8")
    assert "tsql-functions.md" in (ROOT / "docs" / "local-data.md").read_text(encoding="utf-8")
    assert re.search(r'=\s*"tsql-functions\.md"', (ROOT / "zensical.toml").read_text(encoding="utf-8"))
