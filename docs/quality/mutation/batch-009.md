# Mutation testing, batch 9

The notebook's file formats and files, and the SQL formatter, which no open pull request is changing:
- Notebook import and export in the Fabric, Databricks and Jupyter formats (`notebook_formats.py`).
- The notebook's Parameters bar and widgets (`notebook_inputs.py`).
- Notebook search, opening a file, Recently deleted and restore, and what was published (`notebook_files.py`).
- `runMultiple`'s DAG on this PC (`notebook_dag.py`).
- Format SQL (`sql_format.py`).

The run was at `main` 3dca3ce5b (2026-11-12), with `sidecar/tools/mutate.py` and the same method as the earlier batches (sample cap 150, seed 7), on DuckDB 1.5.6 and sqlglot 30.23. `notebook_formats.py` and `notebook_files.py` have more points than the cap, so 150 of each were sampled; the other three were run whole. `notebook.py` was in batch 7 and `sql_dialects.py` in batch 3, so they aren't here again.

Before each run, its test files were run unchanged in a copy of `sidecar/` alone, as the mutator runs them, to make sure every one passes there. Six tests read files outside `sidecar/` (the docs or `examples/`), so they were deselected: `test_snowflake_sql::test_the_docs_table_is_the_codes`, `test_side_by_side::test_examples_in_the_repository_are_the_generated_notebooks`, `test_notebook_parity::test_spark_reads_files_and_groups_like_pyspark` and three in `test_tsql_loud_gaps` that read the docs' refused list. With them in, every mutant would be "killed" by a missing file.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_formats.py` | test_notebook_formats, test_notebook_formats_edges, test_notebook_formats_roundtrip, test_notebook_formats_service_edges, test_deep_nesting, test_chart_libraries, test_snowflake_sql, test_ipynb_round_trip (+ test_notebook_formats_mutation) | 150 of 443 | 128 / 22 | **85.3%** | 145 / 5 | **96.7%** | 5 |
| `notebook_inputs.py` | test_notebook_inputs, test_notebook_inputs_edges, test_notebook_publish, test_prove, test_side_by_side; then also test_integrations_executions, test_side_by_side_edges, test_dbutils, test_dbutils_parity (+ test_notebook_inputs_mutation) | 117 of 117 | 108 / 9 | **92.3%** | 117 / 0 | **100%** | 0 |
| `notebook_files.py` | test_notebook_files, test_notebook_files_edges, test_notebook_files_service_edges, test_cloud_service_more_edges, test_deep_nesting, test_notebook_folders, test_notebook_mutation; then also test_notebook_batch_edges, test_notebook_formats, test_notebook_formats_service_edges, test_notebook_resources, test_kernel_sessions (+ test_notebook_files_mutation) | 150 of 225 | 112 / 38 | **74.7%** | 145 / 5 | **96.7%** | 5 |
| `notebook_dag.py` | test_notebook_run, test_notebookutils_local_mutation, test_notebookutils_parity, test_dbutils, test_dbutils_parity, test_notebook_parity, test_compute_check_ast, test_wizard (+ test_notebook_dag_mutation) | 88 of 88 | 71 / 17 | **80.7%** | 87 / 1 | **98.9%** | 1 |
| `sql_format.py` | test_sql_format_edges, test_sql_editor, test_snowflake_sql_variadic, test_tsql_contract, test_tsql_loud_gaps, test_code_format (+ test_sql_format_mutation) | 134 of 134 | 116 / 18 | **86.6%** | 132 / 2 | **98.5%** | 2 |

The "after" numbers re-run each survivor with `--only` and the new tests in. The existing test files added after the first run ("then also") killed 1 of the 24 survivors they were tried on alone (the resources folder a delete reports); the new tests killed the rest. Timeouts count as killed: a few `sql_format.py` mutants make the comment scanner loop forever, such as on a block comment that is never closed. All five new test files also pass on DuckDB 2.0.

### What the survivors were, and the tests that now kill them

