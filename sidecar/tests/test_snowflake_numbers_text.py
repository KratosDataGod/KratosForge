"""WR-272: Snowflake numbers as text, and text as numbers (rules 2026.11.24, SFX-032 to SFX-036, SFN-035 to SFN-037).

No Snowflake account is used: each expected answer is the one Snowflake's SQL reference gives or states the rule
for, cited beside it. Each test runs the translation through DuckDB, as a Snowflake cell does here.
"""

from __future__ import annotations

import concurrent.futures
from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf

DOCS = "https://docs.snowflake.com/en/sql-reference"


def one(expression: str) -> tuple[object, str]:
    """The value and DuckDB type of SELECT ``expression``, as a Snowflake cell runs it here."""
    translation = sf.translate("SELECT " + expression)
    assert translation.runnable, translation.first_refusal()
    result = duckdb.connect().execute(translation.duckdb_sql)
    return result.fetchall()[0][0], str(result.description[0][1])


def value(expression: str) -> object:
    return one(expression)[0]


def stops(expression: str) -> str:
    translation = sf.translate("SELECT " + expression)
    assert translation.runnable, translation.first_refusal()
    with pytest.raises(duckdb.Error) as raised:
        duckdb.connect().execute(translation.duckdb_sql).fetchall()
    return str(raised.value)


def refusal(expression: str) -> sf.Note:
    translation = sf.translate("SELECT " + expression)
    assert not translation.runnable
    return translation.statements[0].notes[0]


def size(sql: str) -> int:
    return len(sf.translate(sql).duckdb_sql)


def grows_linearly(make) -> bool:  # noqa: ANN001
    """The SQL for a nest twice as deep is about twice as long, not four times or more."""
    small, large = size(make(8)), size(make(16))
    return large < 2.3 * small


# ---------------------------------------------------------------- SFX-032: a FLOAT written as text

# data-type-conversion: "When a FLOAT value is cast to a VARCHAR value, trailing zeros are omitted" (5.000 is '5');
# data-types-numeric shows a FLOAT as 1.23456789012346e+28 (15 significant digits) and its infinity as inf.
@pytest.mark.parametrize(("sql", "want"), [
    ("TO_VARCHAR(100::FLOAT)", "100"),
    ("5.000::FLOAT::VARCHAR", "5"),
    ("CAST(1e20::DOUBLE AS VARCHAR)", "1e+20"),
    ("0.1::FLOAT::STRING", "0.1"),
    ("TO_CHAR(1e15::FLOAT)", "1e+15"),
    ("TO_VARCHAR(1 / 3::FLOAT)", "0.333333333333333"),
    ("TO_VARCHAR(12345678901234567890123456789::FLOAT)", "1.23456789012346e+28"),
    ("TO_VARCHAR('inf'::FLOAT)", "inf"),
    ("TO_VARCHAR('-inf'::FLOAT)", "-inf"),
    ("TO_VARCHAR('nan'::FLOAT)", "NaN"),
    ("TO_VARCHAR(SQRT(4))", "2"),
    ("TO_VARCHAR(2.5::REAL * 2)", "5"),
])
def test_sfx032_a_float_is_written_without_trailing_zeros(sql, want):
    assert value(sql) == want


def test_sfx032_a_column_is_written_by_the_type_it_holds():
    assert value("x::VARCHAR FROM (SELECT 100::DOUBLE AS x)") == "100"
    assert value("TO_VARCHAR(x) FROM (SELECT 1.50::NUMBER(4, 2) AS x)") == "1.50"
    assert value("x::VARCHAR FROM (SELECT 'abc' AS x)") == "abc"
    assert value("TO_VARCHAR(x) FROM (SELECT NULL::FLOAT AS x)") is None


def test_sfx032_a_number_keeps_its_scale_as_text():
    # to_char: a NUMBER is written with its scale (TO_NUMBER(expr, 10, 8) of '12.3456' shows 12.34560000)
    assert value("TO_VARCHAR('12.3456'::NUMBER(10, 8))") == "12.34560000"


def test_sfx032_grows_linearly():
    assert grows_linearly(lambda n: "SELECT " + "TO_VARCHAR(" * n + "1.5::FLOAT" + ")::FLOAT" * n)


# ---------------------------------------------------------------- SFX-033: number format models

