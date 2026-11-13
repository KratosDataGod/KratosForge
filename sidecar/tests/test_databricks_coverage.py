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
    assert status["abs"] == "runs" and status["rand"] == "runs" and status["version"] == "not checked"
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
    assert status["session_user"] == "not checked"  # DuckDB accepts it; its value is the session's
    assert status["iff"] == "runs"  # Databricks' own example, value checked
    notes = {r["name"]: r["note"] for r in rows}
    assert status["vector_norm"] == "runs" and "FLOAT in Databricks" in notes["vector_norm"]  # its listed note
    assert status["vector_avg"] == "refused"  # no element-wise aggregate here, said by name
    assert status["from_xml"] == "refused"  # no XML reader here, said by name
    assert [name for name, s in status.items() if s == "fails"] == []  # every function runs, differs, or is refused


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
    assert status["cube"] == "not checked"  # runs here; no value check yet
    for name in ("try_cast", "percentile_cont", "percentile_disc", "timestampdiff", "timestampadd"):
        assert status[name] == "runs", name  # Databricks' own example, value checked
    assert status["collate"] == "runs"  # UTF8_LCASE is DuckDB's NOCASE


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


# Every example in Databricks' reference for the byte-level string functions, with the value it shows
@pytest.mark.parametrize(("code", "want"), [
    ("SELECT is_valid_utf8('Spark')", True),
    ("SELECT is_valid_utf8('SQL' COLLATE UTF8_LCASE)", True),
    ("SELECT is_valid_utf8(x'61')", True),
    ("SELECT is_valid_utf8(x'80')", False),
    ("SELECT is_valid_utf8(x'61C262')", False),
    ("SELECT is_valid_utf8(NULL)", None),
    ("SELECT make_valid_utf8('Spark')", "Spark"),
    ("SELECT make_valid_utf8('SQL' COLLATE UTF8_LCASE)", "SQL"),
    ("SELECT make_valid_utf8(x'61')", "a"),
    ("SELECT make_valid_utf8(x'80')", "\ufffd"),
    ("SELECT make_valid_utf8(x'61C262')", "a\ufffdb"),
    ("SELECT validate_utf8('Spark')", "Spark"),
    ("SELECT validate_utf8('SQL' COLLATE UTF8_LCASE)", "SQL"),
    ("SELECT validate_utf8(x'61')", "a"),
    ("SELECT try_validate_utf8('Spark')", "Spark"),
    ("SELECT try_validate_utf8('SQL' COLLATE UTF8_LCASE)", "SQL"),
    ("SELECT try_validate_utf8(x'61')", "a"),
    ("SELECT try_validate_utf8(x'80')", None),
    ("SELECT try_validate_utf8(x'61C262')", None),
    ("SELECT url_decode('http%3A%2F%2Fspark.apache.org%2Fpath%3Fquery%3D1')", "http://spark.apache.org/path?query=1"),
    ("SELECT try_url_decode('http%3A%2F%2Fspark.apache.org%2Fpath%3Fquery%3D1')",
     "http://spark.apache.org/path?query=1"),
    ("SELECT try_url_decode('http%3A%2F%2spark.apache.org')", None),
    ("SELECT url_decode('a+b%20c%C3%A9')", "a b cé"),  # + is a space; the bytes are UTF-8
    ("SELECT url_decode('%80')", "\ufffd"),  # java.net.URLDecoder's U+FFFD, where DuckDB's url_decode stops
    ("SELECT url_decode(NULL)", None),
    ("SELECT hex(hmac('key', 'message'))", "6E9EF29B75FFFC5B7ABAE527D58FDADB2FE42E7219011976917343065F58ED4A"),
    ("SELECT hex(hmac('key', 'message', 'SHA-512'))",
     ("E477384D7CA229DD1426E64B63EBF2D36EBD6D7E669A6735424E72EA6C01D3F8B56EB39C36D8232F5427999B8D1A3F9CD1128FC69F4D"
     "75B434216810FA367E98")),
    ("SELECT hex(hmac(hmac('key', 'message'), 'message2'))",
     "28042756E0362D954B61661BB4DEC9FF9F6C970D346CAEB6DB36ED7B735AB38C"),
    ("SELECT hmac(CAST(NULL AS BINARY), 'message')", None),
    ("SELECT hex(hmac(CAST('key' AS BINARY), x'6D657373616765', 'sha256'))",
     "6E9EF29B75FFFC5B7ABAE527D58FDADB2FE42E7219011976917343065F58ED4A"),
    ("SELECT bit_reverse(64Y)", 2),
    ("SELECT bit_reverse(64L)", 144115188075855872),
    ("SELECT bit_reverse(-1)", -1),
    ("SELECT bit_reverse(-2)", 2147483647),
    ("SELECT bit_reverse(NULL)", None),
    ("SELECT 'Café' = 'café' COLLATE UTF8_LCASE", True),
    ("SELECT 'Cafe' = 'café' COLLATE UTF8_LCASE", False),
    ("SELECT 'a' COLLATE UTF8_BINARY = 'A'", False),
    ("SELECT 'a' COLLATE utf8_lcase = 'A'", True),
])
def test_databricks_string_examples_give_its_values(code, want):
    assert _one(code) == [[want]]


