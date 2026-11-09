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


def test_a_string_degree_is_cast_and_a_column_named_like_a_lambda_argument_is_kept():
    assert _one("SELECT vector_norm(array(3.0f, 4.0f), '1.0'), vector_norm(array(3.0f, 4.0f), 'inf')") == [
        ["float:7", "float:4"]]
    assert _one("SELECT vector_normalize(__y) FROM VALUES (array(3.0f, 4.0f)) AS t(__y)") == [
        [["float:0.6", "float:0.8"]]]


def test_a_vector_that_is_null_or_holds_a_null_gives_null():
    # Databricks documents NULL for both; DuckDB's list functions would stop on the NULL element
    got = _one("SELECT vector_inner_product(e, e), vector_l2_distance(e, e), vector_cosine_similarity(e, e), "
               "vector_norm(e), vector_normalize(e) FROM VALUES (array(1.0f, NULL)), (NULL) AS t(e)")
    assert got == [[None] * 5, [None] * 5]


@pytest.mark.parametrize(("code", "match"), [
    ("SELECT vector_norm(array(3.0f), 3.0)", "INVALID_VECTOR_NORM_DEGREE"),
    ("SELECT vector_norm(array(3.0f), -1.0)", r"INVALID_VECTOR_NORM_DEGREE\), not -1.0\."),
    ("SELECT vector_normalize(array(3.0f), -float('inf'))", "INVALID_VECTOR_NORM_DEGREE"),
    ("SELECT vector_norm(array(3.0f), d) FROM VALUES (2.0) AS t(d)", "isn't a constant"),
    ("SELECT vector_sum(e) FROM VALUES (array(1.0f)) AS t(e)", "element by element"),
    ("SELECT zeroifnull(1, 2)", "takes 1 argument"),
])
def test_what_databricks_would_stop_on_or_this_pc_cant_match_stops_by_name(code, match):
    from fabric_desktop.engines.local_sql import _databricks
    with pytest.raises(ValueError, match=match):
        _databricks(code)


# timestampdiff counts whole units elapsed, in Spark SQL and Databricks SQL; DuckDB's date_diff counts the
# boundaries crossed. The first three are Databricks' timediff examples (a synonym of timestampdiff).
@pytest.mark.parametrize(("code", "want"), [
    ("timediff(MONTH, TIMESTAMP'2021-02-28 12:00:00', TIMESTAMP'2021-03-28 11:59:59')", 0),
    ("timediff(MONTH, TIMESTAMP'2021-02-28 12:00:00', TIMESTAMP'2021-03-28 12:00:00')", 1),
    ("timediff(YEAR, DATE'2021-01-01', DATE'1900-03-28')", -120),
    ("timestampdiff(HOUR, TIMESTAMP'2021-01-01 10:59:00', TIMESTAMP'2021-01-01 11:00:00')", 0),
    ("datediff(DAY, TIMESTAMP'2021-01-01 23:00:00', TIMESTAMP'2021-01-02 01:00:00')", 0),
    ("date_diff(WEEK, DATE'2021-01-01', DATE'2021-01-14')", 1),
    ("timestampdiff(MONTH, DATE'2021-01-31', DATE'2021-02-28')", 0),  # Java's ChronoUnit: day 28 < 31
    ("timestampdiff(MONTH, DATE'2021-03-31', DATE'2021-02-28')", -1),
    ("timestampdiff(QUARTER, DATE'2021-01-01', DATE'2021-12-31')", 3),
    ("timestampdiff(SECOND, TIMESTAMP'2021-01-01 00:00:01.5', TIMESTAMP'2021-01-01 00:00:00')", -1),
    ("datediff(TIMESTAMP'2021-01-02 01:00:00', TIMESTAMP'2021-01-01 23:00:00')", 1),  # two arguments: days
])
def test_timestampdiff_counts_whole_units_as_spark_does(code, want):
    from fabric_desktop import sql
    assert _one(f"SELECT {code}") == [[want]]
    if not code.startswith("timediff"):  # Spark SQL reads the other spellings the same way
        con = databricks_coverage.connect()
        assert con.sql(sql.duckdb_sql(sql.parse(f"SELECT {code}", "spark")[0])).fetchall() == [(want,)]