# sql-format-models, "Sign position" examples for 12 and -7, and the sample_numbers output.
@pytest.mark.parametrize(("number", "model", "want"), [
    (12, "99", " 12"), (-7, "99", " -7"),
    (12, "S99", "+12"), (-7, "S99", " -7"),
    (12, "99S", "12+"), (-7, "99S", " 7-"),
    (12, "MI99", " 12"), (-7, "MI99", "- 7"),
    (12, "99MI", "12 "), (-7, "99MI", " 7-"),
    (12, "$99", " $12"), (-7, "$99", " -$7"),
    (12, "FM99", "12"), (-7, "FM99", "-7"),
    ("1.2", "999,999.999", "       1.2  "),
    ("123.456", "999,999.999", "     123.456"),
    ("1234.56", "999,999.999", "   1,234.56 "),
    ("-123456.789", "999,999.999", "-123,456.789"),
    ("1.2", "S000,000.000", "+000,001.200"),
    ("1234.56", "S000,000.000", "+001,234.560"),
    ("-123456.789", "S000,000.000", "-123,456.789"),
    ("1.2", "FM999,999.999", "1.2"),
    ("1234.56", "FM999,999.999", "1,234.56"),
    ("-123456.789", "FM999,999.999", "-123,456.789"),
])
def test_sfx033_number_formats_print_as_the_reference_shows(number, model, want):
    assert value(f"TO_CHAR({number}, '{model}')") == want


@pytest.mark.parametrize(("sql", "want"), [
    ("TO_CHAR(12.5, '999.99')", "  12.5 "),         # a trailing zero of the fraction is a blank under 9
    ("TO_CHAR(12, '0000')", " 0012"),               # 0 prints leading zeros; the sign's place stays
    ("TO_CHAR(1234, '$9,999')", " $1,234"),
    ("TO_CHAR(-5, '99MI')", " 5-"),
    ("TO_CHAR(5, 'S99')", " +5"),
    ("TO_CHAR(0.5, '99.99')", "  0.5 "),           # a zero whole part is a single 0
    ("TO_CHAR(12.345, '99.99')", " 12.35"),        # rounded half away from zero to the format's places
    ("TO_CHAR(-12.345, '99.99')", "-12.35"),
    ("TO_VARCHAR(1234.5::FLOAT, 'FM$9,999.00')", "$1,234.50"),
    ("TO_CHAR(x, '999') FROM (SELECT 5 AS x)", "   5"),
    ("TO_CHAR(NULL::NUMBER, '999')", None),
])
def test_sfx033_more_formats(sql, want):
    assert value(sql) == want


def test_sfn037_more_whole_digits_than_places_stop_by_name():
    error = stops("TO_CHAR(123, '99')")
    assert "SFN-037" in error and "nothing after it ran" in error


@pytest.mark.parametrize("model", ["TM9", "TME", "9.99EEEE", "XXXX", "B999", '">"99"<"', "9S9", "99$"])
def test_sfx003_other_number_formats_are_refused_by_name(model):
    note = refusal(f"TO_CHAR(12, '{model}')")
    assert note.id == "SFX-003" and "nothing ran" in note.reason and note.source.endswith("sql-format-models")


def test_sfx033_grows_linearly():
    assert grows_linearly(lambda n: "SELECT " + "TO_CHAR(" * n + "1" + ", '999.99')::NUMBER(10, 2)" * n)


# ---------------------------------------------------------------- SFX-034, SFN-036: number types

def test_sfx034_real_is_a_64_bit_float():
    # data-types-numeric: FLOAT, FLOAT4, FLOAT8, DOUBLE, REAL are all double-precision
    assert value("0.1::REAL * 3") == 0.1 * 3
    assert one("CAST(1 AS FLOAT4)")[1] == "DOUBLE"


def test_sfx034_text_to_a_whole_number():
    # data-types-numeric: INT and its kin are NUMBER(38, 0); to_decimal: "Strings are converted as decimal,
    # integer, fractional, or floating-point numbers", and hex is read only by the X format element.
    assert value("' 12 '::INT") == 12 and value("'+12'::INT") == 12 and value("'1.5e3'::INT") == 1500
    assert value("'12.5'::INT") == 13 and value("'-12.5'::INT") == -13
    assert value("TRY_CAST('0x1F' AS INT)") is None
    assert "0x1F" in stops("'0x1F'::INT")
    # data-type-conversion: "Converting a FLOAT value to an INTEGER value rounds the value."
    assert value("2.5::FLOAT::INT") == 3 and value("(-2.5)::FLOAT::BIGINT") == -3
    assert value("LEFT('abc', '2'::INT)") == "ab"  # still DuckDB's INTEGER, so a function takes it


