"""spark_decimals.py paths that the uncapped mutation run (batch 221, docs/quality/mutation-testing.md)
showed no test checked."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.spark_decimals import spark_decimal_types


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TEMP VIEW t AS SELECT DATE '2024-01-31' AS d, CAST(10 AS DECIMAL(10,2)) AS a, "
              "CAST(3 AS DECIMAL(10,2)) AS b, CAST(2 AS INTEGER) AS n")
    yield c
    c.close()


def answer(conn, query):
    statement = spark_decimal_types(sql.parse(query)[0], conn)
    return [str(t) for t in conn.sql(sql.duckdb_sql(statement)).types]


@pytest.mark.parametrize("expr", ["d + INTERVAL 1 DAY", "d - INTERVAL 2 MONTH", "INTERVAL 1 WEEK + d"])
def test_a_date_plus_or_minus_whole_days_is_a_date(conn, expr):
    assert answer(conn, f"SELECT {expr} AS v FROM t") == ["DATE"]


@pytest.mark.parametrize("cast, kind", [
    ("CAST(s AS INT)", "DECIMAL(21,2)"),
    ("CAST(s AS TINYINT)", "DECIMAL(14,2)"),
    ("CAST(trim(s) AS INT)", "DECIMAL(21,2)"),       # a cast whose SQL sqlglot can't type: its own type counts
    ("CAST(s || '' AS SMALLINT)", "DECIMAL(16,2)"),
])
def test_a_spark_cast_to_a_whole_number_is_that_integer_type(conn, cast, kind):
    conn.execute("CREATE TEMP VIEW u AS SELECT CAST(10 AS DECIMAL(10,2)) AS a, '7' AS s")
    assert answer(conn, f"SELECT a * {cast} AS v FROM u") == [kind]


def _big_sum(conn):
    # decimal(36,2) sums as decimal(38,2): 36 whole digits. 120 rows of 34 nines make 37.
    conn.execute("CREATE TEMP VIEW big AS SELECT CAST('9999999999999999999999999999999999.99' AS DECIMAL(36,2)) AS x "
                 "FROM range(120)")
    return sql.parse("SELECT sum(x) AS v FROM big")[0]


def test_a_sum_past_its_whole_digits_is_an_error_with_ansi_on(conn):
    with pytest.raises(duckdb.Error, match="(?i)overflow in sum of decimal"):
        conn.sql(sql.duckdb_sql(spark_decimal_types(_big_sum(conn), conn, ansi=True))).fetchall()


@pytest.mark.xfail(int(duckdb.__version__.split(".")[0]) >= 2, strict=True,
                   reason="bug on DuckDB 2: its own sum stops at 38 digits with an error before the "
                          "check that gives Spark's NULL (ANSI off) runs")
def test_a_sum_past_its_whole_digits_is_null_with_ansi_off(conn):
    assert conn.sql(sql.duckdb_sql(spark_decimal_types(_big_sum(conn), conn))).fetchall() == [(None,)]


def test_spark_s_message_names_the_sum_overflow_on_duckdb_1(conn):
    if int(duckdb.__version__.split(".")[0]) >= 2:
        pytest.skip("DuckDB 2 stops the sum itself first (see the test above)")
    with pytest.raises(duckdb.Error, match=r"\[ARITHMETIC_OVERFLOW\] Overflow in sum of decimals"):
        conn.sql(sql.duckdb_sql(spark_decimal_types(_big_sum(conn), conn, ansi=True))).fetchall()


def test_a_frame_without_decimals_is_left_alone_unless_a_double_is_rounded():
    # Spark's decimal rules change only an expression with a decimal operand, or a double's round: a
    # frame with a double but no round, or a round but no double, is left to the DataFrame API as it is.
    from fabric_desktop.spark_decimals import spark_decimal_columns
    columns = [("f", "DOUBLE"), ("n", "INTEGER"), ("m", "BIGINT")]
    assert spark_decimal_columns(["n // m", "n * m", "ceil(f)"], columns) == [None, None, None]
    assert spark_decimal_columns(["round(n, 1)"], [("n", "INTEGER")]) == [None]


@pytest.fixture
def whole(conn):
    conn.execute("CREATE TEMP VIEW w AS SELECT CAST(100 AS TINYINT) AS ti, CAST(9223372036854775807 AS BIGINT) AS big, "
                 "CAST(-7.5 AS DECIMAL(3,1)) AS x, CAST(2.0 AS DECIMAL(2,1)) AS y")
    return conn


def rows(conn, query):
    return conn.sql(sql.duckdb_sql(spark_decimal_types(sql.parse(query)[0], conn))).fetchall()


@pytest.mark.parametrize("expr, value", [
    ("ti + ti", -56),                          # TINYINT wraps in 8 bits, as Java's byte
    ("big + 1", -9223372036854775808),         # BIGINT wraps in 64 bits
    ("big * big", 1),
])
def test_whole_numbers_wrap_in_their_own_width(whole, expr, value):
    assert rows(whole, f"SELECT {expr} AS v FROM w") == [(value,)]


def test_pmod_of_a_negative_decimal_is_positive_and_percent_keeps_the_sign(whole):
    from decimal import Decimal
    assert rows(whole, "SELECT pmod(x, y) AS v FROM w") == [(Decimal("0.5"),)]
    assert rows(whole, "SELECT x % y AS v FROM w") == [(Decimal("-1.5"),)]


@pytest.fixture
def mixed(conn):
    conn.execute("CREATE TEMP VIEW m AS SELECT CAST(7.5 AS DECIMAL(10,2)) AS a, CAST(2 AS INTEGER) AS n, "
                 "1.5::DOUBLE AS f, '7' AS s")
    return conn


def test_div_of_a_decimal_by_a_whole_number_truncates_toward_zero(mixed):
    assert answer(mixed, "SELECT a div n AS v FROM m") == ["BIGINT"]
    assert rows(mixed, "SELECT a div n AS v, -a div n AS w FROM m") == [(3, -3)]


@pytest.mark.parametrize("operand, value", [("f", (2, 1)), ("n", (2, 2))])
def test_ceil_and_floor_of_a_double_or_a_whole_number_are_bigints(mixed, operand, value):
    query = f"SELECT ceil({operand}) AS v, floor({operand}) AS w FROM m"
    assert answer(mixed, query) == ["BIGINT", "BIGINT"]
    assert rows(mixed, query) == [value]


def test_round_of_a_whole_number_keeps_duckdbs_type(mixed):
    assert answer(mixed, "SELECT round(n, 1) AS v FROM m") == ["INTEGER"]


def test_round_of_text_is_not_rewritten_as_a_doubles_round(mixed):
    # Only a value sqlglot types as DOUBLE or FLOAT takes the double's round; text is left as it was.
    statement = sql.parse("SELECT round(s, 1) AS v FROM m")[0]
    before = sql.duckdb_sql(statement.copy())
    assert sql.duckdb_sql(spark_decimal_types(statement, mixed)) == before


def test_a_frame_with_a_column_named_like_the_rewrites_lambdas_is_left_alone():
    from fabric_desktop.spark_decimals import spark_decimal_columns
    columns = [("a", "DECIMAL(10,2)"), ("kf_decv", "DECIMAL(10,2)")]
    assert spark_decimal_columns(["a * kf_decv", "a + a"], columns) == [None, None]


def test_a_doubles_round_past_20_places_is_refused_in_the_dataframe_api_too():
    from fabric_desktop.spark_decimals import Refused, spark_decimal_columns
    with pytest.raises(Refused, match=r"round\(\) of a double to 25 places can't run on this PC"):
        spark_decimal_columns(["round(f, 25)"], [("f", "DOUBLE")])


def test_a_known_date_plus_months_is_a_date_and_a_timestamp_plus_days_stays_a_timestamp(conn):
    assert answer(conn, "SELECT DATE '2024-01-31' + INTERVAL 1 MONTH AS v FROM t") == ["DATE"]
    conn.execute("CREATE TEMP VIEW ts AS SELECT TIMESTAMP '2024-01-31 10:00:00' AS stamp, CAST(1 AS DECIMAL(10,2)) AS a")
    assert answer(conn, "SELECT stamp + INTERVAL 1 DAY AS v, a + a AS w FROM ts") == ["TIMESTAMP", "DECIMAL(11,2)"]


def test_try_add_of_whole_numbers_is_null_past_the_range_not_wrapped(whole):
    assert rows(whole, "SELECT try_add(big, 1) AS v FROM w") == [(None,)]


def test_div_of_a_whole_number_by_a_decimal_truncates_toward_zero(mixed):
    assert answer(mixed, "SELECT -n div 0.75 AS v FROM m") == ["BIGINT"]
    assert rows(mixed, "SELECT -n div 0.75 AS v FROM m") == [(-2,)]


def test_a_date_plus_days_then_months_is_still_a_date_and_plus_hours_is_a_timestamp(conn):
    assert answer(conn, "SELECT (d + INTERVAL 1 DAY) + INTERVAL 1 MONTH AS v FROM t") == ["DATE"]
    assert answer(conn, "SELECT d + INTERVAL 1 HOUR AS v FROM t") == ["TIMESTAMP"]


def test_a_doubles_round_under_1_reads_every_digit_of_its_text(conn):
    # 4.499999999999999E-19 has digits past 10^-20: read at only 20 places it would be 4.5E-19 and round up.
    conn.execute("CREATE TEMP VIEW tiny AS SELECT 4.499999999999999e-19::DOUBLE AS x")
    assert rows(conn, "SELECT round(x, 19) AS v FROM tiny") == [(4e-19,)]


def test_a_sum_of_a_function_of_a_decimal_is_typed_by_that_function(conn):
    assert answer(conn, "SELECT sum(coalesce(a, b)) AS v FROM t") == ["DECIMAL(20,2)"]


def test_a_div_result_counts_as_a_bigint_in_a_later_product(mixed):
    # div is a BIGINT, which widens to decimal(20,0): decimal(10,2) * decimal(20,0) is decimal(31,2).
    assert answer(mixed, "SELECT a * (a div n) AS v FROM m") == ["DECIMAL(31,2)"]


def test_a_doubles_round_past_10_to_the_37_keeps_the_value(conn):
    conn.execute("CREATE TEMP VIEW huge AS SELECT 2e38::DOUBLE AS x")
    assert rows(conn, "SELECT round(x, 2) AS v FROM huge") == [(2e38,)]


def test_an_array_index_does_not_stop_the_array_itself_from_wrapping(whole):
    assert rows(whole, "SELECT array(big + 1)[0] AS v FROM w") == [(-9223372036854775808,)]


@pytest.mark.parametrize("expr", ["a * (n div 2)", "a * ceil(f)", "a * ceil(n)"])
def test_whole_number_div_and_ceil_count_as_bigints_later(mixed, expr):
    assert answer(mixed, f"SELECT {expr} AS v FROM m") == ["DECIMAL(31,2)"]


def test_div_of_wide_decimals_is_exact_when_their_digits_fit_38(conn):
    # decimal(30,10) div decimal(30,10): 20 whole digits at scale 10 fit 38, so no DOUBLE is used (it
    # would give 1234567890123456768).
    conn.execute("CREATE TEMP VIEW wide AS SELECT CAST('1234567890123456789.1234567891' AS DECIMAL(30,10)) AS d, "
                 "CAST(1 AS DECIMAL(30,10)) AS one")
    assert rows(conn, "SELECT d div one AS v FROM wide") == [(1234567890123456789,)]


@pytest.mark.xfail(strict=True, reason="bug: the smallest BIGINT div -1 stops with DuckDB's overflow error; "
                                       "Spark with ANSI off gives -9223372036854775808")
def test_the_smallest_bigint_div_minus_one_is_itself(conn):
    conn.execute("CREATE TEMP VIEW lo AS SELECT CAST(-9223372036854775808 AS BIGINT) AS lo, CAST(-1 AS BIGINT) AS m1")
    assert rows(conn, "SELECT lo div m1 AS v FROM lo") == [(-9223372036854775808,)]


@pytest.fixture
def wide(conn):
    conn.execute("CREATE TEMP VIEW wide AS SELECT CAST('12345678901234567890.1234567891' AS DECIMAL(30,10)) AS d, "
                 "CAST(7 AS DECIMAL(12,2)) AS e, 250.0::DOUBLE AS f, 2 AS n")
    return conn


def test_a_remainder_of_wide_decimals_takes_sparks_type_when_its_operands_fit(wide):
    # scale 10; precision min(20, 10) + 10: each operand's whole digits at scale 10 fit 38.
    assert answer(wide, "SELECT d % e AS v, e % d AS w FROM wide") == ["DECIMAL(20,10)", "DECIMAL(20,10)"]


def test_bround_of_a_double_to_tens_rounds_half_to_even_next_to_a_decimal_product(wide):
    assert rows(wide, "SELECT bround(f, -2) AS v FROM wide") == [(200.0,)]
    assert answer(wide, "SELECT bround(f, -2) AS v, e * e AS w FROM wide") == ["DOUBLE", "DECIMAL(25,4)"]


def test_a_refused_round_in_a_cases_else_is_named_round_not_bround(wide):
    from fabric_desktop.spark_decimals import Refused
    statement = sql.parse("SELECT CASE WHEN n > 0 THEN 1 ELSE round(f, 25) END AS v FROM wide")[0]
    with pytest.raises(Refused, match=r"^round\(\) of a double to 25 places"):
        spark_decimal_types(statement, wide)


@pytest.fixture
def edges(conn):
    conn.execute("CREATE TEMP VIEW edges AS SELECT 2 AS n, 'NaN'::DOUBLE AS nan, 1e300::DOUBLE AS big")
    return conn


def test_a_whole_number_literal_past_int_is_a_bigint_and_past_bigint_is_not_wrapped(edges):
    assert answer(edges, "SELECT n + 2147483648 AS v FROM edges") == ["BIGINT"]
    assert rows(edges, "SELECT n + 2147483648 AS v FROM edges") == [(2147483650,)]
    assert rows(edges, "SELECT n + 9223372036854775808 AS v FROM edges") == [(9223372036854775810,)]


def test_ceil_and_floor_of_a_double_give_0_for_nan_and_the_nearest_end_past_the_range(edges):
    assert rows(edges, "SELECT ceil(nan) AS a, floor(big) AS b, ceil(-big) AS c FROM edges") == [
        (0, 9223372036854775807, -9223372036854775808)]


def test_the_smallest_bigint_literal_wraps_and_one_under_it_does_not(conn):
    conn.execute("CREATE TEMP VIEW two AS SELECT CAST(2 AS BIGINT) AS b")
    # -9223372036854775808 is a BIGINT, so its difference wraps in 64 bits, as in Spark ...
    assert rows(conn, "SELECT -9223372036854775808 - b AS v FROM two") == [(9223372036854775806,)]
    # ... and one under it is a decimal, which doesn't wrap.
    assert rows(conn, "SELECT -9223372036854775809 - b AS v FROM two") == [(-9223372036854775811,)]


def test_a_doubles_round_to_20_places_either_way_runs(edges):
    assert rows(edges, "SELECT round(0.25D, 20) AS a, round(2.55e21D, -20) AS b FROM edges") == [(0.25, 2.6e21)]


def test_a_refused_round_to_negative_places_suggests_a_whole_decimal(edges):
    from fabric_desktop.spark_decimals import Refused
    statement = sql.parse("SELECT round(1.5D, -25) AS v FROM edges")[0]
    with pytest.raises(Refused, match=r"round\(CAST\(x AS DECIMAL\(38, 0\)\), -25\)"):
        spark_decimal_types(statement, edges)
