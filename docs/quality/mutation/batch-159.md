# Mutation testing, batch 159

A boundary re-score of six modules first scored in batches 1, 9, 10, 13 and 14, before `mutate.py` made boundary mutants (batch 36), run again with `--kinds boundary` only:
- PySpark's schema inference on this PC: bigint's two ends, decimal(38,18)'s limit, and rows with fewer names or values than fields (`spark_infer.py`, batch 10).
- The Databricks SQL client: an HTTP 400, a 500 to a statement, and a Catalog list that reaches its limit (`engines/databricks_sql.py`, batch 13).
- Local Delta types: quoted names, decimals of 38 digits, and the whole numbers a float holds (`localdata/delta.py`, batch 1).
- Notebook imports: a source file, an .ipynb and a .dbc archive at each of their size and count limits (`notebook_formats.py`, batch 9).
- The DataFrame methods this PC adds: randomSplit's weights, crosstab's 10000 columns, colRegex, sampleBy's fractions, and how long a body is a real one (`spark_api.py`, batch 10).
- A metadata project: a lookback and a delete count of 0, exceptions on their last day, and reads from an entity's own layer (`meta/project.py`, batch 14).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`), with the test files of those batches, given as paths relative to `sidecar/`. Each module has fewer than 150 boundary mutants, so all of them ran. Tests that read `docs/` were left out with `--deselect`, because `mutate.py` copies only `sidecar/`: for `notebook_formats.py`, test_snowflake_sql's `test_the_docs_table_is_the_codes`; for `spark_api.py`, test_spark_api's `test_the_coverage_page_matches_the_api`, test_spark_api_coverage's `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date`, `test_the_docs_link_the_list` and `test_the_readme_counts_match_spark_api_coverage`, and test_spark_coverage's `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`. The tests first passed once on the unmutated copy.

The new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, on DuckDB 1.5.6 and 2.0.0. They set the size and count limits to the exact size of a small file or archive. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_infer.py` | test_spark_infer_edges, test_spark_infer_schema, test_spark_infer_mutation (+ test_spark_infer_boundary) | 8 of 8 | 5 / 3 | **62.5%** | 8 / 0 | **100%** | none |
| `engines/databricks_sql.py` | test_credential_expiry, test_databricks_discovery, test_databricks_sql, test_databricks_sql_errors, test_databricks_sql_robust, test_paging, test_remote_text_lines, test_reply_text_one_line, test_databricks_sql_mutation (+ test_databricks_sql_boundary) | 10 of 10 | 7 / 3 | **70.0%** | 10 / 0 | **100%** | none |
| `localdata/delta.py` | test_append_conversions, test_conversions_everywhere, test_local_delta_types, test_parse_errors_plain, test_localdata_delta_mutation, test_safe_parse_readers (+ test_localdata_delta_boundary) | 7 of 7 | 5 / 2 | **71.4%** | 6 / 1 | **85.7%** | 1 equivalent |
| `notebook_formats.py` | test_notebook_formats, test_notebook_formats_edges, test_notebook_formats_roundtrip, test_notebook_formats_service_edges, test_deep_nesting, test_chart_libraries, test_snowflake_sql, test_ipynb_round_trip, test_notebook_formats_mutation (+ test_notebook_formats_boundary) | 9 of 9 | 3 / 6 | **33.3%** | 9 / 0 | **100%** | none |
| `spark_api.py` | test_spark_api, test_spark_api_coverage, test_spark_api_parity, test_spark_coverage, test_spark_local, test_spark_stat_methods, test_spark_views_text, test_spark_writer, test_spark_refusals, test_spark_api_mutation (+ test_spark_api_boundary) | 8 of 8 | 1 / 7 | **12.5%** | 7 / 1 | **87.5%** | 1 equivalent |
| `meta/project.py` | test_meta_project_edges, test_meta_project_folder, test_meta_atomic_save, test_meta_compare_edges, test_meta_compare_totals, test_meta_engine_edges, test_meta_source_systems_edges, test_project_version_walk, test_schema_evolution, test_versioned_contracts, test_lineage, test_shared_keys, test_breaking_changes, test_model_date_table, test_sealed_settings, test_platform_service_paths, test_meta_service_edges, test_meta_service_keys_edges, test_meta_keys_edges, test_meta_project_mutation (+ test_meta_project_boundary) | 12 of 12 | 10 / 2 | **83.3%** | 12 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_infer.py**: a decimal of exactly 10^20 is out of range and 10^20 - 1 is kept; -2^63 and 2^63 - 1 share the int shape and one past either is "big"; a struct of fewer values than fields fills the rest with NULL. → `test_spark_infer_boundary.py`.
- **engines/databricks_sql.py**: an HTTP 400 is a refusal; a 500 to a statement says it may have started; a Catalog list stops once it has exactly CATALOG_LIMIT items. → `test_databricks_sql_boundary.py`.
- **localdata/delta.py**: two quotes are an empty name. → `test_localdata_delta_boundary.py`.
- **notebook_formats.py**: an .ipynb of exactly MAX_TEXT is read; an archive of exactly MAX_DBC bytes, exactly MAX_DBC_ENTRIES files, and entries adding up to exactly MAX_DBC_TOTAL are read; exactly MAX_NOTEBOOKS notebooks are imported. → `test_notebook_formats_boundary.py`.
- **spark_api.py**: a weight of 0 is allowed and weights adding up to 0 are refused; a crosstab of exactly 10000 values is made; two backquotes are an empty regex; fractions of exactly 0 and 1 are allowed; a body of exactly 25 lines raising ContributionsAcceptedError is a real body, not a stub. → `test_spark_api_boundary.py`.
- **meta/project.py**: a lookback and a delete count of 0 are allowed. → `test_meta_project_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/delta.py` | 414 | `dst.bit_width >= src.bit_width` to `>` for floats | Equivalent: two floats of the same width are the same type, which `src.equals(dst)` already keeps. |
| `spark_api.py` | 399 | `value > 0` to `>=` for an infinite float in toJSON | Equivalent: it is only reached for an infinity, never 0. |

