# Mutation testing, batch 325

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Spark's comparisons of text with a number, for Spark SQL and the DataFrame API on this PC (`spark_text_compare.py`, WR-254).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 206 of 206 mutants ran (204 point + 2 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_spark_text_comparisons, the module's own file, about 3 s a session. The T-SQL comparison tests (test_tsql_text_comparisons, test_local_tsql) are about another module and were left out. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_text_compare.py` | test_spark_text_comparisons (+ test_spark_text_compare_mutation) | 206 of 206 (204 + 2) | 134 / 72 | **65.0%** | 134/204 → 189/204 | 0/2 → 2/2 | 191 / 15 | **92.7%** | 14 equivalent, 1 not reached |

The end-to-end tests read Spark's answers through the kernel, where many rewrites give the same answer as DuckDB would for the values used, so most survivors were in which comparisons are rewritten at all and in the exact casts. The new file reads the rewrite directly, against a `MappingSchema`, and runs some rewrites on DuckDB.

## Bugs found

- **The DataFrame API's `<=>` (`eqNullSafe`) of text with a number isn't rewritten.** `spark_text_comparison_columns` reads an expression only when it has `=`, `<`, `>`, ` IN `, `BETWEEN` or `CASE` in it, and DuckDB writes `<=>` as `IS NOT DISTINCT FROM`, so the comparison goes to DuckDB as it is. On this PC `df.filter(F.col("t").eqNullSafe(1))` and `df.selectExpr("t <=> 1")` over a row whose `t` is `''` stop with "Could not convert string '' to INT32", where Spark gives false (`1 <=> ''` is false in test_spark_text_comparisons, read from Spark 3.5.3). Spark SQL on this PC is right: `SELECT '' <=> 1` gives false. Pinned with `xfail(strict=True)` in `test_the_dataframe_api_rewrites_a_null_safe_comparison`; not fixed here.

### What the survivors were, and the tests that now kill them

- **spark_text_compare.py**:
  - Numbers compared with numbers (`n = 1`, `n BETWEEN 1 AND 3`, `d BETWEEN 1 AND 2`, `CASE n WHEN 1`, `n IN (1, 2)`) are left alone, as is text compared with a date, or a simple CASE whose values are numbers of two types or text and a number.
  - A decimal is compared with text as a double (`CAST(d AS DOUBLE)`, `CAST(1.5 AS DOUBLE)`) and a whole number as it is.
  - A whole literal is an INT up to 2147483647 and a BIGINT from 2147483648 (and at -2147483648), so `'3000000000' = 3000000000` is true.
  - IN writes a double as Spark does (`1e10` is `'1.0E10'`), a decimal literal as written (`1.50` is `'1.50'`), and a whole number as a plain cast.
  - BETWEEN with ends of two types casts the text to each end's type through one lambda; a number BETWEEN text ends casts each text end.
  - A simple CASE keeps its ELSE.
  - A statement with no comparisons, or that can't be typed, is returned as the same object; a column not in the schema doesn't stop the rewrite; a side sqlglot can't type (a SELECT alias in WHERE) leaves only its own comparison alone.
  - A name like the rewrite's lambda, as an alias or a column of any table, leaves the statement as it was.
  - A statement without text asks the connection only the one cheap question, and one without columns asks nothing.
  - The growth limit: a rewrite exactly 20,000 characters past twice the statement runs, one character more is refused by name, also through the DataFrame API.
  - The DataFrame API returns each changed column with its alias, None for an unchanged one, and all None for an expression it can't parse or that splits into two columns.
  - → `test_spark_text_compare_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_text_compare.py` | 121 | `'e'` literal's DOUBLE → `None` | Equivalent: the type then comes from sqlglot, which types `1e2`, `-1e2` and `1e400` as DOUBLE too. |
| `spark_text_compare.py` | 137 | `query … or unnest …` → `and` | Equivalent: an IN over a query or UNNEST has no listed values, so its sides are only `this` either way. |
| `spark_text_compare.py` | 155 (2) | `quote_identifiers=False` or `identify=False` → `True` | Equivalent: they only change how the typed copy writes names; the types read back by mark are the same. |
| `spark_text_compare.py` | 190 | return `before` → `None` when a rewrite raises | Not reached: no statement built for this batch makes `_rewritten` or `duckdb_sql` raise; the guard is defensive. |
| `spark_text_compare.py` | 207 | `return False` → `return None` | Equivalent: the caller only reads it with `not`. |
| `spark_text_compare.py` | 245 | `first is None or second is None` → `and` | Equivalent: both ends are number types, for which `_text_as` never returns None. |
| `spark_text_compare.py` | 274 | `_numeric(operand) and …` → `or` | Equivalent: a text operand with a number value takes the branch before; with only text values, or an operand of another type, `_text_as` gives None and the CASE is left as it was, as before. |
| `spark_text_compare.py` | 273, 276, 292, 324 (4) | `zip(..., strict=True)` → `strict=False` | Equivalent: the lists are built from each other and are always the same length. |
| `spark_text_compare.py` | 285 | `isinstance(node, exp.In) and …` → `or` | Equivalent: every other node type returns before this line, and an IN over a query or UNNEST has only one value, so it is never both text and a number. |
| `spark_text_compare.py` | 319 | `normalize=False` → `True` | Equivalent: the frame's column names are lowercased just before, so normalizing them changes nothing. |
| `spark_text_compare.py` | 322 | `return unchanged` → `None` when the result isn't a SELECT | Equivalent: the rewrite never replaces the SELECT itself, only comparisons in it. |