@pytest.mark.parametrize(("code", "error"), [
    ("SELECT validate_utf8(x'80')", "[INVALID_UTF8_STRING]"),
    ("SELECT url_decode('http%3A%2F%2spark.apache.org')", "[CANNOT_DECODE_URL]"),
    ("SELECT url_decode('100%')", "[CANNOT_DECODE_URL]"),
    ("SELECT hmac('key', 'message', 'SHA-3')", "[INVALID_PARAMETER_VALUE.HMAC_ALGORITHM]"),
    ("SELECT hmac('', 'message')", "[INVALID_PARAMETER_VALUE.HMAC_CRYPTO_ERROR]"),
])
def test_bad_string_input_stops_with_databricks_error_class(code, error):
    with pytest.raises(duckdb.Error, match=re.escape(error)):
        _one(code)


@pytest.mark.parametrize("code", [
    "SELECT zstd_compress('Apache Spark')",
    "SELECT zstd_decompress(x'DEADBEEF')",
    "SELECT try_zstd_decompress(x'DEADBEEF')",
    "SELECT 'Café' = 'cafe' COLLATE UNICODE_CI",
])
def test_zstd_and_icu_collations_are_refused_by_name(code):
    with pytest.raises(ValueError, match="can't run on this PC the way it runs in Databricks"):
        _one(code)


def test_url_decode_reads_escapes_as_java_does():
    from fabric_desktop import databricks_bytes
    assert databricks_bytes.url_decode("%+1%-0", False) == "\x01\x00"  # Integer.parseInt(.., 16) takes a sign
    assert databricks_bytes.url_decode("%-1", True) is None


def test_byte_functions_read_a_column_of_either_type():
    assert _one("SELECT is_valid_utf8(c), make_valid_utf8(c) FROM VALUES (X'61'), (X'FF'), (NULL) AS t(c)") == [
        [False, "\ufffd"], [None, None], [True, "a"]]  # run() sorts the rows
    assert _one("SELECT bit_reverse(c) FROM VALUES (CAST(1 AS SMALLINT)) AS t(c)") == [[-32768]]


def test_the_engine_session_has_the_byte_functions():
    from fabric_desktop.engines.contract import SessionConfig
    from fabric_desktop.engines.local_sql import DuckDBSession
    session = DuckDBSession(SessionConfig(notebook="bytes"))
    try:
        assert session._con.sql("SELECT __kf_bit_reverse('TINYINT', 64)").fetchall() == [(2,)]
    finally:
        session.close()


_STORE = "parse_json('{\"key\": 123, \"data\": [4, {\"a\": \"hello\"}, \"str\"]}')"


