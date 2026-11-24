# Mutation testing, batch 28

Three modules, none touched by an open pull request:
- Will this SQL run on another platform: the size limit at its edge, findings that can't be changed, QUALIFY's ORDER BY when a key names an output column (plain, qualified by the same table or another one, or an alias over another column), the hidden columns it adds otherwise, and Snowflake SQL that can't be rewritten for DuckDB but still goes through sqlglot for another platform (`sql_portability.py`).
- Spark's decimal result types: TINYINT, SMALLINT and the literal 0 as operands, a DECIMAL without its precision, a sum of an integer or a double next to an expression that changes, the names of unnamed columns before and after a star, a statement that isn't a query, an average that stays exact up to its limit, and the DataFrame API's alias (`spark_decimals.py`).
- Databricks VARIANT on this PC: where a number stops being a BIGINT or a DECIMAL, integer casts at the edges of their ranges, how much of a value an error repeats, a space in a string, nesting counted outside strings, an empty path, and a decimal type without a scale (`databricks_variant.py`).

Batch 27's modules (`integrations/scope.py`, `nbsymbols.py` and `spark_number_format.py`) were left to batch 27, which ran at the same time.

The run was at `main` 91ab7b3f6 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 27 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Three tests in `test_databricks_coverage.py` read `docs/`, so they were left out of the `databricks_variant.py` run: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_portability.py` | test_sql_portability, test_kudp_guardrails, test_snowflake_sql_variadic (+ test_sql_portability_mutation) | 145 of 145 | 127 / 18 | **87.6%** | 142 / 3 | **97.9%** | 3 equivalent |
| `spark_decimals.py` | test_spark_decimal_types, test_spark_dataframe_decimals, test_shell_and_decimals_edges (+ test_spark_decimals_mutation) | 150 of 237 | 129 / 21 | **86.0%** | 146 / 4 | **97.3%** | 4 equivalent |
| `databricks_variant.py` | test_databricks_variant_edges, test_databricks_coverage (+ test_databricks_variant_mutation) | 150 of 241 | 122 / 28 | **81.3%** | 143 / 7 | **95.3%** | 7 equivalent |

The three new test files pass on DuckDB 1.5.6. Two real bugs were found (below), and each has a strict xfail that a fix will turn into a pass.

### What the survivors were, and the tests that now kill them

- **sql_portability.py**:
  - SQL of exactly 200,000 characters being checked and one character more being refused, with the message.
  - A finding that can't be changed.
  - An ORDER BY key that names an output column being used as it is: plain, in another case, or qualified by the same table.
  - A key that names another table's column, or a table column that shares its name with an output alias, being hidden in the subquery as `_o1` and not returned.
  - Each hidden key getting its own name, in order.
  - Snowflake SQL that DuckDB can't take still being rewritten by sqlglot for Spark and PostgreSQL, with sqlglot's reason.
  - → `test_sql_portability_mutation.py`.
- **spark_decimals.py**:
  - A TINYINT operand being decimal(3,0), a SMALLINT decimal(5,0), the literal 0 decimal(1,0), and `00.5` decimal(1,1).
  - A DECIMAL without its precision leaving only that expression as it was.
  - A sum of an integer or a double keeping DuckDB's type next to an expression that changes.
  - Unnamed columns, without a star and after one, keeping DuckDB's names.
  - An INSERT ... VALUES still being rewritten.
  - An average of a 34-digit decimal staying exact, not going through DOUBLE.
  - A DataFrame expression's alias being kept apart from its SQL.
  - → `test_spark_decimals_mutation.py`.
- **databricks_variant.py**:
  - An error repeating at most 100 characters of a value.
  - 2^63 being a decimal, so a BIGINT cast stops by name, while the smallest and largest BIGINT cast.
  - TINYINT, SMALLINT and INT casts holding exactly their ranges.
  - A decimal having at most scale 38 and 38 digits, so one more of either makes it a DOUBLE.
  - A space in a string being kept, not escaped.
  - Nesting being counted outside strings, after an escaped quote or backslash, and not across siblings.
  - An empty path not being a path.
  - A decimal type without a scale stopping by name.
  - A double that prints without an exponent casting to a decimal.
  - → `test_databricks_variant_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `sql_portability.py` | 137 (2) | no QUALIFY: `return False` dropped, or → `True` | Equivalent: it's only called for a SELECT that has QUALIFY. |
| `sql_portability.py` | 179 | `return` dropped for a rewrite that isn't a SELECT | Equivalent: sqlglot's `eliminate_qualify` gives a SELECT back for a SELECT. |
| `spark_decimals.py` | 122 | `scale + 1` → `scale` in the division's unit | Equivalent: the unit is below 1, so DECIMAL(scale, scale) holds it too, and the scale is at least 4 here. |
| `spark_decimals.py` | 219 | `return unchanged` dropped when the root isn't a SELECT | Equivalent: the rewrite never replaces the root SELECT. |
| `spark_decimals.py` | 244, 245 | `quote_identifiers` and `identify` → `True` | Equivalent: the qualified copy is only read for types. |
| `databricks_variant.py` | 35 | BIGINT's 64 bits → 65 | Equivalent: a JSON integer past 2^63 is a decimal, so a BIGINT cast never sees it. |
| `databricks_variant.py` | 57 | `copysign(1, x) < 0` → `< 1` | Equivalent: `copysign(1, x)` is 1 or -1. |
| `databricks_variant.py` | 77 | 20 characters → 21 for a BIGINT | Equivalent: JSON has no leading zeros, so 21 characters is always past 2^63. |
| `databricks_variant.py` | 82 | `max(-exponent, 0)` → `max(-exponent, 1)` | Equivalent: the scale is only compared with 38. |
| `databricks_variant.py` | 238 | `_at`'s missing path → found | Equivalent: a missing path and a VARIANT null both give NULL. |
| `databricks_variant.py` | 293 | `and` → `or` in the double check | Equivalent: a VARIANT is read back from its text, where a double without an exponent reads as a decimal, so a double always has one. |
| `databricks_variant.py` | 322 | `side_effects=False` → `True` | Equivalent in results: it only stops DuckDB from folding a call with constant arguments. |

### Real bugs found

- `databricks_variant.parse_json` rounds a decimal with more than 28 digits, because `Decimal.normalize()` uses the default 28-digit context. For example, `1111111111111111111111111111111111111.5` comes back as `1111111111111111111111111111000000000`. Filed as #2662, and `test_a_decimal_keeps_all_38_digits` is a strict xfail for it. (The 39-digit mutant is still killed, because a 39-digit number prints as a DOUBLE.)
- Spark's decimal result types are skipped for a table or column whose name has a capital letter, in Spark SQL and in the DataFrame API: `SELECT Amt / b FROM t` gives DOUBLE, not DECIMAL(23,13). The schema keeps names as written (`normalize=False`), while Spark's `qualify` lower-cases the query. Filed as #2663, with two strict xfails. They also kill the two `normalize=False` → `True` mutants, which fix it.

### Not in this batch

Batch 27's modules.