- **notebook_formats.py**:
  - The 32 MB limit on a source file, and the .dbc limits: 16 MB for one entry, 128 MB in all (one byte over is refused), both checked from the archive's headers before anything is unpacked, and an entry exactly at the limit still read whole.
  - The unsafe-path message, which names the first 120 characters.
  - A Fabric .py export: a `# DBTITLE` line in Python code written as it is, and a Fabric cell marker in Python code escaped so it reads back as one cell.
  - A Fabric METADATA block giving its language to its own cell, including after a markdown cell.
  - The options note on a cell magic line, whole-notebook notes sorted before cell notes, a table's plain text (header and blank nulls), an empty markdown chunk in a percent script, .dbc commands without a position coming first, and a .dbc notebook named by its document or else its file.
  - → `test_notebook_formats_mutation.py`.
- **notebook_inputs.py**:
  - An annotated assignment (`days: int = 7`) as a parameter.
  - A widget left out when its default, its choices or its name isn't a literal.
  - Which widgets are checked against their choices: dropdown and multiselect only, a multiselect split on commas, a dropdown choice with a comma kept whole, and a value left at its default not checked.
  - → `test_notebook_inputs_mutation.py`.
- **notebook_files.py**:
  - Search: an empty search, at most 20 hits per notebook and 100 notebooks, and the 160-character snippet around a match at the start, middle and end of a long line.
  - A free name past "<name> 2", and its 80-character cut.
  - The 20 MB limit on a file to open, and the summary of an opened notebook (counts, language, which cells use Spark, singular and plural).
  - Keeping what was published in a folder made for it, twice; Fabric's cells with ids compared as a reorder.
  - Recently deleted: only entries older than 30 days purged, an expired entry whose file is gone, newest first, whether a resources folder was kept, a restore that brings back two levels of removed folders, and a restore that finds the name taken after all keeping both the notebook there and the deleted copy.
  - → `test_notebook_files_mutation.py`.
- **notebook_dag.py**:
  - The activities a list or a DAG gives when retries, intervals and cell timeouts are left out, and a non-dict activity.
  - The concurrency note only above one.
  - With a fake clock: each run given the DAG's time left (or no limit without one), an activity or a retry skipped once the time is out and still run with a second left, and the retry interval waited between attempts but not after the last or after a success.
  - → `test_notebook_dag_mutation.py`.
- **sql_format.py**:
  - Finding the `--` comments: after a string or name holding dashes, after doubled quotes, after a block comment (empty, holding dashes or a quote), a comment with no newline or space, nested array indexes, and a string or block comment that is never closed.
  - The row-limit check that stops LIMIT turning into TOP, the empty text for no difference, and a lost comment named by its first 40 characters.
  - → `test_sql_format_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `notebook_formats.py` | 214 | the options start 2 → 3 characters after the magic name | The character after the name is always a space or the line's end, which `strip()` removes either way. |
| `notebook_formats.py` | 443 | `lines[1:]` → `lines[0:]` | The header line comes before any marker, so it is never added to a block. |
| `notebook_formats.py` | 480, 495 | `n` default 0 → 1 | Every caller passes `n`. |
| `notebook_formats.py` | 547 | `m.group(1)` → `m.group(0)` | The heading is only searched for `[markdown]` and `parameters`, which `# %%` doesn't change. |
| `notebook_files.py` | 133, 353, 413, 414 | `unlink(missing_ok=True)` → `False` | Each file was written or read just before. |
| `notebook_files.py` | 333 | the purged entry's `unlink(missing_ok=True)` → `False` | Its JSON file was read just before. |
| `notebook_dag.py` | 72 | `concurrency or 0` → `or 1` | 0 and 1 are both "not above one". |
| `sql_format.py` | 156 | the doubled-quote loop turned off (`end == -1`, `end == -0`) | A doubled quote read as a closing quote and an opening one ends the same string. |

### Real bugs found

None in this batch.

### Not in this batch

- `sql.py` and `notebook_practices.py`, which open pull requests change.
- `notebook_folders.py` and the notebook `*_service.py` wrappers, for a later batch (`notebook_versions.py` and `notebook_resources.py` were in batch 4).