# Every example in Databricks' reference for its VARIANT functions, with the value it shows
@pytest.mark.parametrize(("code", "want"), [
    ("SELECT is_variant_null(v:key), is_variant_null(v:a) FROM VALUES(parse_json('{\"key\": null, \"a\": 1}')) AS T(v)",
     [[True, False]]),
    ("SELECT is_variant_null(v:key), v:key IS NULL FROM VALUES(parse_json('{\"key\": null}')) AS T(v)", [[True, False]]),
    ("SELECT parse_json('{\"key\": 123, \"data\": [4, 5, \"str\"]}')", [['{"data":[4,5,"str"],"key":123}']]),
    ("SELECT parse_json('123')", [["123"]]),
    ("SELECT parse_json(NULL)", [[None]]),
    ("SELECT try_parse_json('{\"key\": 123, \"data\": [4, 5, \"str\"]}')", [['{"data":[4,5,"str"],"key":123}']]),
    ("SELECT try_parse_json('123')", [["123"]]),
    ("SELECT try_parse_json('{ bad }')", [[None]]),
    (f"SELECT variant_get({_STORE}, '$.data[1].a', 'string')", [["hello"]]),
    (f"SELECT variant_get({_STORE}, '$.missing', 'int')", [[None]]),
    (f"SELECT try_variant_get({_STORE}, '$.data[1].a', 'string')", [["hello"]]),
    (f"SELECT try_variant_get({_STORE}, '$.missing', 'int')", [[None]]),
    (f"SELECT try_variant_get({_STORE}, '$.key', 'boolean')", [[None]]),
    ("SELECT to_variant_object(named_struct('a', 1, 'b', 2))", [['{"a":1,"b":2}']]),
    ("SELECT to_variant_object(array(1, 2, 3))", [["[1,2,3]"]]),
    ("SELECT to_variant_object(array(named_struct('a', 1)))", [['[{"a":1}]']]),
    ("SELECT to_variant_object(array(map('a', 2)))", [['[{"a":2}]']]),
    ("SELECT * FROM variant_explode(parse_json('[1, \"a\", {\"b\": \"hello\"}]'))",
     [[0, None, "1"], [1, None, '"a"'], [2, None, '{"b":"hello"}']]),
    ("SELECT * FROM variant_explode(parse_json('{\"foo\":1,\"bar\":\"hello\"}'))", [[0, "bar", '"hello"'], [1, "foo", "1"]]),
    (("SELECT t.value AS outer, u.value AS inner FROM variant_explode(parse_json('[[1, 2], [3, 4]]')) AS t, "
     "LATERAL variant_explode(t.value) AS u"), [["[1,2]", "1"], ["[1,2]", "2"], ["[3,4]", "3"], ["[3,4]", "4"]]),
    (("SELECT s.id, e.pos, e.key, e.value FROM VALUES (1, parse_json('[{\"a\":1},{\"b\":2}]')), (2, parse_json('[]')), "
     "(3, parse_json('{\"x\":10,\"y\":20}')) AS s(id, v), LATERAL variant_explode(s.v) AS e"),
     [[1, 0, None, '{"a":1}'], [1, 1, None, '{"b":2}'], [3, 0, "x", "10"], [3, 1, "y", "20"]]),
    ("SELECT * FROM variant_explode_outer(parse_json('[1, \"a\", {\"b\": \"hello\"}]'))",
     [[0, None, "1"], [1, None, '"a"'], [2, None, '{"b":"hello"}']]),
    ("SELECT value FROM variant_explode_outer(null) AS t(pos, key, value)", [[None]]),
    ("SELECT value FROM variant_explode_outer(parse_json('123')) AS t(pos, key, value)", [[None]]),
    (("SELECT t.value AS outer, u.value AS inner FROM variant_explode_outer(parse_json('[[1, 2], [3, 4]]')) AS t, "
     "LATERAL variant_explode(t.value) AS u"), [["[1,2]", "1"], ["[1,2]", "2"], ["[3,4]", "3"], ["[3,4]", "4"]]),
    ("SELECT PARSE_JSON('{ \"price\": 5 }'):price, PARSE_JSON('{ \"price\": 5 }'):price::decimal(5,2)",
     [["5", "decimal:5.00"]]),
    (("SELECT parse_json(NULL) IS NULL, parse_json('null') IS NULL, parse_json('{ \"field_a\": null }'):field_a IS NULL, "
     "parse_json('{ \"field_a\": null }'):missing IS NULL"), [[True, False, False, True]]),
])
def test_databricks_variant_examples_give_its_values(code, want):
    assert _one(code) == want


