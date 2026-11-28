# Mutation testing, batch 504

A re-score of one module, run with `--kinds all`:
- Spark's decimal result types on this PC, in Spark SQL and the DataFrame API: arithmetic, sums and averages, `div`, round, bround, ceil and floor, Spark's round of a double, whole numbers that wrap as Java's do, and a date moved by whole days (`spark_decimals.py`). It was last scored in batch 28 at 86.0% before and 97.3% after (point mutants only), and its boundary mutants in batch 163 at 21.4% before and 71.4% after (`--kinds boundary`). The module has grown since: 237 point mutants then, 597 mutants now.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. One test reads `docs/`, so it was left out with `--deselect`: `test_spark_decimal_rules.py::test_what_still_differs_is_listed_in_the_compatibility_docs`, as in batch 163. The module has 597 mutants, so a sample of 150 was run (141 point + 9 boundary).

The test set was batch 28's and batch 163's files and every other test file that uses the module today: test_spark_decimal_types, test_spark_dataframe_decimals, test_shell_and_decimals_edges, test_spark_decimals_mutation, test_spark_decimals_boundary, test_spark_decimal_rules, test_spark_decimals_cov3, test_spark_ansi, test_spark_branch_types, test_spark_branch_types_mutation_b, test_spark_double_rounding, test_spark_integer_values, test_spark_text_compare_mutation and test_sql_run_edges.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, where two of the strict xfails below are set by DuckDB's version. The "after" run reran the survivors with `--only`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_decimals.py` | the 14 test files above (+ test_spark_decimals_mutation_b) | 150 of 597 (141 + 9) | 126 / 24 | **84.0%** | 122/141 → 133/141 | 4/9 → 7/9 | 140 / 10 | **93.3%** | 9 equivalent, 1 left without a test |

Four real bugs were found (below). Each has a strict xfail that a fix will turn into a pass.

### What the survivors were, and the tests that now kill them

- **spark_decimals.py**:
  - A sum is checked against its type's whole digits, not its precision: DECIMAL(20,2) holds 18, so 19 is NULL with ANSI off and Spark's overflow error with it on.
  - A timestamp moved by whole days keeps its time, and a DATE column moved by whole days stays a date.
  - Ceil and floor of a double round up and down and give whole numbers, and `ceil(double)` counts as a BIGINT operand: `ceil(f) * 1.5` is DECIMAL(23,1).
  - A round of a double to exactly 20 places, or -20, runs. A refused round in a CASE's ELSE is named `round()`, not `bround()`.
  - The literal 2147483648 is a BIGINT, so adding 1 doesn't wrap, while 2147483647 + 1 does. The smallest BIGINT, -9223372036854775808, wraps as a BIGINT when 1 is taken away.
  - An expression text that holds two columns ("a * 2, b") is left alone in the DataFrame path.
  - The strict xfails for the bugs below also kill three mutants that fix them: the two that drop the early return in `spark_decimal_columns` (lines 273 and 275), and the one that takes any typed operand as a double (line 654).
  - → `test_spark_decimals_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_decimals.py` | 314 | `marked = 0` to `1` | Equivalent: the marks are only keys, and a missing mark is tested with `is not None`. |
| `spark_decimals.py` | 407 | `or` to `and` in `_output_names` | Equivalent: a Select is always a Query, and a non-Select Query (a Subquery in a UNION) has no expressions, so the list is empty either way. |
| `spark_decimals.py` | 371 | `return False` to `True` when the sum is already cast to DATE | Equivalent: the sum is then cast to DATE twice, which gives the same value. |
| `spark_decimals.py` | 230 | `normalize=False` to `True` in `_schema` | Equivalent: since #2663 the names are put in lower case before the schema is made, so normalizing changes nothing. |
| `spark_decimals.py` | 667 | `10 ** 17` to `10 ** 18` in `_DOUBLE_SCALES` | Equivalent: a double from 10^17 up to 10^18 is a whole number with at most 18 digits, which DECIMAL(38, 20) also holds exactly. |
| `spark_decimals.py` | 457 | `operands[1] is None` to `operands[0] is None` | Equivalent: a parsed arithmetic node always has its right side. |
| `spark_decimals.py` | 299 | `zip(..., strict=True)` to `strict=False` | Equivalent: both lists come from the same SELECT, so they are the same length. |
| `spark_decimals.py` | 708 | `places >= 0` to `>` in `_ceil_floor` | Equivalent, as batch 163 said (line 532 then): at 0 places both branches multiply and divide by 1. |
| `spark_decimals.py` | 682 | `places > 0` to `>=` in the refusal's example | Equivalent: a round is refused only past 20 places either way, so places is never 0 there. |
| `spark_decimals.py` | 584 | `a[0] == "int"` to `a[1]` in `div` | Left without a test: two whole numbers then go through the decimal path, which gives the same BIGINT quotient. They differ only for -9223372036854775808 div -1, where the code stops with DuckDB's overflow error and the mutant gives NULL. Spark's answer there wasn't checked, so a test would pin an answer that may be wrong. |

### Real bugs found

- In the DataFrame API, `round()` of a double gives DuckDB's answer, not Spark's, when the frame has no DOUBLE or DECIMAL column: `spark.createDataFrame([(1,)], "n int").select(F.round(F.lit(0.285).cast("double"), 2))` gives 0.28, where Spark gives 0.29. `spark_decimal_columns` returns early on the frame's column types, so a double that is not a column is never seen. `test_round_of_a_double_in_a_frame_of_whole_numbers_is_sparks` is a strict xfail for it.
- On DuckDB 1.5.6, `F.round(F.lit(0.285), 2)` next to a DOUBLE column comes back as a Decimal, where Spark gives the double 0.29: the literal's text is read as a SQL decimal. `test_round_of_a_float_literal_stays_a_double` is a strict xfail on DuckDB 1.x; it passes on DuckDB 2.0.0.
- `round('0.285', 2)` stops with DuckDB's binder error, where Spark casts the string to a double and gives 0.29. `test_round_of_a_numeric_string_is_sparks` is a strict xfail for it.
- On DuckDB 2.0.0, a sum of DECIMAL(38,5) values past 38 digits stops with DuckDB's own "Overflow in SUM of DECIMAL", where Spark with ANSI off gives NULL (DuckDB 1.5.6 gives NULL). `test_a_sum_with_more_whole_digits_than_its_type_holds_is_null` is a strict xfail on DuckDB 2.
