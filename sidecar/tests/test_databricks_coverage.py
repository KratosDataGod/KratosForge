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
    assert [r["name"] for r in rows] == [f["name"] for f in spark_coverage.load()] + [
        f["name"] for f in databricks_coverage.load()]
    assert {r["status"] for r in rows} <= set(databricks_coverage.STATUSES)


def test_every_function_marked_as_running_has_a_value_check_that_passes(rows):
    data = {f["name"]: f for f in [*spark_coverage.load(), *databricks_coverage.load()]}
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


def test_databricks_own_functions_are_listed_once_and_not_spark_ones():
    own = [f["name"] for f in databricks_coverage.load()]
    assert own == sorted(set(own)) and len(own) > 100
    assert not set(own) & {f["name"] for f in spark_coverage.load()}
    for name in ("zeroifnull", "try_parse_json", "ip_host", "vector_l2_distance", "kll_sketch_agg_bigint"):
        assert name in own
    assert not [n for n in own if n.startswith(("ai_", "h3_", "st_", "read_"))]  # need Databricks' services


def test_a_databricks_function_with_no_check_is_probed(rows):
    status = {r["name"]: r["status"] for r in rows}
    assert status["iff"] == "not checked"  # DuckDB accepts it; no value check yet
    notes = {r["name"]: r["note"] for r in rows}
    assert status["vector_norm"] == "runs" and "FLOAT in Databricks" in notes["vector_norm"]  # its listed note
    assert status["vector_avg"] == "refused"  # no element-wise aggregate here, said by name
    assert status["from_xml"] == "fails"  # DuckDB has none and nothing writes it


@pytest.mark.parametrize("code", [
    "SELECT kll_sketch_agg_bigint(x) FROM VALUES (1), (2) AS t(x)",
    "SELECT theta_sketch_estimate(theta_sketch_agg(x)) FROM VALUES (1) AS t(x)",
    "SELECT tuple_sketch_agg_double(k, v) FROM VALUES (1, 2.0) AS t(k, v)",
    "SELECT approx_top_k_estimate(approx_top_k_accumulate(x, 5), 3) FROM VALUES ('a') AS t(x)",
])
def test_databricks_sketches_are_refused_by_name(code):
    from fabric_desktop.engines.local_sql import _databricks
    with pytest.raises(ValueError, match=r"can't run on this PC the way it runs in Databricks.*DataSketches"):
        _databricks(code)


def test_a_sketch_name_in_spark_sql_is_left_alone():
    from fabric_desktop import sql
    assert sql.parse("SELECT kll_sketch_agg_bigint(x) FROM t", "spark")  # Spark 3.5 has none: DuckDB says so


def test_a_function_with_its_own_syntax_is_probed_in_that_syntax(rows):
    status = {r["name"]: r["status"] for r in rows}
    for name in ("try_cast", "cube", "percentile_cont", "percentile_disc", "timestampdiff", "timestampadd"):
        assert status[name] == "not checked", name  # runs here; no value check yet
    assert status["collate"] == "fails"  # no UTF8_LCASE collation here


def test_a_function_of_ones_own_named_like_a_sketch_is_left_alone():
    from fabric_desktop.engines.local_sql import _databricks
    assert _databricks("SELECT tuple_merge(a) FROM t")  # not a Databricks sketch: DuckDB says whether it exists


def _one(code):
    con = databricks_coverage.connect()
    return databricks_coverage.run(code, con)


# Every example in Databricks' reference for each function written for DuckDB, with the value it shows
@pytest.mark.parametrize(("code", "want"), [
    ("SELECT zeroifnull(0)", 0), ("SELECT zeroifnull(NULL)", 0), ("SELECT zeroifnull(5)", 5),
    ("SELECT nullifzero(0)", None), ("SELECT nullifzero(NULL)", None), ("SELECT nullifzero(5)", 5),
    ("SELECT try_mod(2, 1.8)", "decimal:0.2"), ("SELECT try_mod(2, 0)", None),
    ("SELECT vector_cosine_similarity(array(1.0f, 2.0f, 3.0f), array(4.0f, 5.0f, 6.0f))", "float:0.974631846197"),
    ("SELECT vector_cosine_similarity(array(1.0f, 0.0f, 0.0f), array(1.0f, 0.0f, 0.0f))", "float:1"),
    ("SELECT vector_cosine_similarity(array(1.0f, 0.0f), array(0.0f, 1.0f))", "float:0"),
    ("SELECT vector_inner_product(array(1.0f, 2.0f, 3.0f), array(4.0f, 5.0f, 6.0f))", "float:32"),
    ("SELECT vector_inner_product(array(1.0f, 0.0f), array(0.0f, 1.0f))", "float:0"),
    ("SELECT vector_inner_product(array(3.0f, 4.0f), array(3.0f, 4.0f))", "float:25"),
    ("SELECT vector_l2_distance(array(1.0f, 2.0f, 3.0f), array(4.0f, 5.0f, 6.0f))", "float:5.19615242271"),
    ("SELECT vector_l2_distance(array(1.0f, 2.0f), array(1.0f, 2.0f))", "float:0"),
    ("SELECT vector_l2_distance(array(0.0f, 0.0f), array(3.0f, 4.0f))", "float:5"),
    ("SELECT vector_norm(array(3.0f, 4.0f), 2.0f)", "float:5"),
    ("SELECT vector_norm(array(3.0f, 4.0f), 1.0f)", "float:7"),
    ("SELECT vector_norm(array(3.0f, 4.0f), float('inf'))", "float:4"),
    ("SELECT vector_norm(array(), 2.0)", "float:0"),
    ("SELECT vector_normalize(array(3.0f, 4.0f), 2.0f)", ["float:0.6", "float:0.8"]),
    ("SELECT vector_norm(vector_normalize(array(1.0f, 2.0f, 3.0f), 2.0f), 2.0f)", "float:1"),
    ("SELECT vector_normalize(array(0.0f, 0.0f, 0.0f), 2.0f)", None),
])
def test_databricks_documented_examples_give_its_values(code, want):
    assert _one(code) == [[want]]


def test_a_vector_that_is_null_or_holds_a_null_gives_null():
    # Databricks documents NULL for both; DuckDB's list functions would stop on the NULL element
    got = _one("SELECT vector_inner_product(e, e), vector_l2_distance(e, e), vector_cosine_similarity(e, e), "
               "vector_norm(e), vector_normalize(e) FROM VALUES (array(1.0f, NULL)), (NULL) AS t(e)")
    assert got == [[None] * 5, [None] * 5]


@pytest.mark.parametrize(("code", "match"), [
    ("SELECT vector_norm(array(3.0f), 3.0)", "INVALID_VECTOR_NORM_DEGREE"),
    ("SELECT vector_norm(array(3.0f), -1.0)", "INVALID_VECTOR_NORM_DEGREE"),
    ("SELECT vector_normalize(array(3.0f), -float('inf'))", "INVALID_VECTOR_NORM_DEGREE"),
    ("SELECT vector_norm(array(3.0f), d) FROM VALUES (2.0) AS t(d)", "isn't a constant"),
    ("SELECT vector_sum(e) FROM VALUES (array(1.0f)) AS t(e)", "element by element"),
    ("SELECT zeroifnull(1, 2)", "takes 1 argument"),
])
def test_what_databricks_would_stop_on_or_this_pc_cant_match_stops_by_name(code, match):
    from fabric_desktop.engines.local_sql import _databricks
    with pytest.raises(ValueError, match=match):
        _databricks(code)