def test_a_timestampdiff_inside_another_counts_whole_units_too():
    from fabric_desktop import sql
    code = ("SELECT timestampdiff(DAY, DATE'2021-01-01', date_add(DATE'2021-01-01', "
            "timestampdiff(HOUR, TIMESTAMP'2021-01-01 10:59:00', TIMESTAMP'2021-01-01 11:00:00')))")
    assert _one(code) == [[0]]
    con = databricks_coverage.connect()
    assert con.sql(sql.duckdb_sql(sql.parse(code, "spark")[0])).fetchall() == [(0,)]


def test_a_diff_spelling_inside_a_string_is_left_alone():
    from fabric_desktop import sql
    assert sql.diff_spellings("SELECT 'datediff(DAY, a, b)', datediff(DAY, a, b)") == (
        "SELECT 'datediff(DAY, a, b)', timestampdiff(DAY, a, b)")


def test_only_a_three_argument_diff_is_timestampdiff():
    from fabric_desktop import sql
    # two arguments are datediff(end, start), whatever the first column is called
    assert sql.diff_spellings("SELECT datediff(day, d), datediff(DAY, f(a, b), g(c))") == (
        "SELECT datediff(day, d), timestampdiff(DAY, f(a, b), g(c))")


_T = "timestamp:2014-12-28T06:30:45.887000"


# Every example in Databricks' reference for its TIME and try_ date functions, with the value it shows
@pytest.mark.parametrize(("code", "want"), [
    ("SELECT time_from_seconds(52200.5)", "time:14:30:00.500000"), ("SELECT time_from_seconds(0)", "time:00:00:00"),
    ("SELECT time_from_millis(52200500)", "time:14:30:00.500000"), ("SELECT time_from_millis(0)", "time:00:00:00"),
    ("SELECT time_from_micros(52200500000)", "time:14:30:00.500000"), ("SELECT time_from_micros(0)", "time:00:00:00"),
    ("SELECT time_to_seconds(TIME'14:30:00.5')", "decimal:52200.500000"),
    ("SELECT time_to_seconds(TIME'00:00:00')", "decimal:0.000000"),
    ("SELECT time_to_millis(TIME'14:30:00.5')", 52200500), ("SELECT time_to_millis(TIME'00:00:00')", 0),
    ("SELECT time_to_micros(TIME'14:30:00.5')", 52200500000), ("SELECT time_to_micros(TIME'00:00:00')", 0),
    ("SELECT time_diff(HOUR, TIME'08:00:00', TIME'21:30:00')", 13),
    ("SELECT time_diff(MINUTE, TIME'08:00:00', TIME'08:45:30')", 45),
    ("SELECT time_diff(SECOND, TIME'10:00:00', TIME'10:00:30')", 30),
    ("SELECT time_diff(HOUR, TIME'21:00:00', TIME'08:00:00')", -13),
    ("SELECT time_trunc('HOUR', TIME'13:08:15.123')", "time:13:00:00"),
    ("SELECT time_trunc('MINUTE', TIME'13:08:15.123')", "time:13:08:00"),
    ("SELECT time_trunc('SECOND', TIME'13:08:15.123456')", "time:13:08:15"),
    ("SELECT time_trunc('MILLISECOND', TIME'13:08:15.123456')", "time:13:08:15.123000"),
    ("SELECT to_time('10:30:00')", "time:10:30:00"), ("SELECT to_time('10:30:00', 'HH:mm:ss')", "time:10:30:00"),
    ("SELECT to_time('10.30.00', 'HH.mm.ss')", "time:10:30:00"),
    ("SELECT try_to_time('10:30:00')", "time:10:30:00"), ("SELECT try_to_time('10:30:00', 'HH:mm:ss')", "time:10:30:00"),
    ("SELECT try_to_time('not-a-time')", None), ("SELECT try_to_time('10:30:00', 'HH:mm:ss') IS NOT NULL", True),
    ("SELECT try_to_date('2016-12-31')", "date:2016-12-31"),
    ("SELECT try_to_date('31/12/2016', 'dd/MM/yyyy')", "date:2016-12-31"),
    ("SELECT try_to_date('2016-12-32', 'yyyy-MM-dd')", None),
    ("SELECT try_make_timestamp_ntz(2014, 12, 28, 6, 30, 45.887)", _T),
    ("SELECT try_make_timestamp_ntz(2014, 13, 28, 6, 30, 45.887)", None),
    ("SELECT try_make_timestamp_ntz(DATE'2014-12-28', TIME'06:30:45.887')", _T),
    ("SELECT try_make_timestamp_ltz(2014, 13, 28, 6, 30, 45.887)", None),
    ("SELECT try_make_timestamp_ltz(DATE'2014-12-28', TIME'06:30:45.887')", _T),
    # Databricks shows 2014-12-27 21:30:45.887 in a Los Angeles session: 05:30:45.887 UTC, the session here
    ("SELECT try_make_timestamp_ltz(2014, 12, 28, 6, 30, 45.887, 'CET')", "timestamp:2014-12-28T05:30:45.887000"),
])
def test_databricks_time_examples_give_its_values(code, want):
    assert _one(code) == [[want]]