def test_sfx034_text_to_number_and_float():
    assert value("' 12 '::NUMBER") == 12 and value("'+12'::NUMBER") == 12 and value("'1e3'::NUMBER") == 1000
    assert value("' 1.5 '::FLOAT") == 1.5 and value("'+1.5'::FLOAT") == 1.5 and value("'1e3'::FLOAT") == 1000.0
    with pytest.raises(duckdb.Error):
        duckdb.connect().execute(sf.translate("SELECT '0x1F'::FLOAT").duckdb_sql).fetchall()


def test_sfn036_a_whole_number_past_duckdbs_type_stops_by_name():
    error = stops("'3000000000'::INT")
    assert "SFN-036" in error and sf.stopped_by_name(duckdb.Error(error))
    assert value("'3000000000'::BIGINT") == 3000000000
    assert "SFN-036" in stops("40000::SMALLINT")


def test_sfx034_grows_linearly():
    assert grows_linearly(lambda n: "SELECT " + "(" * n + "'1'" + "::INT + 1)" * n)


# ---------------------------------------------------------------- TO_NUMBER, SFN-035

def test_to_number_rounds_to_its_scale_and_stops_out_of_range():
    # to_decimal: TO_NUMBER('12.3456', 10, 1) is 12.3, ('98.76546', 10, 1) is 98.8, (expr) is 12 and 99,
    # (expr, 10, 8) is 12.34560000; (expr, 10, 9) is "Numeric value '12.3456' is out of range".
    assert value("TO_NUMBER('12.3456', 10, 1)") == Decimal("12.3")
    assert value("TO_NUMBER('98.76546', 10, 1)") == Decimal("98.8")
    assert value("TO_NUMBER('12.3456')") == 12 and value("TO_NUMBER('98.76546')") == 99
    assert str(value("TO_NUMBER('12.3456', 10, 8)")) == "12.34560000"
    stops("TO_NUMBER('12.3456', 10, 9)")
    assert value("TRY_TO_NUMBER('12.3456', 10, 9)") is None
    assert value("TO_DECIMAL('-12.345', 10, 2)") == Decimal("-12.35")


@pytest.mark.parametrize("sql", ["TO_NUMBER('$3,741.72', '$9,999.99', 6, 2)", "TO_DECIMAL('ae5', 'XXX')",
                                 "TRY_TO_NUMBER('12', '99')", "TO_NUMERIC('1.0', '99.9')"])
def test_sfn035_to_number_with_a_format_is_refused_by_name(sql):
    note = refusal(sql)
    assert note.id == "SFN-035" and "nothing ran" in note.reason and note.source == f"{DOCS}/functions/to_decimal"


# ---------------------------------------------------------------- SFX-035: NUMBER scales

@pytest.mark.parametrize(("sql", "want", "kind"), [
    # operators-arithmetic: 2 / 7 is 0.285714 and 10.01 * 1.1 is 11.011
    ("2 / 7", "0.285714", "DECIMAL(7,6)"),
    ("10.01 * 1.1", "11.011", None),
    ("10 / 4", "2.500000", None),
    ("1::NUMBER(10, 0) / 3::NUMBER(10, 0)", "0.333333", None),
    ("1.0::NUMBER(10, 1) / 3", "0.3333333", None),             # scale 1 + 6
    ("1::NUMBER(20, 8) / 3", "0.333333333333", None),          # 8 + 6, at most 12
    ("1::NUMBER(20, 14) / 3", "0.33333333333333", None),       # the numerator's scale past 12 stays
    ("2 / 3", "0.666667", None),                               # rounded, not cut off
    ("-2 / 3", "-0.666667", None),
    ("0.12345678::NUMBER(20, 8) * 0.12345678::NUMBER(20, 8)", "0.015241576528", None),
    ("1.25::NUMBER(10, 2) * 1.5::NUMBER(10, 1)", "1.875", None),
    ("1.25::NUMBER(10, 2) + 1.5::NUMBER(10, 1)", "2.75", None),
    ("100000 * 100000", "10000000000", None),
    ("2147483647 + 1", "2147483648", None),
    ("'7'::INT / 2", "3.500000", None),
    ("DIV0(1, 3)", "0.333333", None),
    ("DIV0(5, 0)", "0.000000", None),
    ("DIV0NULL(7, 2)", "3.500000", None),
    ("DIV0NULL(7, 0)", "0.000000", None),
])
def test_sfx035_number_arithmetic_has_snowflakes_scale(sql, want, kind):
    got, duck_type = one(sql)
    assert str(got) == want
    if kind:
        assert duck_type == kind


