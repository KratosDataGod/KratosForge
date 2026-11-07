"""Columns made by F functions, Column.cast and like / rlike / contains / startswith / endswith in a
notebook cell on this PC carry the names Spark 3.5 gives them (min(age), avg(h) for F.mean,
POWER(age, 2), round(h, 0), regexp_replace(name, l, L, 1)), not DuckDB's. Each expected name was
given by Apache Spark 3.5.3 for the same call on the same frame."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel

SETUP = 'from pyspark.sql import functions as F\ndf = spark.createDataFrame([(2, "Alice", 1.5, "2024-01-02", [1, 2])], ["age", "name", "h", "d", "arr"])\nn, a, h, d, arr = df.name, df.age, df.h, F.to_date(df.d), df.arr\n'

# (call, aggregate?, Spark's name)
CASES = [
    ('F.min(a)', True, 'min(age)'),
    ('F.max("age")', True, 'max(age)'),
    ('F.sum(a)', True, 'sum(age)'),
    ('F.avg(a)', True, 'avg(age)'),
    ('F.mean(h)', True, 'avg(h)'),
    ('F.count(h)', True, 'count(h)'),
    ('F.count("*")', True, 'count(1)'),
    ('F.countDistinct("age", "name")', True, 'count(DISTINCT age, name)'),
    ('F.count_distinct(a)', True, 'count(DISTINCT age)'),
    ('F.first(n)', True, 'first(name)'),
    ('F.first(n, True)', True, 'first(name)'),
    ('F.last(n)', True, 'last(name)'),
    ('F.collect_list(a)', True, 'collect_list(age)'),
    ('F.collect_set(a)', True, 'collect_set(age)'),
    ('F.stddev(a)', True, 'stddev(age)'),
    ('F.stddev_samp(a)', True, 'stddev_samp(age)'),
    ('F.stddev_pop(a)', True, 'stddev_pop(age)'),
    ('F.variance(a)', True, 'var_samp(age)'),
    ('F.var_samp(a)', True, 'var_samp(age)'),
    ('F.var_pop(a)', True, 'var_pop(age)'),
    ('F.sumDistinct(a)', True, 'sum(DISTINCT age)'),
    ('F.sum_distinct(a)', True, 'sum(DISTINCT age)'),
    ('F.approx_count_distinct(a)', True, 'approx_count_distinct(age)'),
    ('F.max_by(n, a)', True, 'max_by(name, age)'),
    ('F.min_by(n, a)', True, 'min_by(name, age)'),
    ('F.any_value(n)', True, 'any_value(name)'),
    ('F.median(a)', True, 'median(age)'),
    ('F.mode(a)', True, 'mode(age)'),
    ('F.percentile_approx(a, 0.5)', True, 'percentile_approx(age, 0.5, 10000)'),
    ('F.corr(a, h)', True, 'corr(age, h)'),
    ('F.covar_samp(a, h)', True, 'covar_samp(age, h)'),
    ('F.kurtosis(a)', True, 'kurtosis(age)'),
    ('F.skewness(a)', True, 'skewness(age)'),
    ('F.product(a)', True, 'product(age)'),
    ('F.array_agg(a)', True, 'collect_list(age)'),
    ('F.upper(n)', False, 'upper(name)'),
    ('F.lower("name")', False, 'lower(name)'),
    ('F.coalesce(h, a)', False, 'coalesce(h, age)'),
    ('F.abs(a)', False, 'abs(age)'),
    ('F.round(h)', False, 'round(h, 0)'),
    ('F.round(h, 1)', False, 'round(h, 1)'),
    ('F.bround(h)', False, 'bround(h, 0)'),
    ('F.concat(n, F.lit("!"))', False, 'concat(name, !)'),
    ('F.length("name")', False, 'length(name)'),
    ('F.trim(n)', False, 'trim(name)'),
    ('F.ltrim(n)', False, 'ltrim(name)'),
    ('F.rtrim(n)', False, 'rtrim(name)'),
    ('F.concat_ws("-", n, "d")', False, 'concat_ws(-, name, d)'),
    ('F.to_date(df.d)', False, 'to_date(d)'),
    ('F.to_date(df.d, "yyyy-MM-dd")', False, 'to_date(d, yyyy-MM-dd)'),
    ('F.year(d)', False, 'year(to_date(d))'),
    ('F.month(d)', False, 'month(to_date(d))'),
    ('F.dayofmonth(d)', False, 'dayofmonth(to_date(d))'),
    ('F.dayofweek(d)', False, 'dayofweek(to_date(d))'),
    ('F.date_format(d, "yyyy")', False, 'date_format(to_date(d), yyyy)'),
    ('F.date_add(d, 1)', False, 'date_add(to_date(d), 1)'),
    ('F.date_sub(d, 1)', False, 'date_sub(to_date(d), 1)'),
    ('F.datediff(d, d)', False, 'datediff(to_date(d), to_date(d))'),
    ('F.add_months(d, 1)', False, 'add_months(to_date(d), 1)'),
    ('F.last_day(d)', False, 'last_day(to_date(d))'),
    ('F.current_date()', False, 'current_date()'),
    ('F.regexp_replace("name", "l", "L")', False, 'regexp_replace(name, l, L, 1)'),
    ('F.regexp_extract("name", "(A)", 1)', False, 'regexp_extract(name, (A), 1)'),
    ('F.split("d", "-")', False, 'split(d, -, -1)'),
    ('F.isnull("h")', False, '(h IS NULL)'),
    ('F.isnan(h)', False, 'isnan(h)'),
    ('F.nvl(h, F.lit(0))', False, 'nvl(h, 0)'),
    ('F.ifnull(h, F.lit(0))', False, 'ifnull(h, 0)'),
    ('F.nanvl(h, h)', False, 'nanvl(h, h)'),
    ('F.sqrt(a)', False, 'SQRT(age)'),
    ('F.exp(a)', False, 'EXP(age)'),
    ('F.log2(a)', False, 'LOG2(age)'),
    ('F.ln(a)', False, 'ln(age)'),
    ('F.ceil(h)', False, 'CEIL(h)'),
    ('F.floor(h)', False, 'FLOOR(h)'),
    ('F.signum(a)', False, 'SIGNUM(age)'),
    ('F.cbrt(a)', False, 'CBRT(age)'),
    ('F.sin(a)', False, 'SIN(age)'),
    ('F.cos(a)', False, 'COS(age)'),
    ('F.degrees(a)', False, 'DEGREES(age)'),
    ('F.radians(a)', False, 'RADIANS(age)'),
    ('F.greatest(a, h)', False, 'greatest(age, h)'),
    ('F.least(a, h)', False, 'least(age, h)'),
    ('F.array(a, h)', False, 'array(age, h)'),
    ('F.struct(a, n)', False, 'struct(age, name)'),
    ('F.size(arr)', False, 'size(arr)'),
    ('F.explode(arr)', False, 'col'),
    ('F.array_contains(arr, 1)', False, 'array_contains(arr, 1)'),
    ('F.element_at(arr, 1)', False, 'element_at(arr, 1)'),
    ('F.sort_array(arr)', False, 'sort_array(arr, true)'),
    ('F.array_distinct(arr)', False, 'array_distinct(arr)'),
    ('F.lpad(n, 8, "*")', False, 'lpad(name, 8, *)'),
    ('F.rpad(n, 8, "*")', False, 'rpad(name, 8, *)'),
    ('F.repeat(n, 2)', False, 'repeat(name, 2)'),
    ('F.reverse(n)', False, 'reverse(name)'),
    ('F.initcap(n)', False, 'initcap(name)'),
    ('F.instr(n, "l")', False, 'instr(name, l)'),
    ('F.substring(n, 1, 2)', False, 'substring(name, 1, 2)'),
    ('F.md5(n)', False, 'md5(name)'),
    ('F.sha1(n)', False, 'sha1(name)'),
    ('F.sha2(n, 256)', False, 'sha2(name, 256)'),
    ('F.crc32(n)', False, 'crc32(name)'),
    ('F.hash(n)', False, 'hash(name)'),
    ('F.base64(n)', False, 'base64(name)'),
    ('F.ascii(n)', False, 'ascii(name)'),
    ('F.format_number(h, 2)', False, 'format_number(h, 2)'),
    ('F.expr("age + 1")', False, '(age + 1)'),
    ('F.lit(1)', False, '1'),
    ('F.lit("x")', False, 'x'),
    ('F.lit(None)', False, 'NULL'),
    ('F.col("age")', False, 'age'),
    ('F.monotonically_increasing_id()', False, 'monotonically_increasing_id()'),
    ('F.current_timestamp()', False, 'current_timestamp()'),
    ('F.to_timestamp(df.d)', False, 'to_timestamp(d)'),
    ('F.unix_timestamp(df.d, "yyyy-MM-dd")', False, 'unix_timestamp(d, yyyy-MM-dd)'),
    ('F.from_unixtime(a)', False, 'from_unixtime(age, yyyy-MM-dd HH:mm:ss)'),
    ('F.date_trunc("month", d)', False, 'date_trunc(month, to_date(d))'),
    ('F.trunc(d, "month")', False, 'trunc(to_date(d), month)'),
    ('F.weekofyear(d)', False, 'weekofyear(to_date(d))'),
    ('F.quarter(d)', False, 'quarter(to_date(d))'),
    ('F.hour(F.to_timestamp(df.d))', False, 'hour(to_timestamp(d))'),
    ('F.nullif(a, a)', False, 'nullif(age, age)'),
    ('F.nvl2(h, a, a)', False, 'nvl2(h, age, age)'),
    ('F.ifnull(h, a)', False, 'ifnull(h, age)'),
    ('F.hex(a)', False, 'hex(age)'),
    ('F.positive(a)', False, '(+ age)'),
    ('F.negative(a)', False, 'negative(age)'),
    ('F.btrim(n)', False, 'btrim(name)'),
    ('F.left(n, F.lit(2))', False, 'left(name, 2)'),
    ('F.right(n, F.lit(2))', False, 'right(name, 2)'),
    ('F.startswith(n, F.lit("A"))', False, 'startswith(name, A)'),
    ('F.contains(n, F.lit("l"))', False, 'contains(name, l)'),
    # functions this PC added later, from the same capture
    ('F.bool_and(a > 1)', True, 'bool_and((age > 1))'),
    ('F.every(a > 1)', True, 'every((age > 1))'),
    ('F.bool_or(a > 1)', True, 'bool_or((age > 1))'),
    ('F.some(a > 1)', True, 'some((age > 1))'),
    ('F.count_if(a > 1)', True, 'count_if((age > 1))'),
    ('F.bit_and(a)', True, 'bit_and(age)'),
    ('F.bit_or(a)', True, 'bit_or(age)'),
    ('F.bit_xor(a)', True, 'bit_xor(age)'),
    ('F.split("d", "-", 2)', False, 'split(d, -, 2)'),
    ('F.log(a)', False, 'ln(age)'),
    ('F.log(2.0, a)', False, 'LOG(2.0, age)'),
    ('F.log10(a)', False, 'LOG10(age)'),
    ('F.pow(a, 2)', False, 'POWER(age, 2)'),
    ('F.power(a, 2)', False, 'POWER(age, 2)'),
    ('F.locate("l", n)', False, 'locate(l, name, 1)'),
    ('F.substring_index(n, "l", 1)', False, 'substring_index(name, l, 1)'),
    ('F.translate(n, "A", "a")', False, 'translate(name, A, a)'),
    ('F.rand(1)', False, 'rand(1)'),
    ('F.bin(a)', False, 'bin(age)'),
    ('F.factorial(a)', False, 'factorial(age)'),
    ('F.char_length(n)', False, 'char_length(name)'),
]


@pytest.fixture(scope="module")
def kernel():
    k = Kernel()
    out = k.run(SETUP)
    assert out["status"] == "ok", out["outputs"]
    return k


@pytest.mark.parametrize(("call", "agg", "spark"), CASES, ids=[c[0] for c in CASES])
def test_a_function_column_is_named_as_spark_names_it(kernel, call, agg, spark):
    out = kernel.run(f"print((df.agg if {agg} else df.select)({call}).columns[0])")
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]).strip() == spark


def test_a_cast_of_a_plain_column_keeps_its_name_and_text_methods_read_as_spark(kernel):
    out = kernel.run("print(df.select(df.age.cast('string'), (df.age + 1).cast('double'), df.name.like('A%'), "
                     "df.name.rlike('^A'), df.name.contains('l'), df.name.startswith('A'), df.name.endswith('b'), "
                     "df.name.ilike('a%')).columns)")
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]).strip() == (
        "['age', 'CAST((age + 1) AS DOUBLE)', 'name LIKE A%', 'RLIKE(name, ^A)', 'contains(name, l)', "
        "'startswith(name, A)', 'endswith(name, b)', 'ilike(name, a%)']")


def test_quotes_in_a_column_name_and_a_literal_stay_apart(kernel):
    out = kernel.run("q = spark.createDataFrame([('a',)], [\"c'd\"])\n"
                     "print(q.select(F.concat(F.col(\"c'd\"), F.lit(\"x'y\")), F.upper(F.col(\"c'd\"))).columns)")
    assert out["status"] == "ok", out["outputs"]
    assert "".join(o.get("text", "") for o in out["outputs"]).strip() == "[\"concat(c'd, x'y)\", \"upper(c'd)\"]"
