# Mutation testing, batch 221

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Spark's decimal result types on this PC: `+`, `-`, `*`, `/`, `%`, `pmod`, `div`, sums and averages, `round`, `bround`, `ceil` and `floor`, whole numbers that wrap as Java's do, a date plus whole days, and the DataFrame API's side (`spark_decimals.py`).

Batch 28 sampled 150 of this module's 237 mutants then (86.0% before, 97.3% after), and batch 163 re-scored its 14 boundary mutants (21.4% before, 71.4% after). It now has 597, and this run ran all 597 of 597.

The run was at `main` 3424d8f335 (2026-11-23), with the method of batch 218: tests first passed on the unmutated copy, paths were relative to `sidecar/`, and each pytest session got a fresh `--basetemp` folder of its own from a small run-only plugin that isn't committed and that removed the folder when the session ended. A fixed per-lane `--basetemp` isn't enough, because pytest deletes it at the start of every session, so a folder nested too deep by one mutant would fail every later session and score each later mutant as killed. The plugin's note of any folder nested 40 levels deep was cleared by the start of the "after" run before it was read, so that check is missing for this run; the last kills of the run don't have near-identical times. A first run, at `main` ea988e99e, was stopped part way by a pause in the push and thrown away; the module hadn't changed since, and the full run was made again from the start. Three kills picked at random were checked by hand, as in batch 182: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (22, 15 and 224).

The set is every test file that uses the module, about 25 seconds on this busy machine, so no file was left out. Two tests read `docs/sql-compatibility.md`, which the tool's copy of `sidecar/` doesn't have, so they were left out with `--deselect`: `test_spark_decimal_rules.py::test_what_still_differs_is_listed_in_the_compatibility_docs` and `test_spark_integer_values.py::test_whole_number_arithmetic_answers_as_spark_3_5`. The second also checks Spark's answers for whole-number arithmetic, so several survivors below are mutants it may kill; the new tests check those answers on their own.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways, with DuckDB 1.5.6 and 2.0.0. No mutant timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_decimals.py` | test_shell_and_decimals_edges, test_spark_ansi, test_spark_dataframe_decimals, test_spark_decimal_rules, test_spark_decimal_types, test_spark_decimals_boundary, test_spark_decimals_cov3, test_spark_decimals_mutation, test_spark_double_rounding, test_spark_integer_values, test_sql_run_edges, with the two `--deselect`s above (+ test_spark_decimals_uncapped) | 597 of 597 (572 + 25), uncapped | 483 / 114 | **80.9%** | 472/572 → 534/572 | 11/25 → 16/25 | 550 / 47 | **92.1%** | 41 equivalent, 2 not reached, 2 tied to a bug, 2 not killed |

The score is under the 95% goal. Of the 47 left, 41 are equivalent, and the other six are explained in the table below.

### Bugs found

- **spark_decimals.py**: the smallest BIGINT `div -1` stops with DuckDB's "Overflow in division" error. Spark with ANSI off gives -9223372036854775808, as Java's long division does. Whole-number `div` is written as `CAST(a // NULLIF(b, 0) AS BIGINT)` (`_integral_divide`), and DuckDB's `//` refuses that one quotient. `test_the_smallest_bigint_div_minus_one_is_itself` pins it as a strict expected failure.
- **spark_decimals.py**, on DuckDB 2.0 only: a decimal sum past its type's whole digits stops with DuckDB's own "Overflow in SUM of DECIMAL" error, where Spark with ANSI off, and this PC on DuckDB 1.5.6, give NULL. The sum's digits only go past its type after they go past 38, and DuckDB 2.0's `sum` checks 38 digits itself, before `_summed`'s check runs. `test_a_sum_past_its_whole_digits_is_null_with_ansi_off` pins it as a strict expected failure on DuckDB 2.

Neither is fixed here.

### What the survivors were, and the tests that now kill them