@pytest.mark.parametrize(("code", "match"), [
    ("SELECT to_time('not-a-time')", "not-a-time"),  # Databricks: CAST_INVALID_INPUT
    ("SELECT time_from_seconds(86400)", "0 to under 86400"),
    ("SELECT time_trunc('DAY', TIME'13:08:15')", "HOUR, MINUTE, SECOND"),
    ("SELECT parse_timestamp('2024-12-09T19:30:01')", "can't run on this PC.*#extended"),
])
def test_a_time_databricks_stops_on_or_this_pc_cant_read_stops(code, match):
    with pytest.raises(Exception, match=match):
        _one(code)


# Every example in Databricks' reference for its IP address functions, with the value it shows
@pytest.mark.parametrize(("code", "want"), [
    ("SELECT hex(ip_as_binary('192.168.1.1'))", 'C0A80101'),
    ("SELECT hex(ip_as_binary('2001:db8::1'))", '20010DB8000000000000000000000001'),
    ("SELECT hex(ip_as_binary('192.168.1.5/24'))", 'C0A8010018'),
    ("SELECT hex(ip_as_binary(X'C0A80101'))", 'C0A80101'),
    ('SELECT ip_as_binary(NULL)', None),
    ("SELECT ip_as_string('192.168.1.1')", '192.168.1.1'),
    ("SELECT ip_as_string('2001:0db8:0000:0000:0000:0000:0000:0001')", '2001:db8::1'),
    ("SELECT ip_as_string('192.168.1.5/24')", '192.168.1.0/24'),
    ("SELECT ip_as_string(X'C0A80101')", '192.168.1.1'),
    ("SELECT ip_as_string(X'20010DB8000000000000000000000001')", '2001:db8::1'),
    ('SELECT ip_as_string(NULL)', None),
    ("SELECT ip_cidr('192.168.1.100/24')", '192.168.1.0/24'),
    ("SELECT ip_cidr('2001:0db8:0000:0000:0000:0000:0000:0001/32')", '2001:db8::/32'),
    ("SELECT ip_cidr('::ffff:192.0.2.128/120')", '::ffff:192.0.2.0/120'),
    ("SELECT hex(ip_cidr(X'C0A8016418'))", 'C0A8010018'),
    ('SELECT ip_cidr(NULL)', None),
    ("SELECT ip_cidr_contains('192.168.1.0/24', '192.168.2.1')", False),
    ("SELECT ip_cidr_contains('2001:db8::/32', '2001:db8::1')", True),
    ("SELECT ip_cidr_contains('::ffff:192.168.1.0/120', '192.168.1.100')", True),
    ("SELECT ip_cidr_contains(X'C0A8010018', X'C0A80164')", True),
    ("SELECT ip_cidr_contains('192.168.1.0/24', X'C0A80164')", True),
    ("SELECT ip_cidr_contains(NULL, '192.168.1.1')", None),
    ("SELECT ip_cidr_contains('192.168.1.0/24', NULL)", None),
    ('SELECT ip_cidr_contains(NULL, NULL)', None),
    ("SELECT ip_host('192.168.1.5')", '192.168.1.5'),
    ("SELECT ip_host('2001:0db8:0000:0000:0000:0000:0000:0001')", '2001:db8::1'),
    ("SELECT ip_host('2001:DB8::1')", '2001:db8::1'),
    ("SELECT ip_host('::ffff:192.0.2.128')", '::ffff:192.0.2.128'),
    ("SELECT hex(ip_host(X'C0A80101'))", 'C0A80101'),
    ('SELECT ip_host(NULL)', None),
    ("SELECT ip_network('192.168.1.100/24')", '192.168.1.0'),
    ("SELECT ip_network('10.32.1.5/16')", '10.32.0.0'),
    ("SELECT ip_network('2001:db8::1/32')", '2001:db8::'),
    ("SELECT ip_network('::ffff:192.0.2.128/120')", '::ffff:192.0.2.0'),
    ("SELECT hex(ip_network(X'C0A8016418'))", 'C0A80100'),
    ('SELECT ip_network(NULL)', None),
    ("SELECT ip_network_first('192.168.1.100/24')", '192.168.1.0'),
    ("SELECT ip_network_first('10.32.1.5/16')", '10.32.0.0'),
    ("SELECT ip_network_first('2001:db8::1/32')", '2001:db8::'),
    ("SELECT ip_network_first('::ffff:192.0.2.128/120')", '::ffff:192.0.2.0'),
    ("SELECT hex(ip_network_first(X'C0A8016418'))", 'C0A80100'),
    ('SELECT ip_network_first(NULL)', None),
    ("SELECT ip_network_last('192.168.1.0/24')", '192.168.1.255'),
    ("SELECT ip_network_last('2001:db8::/32')", '2001:db8:ffff:ffff:ffff:ffff:ffff:ffff'),
    ("SELECT ip_network_last('::ffff:192.0.2.0/120')", '::ffff:192.0.2.255'),
    ("SELECT hex(ip_network_last(X'C0A8016418'))", 'C0A801FF'),
    ('SELECT ip_network_last(NULL)', None),
    ("SELECT ip_prefix_length('192.168.1.1/24')", 24),
    ("SELECT ip_prefix_length('2001:db8::1/64')", 64),
    ("SELECT ip_prefix_length('::ffff:192.0.2.1/96')", 96),
    ("SELECT ip_prefix_length(X'C0A8010118')", 24),
    ('SELECT ip_prefix_length(NULL)', None),
    ("SELECT ip_version('192.168.1.5')", 4),
    ("SELECT ip_version('192.168.1.0/24')", 4),
    ("SELECT ip_version('2001:db8::1')", 6),
    ("SELECT ip_version('2001:db8::/32')", 6),
    ("SELECT ip_version('::ffff:192.168.1.1')", 6),
    ("SELECT ip_version(X'C0A80101')", 4),
    ('SELECT ip_version(NULL)', None),
    ("SELECT hex(try_ip_as_binary('192.168.1.1'))", 'C0A80101'),
    ("SELECT hex(try_ip_as_binary('192.168.1.5/24'))", 'C0A8010018'),
    ("SELECT hex(try_ip_as_binary('2001:db8::1'))", '20010DB8000000000000000000000001'),
    ("SELECT hex(try_ip_as_binary('::ffff:192.168.1.1'))", '00000000000000000000FFFFC0A80101'),
    ("SELECT hex(try_ip_as_binary(X'C0A80101'))", 'C0A80101'),
    ("SELECT try_ip_as_binary('invalid')", None),
    ("SELECT try_ip_as_binary(X'')", None),
    ('SELECT try_ip_as_binary(NULL)', None),
    ("SELECT try_ip_as_string('192.168.1.1')", '192.168.1.1'),
    ("SELECT try_ip_as_string('2001:0db8:0000:0000:0000:0000:0000:0001')", '2001:db8::1'),
    ("SELECT try_ip_as_string('192.168.1.5/24')", '192.168.1.0/24'),
    ("SELECT try_ip_as_string(X'C0A80101')", '192.168.1.1'),
    ("SELECT try_ip_as_string(X'20010DB8000000000000000000000001')", '2001:db8::1'),
    ("SELECT try_ip_as_string('invalid')", None),
    ("SELECT try_ip_as_string(X'')", None),
    ('SELECT try_ip_as_string(NULL)', None),
    ("SELECT try_ip_cidr('192.168.1.100/24')", '192.168.1.0/24'),
    ("SELECT try_ip_cidr('2001:0db8:0000:0000:0000:0000:0000:0001/32')", '2001:db8::/32'),
    ("SELECT try_ip_cidr('::ffff:192.0.2.128/120')", '::ffff:192.0.2.0/120'),
    ("SELECT hex(try_ip_cidr(X'C0A8016418'))", 'C0A8010018'),
    ("SELECT try_ip_cidr('invalid')", None),
    ("SELECT try_ip_cidr(X'')", None),
    ('SELECT try_ip_cidr(NULL)', None),
    ("SELECT try_ip_host('192.168.1.5')", '192.168.1.5'),
    ("SELECT try_ip_host('2001:0db8:0000:0000:0000:0000:0000:0001')", '2001:db8::1'),
    ("SELECT try_ip_host('2001:DB8::1')", '2001:db8::1'),
    ("SELECT try_ip_host('::ffff:192.0.2.128')", '::ffff:192.0.2.128'),
    ("SELECT hex(try_ip_host(X'C0A80101'))", 'C0A80101'),
    ("SELECT try_ip_host('invalid')", None),
    ("SELECT try_ip_host(X'')", None),
    ('SELECT try_ip_host(NULL)', None),
])
def test_databricks_ip_examples_give_its_values(code, want):
    assert _one(code) == [[want]]


