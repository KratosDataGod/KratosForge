# Mutation testing, batch 241

A re-score of `engines/local_sql.py`, which batch 7 left at **94.7%**, and `snowflake_sql_parity.py`, which batch 18 left at **94.0%**, on today's `main`. Both were scored before boundary mutants, and `local_sql.py` has since gained Databricks SQL, Snowflake VARIANT results, the DuckDB 2.0 paths and refusals of statements that reach outside the session. So this run uses `--kinds all` and new samples. Batch 158 has already re-scored `local_sql.py`'s boundary mutants alone.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. `local_sql.py` now has 318 mutants and `snowflake_sql_parity.py` 646, so 150 of each were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Four tests read `docs/`, so they were deselected: `test_snowflake_sql_parity.py::test_the_docs_carry_the_table_and_say_no_account_was_used`, and `test_snowflake_coverage.py`'s `test_the_docs_link_the_list`, `test_the_docs_page_is_up_to_date` and `test_what_the_docs_say_runs_still_runs`. The machine was shared with other mutation runs, so the "after" runs raised each mutant's timeout from 120 s to 600 s (`mutate.TIMEOUT`); no run timed out.

`local_sql.py`'s set was batch 7's. Its "after" run adds the existing tests that cover the module's newer paths: test_databricks_table_statements, test_databricks_statement_parity, test_databricks_builtins_sql_cov2, test_variant_results, test_spark_ansi, test_spark_hash_refused, test_snowflake_sql_statements, test_local_sql_errors and test_databricks_coverage. From test_databricks_coverage, the three tests that read `docs/` were deselected.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/local_sql.py` | test_engines, test_local_sql_results, test_engine_compare, test_snowflake_sql_variadic, test_untranslatable_sql, test_local_sql_mutation, test_local_sql_boundary, test_local_sql_empty_cell (+ test_local_sql_rescore, and the existing files named above) | 150 of 318 (143 + 7) | 118 / 32 | **78.7%** | 113/143 → 138/143 | 5/7 → 5/7 | 143 / 7 | **95.3%** | 7 equivalent |
| `snowflake_sql_parity.py` | test_snowflake_sql_parity, test_snowflake_coverage_edges, test_snowflake_coverage, test_snowflake_sql_parity_mutation | 150 of 646 (150 + 0) | 145 / 5 | **96.7%** | 145/150 → 145/150 | none | 145 / 5 | **96.7%** | 5 equivalent; no new tests needed |

One gap was found and pinned with a strict xfail, not fixed. Take a Snowflake result with two VARIANT or ARRAY columns whose names differ only in case, such as `SELECT [1, 2]::ARRAY AS a, [3]::ARRAY AS A`. `_variants_as_json` leaves such a result as it is, because a projection by name can't tell the columns apart. So DuckDB 1.5.6 stops with "Unsupported Arrow type VARIANT", and 2.0.0 shows the VARIANTs' stored bytes. Test: `test_two_array_columns_named_alike_but_for_case_show_as_json`. A second test checks that one column never shows the other's values. The mutant that projects anyway does show them.

### What the survivors were, and the tests that now kill them

- **engines/local_sql.py**: most survivors are killed once the existing files above join the set: Databricks SQL's table statements and its refusal wording, `ERROR(...)` refusals written into a query, and the error paths. New tests:
  - The preview of a kept result starts at its first row.
  - A statement that reaches outside the session is named by its first word (`ATTACH isn't allowed ...`).
  - Spark SQL writes `to_json` as Spark does (a timestamp as `2026-01-02T03:04:05.000Z`), and Databricks SQL keeps DuckDB's.
  - SQLite refuses another dialect, plans only one statement, and shows the plan's steps (`SCAN`), not its row ids.
  - A VARIANT column never shows another column's values.
  - → `test_local_sql_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/local_sql.py` | 259, 565, 567 | `zip(..., strict=True)` to `False` | Equivalent: the names and types come from one relation, and SQLite's rows and names always have the same length (as batch 7 found). |
| `engines/local_sql.py` | 324 | the re-raise of a refusal that doesn't name Fabric removed | Equivalent: the `raise ValueError(_databricks_wording(text))` below raises a ValueError with the same text. The other Fabric phrases `_databricks_wording` rewrites only appear in refusals that also say "the way it runs in Fabric". |
| `engines/local_sql.py` | 429 (×2) | `duckdb_major() >= 2` to `>= 3` and to `> 2` | Equivalent, as in batch 158 (its line 403): on 1.5.6 neither takes the 2.0 path, and on 2.0.0 the older path gives the same rows. |
| `engines/local_sql.py` | 611 | `have < keep` to `<=` in SQLite's `_run` | Equivalent, as in batch 158 (its line 558): once `have` is `keep`, the slice is empty. |
| `snowflake_sql_parity.py` | 487 | HLL's sample `2` to `3` | Equivalent, as in batch 18 (its line 405): there are still two distinct values. |
| `snowflake_sql_parity.py` | 492 | APPROX_PERCENTILE's `range(11)` to `range(12)` | Equivalent, as in batch 18 (its line 411). |
| `snowflake_sql_parity.py` | 986, 989, 1028 | the expected value of an "untranslated" case | Equivalent: `run()` returns "untranslated" before it reads the expected value, and the docs table doesn't show it. |