def test_sfx035_division_by_zero_and_nulls():
    assert "Division by zero" in stops("1 / 0")
    assert value("DIV0(NULL, 0)") is None and value("NULL::NUMBER / 2") is None
    assert "SFD-003" not in [n.id for n in sf.translate("SELECT 2 / 7").statements[0].notes]
    assert "SFD-003" in [n.id for n in sf.translate("SELECT a / b FROM t").statements[0].notes]


def test_sfx035_grows_linearly():
    assert grows_linearly(lambda n: "SELECT " + "(" * n + "1" + " / 3)" * n)
    assert grows_linearly(lambda n: "SELECT " + "(" * n + "0.5::NUMBER(20, 8)" + " * 0.5::NUMBER(20, 8))" * n)
    # sqlglot's own DIV0 reads its numerator twice, so the nest is inside one DIV0
    assert grows_linearly(lambda n: "SELECT DIV0(" + "(" * n + "1" + " / 3)" * n + ", 3)")


# ---------------------------------------------------------------- SFX-036: ROUND and TRUNC to tens

@pytest.mark.parametrize(("sql", "want"), [
    # round: "If scale_expr is negative, it specifies the number of places before the decimal point";
    # half-points round away from zero. trunc: the same places, toward zero.
    ("ROUND(2147483647, -1)", 2147483650),
    ("ROUND(1250, -2)", 1300), ("ROUND(-1250, -2)", -1300), ("ROUND(1234.5, -2)", 1200),
    ("ROUND(5, -3)", 0), ("ROUND(1234.5::FLOAT, -2)", 1200.0),
    ("TRUNC(1299, -2)", 1200), ("TRUNC(-1299, -2)", -1200), ("TRUNCATE(1299.99, -2)", 1200),
    ("TRUNC(2147483647, -1)", 2147483640),
    ("ROUND(x, -1) FROM (SELECT 2147483647::INT AS x)", 2147483650),
])
def test_sfx036_round_and_trunc_to_tens(sql, want):
    assert value(sql) == want


def test_sfx036_grows_linearly():
    assert grows_linearly(lambda n: "SELECT " + "ROUND(" * n + "1" + ", -1)" * n)


def test_the_new_rules_are_listed():
    table = sf.docs_table()
    assert sf.RULES_VERSION == "2026.11.24"
    assert all(f"| {rule} |" in table for rule in ("SFX-032", "SFX-033", "SFX-034", "SFX-035", "SFX-036",
                                                     "SFN-035", "SFN-036", "SFN-037"))


@pytest.mark.parametrize("sql", [
    "SELECT 1::NUMBER(38,0) / 2::NUMBER(38,99999999)",  # a scale past any NUMBER
    "SELECT 1::NUMBER(38,0) / 2::NUMBER(38,5000)",
    "SELECT 1::NUMBER(38,0) / 2::NUMBER(99999999999999999999,2)",
    "SELECT 1 / 1" + "0" * 5000,  # a literal past 38 digits
    "SELECT " + "9" * 5000 + " * 2",
    "SELECT " + " * ".join(["99999999999999999999"] * 400),
])
def test_a_number_past_snowflakes_sizes_is_not_worked_out_and_comes_back_quickly(sql):
    # data-types-numeric: precision is 1 to 38 and scale 0 to the precision. Sizes outside that aren't rewritten,
    # so no power of ten is built from them and the translation returns at once.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(sf.translate, sql).result(timeout=20)


@pytest.mark.parametrize(("expression", "want"), [
    # operators-arithmetic: "Snowflake rounds the output (rather than truncating the output)"; an exact half at
    # the result's scale (here 6) goes away from zero.
    ("1 / 2000000", Decimal("0.000001")), ("-1 / 2000000", Decimal("-0.000001")),
    ("3 / 2000000", Decimal("0.000002")), ("-3 / 2000000", Decimal("-0.000002")),
])
def test_division_rounds_an_exact_half_away_from_zero(expression, want):
    assert value(expression) == want


def test_a_number_written_with_an_exponent_is_a_float_so_its_division_is_not_a_number():
    # data-types-numeric: a numeric constant with an exponent (1e3) is a FLOAT, so 1e3 / 7 keeps every digit
    assert one("1e3 / 7") == (pytest.approx(1000 / 7), "DOUBLE")
    assert value("1000 / 7") == Decimal("142.857143")