@pytest.mark.parametrize(("code", "error"), [
    ("SELECT ip_as_binary('invalid.ip')", 'INVALID_IP_ADDRESS_OR_CIDR_BLOCK'),
    ("SELECT ip_as_string('invalid.ip')", 'INVALID_IP_ADDRESS_OR_CIDR_BLOCK'),
    ("SELECT ip_cidr('192.168.1.5')", 'INVALID_CIDR_BLOCK'),
    ("SELECT ip_cidr_contains('invalid', '192.168.1.1')", 'INVALID_CIDR_BLOCK'),
    ("SELECT ip_host('invalid.ip')", 'INVALID_IP_ADDRESS'),
    ("SELECT ip_network('192.168.1.5')", 'INVALID_CIDR_BLOCK'),
    ("SELECT ip_network_first('192.168.1.5')", 'INVALID_CIDR_BLOCK'),
    ("SELECT ip_network_last('192.168.1.5')", 'INVALID_CIDR_BLOCK'),
    ("SELECT ip_prefix_length('192.168.1.5')", 'INVALID_CIDR_BLOCK'),
    ("SELECT ip_version('invalid.ip')", 'INVALID_IP_ADDRESS_OR_CIDR_BLOCK'),
])
def test_a_bad_ip_stops_with_databricks_error_class(code, error):
    with pytest.raises(duckdb.Error, match=re.escape(f"[{error}]")):
        _one(code)


