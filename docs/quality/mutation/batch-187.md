# Mutation testing, batch 187

One module, run uncapped: every one of its mutants, not a sample of 150.
- The Spark SQL built-ins written as DuckDB SQL (`spark_builtins_sql.py`).

Batch 8 sampled 150 of this module's mutants then, 591 in all (75.3% before, 93.3% after). The module has grown since, with upper, lower, initcap, levenshtein, array_join, shifts and more, to 879 mutants. This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 879 of 879, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` bdf2c1775 (2026-11-19). `main` has changed the module since (b8152b64f, #3069, at 82f8e2101); the new tests pass there too, on both DuckDB versions, and the two bugs below still fail there. It used `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy. Each pytest run had a temporary folder of its own, a fresh one per run through a local plugin, and none was left too deep to remove, so another run's leftover files could not fail a mutant. A few "killed" mutants of both runs were run again by hand to see a test really fail.

Batch 8 ran test_spark_builtins_sql, its _edges and _mutation files and test_spark_session_functions. A first run with those and test_spark_builtins_sql_cov2 was stopped after 59 mutants: most of what the module gained since batch 8 is tested in other files, so it scored code those files test as untested. The run here adds test_spark_string_edges, test_spark_text_arg_nesting, test_spark_format_string, test_spark_hash_refused, test_spark_complex and test_spark_datetime; the score counts only the files given. Two of them read `docs/`, so `test_spark_complex.py::test_docs_list_what_still_differs` and `test_spark_datetime.py::test_what_still_differs_is_listed_in_the_compatibility_docs` were left out with `--deselect`. Others that test the module, such as test_spark_arrays_strings_values (25 s), test_spark_differential_fixes (30 s) and test_spark_hashing_values (76 s), were left out for time.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used.

