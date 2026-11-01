"""Which Databricks SQL built-in functions answer the same on this PC (DuckDB, after sqlglot).

The DuckDB engine runs a Databricks SQL cell by translating it with sqlglot's Databricks dialect
(local_sql._databricks, the same path a cell takes) and running it in DuckDB. Each case below is one
documented Databricks built-in function with a sample call and the value Databricks documents for
it (the examples in Databricks' built-in functions reference, https://learn.microsoft.com/azure/
databricks/sql/language-manual/sql-ref-functions-builtin-alpha, or worked by hand where the page
gives none, marked ``# by hand``). ``outcome`` is what this version of sqlglot and DuckDB do with it:

    same          runs on this PC and gives the documented value
    differs       runs, but gives another value: check results that use it on the warehouse
    varies        runs, and gives the documented value on some machines and not others (hash order)
    error         translates, but DuckDB refuses the translated SQL
    untranslated  sqlglot can't read or translate it

tests/test_databricks_sql_parity.py runs every case again and fails when an outcome changes, so a
sqlglot or DuckDB upgrade that fixes or breaks a function shows up; docs/engines.md carries the
table (``table_markdown``), checked by the same test.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from ..duckdb_rows import first_row

D = dt.date


@dataclass(frozen=True)
class Case:
    function: str
    group: str
    sql: str          # a SELECT whose first column of the first row is the answer
    expected: Any     # the value Databricks documents
    outcome: str      # same | differs | varies | error | untranslated (this version)
    note: str = ""


CASES: tuple[Case, ...] = (
    Case('concat', 'String', "SELECT concat('Spark', 'SQL')", 'SparkSQL', 'same'),
    Case('concat_ws', 'String', "SELECT concat_ws(' ', 'Spark', 'SQL')", 'Spark SQL', 'same'),
    Case('upper', 'String', "SELECT upper('SparkSql')", 'SPARKSQL', 'same'),
    Case('lower', 'String', "SELECT lower('SparkSql')", 'sparksql', 'same'),
    Case('length', 'String', "SELECT length('Spark SQL ')", 10, 'same'),
    Case('substring', 'String', "SELECT substring('Spark SQL', 5)", 'k SQL', 'same'),
    Case('substr', 'String', "SELECT substr('Spark SQL', 5, 1)", 'k', 'same'),
    Case('trim', 'String', "SELECT trim('    SparkSQL   ')", 'SparkSQL', 'same'),
    Case('ltrim', 'String', "SELECT ltrim('    SparkSQL   ')", 'SparkSQL   ', 'same'),
    Case('rtrim', 'String', "SELECT rtrim('    SparkSQL   ')", '    SparkSQL', 'same'),
    Case('lpad', 'String', "SELECT lpad('hi', 5, '??')", '???hi', 'same'),
    Case('rpad', 'String', "SELECT rpad('hi', 5, '??')", 'hi???', 'same'),
    Case('replace', 'String', "SELECT replace('ABCabc', 'abc', 'DEF')", 'ABCDEF', 'same'),
    Case('instr', 'String', "SELECT instr('SparkSQL', 'SQL')", 6, 'same'),
    Case('locate', 'String', "SELECT locate('bar', 'foobarbar')", 4, 'same'),
    Case('position', 'String', "SELECT position('bar', 'foobarbar')", 4, 'same'),
    Case('split', 'String', "SELECT split('oneAtwoBthreeC', '[ABC]')", ['one', 'two', 'three', ''], 'same'),
    Case('split_part', 'String', "SELECT split_part('11.12.13', '.', 3)", '13', 'same'),
    Case('regexp_replace', 'String', "SELECT regexp_replace('100-200', '(\\\\d+)', 'num')", 'num-num', 'same'),
    Case('regexp_extract', 'String', "SELECT regexp_extract('100-200', '(\\\\d+)-(\\\\d+)', 1)", '100', 'same'),
    Case('regexp_like', 'String', "SELECT regexp_like('Steven', '^Ste(v|ph)en$')", True, 'same'),
    Case('initcap', 'String', "SELECT initcap('sPark sql')", 'Spark Sql', 'same'),
    Case('reverse', 'String', "SELECT reverse('Spark SQL')", 'LQS krapS', 'same'),
    Case('repeat', 'String', "SELECT repeat('123', 2)", '123123', 'same'),
    Case('left', 'String', "SELECT left('Spark SQL', 3)", 'Spa', 'same'),
    Case('right', 'String', "SELECT right('Spark SQL', 3)", 'SQL', 'same'),
    Case('startswith', 'String', "SELECT startswith('SparkSQL', 'Spark')", True, 'same'),
    Case('endswith', 'String', "SELECT endswith('SparkSQL', 'SQL')", True, 'same'),
    Case('contains', 'String', "SELECT contains('SparkSQL', 'Spark')", True, 'same'),
    Case('md5', 'String', "SELECT md5('Spark')", '8cde774d6f7333752ed72cacddb05126', 'same'),
    Case('sha2', 'String', "SELECT sha2('Spark', 256)", '529bc3b07127ecb7e53a4dcf1991d9152c24537d919178022b2c42657f79a26b', 'same'),
    Case('base64', 'String', "SELECT base64('Spark SQL')", 'U3BhcmsgU1FM', 'same'),
    Case('ascii', 'String', "SELECT ascii('234')", 50, 'same'),
    Case('chr', 'String', 'SELECT chr(65)', 'A', 'same'),
    Case('translate', 'String', "SELECT translate('AaBbCc', 'abc', '123')", 'A1B2C3', 'same'),
    Case('format_number', 'String', 'SELECT format_number(12332.123456, 4)', '12,332.1235', 'same'),
    Case('levenshtein', 'String', "SELECT levenshtein('kitten', 'sitting')", 3, 'same'),
    Case('soundex', 'String', "SELECT soundex('Miller')", 'M460', 'same'),
    Case('abs', 'Math', 'SELECT abs(-1)', 1, 'same'),
    Case('round', 'Math', 'SELECT round(2.5, 0)', 3, 'same'),
    Case('round (negative half)', 'Math', 'SELECT round(-2.5, 0)', -3, 'same'),
    Case('bround', 'Math', 'SELECT bround(2.5, 0)', 2, 'same'),
    Case('ceil', 'Math', 'SELECT ceil(-0.1)', 0, 'same'),
    Case('floor', 'Math', 'SELECT floor(-0.1)', -1, 'same'),
    Case('mod (%)', 'Math', 'SELECT -7 % 3', -1, 'same'),
    Case('pmod', 'Math', 'SELECT pmod(-7, 3)', 2, 'same'),
    Case('div', 'Math', 'SELECT 3 div 2', 1, 'same'),
    Case('power', 'Math', 'SELECT power(2, 3)', 8, 'same'),
    Case('sqrt', 'Math', 'SELECT sqrt(4)', 2, 'same'),
    Case('ln', 'Math', 'SELECT ln(1)', 0, 'same'),
    Case('log10', 'Math', 'SELECT log10(10)', 1, 'same'),
    Case('log', 'Math', 'SELECT log(10, 100)', 2, 'same'),
    Case('greatest', 'Math', 'SELECT greatest(10, 9, 2, 4, 3)', 10, 'same'),
    Case('least', 'Math', 'SELECT least(10, 9, 2, 4, 3)', 2, 'same'),
    Case('sign', 'Math', 'SELECT sign(-5)', -1, 'same'),
    Case('try_divide', 'Math', 'SELECT try_divide(3, 0)', None, 'same'),
    Case('hex', 'Math', 'SELECT hex(17)', '11', 'same'),
    Case('bin', 'Math', 'SELECT bin(13)', '1101', 'same'),
    Case('conv', 'Math', "SELECT conv('100', 2, 10)", '4', 'same'),
    Case('factorial', 'Math', 'SELECT factorial(5)', 120, 'same'),
    Case('date_add', 'Date and time', "SELECT date_add('2016-07-30', 1)", D(2016, 7, 31), 'same'),
    Case('date_sub', 'Date and time', "SELECT date_sub('2016-07-30', 1)", D(2016, 7, 29), 'same'),
    Case('datediff', 'Date and time', "SELECT datediff('2009-07-31', '2009-07-30')", 1, 'same'),
    Case('add_months', 'Date and time', "SELECT add_months('2016-08-31', 1)", D(2016, 9, 30), 'same'),
    Case('last_day', 'Date and time', "SELECT last_day('2009-01-12')", D(2009, 1, 31), 'same'),
    Case('year', 'Date and time', "SELECT year(DATE'2016-07-30')", 2016, 'same'),
    Case('month', 'Date and time', "SELECT month(DATE'2016-07-30')", 7, 'same'),
    Case('dayofmonth', 'Date and time', "SELECT dayofmonth(DATE'2009-07-30')", 30, 'same'),
    Case('dayofweek', 'Date and time', "SELECT dayofweek(DATE'2009-07-30')", 5, 'same'),
    Case('weekofyear', 'Date and time', "SELECT weekofyear(DATE'2008-02-20')", 8, 'same'),
    Case('quarter', 'Date and time', "SELECT quarter(DATE'2016-08-31')", 3, 'same'),
    Case('date_format', 'Date and time', "SELECT date_format(DATE'2016-04-08', 'yyyy-MM-dd')", '2016-04-08', 'same'),
    Case('to_date', 'Date and time', "SELECT to_date('2016-12-31', 'yyyy-MM-dd')", D(2016, 12, 31), 'same'),
    Case('to_timestamp', 'Date and time', "SELECT to_timestamp('2016-12-31 00:12:00')", dt.datetime(2016, 12, 31, 0, 12), 'same'),
    Case('date_trunc', 'Date and time', "SELECT date_trunc('MM', TIMESTAMP'2015-03-05 09:32:05.359')", dt.datetime(2015, 3, 1, 0, 0), 'same'),
    Case('trunc', 'Date and time', "SELECT trunc(DATE'2019-08-04', 'MM')", D(2019, 8, 1), 'same'),
    Case('make_date', 'Date and time', 'SELECT make_date(2013, 7, 15)', D(2013, 7, 15), 'same'),
    Case('next_day', 'Date and time', "SELECT next_day('2015-01-14', 'TU')", D(2015, 1, 20), 'same'),
    Case('months_between', 'Date and time', "SELECT months_between(DATE'1997-02-28', DATE'1996-10-30', false)", 3.935483870967742, 'same'),
    Case('array', 'Array and map', 'SELECT array(1, 2, 3)', [1, 2, 3], 'same'),
    Case('size', 'Array and map', "SELECT size(array('b', 'd', 'c', 'a'))", 4, 'same'),
    Case('array_contains', 'Array and map', 'SELECT array_contains(array(1, 2, 3), 2)', True, 'same'),
    Case('element_at', 'Array and map', 'SELECT element_at(array(1, 2, 3), 2)', 2, 'same'),
    Case('array_distinct', 'Array and map', 'SELECT array_distinct(array(1, 2, 3, 3))', [1, 2, 3], 'same'),
    Case('array_sort', 'Array and map', "SELECT array_sort(array('b', 'd', NULL, 'c', 'a'))", ['a', 'b', 'c', 'd', None], 'same'),
    Case('sort_array', 'Array and map', "SELECT sort_array(array('b', 'd', NULL, 'c', 'a'), true)", [None, 'a', 'b', 'c', 'd'], 'same'),
    Case('array_join', 'Array and map', "SELECT array_join(array('hello', 'world'), ' ')", 'hello world', 'same'),
    Case('array_max', 'Array and map', 'SELECT array_max(array(1, 20, NULL, 3))', 20, 'same'),
    Case('array_min', 'Array and map', 'SELECT array_min(array(1, 20, NULL, 3))', 1, 'same'),
    Case('flatten', 'Array and map', 'SELECT flatten(array(array(1, 2), array(3, 4)))', [1, 2, 3, 4], 'same'),
    Case('slice', 'Array and map', 'SELECT slice(array(1, 2, 3, 4), 2, 2)', [2, 3], 'same'),
    Case('sequence', 'Array and map', 'SELECT sequence(1, 5)', [1, 2, 3, 4, 5], 'same'),
    Case('array_position', 'Array and map', 'SELECT array_position(array(3, 2, 1), 1)', 3, 'same'),
    Case('arrays_overlap', 'Array and map', 'SELECT arrays_overlap(array(1, 2, 3), array(3, 4, 5))', True, 'same'),
    Case('array_union', 'Array and map', 'SELECT array_union(array(1, 2, 3), array(1, 3, 5))', [1, 2, 3, 5], 'same'),
    Case('array_intersect', 'Array and map', 'SELECT array_intersect(array(1, 2, 3), array(1, 3, 5))', [1, 3], 'same'),
    Case('array_except', 'Array and map', 'SELECT array_except(array(1, 2, 3), array(1, 3, 5))', [2], 'same'),
    Case('transform', 'Array and map', 'SELECT transform(array(1, 2, 3), x -> x + 1)', [2, 3, 4], 'same'),
    Case('filter', 'Array and map', 'SELECT filter(array(1, 2, 3), x -> x % 2 = 1)', [1, 3], 'same'),
    Case('aggregate', 'Array and map', 'SELECT aggregate(array(1, 2, 3), 0, (acc, x) -> acc + x)', 6, 'same'),
    Case('exists', 'Array and map', 'SELECT exists(array(1, 2, 3), x -> x % 2 = 0)', True, 'same'),
    Case('explode', 'Array and map', 'SELECT explode(array(10, 20))', 10, 'same'),
    Case('map_keys', 'Array and map', "SELECT map_keys(map(1, 'a', 2, 'b'))", [1, 2], 'same'),
    Case('map_values', 'Array and map', "SELECT map_values(map(1, 'a', 2, 'b'))", ['a', 'b'], 'same'),
    Case('coalesce', 'Conditional', 'SELECT coalesce(NULL, 1, NULL)', 1, 'same'),
    Case('nvl', 'Conditional', 'SELECT nvl(NULL, 2)', 2, 'same'),
    Case('ifnull', 'Conditional', 'SELECT ifnull(NULL, 2)', 2, 'same'),
    Case('nullif', 'Conditional', 'SELECT nullif(2, 2)', None, 'same'),
    Case('nvl2', 'Conditional', 'SELECT nvl2(NULL, 2, 1)', 1, 'same'),
    Case('if', 'Conditional', "SELECT if(1 < 2, 'a', 'b')", 'a', 'same'),
    Case('iff', 'Conditional', "SELECT iff(1 < 2, 'a', 'b')", 'a', 'same'),
    Case('decode', 'Conditional', "SELECT decode(2, 1, 'one', 2, 'two', 'other')", 'two', 'same'),
    Case('get_json_object', 'JSON', 'SELECT get_json_object(\'{"a":"b"}\', \'$.a\')', 'b', 'same'),
    Case('to_json', 'JSON', "SELECT to_json(named_struct('a', 1, 'b', 2))", '{"a":1,"b":2}', 'same'),
    Case('json_array_length', 'JSON', "SELECT json_array_length('[1,2,3,4]')", 4, 'same'),
    Case('count_if', 'Aggregate', 'SELECT count_if(col % 2 = 0) FROM VALUES (0), (1), (2), (3) AS tab(col)', 2, 'same'),
    Case('collect_list', 'Aggregate', 'SELECT array_sort(collect_list(col)) FROM VALUES (1), (2), (1) AS tab(col)', [1, 1, 2], 'same'),
    Case('collect_set', 'Aggregate', 'SELECT array_sort(collect_set(col)) FROM VALUES (1), (2), (1) AS tab(col)', [1, 2], 'same'),
    Case('array_agg', 'Aggregate', 'SELECT array_sort(array_agg(col)) FROM VALUES (1), (2), (1) AS tab(col)', [1, 1, 2], 'same'),
    Case('max_by', 'Aggregate', "SELECT max_by(x, y) FROM VALUES ('a', 10), ('b', 50), ('c', 20) AS tab(x, y)", 'b', 'same'),
    Case('min_by', 'Aggregate', "SELECT min_by(x, y) FROM VALUES ('a', 10), ('b', 50), ('c', 20) AS tab(x, y)", 'a', 'same'),
    Case('bool_and', 'Aggregate', 'SELECT bool_and(col) FROM VALUES (true), (true), (false) AS tab(col)', False, 'same'),
    Case('bool_or', 'Aggregate', 'SELECT bool_or(col) FROM VALUES (true), (false), (false) AS tab(col)', True, 'same'),
    Case('median', 'Aggregate', 'SELECT median(col) FROM VALUES (0), (10) AS tab(col)', 5, 'same'),
    Case('percentile', 'Aggregate', 'SELECT percentile(col, 0.3) FROM VALUES (0), (10) AS tab(col)', 3, 'same'),
    Case('percentile_approx', 'Aggregate', 'SELECT percentile_approx(col, 0.5) FROM VALUES (0), (1), (2), (10) AS tab(col)', 1, 'differs', 'Approximate on both, but the estimates differ.'),
    Case('approx_count_distinct', 'Aggregate', 'SELECT approx_count_distinct(col) FROM VALUES (1), (1), (2), (2), (3) AS tab(col)', 3, 'same'),
    Case('stddev', 'Aggregate', 'SELECT stddev(col) FROM VALUES (1), (2), (3) AS tab(col)', 1.0, 'same'),
    Case('variance', 'Aggregate', 'SELECT variance(col) FROM VALUES (1), (2), (3) AS tab(col)', 1.0, 'same'),
    Case('any_value', 'Aggregate', 'SELECT any_value(col) FROM VALUES (10), (10) AS tab(col)', 10, 'same'),
    Case('sort_array (descending)', 'Array and map', "SELECT sort_array(array('b', NULL, 'a'), false)", ['b', 'a', None], 'same'),
    Case('arrays_overlap (only NULL could match)', 'Array and map', 'SELECT arrays_overlap(array(1, NULL), array(3, 4))', None, 'same'),
    Case('array_union (NULLs)', 'Array and map', 'SELECT array_union(array(1, NULL), array(NULL, 2))', [1, None, 2], 'same'),
    Case('pmod (negative divisor)', 'Math', 'SELECT pmod(7, -3)', 1, 'same'),
    Case('pmod (both negative)', 'Math', 'SELECT pmod(-7, -3)', -1, 'same'),
    Case('dayofweek (Sunday)', 'Date and time', "SELECT dayofweek(DATE'2009-07-26')", 1, 'same'),
    Case('array_intersect (order and NULL)', 'Array and map', 'SELECT array_intersect(array(5, 1, NULL, 3, 5), array(3, NULL, 5))', [5, None, 3], 'same'),
    Case('array_except (order and NULL)', 'Array and map', 'SELECT array_except(array(9, 5, 1, NULL, 3, 1, NULL), array(3))', [9, 5, 1, None], 'same'),
    Case('array_distinct (order and NULL)', 'Array and map', 'SELECT array_distinct(array(3, NULL, 1, 3, NULL))', [3, None, 1], 'same'),
    Case('bround (one argument, odd)', 'Math', 'SELECT bround(3.5)', 4, 'same'),
    Case('bround (negative places)', 'Math', 'SELECT bround(25, -1)', 20, 'same'),
    Case('conv (negative to a signed base)', 'Math', 'SELECT conv(-10, 16, -10)', '-16', 'same'),
    Case('conv (negative to an unsigned base)', 'Math', "SELECT conv('-10', 16, 10)", '18446744073709551600', 'same'),
    Case('conv (stops at an invalid digit)', 'Math', "SELECT conv('12a', 10, 16)", 'C', 'same'),
    Case('conv (base out of range)', 'Math', "SELECT conv('10', 1, 10)", None, 'same'),
    Case('soundex (H and W keep a run)', 'String', "SELECT soundex('Ashcraft')", 'A261', 'same'),
    Case('soundex (not a letter first)', 'String', "SELECT soundex('1abc')", '1abc', 'same'),
    Case('format_number (no decimals)', 'String', 'SELECT format_number(1234567, 0)', '1,234,567', 'same'),
    Case('format_number (half-even)', 'String', 'SELECT format_number(2.5, 0)', '2', 'same'),
    Case('exists (NULL and no match)', 'Array and map', 'SELECT exists(array(1, NULL, 3), x -> x % 2 = 0)', None, 'same'),
    Case('transform (with index)', 'Array and map', 'SELECT transform(array(1, 2, 3), (x, i) -> x + i)', [1, 3, 5], 'same'),
    Case('filter (with index)', 'Array and map', 'SELECT filter(array(0, 2, 3), (x, i) -> x > i)', [2, 3], 'same'),
    Case('filter (drop NULLs)', 'Array and map', 'SELECT filter(array(0, NULL, 2, 3, NULL), x -> x IS NOT NULL)', [0, 2, 3], 'same'),
    Case('exists (looking for NULL)', 'Array and map', 'SELECT exists(array(0, NULL, 2, 3, NULL), x -> x IS NULL)', True, 'same'),
    Case('forall', 'Array and map', 'SELECT forall(array(1, 2, 3), x -> x % 2 == 0)', False, 'same'),
    Case('forall (all match)', 'Array and map', 'SELECT forall(array(2, 4, 8), x -> x % 2 == 0)', True, 'same'),
    Case('forall (NULL and a miss)', 'Array and map', 'SELECT forall(array(1, NULL, 3), x -> x % 2 == 0)', False, 'same'),
    Case('forall (NULL and no miss)', 'Array and map', 'SELECT forall(array(2, NULL, 8), x -> x % 2 == 0)', None, 'same'),
    Case('aggregate (finish)', 'Array and map', 'SELECT aggregate(array(1, 2, 3), 0, (acc, x) -> acc + x, acc -> acc * 10)', 60, 'same'),
    Case('aggregate (struct accumulator)', 'Array and map', "SELECT aggregate(array(1, 2, 3, 4), named_struct('sum', 0, 'cnt', 0), (acc, x) -> named_struct('sum', acc.sum + x, 'cnt', acc.cnt + 1), acc -> acc.sum / acc.cnt)", 2.5, 'same'),
    Case('reduce', 'Array and map', 'SELECT reduce(array(1, 2, 3), 0, (acc, x) -> acc + x)', 6, 'same'),
    Case('reduce (finish)', 'Array and map', 'SELECT reduce(array(1, 2, 3), 0, (acc, x) -> acc + x, acc -> acc * 10)', 60, 'same'),
    Case('array_sort (comparator)', 'Array and map', 'SELECT array_sort(array(5, 6, 1), (left, right) -> CASE WHEN left < right THEN -1 WHEN left > right THEN 1 ELSE 0 END)', [1, 5, 6], 'same'),
    Case('array_sort (descending comparator)', 'Array and map', "SELECT array_sort(array('bc', 'ab', 'dc'), (left, right) -> CASE WHEN left IS NULL AND right IS NULL THEN 0 WHEN left IS NULL THEN -1 WHEN right IS NULL THEN 1 WHEN left < right THEN 1 WHEN left > right THEN -1 ELSE 0 END)", ['dc', 'bc', 'ab'], 'same'),
    Case('zip_with', 'Array and map', 'SELECT zip_with(array(1, 2), array(3, 4), (x, y) -> x + y)', [4, 6], 'same'),
    Case('zip_with (strings)', 'Array and map', "SELECT zip_with(array('a', 'b', 'c'), array('d', 'e', 'f'), (x, y) -> concat(x, y))", ['ad', 'be', 'cf'], 'same'),
    Case('zip_with (unequal lengths)', 'Array and map', 'SELECT zip_with(array(1, 2, 3), array(10), (x, y) -> coalesce(y, 0) + x)', [11, 2, 3], 'same'),  # by hand: the shorter array is padded with NULL
    Case('map_filter', 'Array and map', 'SELECT map_filter(map(1, 0, 2, 2, 3, -1), (k, v) -> k > v)', {1: 0, 3: -1}, 'same'),
    Case('transform_keys', 'Array and map', 'SELECT transform_keys(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> k + 1)', {2: 1, 3: 2, 4: 3}, 'same'),
    Case('transform_keys (key and value)', 'Array and map', 'SELECT transform_keys(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> k + v)', {2: 1, 4: 2, 6: 3}, 'same'),
    Case('transform_values', 'Array and map', 'SELECT transform_values(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> v + 1)', {1: 2, 2: 3, 3: 4}, 'same'),
    Case('transform_values (key and value)', 'Array and map', 'SELECT transform_values(map_from_arrays(array(1, 2, 3), array(1, 2, 3)), (k, v) -> k + v)', {1: 2, 2: 4, 3: 6}, 'same'),
    Case('map_from_arrays', 'Array and map', "SELECT map_from_arrays(array(1.0, 3.0), array('2', '4'))", {1: '2', 3: '4'}, 'same'),
    Case('map_concat', 'Array and map', "SELECT map_concat(map(1, 'a', 2, 'b'), map(3, 'c'))", {1: 'a', 2: 'b', 3: 'c'}, 'same'),
    Case('map_from_entries', 'Array and map', "SELECT map_from_entries(array(struct(1, 'a'), struct(2, 'b')))", {1: 'a', 2: 'b'}, 'same'),
    Case('map_entries', 'Array and map', "SELECT map_entries(map(1, 'a', 2, 'b'))", [{'key': 1, 'value': 'a'}, {'key': 2, 'value': 'b'}], 'same'),
    Case('str_to_map', 'Array and map', "SELECT str_to_map('a:1,b:2,c:3', ',', ':')", {'a': '1', 'b': '2', 'c': '3'}, 'same'),
    Case('str_to_map (no value)', 'Array and map', "SELECT str_to_map('a')", {'a': None}, 'same'),
    Case('element_at (map)', 'Array and map', "SELECT element_at(map(1, 'a', 2, 'b'), 2)", 'b', 'same'),
    Case('element_at (map, missing key)', 'Array and map', "SELECT element_at(map(1, 'a', 2, 'b'), 3)", None, 'same'),
    Case('element_at (negative index)', 'Array and map', 'SELECT element_at(array(1, 2, 3), -1)', 3, 'same'),  # by hand: counts from the end
    Case('try_element_at', 'Array and map', 'SELECT try_element_at(array(1, 2, 3), 5)', None, 'same'),
    Case('map_contains_key', 'Array and map', "SELECT map_contains_key(map(1, 'a', 2, 'b'), 2)", True, 'same'),
    Case('map_contains_key (missing key)', 'Array and map', "SELECT map_contains_key(map(1, 'a', 2, 'b'), 3)", False, 'same'),
    Case('get_json_object (nested)', 'JSON', 'SELECT get_json_object(\'{"a":{"b":"c"}}\', \'$.a.b\')', 'c', 'same'),
    Case('get_json_object (array index)', 'JSON', 'SELECT get_json_object(\'{"items":["apple","pear"]}\', \'$.items[0]\')', 'apple', 'same'),
    Case('get_json_object (every element)', 'JSON', 'SELECT get_json_object(\'{"items":[{"n":1},{"n":2}]}\', \'$.items[*].n\')', '[1,2]', 'untranslated', "Refused here: a [*] path would give a list, not the JSON text '[1,2]'. Read the values with from_json, or use a path to one element."),
    Case('get_json_object (bracket name)', 'JSON', 'SELECT get_json_object(\'{"first name":"Ada"}\', \'$["first name"]\')', 'Ada', 'same'),
    Case('get_json_object (no match)', 'JSON', 'SELECT get_json_object(\'{"a":"b"}\', \'$.missing\')', None, 'same'),
    Case('from_json', 'JSON', 'SELECT from_json(\'{"a":1, "b":0.8}\', \'a INT, b DOUBLE\')', {'a': 1, 'b': 0.8}, 'same'),
    Case('from_json (name case)', 'JSON', 'SELECT from_json(\'{"a":1}\', \'A INT\')', {'A': None}, 'same'),
    Case('to_json (array of structs)', 'JSON', "SELECT to_json(array(named_struct('a', 1, 'b', 2)))", '[{"a":1,"b":2}]', 'same'),
    Case('to_json (map)', 'JSON', "SELECT to_json(map('a', named_struct('b', 1)))", '{"a":{"b":1}}', 'same'),
    Case('json_tuple', 'JSON', 'SELECT j.*, \'Spark SQL\' FROM json_tuple(\'{"a":1, "b":2}\', \'a\', \'b\') AS j', '1', 'error', 'DuckDB has no json_tuple. Use get_json_object for each field.'),
    Case('json_tuple (in the select list)', 'JSON', 'SELECT json_tuple(\'{"a":1, "b":2}\', \'a\', \'c\'), \'Spark SQL\'', '1', 'error', 'DuckDB has no json_tuple. Use get_json_object for each field.'),
    Case('schema_of_json', 'JSON', 'SELECT schema_of_json(\'[{"col":0}]\')', 'ARRAY<STRUCT<`col`: BIGINT>>', 'error', 'DuckDB has no schema_of_json.'),
    Case('lag', 'Window', "SELECT lag(b) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 1, 'same'),
    Case('lag (default)', 'Window', "SELECT lag(b, 1, 0) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a DESC LIMIT 1", 0, 'same'),  # by hand: no row before, so the default
    Case('lag (offset 2)', 'Window', "SELECT lag(b, 2) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 1, 'same'),  # by hand: two rows back
    Case('lead', 'Window', "SELECT lead(b) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", None, 'same'),
    Case('lead (default)', 'Window', "SELECT lead(b, 1, -1) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", -1, 'same'),  # by hand: no row after, so the default
    Case('nth_value', 'Window', "SELECT nth_value(b, 2) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 1, 'same'),
    Case('nth_value (too few rows)', 'Window', "SELECT nth_value(b, 2) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a DESC LIMIT 1", None, 'same'),
    Case('ntile', 'Window', "SELECT ntile(2) OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 2, 'same'),
    Case('percent_rank', 'Window', "SELECT percent_rank() OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A1', 3), ('A1', 6), ('A1', 7), ('A1', 7), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 0.8333333333333334, 'same'),
    Case('cume_dist', 'Window', "SELECT cume_dist() OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b LIMIT 1", 0.6666666666666666, 'same'),
    Case('rank', 'Window', "SELECT rank() OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 3, 'same'),
    Case('dense_rank', 'Window', "SELECT dense_rank() OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 2, 'same'),
    Case('row_number', 'Window', "SELECT row_number() OVER (PARTITION BY a ORDER BY b) FROM VALUES ('A1', 2), ('A1', 1), ('A2', 3), ('A1', 1) AS tab(a, b) ORDER BY a, b DESC LIMIT 1", 3, 'same'),
)


def run(case: Case, con: Any = None) -> tuple[str, Any]:
    """(outcome, value) for ``case`` on this PC: the value is DuckDB's answer, or the error text."""
    import duckdb

    from .. import sql
    from .local_sql import _databricks

    try:
        translated = ";\n".join(sql.duckdb_sql(s) for s in _databricks(case.sql))
    except Exception as exc:  # noqa: BLE001 - any failure to translate is the outcome being measured
        return "untranslated", f"{type(exc).__name__}: {exc}"
    con = con or duckdb.connect()
    try:
        row = first_row(con.execute(translated))
    except Exception as exc:  # noqa: BLE001 - DuckDB refusing the translated SQL is the outcome
        return "error", f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    value = row[0] if row else None
    return ("same" if same(value, case.expected) else "differs"), value