@pytest.mark.parametrize(("text", "printed"), [
    ('{"a" : 0.01000}', '{"a":0.01}'),  # Databricks drops a decimal's trailing zeros
    ("1.0", "1"),
    ("-0.0", "0"),
    ("1e2", "100.0"),  # an exponent makes a DOUBLE, printed as Java prints one
    ("1E7", "1.0E7"),
    ("1.5e-5", "1.5E-5"),
    ("12345678901234567890", "12345678901234567890"),  # past BIGINT: a DECIMAL
    ("0.10000000000000000001", "0.10000000000000000001"),  # a DECIMAL keeps all 38 digits
    ('"tab\\there\\u0001"', '"tab\\there\\u0001"'),
    ('{"b": 1, "a": {"d": [true, null], "c": "x"}}', '{"a":{"c":"x","d":[true,null]},"b":1}'),
])
def test_parse_json_prints_as_databricks_keeps_a_variant(text, printed):
    from fabric_desktop import databricks_variant
    assert databricks_variant.parse_json(text, False) == printed


@pytest.mark.parametrize(("code", "error"), [
    ("SELECT parse_json('{ bad }')", "[MALFORMED_RECORD_IN_PARSING]"),
    ("SELECT parse_json('NaN')", "[MALFORMED_RECORD_IN_PARSING]"),
    ("SELECT parse_json('{\"a\": 1, \"a\": 2}')", "[VARIANT_DUPLICATE_KEY]"),
    (f"SELECT variant_get({_STORE}, '$.key', 'array<int>')", "[INVALID_VARIANT_CAST]"),
    (f"SELECT variant_get({_STORE}, '$.key', 'tinyint')", None),
    (f"SELECT variant_get({_STORE}, '$.data', 'int')", "[INVALID_VARIANT_CAST]"),
    ("SELECT variant_get(parse_json('300'), '$', 'tinyint')", "[INVALID_VARIANT_CAST]"),
    ("SELECT variant_get(parse_json('1234.5'), '$', 'decimal(4,1)')", "[INVALID_VARIANT_CAST]"),
    ("SELECT variant_get('{\"a\": 1}', '$.a', 'int')", "[DATATYPE_MISMATCH]"),
    ("SELECT to_variant_object(1)", "[DATATYPE_MISMATCH]"),
])
def test_bad_variant_input_stops_with_databricks_error_class(code, error):
    if error is None:  # 123 fits a TINYINT
        assert _one(code) == [[123]]
        return
    with pytest.raises(duckdb.Error, match=re.escape(error)):
        _one(code)


@pytest.mark.parametrize(("code", "want"), [
    ("SELECT variant_get(parse_json('{\"a\": {\"b c\": [1, 2.50]}}'), '$.a[\"b c\"][1]', 'decimal(5,2)')", "decimal:2.50"),
    ("SELECT variant_get(parse_json('{\"a\": 1}'), '$', 'string')", '{"a":1}'),
    ("SELECT variant_get(parse_json('{\"a\": 1}'), '$.a', 'variant')", "1"),
    ("SELECT variant_get(parse_json('0.10000000000000000001'), '$', 'string')", "0.10000000000000000001"),
    ("SELECT try_variant_get(parse_json('300'), '$', 'tinyint')", None),
    ("SELECT variant_get(parse_json('{\"a\": null}'), '$.a', 'int')", None),
])
def test_variant_get_walks_the_path_itself(code, want):
    assert _one(code) == [[want]]


@pytest.mark.parametrize("code", [
    "SELECT variant_get(parse_json('\"5\"'), '$', 'int')",  # a STRING to a number
    "SELECT variant_get(parse_json('1.5'), '$', 'int')",  # a DECIMAL to an integer
    "SELECT variant_get(parse_json('[1]'), '$', 'array<int>')",
    "SELECT variant_get(parse_json('\"2024-01-01\"'), '$', 'date')",
    "SELECT to_variant_object(named_struct('a', 1.5D))",
    "SELECT schema_of_variant(parse_json('1'))",
    "SELECT schema_of_variant_agg(v) FROM VALUES (parse_json('1')) AS t(v)",
])
def test_a_variant_cast_this_pc_cannot_tell_is_refused_by_name(code):
    with pytest.raises((ValueError, duckdb.Error), match="can't run on this PC the way it runs in Databricks"):
        _one(code)