A mutant whose run times out counts as killed. Two timed out in the "before" run: a changed comparison in the table of characters whose title case DuckDB gets wrong (`_title_table`, line 563), and one in which arguments `_read_once` binds (line 423); each made the test run too slow to finish. None timed out in the "after" run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_builtins_sql.py` | test_spark_builtins_sql, test_spark_builtins_sql_mutation, test_spark_builtins_sql_edges, test_spark_builtins_sql_cov2, test_spark_session_functions, test_spark_string_edges, test_spark_text_arg_nesting, test_spark_format_string, test_spark_hash_refused, test_spark_complex, test_spark_datetime (+ test_spark_builtins_sql_uncapped) | 879 of 879 (859 + 20), uncapped | 681 / 198 | **77.5%** | 668/859 → 803/859 | 13/20 → 15/20 | 818 / 61 | **93.1%** | 57 equivalent, 3 not reached, 1 not killed |

The after score is short of 95%: 57 of the 61 left are equivalent, and 3 more are code no query here reaches. Without those 60, the score would be 818 of 819.

### Bugs found

- `sha2('abc', 256.0)`, a bit length written as a decimal, is NULL here. Spark casts the length to an int and gives the SHA-256. `_sha2` reads a number literal's digits itself (line 99) and takes `256.0` for a length it doesn't know. The test is marked `xfail(strict=True)`; it isn't fixed here.
- `shiftleft` of an int column is a `bigint` here, where Spark's is an `int` (the value is right). `_shift` writes a CASE over the value's type when the value isn't an int literal (line 1241), and its int and long branches make the result a BIGINT. The int literal `-2147483648` gave a `bigint` too, since it reaches `_shift` already rewritten. #3150 has since fixed it on `main`, so `test_shiftleft_of_an_int_column_is_an_int` is now a plain test.

Seen while writing the tests, not pinned: `instr(12345, 34)` stops with DuckDB's Binder error, where Spark casts both arguments to text and gives 3; only the first is cast here.

### What the survivors were, and the tests that now kill them

- **spark_builtins_sql.py**:
  - base64 breaks a line after 76 characters; sha2 with 0, 224, 256, 384, 512, another length, or a length from the data.
  - monotonically_increasing_id numbers a select's rows from 0 and is refused in ORDER BY.
  - Text passes through: a column cast to text is written out without a lambda, a Spark cast to string and upper's result aren't cast again, a call that isn't a column is read once, and levenshtein writes two columns out without binding them.
  - `java_value_text` and `spark_text_arg_sql` write a value as Spark does (an array as DuckDB's text there, `1.0E20`); a DataFrame expression is read with ANSI off.
  - initcap of a rewritten argument and of `ǆ`; upper's `ß`, lower's final sigma and `İ`; levenshtein's threshold.
  - array_insert pads up to 10,000,000 NULLs and refuses one more; array_join skips or replaces NULLs, writes a double as Java does and refuses a nested array; locate of empty text, also from the data; concat_ws with only a separator; contains with a bare NULL.
  - split refuses a pattern that matches empty text and leaves one Python can't read to the engine; substring_index with an overlapping delimiter, written out or from the data.
  - Shifts: an int literal stays an int, `-2147483649` and `2147483648` take the long path, a double is cut to an int, an int shifts by the count & 31.
  - octet_length of a number, text and binary; typeof names a struct and a map in `spark.sql`.
  - to_date with a format (Spark's year without one, two-digit years), Databricks SQL's to_date and to_timestamp_ntz stopping on text that isn't one, to_timestamp_ntz of a column with a format, EXTRACT from text and from an interval.
  - format_string refuses a stray `%` and an array for `%s`; json_tuple refuses a bare NULL; raise_error of a NULL says `null`; url_encode and url_decode; unbase64, unhex, isnan of NULL, sqrt of a negative number.
  - make_timestamp with a zone, a zone from the data and an offset; LATERAL VIEW explode keeps an array or struct element whole; a column named `window` stays the column when the query doesn't group by window(); a lambda's tuple names its fields after the columns, then col1, col2; from_csv of a column; btrim with and without characters; elt with one value; any() in a select list and in AND.
  - → `test_spark_builtins_sql_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_builtins_sql.py` | 1363 | `written_pattern`'s `and` to `or`, so a number reads as a pattern | Not killed: it changes only a number written as date_format's pattern, `date_format(d, 1)`. This PC stops there with DuckDB's Binder error, and the mutant reads `'1'` as the pattern and gives Spark's answer, `'1'`; a test would pin the stop. |
| `spark_builtins_sql.py` | 1779 | the refusal of a hash that lost its mark, removed | Not reached: no query found loses the mark (hash inside format_string, upper, initcap, levenshtein, array_join, to_csv, concat_ws and `\|\|` all kept it). The refusal is a safety net (as batch 8 found). |
| `spark_builtins_sql.py` | 1441 | the StrToTime fallback's `safe=True` to `False` | Not reached: to_date with a text format always has its written pattern here, in Spark SQL, in a DataFrame expression and in Databricks SQL. |
| `spark_builtins_sql.py` | 961 | `strptime.set("safe", True)` to `False` | Not reached: that branch runs only for to_timestamp_ntz, which is built with `safe` False. |
| `spark_builtins_sql.py` | 404, 646 | `_is_text`'s and `_empty_match`'s `return False` to `return None` | Equivalent: each is read only by an `if`. |
| `spark_builtins_sql.py` | 397, 403 (two) | `_is_text`'s typed and Cast branches | Equivalent: a cast to text reaches `_is_text` already marked by `spark_text_cast`, so the Cast branch isn't reached, and only a marked node has a type there. The SQL was the same for CAST, TRY_CAST, `VARCHAR(5)`, `string()` and a cast in brackets. |
| `spark_builtins_sql.py` | 1442 | to_date's written-pattern rewrite, `return` to `return None` | Equivalent: the call left as it is is written the same SQL by the Spark datetime rewrite after it (checked for `'MM-dd'` and `'yy-MM-dd'`). |
| `spark_builtins_sql.py` | 357 | `spark_text_arg`'s default `bind=False` to `True` | Equivalent: every caller passes `bind`. |
| `spark_builtins_sql.py` | 1557 | `if keyed` negated in parse_url | Equivalent: both branches make the same replacement. |
| `spark_builtins_sql.py` | 622 | locate's `or` to `and`, so every locate and instr is rewritten | Equivalent: the rewrite only adds the case of empty text from a start of 1 or more, which is right wherever it applies; without a start it compares NULL, so the call itself is used. |
| `spark_builtins_sql.py` | 968, 977 (two), 1135, 1137 | make_timestamp's zone and arity checks; to_timestamp_ltz's and try_to_timestamp's `safe` | Equivalent: a number given as the zone stops with the same unknown-zone error; a 7-argument make_timestamp is written the same SQL either way and another arity stops either way; the `safe` flag is read only for to_timestamp_ntz. |
| `spark_builtins_sql.py` | 1072, 1073, 1074 | `_spark_literal`'s check and return | Equivalent: hash and xxhash64 arguments are typed before this rewrite, in Spark SQL and in Databricks SQL: the same values for `-2147483648` (723455942, Spark's Murmur3), `2147483648`, `-5`, `-2.5`, `'a'` and `1`. |
| `spark_builtins_sql.py` | 1619 | `_uncast` negated | Equivalent: as batch 8 found, sqlglot writes a column and its identifier the same, and a recast timestamp is the same timestamp. |
| `spark_builtins_sql.py` | 1655 | `_any`'s check negated | Equivalent: an aggregate any() is then left to the rewrite that writes it as BOOL_OR (the same values, also inside AND), and `x = ANY (subquery)` returns at the subquery check just after. |
| `spark_builtins_sql.py` | 132, 158 | the `kf_own_window` mark; some()'s arity | Equivalent: the same SQL with and without the mark, also beside a window with ORDER BY; sqlglot reads some() as its own node, written `BOOL_OR` either way. |
| `spark_builtins_sql.py` | 460 (two) | initcap's delimiter check | Equivalent: sqlglot always gives Spark's initcap the `' '` delimiter. |
| `spark_builtins_sql.py` | 665 (two) | the negative split limit times -1 to divided by -1, and to times -0 | Equivalent: the limit is only compared with 0, and is still 0 or less. |
| `spark_builtins_sql.py` | 910 | from_csv's field check `or` to `and` | Equivalent: the schema parses only to fields with a type; one without (`'a'`) stops with the same DuckDB error. |
| `spark_builtins_sql.py` | 1063 | the hash template cache's size 32 to 33 | Equivalent. |
| `spark_builtins_sql.py` | 1104, 930, 790, 1056 (two) | csc's arity; timestamp_seconds through the decimal path; map_zip_with's third argument; window() with no or one argument | Equivalent, as batch 8 found for each kind: sqlglot writes csc itself; times 1 is the same; the template has no `fj_arg2`; window() needs two arguments and is an error otherwise. |
| `spark_builtins_sql.py` | 1600, 1691 (two), 1796 | EXTRACT of a cast, unix_timestamp's format, trunc's cast | Equivalent: a cast round the cast gives the same field for a number, text and a date (in the session's UTC); the same SQL either way for unix_timestamp with a Spark format; trunc reads a value already cast to DATE as the same date. |
| `spark_builtins_sql.py` | 863, 1612, 1451 | mask with five replacements, date_part with a number as its field, to_char with a number as its format | Equivalent in effect: each stops either way, with another error. |
| `spark_builtins_sql.py` | 868, 562 | `zip(..., strict=True)` to `False` | Equivalent: the lists are always the same length. |
| `spark_builtins_sql.py` | 887, 795 | to_csv's field forms; struct field names quoted | Equivalent: the same SQL and values for named_struct, `struct(1 AS a, ...)` and `struct(x, y)`; field names such as `a b`, `order` and `my x` come out the same unquoted. |
| `spark_builtins_sql.py` | 1672 | the naming fallback's class list | Equivalent: spark_sql_names names every selected rewritten call first. |
| `spark_builtins_sql.py` | 423 (two) | `uses(i) > 1` to `> 0` and `>= 1` | Equivalent: an argument the template names once is then bound too, with the same values; only the SQL's form changes. |
| `spark_builtins_sql.py` | 513 (two), 560, 561 (three) | the character ranges' ends, and the title table's single-character branch | Equivalent: the ends moved by one take in or leave out U+0080, U+20000 or U+E000, none of which has a case; no character has a title case longer than one with an upper case of one. |
| `spark_builtins_sql.py` | 236 | substring_index's overlap check from `k = 0` | Equivalent: `k = 0` compares `''` with the whole delimiter, never equal unless it is `''`, where both ways give `''`. |
| `spark_builtins_sql.py` | 753 | format_string's rounding to 19 places, not 18 | Equivalent: ROUND of a `DECIMAL(38, 18)` to 19 places is the same value. |
| `spark_builtins_sql.py` | 764, 702 | format_string's `<` to `<=` and `>` to `>=` | Equivalent: each only adds `''` to the text. |
| `spark_builtins_sql.py` | 1240 | `-2 ** 31 <= literal` to `<` | Equivalent: `-2147483648` reaches `_shift` already rewritten, not as an int literal. |

