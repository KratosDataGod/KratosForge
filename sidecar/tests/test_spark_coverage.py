"""The Spark function coverage list (#1668): every Spark 3.5 built-in has a status, each one marked
as running has a value check that passes, and docs/spark-functions.md is up to date."""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pytest

from fabric_desktop import spark_api, spark_coverage

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows():
    return spark_coverage.coverage()


def test_the_list_is_spark_35s_and_has_every_builtin():
    data = spark_coverage.load()
    names = [f["name"] for f in data]
    assert names == sorted(set(names)) and len(names) > 400
    assert spark_api.SPARK_VERSION.startswith("3.5")  # the list is Spark 3.5's: capture it again on a change
    for name in ("abs", "sum", "date_add", "from_json", "transform", "hash", "xpath"):
        assert name in names


def test_every_function_has_one_status(rows):
    assert [r["name"] for r in rows] == [f["name"] for f in spark_coverage.load()]
    assert {r["status"] for r in rows} <= set(spark_coverage.STATUSES)


def test_every_function_marked_as_running_has_a_value_check_that_passes(rows):
    data = {f["name"]: f for f in spark_coverage.load()}
    spark = None
    for r in (r for r in rows if r["status"] == "runs"):
        f = data[r["name"]]
        assert f.get("example") and "value" in f and r["name"] not in spark_coverage.VARYING, r["name"]
        if spark is None:
            from fabric_desktop.notebook import Kernel
            spark = Kernel().namespace["spark"]
        got = spark_coverage.rows_value(spark.sql(f["example"]).collect(), r["name"])
        assert got == f["value"], f"{r['name']}: {json.dumps(got)} here, {json.dumps(f['value'])} in Spark"


def test_a_refusal_is_told_from_a_failure(rows):
    status = {r["name"]: r["status"] for r in rows}
    assert status["count_min_sketch"] == "refused" and status["hll_sketch_agg"] == "refused"  # refused by name
    assert status["hash"] == "runs" and status["xxhash64"] == "runs"  # EA-017: Spark's own numbers (spark_hash.py)
    assert status["abs"] == "runs" and status["rand"] == "runs" and status["version"] == "not checked"


def _pinned_duckdb() -> str:
    [pin] = re.findall(r"^duckdb==(\S+)", (ROOT / "sidecar" / "constraints.txt").read_text(encoding="utf-8"), re.M)
    return pin


def test_what_the_docs_say_runs_still_runs(rows):
    """On every DuckDB CI runs (the pinned one and the next major), a function the page says runs still
    gives Spark's value."""
    page = (ROOT / "docs" / "spark-functions.md").read_text(encoding="utf-8")
    documented = {n.replace("\\|", "|") for n in re.findall(r"^\| `([^`]+)` \| Runs \|", page, re.M)}
    assert len(documented) > 200
    now = {r["name"] for r in rows if r["status"] == "runs"}
    assert documented <= now, f"no longer run as in Spark: {sorted(documented - now)}"


def test_the_docs_page_is_up_to_date(rows):
    if duckdb.__version__ != _pinned_duckdb():
        pytest.skip("the page shows the pinned DuckDB's statuses; the next major only has to keep what runs")
    page = (ROOT / "docs" / "spark-functions.md").read_text(encoding="utf-8")
    assert page == spark_coverage.markdown(rows), (
        "docs/spark-functions.md is stale: run `python -m fabric_desktop.spark_coverage` in sidecar/ and commit it")


def test_the_docs_link_the_list():
    assert "spark-functions.md" in (ROOT / "docs" / "engines.md").read_text(encoding="utf-8")
    assert re.search(r'=\s*"spark-functions\.md"', (ROOT / "zensical.toml").read_text(encoding="utf-8"))