def same(got: Any, want: Any) -> bool:
    """Equal as Databricks would show it: numbers by value (3 == 3.0 == DECIMAL 3), dates and
    timestamps by value, lists item by item."""
    if isinstance(want, (list, tuple)):
        return isinstance(got, (list, tuple)) and len(got) == len(want) and all(same(g, w) for g, w in zip(got, want))
    if isinstance(want, bool) or isinstance(got, bool):
        return type(got) is type(want) and got == want
    if isinstance(want, (int, float, Decimal)) and isinstance(got, (int, float, Decimal)):
        return abs(float(got) - float(want)) < 1e-9
    if isinstance(want, dt.datetime) or isinstance(got, dt.datetime):
        return isinstance(got, dt.datetime) and isinstance(want, dt.datetime) and got.replace(tzinfo=None) == want
    return bool(got == want and type(got) is type(want))


def table_markdown() -> str:
    """The docs text: the functions that don't answer the same, as a table, then the ones that do."""
    words = {"differs": "Different result", "varies": "Can differ", "error": "Doesn't run",
             "untranslated": "Not translated"}
    rows = [f"Of {len(CASES)} checks on commonly used Databricks SQL built-in functions, these are the functions the "
            "DuckDB engine doesn't answer the same way.", "",
            "| Function | Group | On this PC | What to know |", "| --- | --- | --- | --- |"]
    rows += [f"| `{c.function}` | {c.group} | {words[c.outcome]} | {c.note} |" for c in CASES if c.outcome != "same"]
    same_ = [f"`{c.function}`" for c in CASES if c.outcome == "same"]
    return "\n".join(rows) + f"\n\nThese {len(same_)} checks answer the same as on Databricks: " + ", ".join(same_) + "."
