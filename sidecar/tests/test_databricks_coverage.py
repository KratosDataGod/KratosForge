"""The Databricks SQL function coverage list: every built-in Databricks SQL shares with Spark 3.5 has a
status from a Databricks SQL cell's own path, each one marked as running has a value check that
passes, and docs/databricks-functions.md is up to date."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from fabric_desktop import databricks_coverage, spark_coverage

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows():
    return databricks_coverage.coverage()


def test_every_function_has_one_status(rows):
    assert [r["name"] for r in rows] == [f["name"] for f in spark_coverage.load()]
    assert {r["status"] for r in rows} <= set(databricks_coverage.STATUSES)


def test_every_function_marked_as_running_has_a_value_check_that_passes(rows):
    data = {f["name"]: f for f in spark_coverage.load()}
    con = databricks_coverage.connect()
    for r in (r for r in rows if r["status"] == "runs"):
        f = data[r["name"]]
        assert f.get("example") and "value" in f and r["name"] not in spark_coverage.VARYING, r["name"]
        want = databricks_coverage.DATABRICKS_VALUES.get(r["name"], f["value"])
        assert databricks_coverage.run(f["example"], con) == want, r["name"]


def test_a_refusal_a_difference_and_a_failure_are_told_apart(rows):
    status = {r["name"]: r["status"] for r in rows}
    assert status["abs"] == "runs" and status["rand"] == "not checked"
    assert status["hll_sketch_agg"] == "refused" and status["xpath"] == "refused"  # refused by name
    assert status["bitmap_count"] == "runs"  # X '1010' reads in Databricks SQL as in Spark SQL
    assert status["named_struct"] == "runs" and status["map_entries"] == "runs"  # a struct isn't a map
    assert status["schema_of_json"] == "runs"  # Databricks' own value, with `col` quoted


def test_a_struct_keeps_its_field_order_and_a_map_doesnt():
    con = databricks_coverage.connect()
    assert databricks_coverage.run("SELECT named_struct('b', 1, 'a', 2)", con) == [
        [{"struct": [["b", 1], ["a", 2]]}]]
    assert databricks_coverage.run("SELECT map('b', 1, 'a', 2)", con) == [[{"map": [["a", 2], ["b", 1]]}]]


def test_hex_literals_with_a_space_read_in_databricks_sql():
    con = databricks_coverage.connect()
    assert databricks_coverage.run("SELECT hex(X '1F')", con) == [["1F"]]


def _pinned_duckdb() -> str:
    [pin] = re.findall(r"^duckdb==(\S+)", (ROOT / "sidecar" / "constraints.txt").read_text(encoding="utf-8"), re.M)
    return pin


def test_what_the_docs_say_runs_still_runs(rows):
    page = (ROOT / "docs" / "databricks-functions.md").read_text(encoding="utf-8")
    documented = {n.replace("\\|", "|") for n in re.findall(r"^\| `([^`]+)` \| Runs \|", page, re.M)}
    assert len(documented) > 200
    now = {r["name"] for r in rows if r["status"] == "runs"}
    assert documented <= now, f"no longer run as in Databricks: {sorted(documented - now)}"


def test_the_docs_page_is_up_to_date(rows):
    if duckdb.__version__ != _pinned_duckdb():
        pytest.skip("the page shows the pinned DuckDB's statuses; the next major only has to keep what runs")
    page = (ROOT / "docs" / "databricks-functions.md").read_text(encoding="utf-8")
    assert page == databricks_coverage.markdown(rows), (
        "docs/databricks-functions.md is stale: run `python -m fabric_desktop.databricks_coverage` in sidecar/ "
        "and commit it")


def test_the_docs_link_the_list():
    assert "databricks-functions.md" in (ROOT / "docs" / "engines.md").read_text(encoding="utf-8")
    assert re.search(r'=\s*"databricks-functions\.md"', (ROOT / "zensical.toml").read_text(encoding="utf-8"))
