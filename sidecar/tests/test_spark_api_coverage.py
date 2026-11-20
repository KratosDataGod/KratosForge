"""The PySpark DataFrame API coverage list: every public method of PySpark 3.5's DataFrame, Column,
GroupedData, reader, writer, na and stat has a status, each one marked as running passes all its
documented examples here, and docs/spark-dataframe-api.md is up to date."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from fabric_desktop import spark_api, spark_api_coverage, spark_coverage

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rows():
    return spark_api_coverage.coverage()


def test_the_list_is_pyspark_35s_and_has_every_class():
    data = spark_api_coverage.load()
    assert spark_api.SPARK_VERSION.startswith("3.5")  # the list is PySpark 3.5's: capture it again on a change
    assert {m["class"] for m in data} == set(spark_api_coverage.CLASSES)
    names = {(m["class"], m["name"]) for m in data}
    assert len(names) == len(data) > 150
    for wanted in (("DataFrame", "select"), ("DataFrame", "join"), ("Column", "alias"), ("GroupedData", "agg"),
                   ("DataFrameReader", "parquet"), ("DataFrameWriter", "saveAsTable")):
        assert wanted in names


def test_every_method_has_one_status(rows):
    assert [(r["class"], r["name"]) for r in rows] == [(m["class"], m["name"]) for m in spark_api_coverage.load()]
    assert {r["status"] for r in rows} <= set(spark_coverage.STATUSES)


def test_a_method_marked_as_running_has_examples_with_spark_s_results(rows):
    data = {(m["class"], m["name"]): m for m in spark_api_coverage.load()}
    running = [r for r in rows if r["status"] == "runs"]
    assert running
    for r in running:
        method = data[(r["class"], r["name"])]
        assert method["examples"] and any("outcome" in e for e in method["examples"]), r


def test_an_api_object_is_told_by_what_it_does_not_its_class_name():
    assert spark_api_coverage.outcome(None, "  x  \n") == {"printed": "  x"}
    assert spark_api_coverage.outcome(3, "") == {"value": 3}

    class Reader:
        def load(self): ...
        def option(self): ...
        def table(self): ...
    assert spark_api_coverage.outcome(Reader(), "") == {"value": {"kind": "DataFrameReader"}}
    assert spark_api_coverage.outcome(Reader, "") == {"value": {"class": "DataFrameReader"}}


def test_what_the_docs_say_runs_still_runs(rows):
    page = (ROOT / "docs" / "spark-dataframe-api.md").read_text(encoding="utf-8")
    documented = set()
    for section in re.split(r"^## ", page, flags=re.M)[1:]:
        cls = section.splitlines()[0].strip()
        documented |= {(cls, n) for n in re.findall(r"^\| `([^`]+)` \| Runs \|", section, re.M)}
    assert documented
    now = {(r["class"], r["name"]) for r in rows if r["status"] == "runs"}
    assert documented <= now, f"no longer run as in Spark: {sorted(documented - now)}"


def _pinned_duckdb() -> str:
    [pin] = re.findall(r"^duckdb==(\S+)", (ROOT / "sidecar" / "constraints.txt").read_text(encoding="utf-8"), re.M)
    return pin


def test_the_docs_page_is_up_to_date(rows):
    if duckdb.__version__ != _pinned_duckdb():
        pytest.skip("the page shows the pinned DuckDB's statuses; the next major only has to keep what runs")
    page = (ROOT / "docs" / "spark-dataframe-api.md").read_text(encoding="utf-8")
    assert page == spark_api_coverage.markdown(rows), (
        "docs/spark-dataframe-api.md is stale: run `python -m fabric_desktop.spark_api_coverage` in sidecar/ "
        "and commit it")


def test_the_docs_link_the_list():
    assert "spark-dataframe-api.md" in (ROOT / "docs" / "engines.md").read_text(encoding="utf-8")


def test_the_readme_counts_match_spark_api_coverage():
    from collections import Counter

    counts = Counter((r["area"], r["state"]) for r in spark_api.coverage())
    frame = {state: n for (area, state), n in counts.items() if area == "DataFrame"}
    session = {state: n for (area, state), n in counts.items() if area == "SparkSession"}
    parts = [f"{frame.get('works', 0)} work",
             f"{frame.get('no-op', 0)} are accepted and do nothing on one PC (such as `cache` and `repartition`)",
             f"{frame.get('Fabric', 0)} run only in Fabric"]
    if frame.get("missing"):
        parts.append(f"{frame['missing']} aren't built yet")
    listed = ", ".join(parts[:-1]) + " and " + parts[-1]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert (f"Of PySpark's {sum(frame.values())} DataFrame methods, {listed}. "
            f"{session.get('works', 0)} of SparkSession's {sum(session.values())} work.") in readme


def test_an_alias_is_checked_with_its_method_s_examples_each_call_renamed():
    data = {(m["class"], m["name"]): m for m in spark_api_coverage.load()}
    for (cls, name), target in spark_api_coverage.ALIASES.items():
        examples, source = data[(cls, name)]["examples"], data[(cls, target)]["examples"]
        assert [e.get("outcome") for e in examples] == [e.get("outcome") for e in source]
        assert any(f".{name}(" in e["source"] and f".{target}(" not in e["source"] for e in examples), name


def test_a_written_example_replaces_one_spark_could_not_compare():
    data = {(m["class"], m["name"]): m for m in spark_api_coverage.load()}
    for key, examples in spark_api_coverage.WRITTEN.items():
        method = data[key]
        assert method["examples"] == examples and "varying" not in method and "spark_error" not in method
        assert any(f"{key[1]}(" in e["source"] for e in examples), key


def test_pandas_on_spark_is_refused_by_name():
    from fabric_desktop.notebook import Kernel
    result = Kernel().run("spark.range(1).to_pandas_on_spark()")
    assert result["status"] != "ok"
    error = next(o for o in result["outputs"] if o.get("type") == "error")
    assert error["ename"] == "NotImplementedError" and "toPandas()" in error["evalue"]