- **spark_decimals.py**:
  - A date plus or minus whole days, weeks or months is a date, also when one is added after another; a date plus hours and a timestamp plus days stay timestamps.
  - A Spark cast to a whole number counts as that integer type, also when sqlglot can't type the cast's SQL.
  - A decimal sum past its whole digits is an error with ANSI on, with Spark's message on DuckDB 1.5.6.
  - A frame with no decimal column is left alone unless it rounds a double, and so is one whose columns are named like the rewrite's lambdas.
  - TINYINT and BIGINT wrap in their own widths. The smallest BIGINT literal wraps, and one under it is a decimal, which doesn't. `try_add` of whole numbers is NULL past the range, and an array index doesn't stop the array itself from wrapping.
  - `pmod` of a negative decimal is positive, and `%` keeps the sign. A remainder of wide decimals takes Spark's type when its operands fit 38 digits.
  - `div` of a decimal by a whole number, and of a whole number by a decimal, truncates toward zero. `div` of wide decimals is exact when their digits fit 38. A `div` result, and `ceil` of a whole number or a double, count as BIGINTs in a later product.
  - `ceil` and `floor` of a double are BIGINTs, 0 for NaN and the nearest end past the range; of a whole number they are BIGINTs too. `round` of a whole number keeps DuckDB's type, and `round` of text isn't rewritten as a double's.
  - A double's `round` refused past 20 places is refused in the DataFrame API too, is named `round` even in a CASE's ELSE, and suggests a whole decimal for negative places. Rounding to exactly 20 places either way runs. A double under 1 is rounded from every digit of its text, and one past 10^37 keeps its value.
  - `bround` of a double to tens rounds half to even next to a decimal product. A sum of a function of a decimal is typed by that function.
  - → `test_spark_decimals_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_decimals.py` | 97, 708 (2), 571 (2), 691 (2), 692, 694 (4), 716, 682 (2), 177, 667 (5) | `_adjust` at exactly 38; places 0 in `_ceil_floor` and in bround's last digit; `bits` 32 or 33; the half's scale; the `::DOUBLE` on a bound; `DECIMAL(0 - places, ...)`; the refusal's suggestion at 1 place; `DECIMAL(scale + 0, ...)` in division; `_DOUBLE_SCALES` bounds and scales | Equivalent: each gives the same SQL value. At precision 38 `_adjust` keeps the scale either way. `_unit(0)` is `1`, and `RIGHT(x, 1)` is `SUBSTR(x, 40, 1)` of 40 characters. 8-, 16- and 32-bit sums fit either wide type. A unit under 1 fits `DECIMAL(s, s)`. A double from 10^17 to 10^18 has no fraction to lose at scale 20, and one from 10^37 to 10^38 fits `DECIMAL(38, 0)` with nothing under 10^21 to round. |
| `spark_decimals.py` | 230, 291, 328 (2) | `normalize=True`, `quote_identifiers=True`, `identify=True` | Equivalent: the names are kept in lower case already, and the qualified copy is only typed, so quoting its names changes no type. |
| `spark_decimals.py` | 299, 282, 297 | `zip(..., strict=False)`; `and` for `or` on the parsed frame's columns; `None` for the unchanged list when the root isn't a SELECT | Equivalent: the parsed SELECT and its rewrite have the same columns (a different count would raise in `zip` and be caught), and `_retype` replaces only nodes inside the SELECT, so its root is always that SELECT. |
| `spark_decimals.py` | 314, 435, 457 (2), 383, 371 (2), 542, 546 | marks counted from 1; `_rewrite`'s default `ansi` (every call passes it); `operands[0]` or `[1]` checked twice (neither is ever None for a binary operator); `None` for `False` | Equivalent. A date sum already inside `CAST(... AS DATE)` is cast again (`371`, `True`), which gives the same date. |
| `spark_decimals.py` | 407 | `or` for `and` in `_output_names`' SELECT and query check | Equivalent: a SELECT is always a query, and a query whose first part isn't a SELECT is a parenthesised one, whose `expressions` are empty, so no column is named either way. |
| `spark_decimals.py` | 425, 427 | `i <= stars[0]`, `i >= stars[-1]` | Equivalent: a star is never one of the changed columns counted. |
| `spark_decimals.py` | 525 | the smallest INT literal (`-2 ** 31 <`) | Equivalent here: `sql.parse` writes `-2147483648` as `CAST(... AS INT)` before this module reads it, so the literal path never sees it. |
| `spark_decimals.py` | 337 | `or` for `and` when a mark is looked up | Not reached: every mark survived `qualify` in every statement tried. A lost one would raise and be caught, leaving the statement as it was. |
| `spark_decimals.py` | 544 | any join parent, not only its ON, stops a sum from wrapping | Not reached: a joined subquery's arithmetic meets its own SELECT first, and a table function's arguments weren't rewritten either way. |
| `spark_decimals.py` | 584 (2) | `a[1] == "int"` or `b[1] == "int"` in `_integral_divide` | Tied to the first bug above: the decimal path gives the same answers, except for the smallest BIGINT `div -1`, where it gives NULL and the whole-number path stops with an error. Neither is Spark's answer. |
| `spark_decimals.py` | 689 (2) | `bround`'s half for negative places made a `TypeError` | Not killed: the error is caught, and `sql.parse`'s own half-even CASE for `bround`, which then stays, gave the same answers in every case tried (halves at 10^2 to 10^22). |