def test_a_variant_path_must_be_a_path():
    with pytest.raises(ValueError, match=re.escape("[INVALID_VARIANT_GET_PATH]")):
        _one("SELECT variant_get(parse_json('[1]'), '$[*]', 'int')")
    with pytest.raises(ValueError, match="is a table function"):
        _one("SELECT variant_explode(parse_json('[1]'))")


def test_the_engine_session_has_the_variant_functions():
    from fabric_desktop.engines.contract import SessionConfig
    from fabric_desktop.engines.local_sql import DuckDBSession
    session = DuckDBSession(SessionConfig(notebook="variant"))
    try:
        assert session._con.sql("SELECT __kf_parse_json('{\"b\": 1, \"a\": 2}', false)").fetchall() == [
            ('{"a":2,"b":1}',)]
    finally:
        session.close()


def test_try_parse_json_stops_on_a_repeated_key():  # Databricks refuses to build that VARIANT at all
    with pytest.raises(duckdb.Error, match=re.escape("[VARIANT_DUPLICATE_KEY]")):
        _one("SELECT try_parse_json('{\"a\": 1, \"a\": 2}')")


@pytest.mark.parametrize("text", [
    "[" * 257 + "]" * 257,  # deeper than this PC reads
    "[" * 5000,  # deep and broken: refused before json.loads could recurse
    "1e400",  # past DOUBLE's range
])
def test_hostile_json_stops_by_name_even_in_try(text):
    from fabric_desktop import databricks_variant
    for safe in (False, True):
        with pytest.raises(ValueError, match="can't run on this PC the way it runs in Databricks"):
            databricks_variant.parse_json(text, safe)


def test_long_input_reads_and_errors_stay_short():
    from fabric_desktop import databricks_variant
    assert databricks_variant.parse_json("[" * 256 + "]" * 256, False) == "[" * 256 + "]" * 256
    assert databricks_variant.parse_json('["[{"]', False) == '["[{"]'  # brackets in a string don't nest
    digits = "1" * 5000  # past int()'s 4300 digits: a DOUBLE, not a ValueError
    assert databricks_variant.parse_json("0." + digits[:300], False).startswith("0.111")
    with pytest.raises(ValueError, match="past DOUBLE's range") as error:
        databricks_variant.parse_json(digits, False)
    assert len(str(error.value)) < 300
    key = "k" * 1000
    with pytest.raises(ValueError, match=re.escape("[VARIANT_DUPLICATE_KEY]")) as error:
        databricks_variant.parse_json(f'{{"{key}": 1, "{key}": 2}}', False)
    assert len(str(error.value)) < 300
    with pytest.raises(ValueError, match=re.escape("[INVALID_VARIANT_CAST]")) as error:
        databricks_variant.variant_get("[" + ",".join(["1"] * 1000) + "]", "JSON", "[]", "int", False)
    assert len(str(error.value)) < 300


_COUNTER = ("('http_requests', TIMESTAMP '2026-01-01T00:00:00', 100), ('http_requests', TIMESTAMP '2026-01-01T00:01:00', 200), "
            "('http_requests', TIMESTAMP '2026-01-01T00:02:00', 400)")


