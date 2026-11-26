# Mutation testing, batch 205

A boundary re-score of four modules first scored before `mutate.py` made boundary mutants (`<` to `<=`, `>` to `>=`; see batch 36). Only the boundary mutants were run (`--kinds boundary`), so the scores below count those alone; each module's point score from its first batch stands as it was. The modules, with the batch that first scored each:
- Spark on this PC: reading, writing, SQL, query profiles and NOT NULL checks (`spark_local.py`, a boundary re-score of batch 8).
- The attached lakehouse: table writes, what a replay records, and OPTIMIZE, VACUUM and RESTORE from notebook code (`lakehouse.py`, a boundary re-score of batch 2).
- The order a notebook's runs go in (`notebook_dag.py`, a boundary re-score of batch 9).
- The Fabric Delta profile a local write keeps to (`delta_profile.py`, a boundary re-score of batch 5).

The run was at `main` bdf2c177 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran; each "after" run re-checked only the survivors, with `--only`. The tests given were each module's test files from its earlier batches, as paths relative to `sidecar/`, and they first passed once on the unmutated copy of `sidecar/`. Tests that read `docs/` or `examples/` fail in that copy, so they were left out with `--deselect`: `test_spark_read_options.py::test_the_docs_page_is_up_to_date` for `spark_local.py`, `test_notebook.py::test_notebook_reads_local_files_and_writes_tables` for `lakehouse.py`, `test_notebook_parity.py::test_spark_reads_files_and_groups_like_pyspark` for `notebook_dag.py`, and two tests each in `test_delta_profile_matrix.py` and `test_delta_profile_readers_matrix.py` for `delta_profile.py`. The `spark_local.py` after run also gave `test_query_profile.py`, which reads query profiles and wasn't in batch 8's set.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_local.py` | test_spark_local, test_spark_local_edges, test_spark_local_more_edges, test_spark_merge, test_spark_read_options, test_spark_refusals, test_spark_write_types, test_append_conversions, test_spark_compat, test_spark_compat_edges, test_duckdb_try_cast, test_spark_writer, test_spark_catalog_names, test_spark_csv_options, test_spark_json_parquet_options, test_spark_json, test_spark_local_mutation, test_spark_local_cov2 (+ test_query_profile, test_spark_local_boundary) | 10 of 10 | 5 / 5 | **50.0%** | 7 / 3 | **70.0%** | 3 equivalent |
| `lakehouse.py` | test_lakehouse_write_edges, test_notebook, test_onelake_writes, test_write_types, test_append_callers (+ test_lakehouse_boundary) | 11 of 11 | 5 / 6 | **45.5%** | 11 / 0 | **100%** | none |
| `notebook_dag.py` | test_notebook_run, test_notebookutils_local_mutation, test_notebookutils_parity, test_dbutils, test_dbutils_parity, test_notebook_parity, test_compute_check_ast, test_wizard, test_notebook_dag_mutation | 5 of 5 | 5 / 0 | **100%** | no change needed | **100%** | none |
| `delta_profile.py` | test_delta_profile_writes, test_local_delta, test_schema_evolution, test_delta_profile_mutation, test_delta_profile_matrix, test_delta_profile_readers, test_delta_profile_modern, test_delta_profile_readers_matrix | 5 of 5 | 5 / 0 | **100%** | no change needed | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_local.py**: a query profile lists up to exactly `PROFILE_NODES` steps and counts the rest; a row shorter than its schema is refused by the NOT NULL field it lacks, not with an `IndexError`. → `test_spark_local_boundary.py`.
- **lakehouse.py**: a VACUUM keeping exactly seven days (168 hours) of history is allowed, both in the check before the guard and against the table's own retention; RESTORE goes to version 0 but not to the current version; a VACUUM that commits no new version writes no history line; a table the notebook creates is recorded for a replay with no version before it (`None`, not -1). The VACUUM boundary was already pinned in `test_maintenance_failure_paths.py`, which isn't in batch 2's set. → `test_lakehouse_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_local.py` | 1453 | `len(pieces) > 1` to `>=` in `sql` | Equivalent: `_setting_pieces` returns `[]` or two or more pieces, never one. |
| `spark_local.py` | 599 | `len(pieces) > 1` to `>=` in `_session_setting_pieces` | Equivalent: its one caller, `_setting_pieces`, hands a single piece on to line 1453, which runs it as one statement, as it would `[]`. |
| `spark_local.py` | 1252 | `len(files) > 1` to `>=` for a Parquet read | Equivalent: DuckDB's `read_parquet` reads `[f]` as it reads `f`. |