def test_an_ipv6_zone_is_not_an_address():
    assert _one("SELECT try_ip_host('fe80::1%eth0')") == [[None]]
    with pytest.raises(duckdb.Error, match=re.escape("[INVALID_IP_ADDRESS]")):
        _one("SELECT ip_host('fe80::1%eth0')")


def test_an_ip_column_gives_strings_and_a_binary_one_says_how_to_mark_it():
    assert _one("SELECT ip_host(c) FROM VALUES ('10.0.0.1'), (NULL), ('::1') AS t(c)") == [
        ["10.0.0.1"], ["::1"], [None]]
    assert _one("SELECT ip_host(CAST(c AS BINARY)) FROM VALUES (X'C0A80101') AS t(c)") == [["binary:c0a80101"]]
    with pytest.raises(duckdb.Error, match=r"CAST\(\.\.\. AS BINARY\)"):
        _one("SELECT ip_host(c) FROM VALUES (X'C0A80101') AS t(c)")


def test_the_engine_session_has_the_ip_functions():
    from fabric_desktop.engines.contract import SessionConfig
    from fabric_desktop.engines.local_sql import DuckDBSession
    session = DuckDBSession(SessionConfig(notebook="ip"))
    try:
        assert session._con.sql("SELECT __kf_ip('ip_version:fixed', 'VARCHAR', hex('::1'))").fetchall() == [("6",)]
    finally:
        session.close()