# Every example in Databricks' reference for these, with the value it shows (timestamps in UTC)
@pytest.mark.parametrize(("code", "want"), [
    ("SELECT try_make_interval(100, 11)", [["interval:3138912000.0"]]),  # 100 years 11 months
    ("SELECT CAST(try_make_interval(100, 11) AS STRING)", [["100 years 11 months"]]),
    ("SELECT try_make_interval(0)", [["interval:0.0"]]),  # 0 seconds
    ("SELECT try_make_interval(2147483647, 0)", [[None]]),
    ("SELECT CAST(try_make_interval(1, 2, 1, 1, 12, 30, 01.001001) AS STRING)", [["1 year 2 months 8 days 12:30:01.001001"]]),
    ("SELECT try_make_interval(NULL, 1)", [[None]]),
    ("SELECT try_make_timestamp(2014, 12, 28, 6, 30, 45.887)", [["timestamp:2014-12-28T06:30:45.887000"]]),
    ("SELECT try_make_timestamp(2014, 13, 28, 6, 30, 45.887)", [[None]]),
    ("SELECT try_make_timestamp(DATE'2014-12-28', TIME'06:30:45.887')", [["timestamp:2014-12-28T06:30:45.887000"]]),
    ("SELECT try_make_timestamp(DATE'2014-12-28')", [["timestamp:2014-12-28T00:00:00"]]),
    ("SELECT try_make_timestamp(2014, 12, 28, 6, 30, 60)", [["timestamp:2014-12-28T06:31:00"]]),  # 60 s: next minute
    ("SELECT string(timestamp_nanos(1230219000123456789))", [["2008-12-25 15:30:00.123456789"]]),
    ("SELECT string(timestamp_nanos(-1.5))", [["1969-12-31 23:59:59.999999998"]]),  # a fraction rounds down
    ("SELECT timestamp_nanos(NULL)", [[None]]),
    ("SELECT unix_nanos(TIMESTAMP'1970-01-01 00:00:01.123456789Z')", [["decimal:1123456789"]]),
    ("SELECT substring(hex(bitmap_and_agg(val)), 0, 6) FROM VALUES(x'F0'), (x'70'), (x'30') AS T(val)", [["300000"]]),
    ("SELECT octet_length(bitmap_and_agg(val)) FROM VALUES(x'F0'), (NULL), (x'30FF') AS T(val)", [[4096]]),
    ("SELECT substring(hex(bitmap_and_agg(val)), 0, 4) FROM VALUES(x'F0'), (x'30FF') AS T(val)", [["3000"]]),
    (f"SELECT m, t, counter_diff(c) OVER (PARTITION BY m ORDER BY t) FROM VALUES {_COUNTER} AS tab(m, t, c)",
     [["http_requests", "timestamp:2026-01-01T00:00:00", None], ["http_requests", "timestamp:2026-01-01T00:01:00", 100],
      ["http_requests", "timestamp:2026-01-01T00:02:00", 200]]),
    ((f"SELECT t, counter_diff(c) OVER (PARTITION BY m ORDER BY t) FROM VALUES {_COUNTER}, "
     "('http_requests', TIMESTAMP '2026-01-01T00:04:00', 50) AS tab(m, t, c)"),
     [["timestamp:2026-01-01T00:00:00", None], ["timestamp:2026-01-01T00:01:00", 100],
      ["timestamp:2026-01-01T00:02:00", 200], ["timestamp:2026-01-01T00:04:00", None]]),
    (("SELECT t, counter_diff(c, st) OVER (PARTITION BY m ORDER BY t) FROM VALUES "
     "('h', TIMESTAMP '2026-01-01T00:01:00', TIMESTAMP '2026-01-01T00:00:00', 100), "
     "('h', TIMESTAMP '2026-01-01T00:02:00', TIMESTAMP '2026-01-01T00:00:00', 200), "
     "('h', TIMESTAMP '2026-01-01T00:04:00', TIMESTAMP '2026-01-01T00:02:31', 400) AS tab(m, t, st, c)"),
     [["timestamp:2026-01-01T00:01:00", None], ["timestamp:2026-01-01T00:02:00", 100],
      ["timestamp:2026-01-01T00:04:00", None]]),
    # a NULL value gives NULL and is skipped; a NULL start_time doesn't reset
    (("SELECT t, counter_diff(c, st) OVER (ORDER BY t) FROM VALUES (1, 10, TIMESTAMP '2026-01-01'), (2, NULL, NULL), "
     "(3, 15, NULL), (4, 18, TIMESTAMP '2026-01-01') AS tab(t, c, st)"), [[1, None], [2, None], [3, 5], [4, 3]]),
])
def test_databricks_time_bitmap_and_counter_examples_give_its_values(code, want):
    assert _one(code) == want


@pytest.mark.parametrize(("code", "error"), [
    ("SELECT counter_diff(c) OVER (ORDER BY t) FROM VALUES (1, -1) AS tab(t, c)", "[COUNTER_DIFF_NEGATIVE_COUNTER_VALUE]"),
    (("SELECT counter_diff(c, st) OVER (ORDER BY t) FROM VALUES (1, 1, TIMESTAMP '2026-01-02'), "
     "(2, 2, TIMESTAMP '2026-01-01') AS tab(t, c, st)"), "[COUNTER_DIFF_START_TIME_DECREASED]"),
])
def test_counter_diff_stops_with_databricks_error_class(code, error):
    with pytest.raises(duckdb.Error, match=re.escape(error)):
        _one(code)


@pytest.mark.parametrize(("code", "message"), [
    ("SELECT counter_diff(1)", "[WINDOW_FUNCTION_WITHOUT_OVER_CLAUSE]"),
    ("SELECT counter_diff(c) OVER (PARTITION BY t) FROM VALUES (1, 1) AS tab(t, c)", "[WINDOW_FUNCTION_FRAME_NOT_ORDERED]"),
    ("SELECT try_reflect('java.util.UUID', 'randomUUID')", "runs no Java"),
    ("SELECT from_xml('<p><a>1</a></p>', 'a INT')", "no XML reader"),
    ("SELECT to_xml(named_struct('a', 1))", "no XML reader"),
    ("SELECT schema_of_xml('<p><a>1</a></p>')", "no XML reader"),
    ("SELECT schema_of_json_agg(j) FROM VALUES ('{\"a\": 1}') AS t(j)", "no merge"),
    ("SELECT bitmap_and_agg(v) FILTER (WHERE v IS NOT NULL) FROM VALUES (x'01') AS t(v)", "plain aggregate"),
])
def test_these_stop_by_name_before_running(code, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _one(code)


@pytest.mark.parametrize(("code", "message"), [
    ("SELECT timestamp_nanos(1e30)", "past the nanoseconds"),
    ("SELECT bitmap_and_agg(v) FROM VALUES (CAST(NULL AS BINARY)) AS t(v)", "isn't documented"),
])
def test_these_stop_by_name_as_they_run(code, message):
    with pytest.raises(duckdb.Error, match=re.escape(message)):
        _one(code)


def test_counter_diff_reads_a_named_window():
    assert _one("SELECT t, counter_diff(c) OVER w FROM VALUES (1, 10), (2, 15) AS tab(t, c) WINDOW w AS (ORDER BY t)") == [
        [1, None], [2, 5]]
    with pytest.raises(ValueError, match="write the window inline"):
        _one("SELECT counter_diff(c) OVER w FROM VALUES (1, 10) AS tab(t, c) WINDOW v AS (ORDER BY t)")


def test_unix_nanos_says_when_a_timestamp_is_past_its_range():
    assert _one("SELECT unix_nanos(NULL), unix_nanos(c), unix_nanos(d) FROM VALUES "
                "(TIMESTAMP '1970-01-01 00:00:02', CAST(NULL AS TIMESTAMP)) AS t(c, d)") == [
        [None, "decimal:2000000000", None]]
    with pytest.raises(duckdb.Error, match="past the nanoseconds"):
        _one("SELECT unix_nanos(TIMESTAMP'2262-04-12')")


@pytest.mark.parametrize(("code", "want"), [
    # Databricks' reference examples: short English day and month names (run() sorts the rows)
    ("SELECT dayname(DATE'2024-11-01' + CAST(offset AS INT)) AS days FROM range(8) AS t(offset)",
     [["Fri"], ["Fri"], ["Mon"], ["Sat"], ["Sun"], ["Thu"], ["Tue"], ["Wed"]]),
    ("SELECT monthname(DATE'2024-11-01'), monthname(NULL)", [["Nov", None]]),
    # listagg and string_agg join with no delimiter unless given one, and skip NULLs
    ("SELECT listagg(col) FROM VALUES ('a'), (NULL), ('c') AS tab(col)", [["ac"]]),
    ("SELECT string_agg(DISTINCT col) WITHIN GROUP (ORDER BY col) FROM VALUES ('a'), ('a'), ('b') AS tab(col)",
     [["ab"]]),
    ("SELECT listagg(col, ', ') WITHIN GROUP (ORDER BY col) FROM VALUES ('b'), ('a'), ('c') AS tab(col)",
     [["a, b, c"]]),
    # time() from a string and from a timestamp's time of day
    ("SELECT time('10:30:00'), time(TIMESTAMP'2026-10-29 16:45:30')", [["time:10:30:00", "time:16:45:30"]]),
])
def test_databricks_names_aggregates_and_time_give_its_values(code, want):
    assert _one(code) == want
